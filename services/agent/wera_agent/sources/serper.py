"""Serper: Google's results over an API, for the Strategist's discovery stage (SRCH-20, stage 2).

Discovery used to be Firecrawl search: one query per title × board, sent with the place as an
exact phrase (`"London, UK"`), and 11 of the last run's 12 came back empty. A browser can't ask
Google either — its robots.txt disallows `/search` for every agent, and WERA doesn't fetch a page
that says no. Serper answers the same question over an API, at one credit a query.

What this module does and doesn't do:

* **Asks, and returns what came back.** Classifying a result (`sources.postings`) and queueing it
  (`sources.targets`) are the caller's; nothing here reads a page.
* **Counts every query against a cap** (`max_queries`), so a run's spend is bounded however many
  tiers the 8–12 rule (SRCH-26) asks for.
* **Retries what is worth retrying** — a rate limit, a server error, a dropped connection — up to
  three tries (SRCH-07), and stops for the run on a refused key rather than paying to be told again.
* **Never returns a LinkedIn link** (SRCH-03): open queries exclude it, and a result that slips
  through is dropped here.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import logfire

from ..errors import ApiError
from ..http import is_denied, request
from ..logging import redact
from ..settings import get_settings

ENDPOINT = "https://google.serper.dev/search"
# Ten results is one credit; more costs more, and a posting past the tenth result is rarely one
# the person would have wanted first.
RESULTS_PER_QUERY = 10
MAX_TRIES = 3  # SRCH-07
BACKOFF_SECONDS = (1.0, 3.0)
# Google's date filter. Three weeks matches SRCH-24; if Serper won't take the numbered form the
# month is asked for instead and the Harvester's 21-day check does the cutting.
WINDOW = "qdr:w3"
FALLBACK_WINDOW = "qdr:m"


@dataclass(frozen=True)
class Result:
    url: str
    title: str
    snippet: str
    # As Google printed it ("3 days ago", "20 Oct 2026"), when it printed one.
    date: str | None
    position: int


@dataclass
class Serper:
    """One run's Serper client: the key, the cap, and what has been spent against it."""

    max_queries: int
    window: str = WINDOW
    sleep: Callable[[float], None] = time.sleep
    sent: int = 0
    answered: int = 0
    errors: list[str] = field(default_factory=list)
    # Why the client stopped asking: "cap" once `max_queries` is reached, or an error code when
    # Serper refused the key. A stopped client answers every later query with None.
    stopped: str | None = None

    def __post_init__(self) -> None:
        key = get_settings().serper_api_key
        if key is None or not key.get_secret_value().strip():
            raise ApiError(424, "SERPER_KEY_MISSING", "Add SERPER_API_KEY to .env.local to search for jobs.")
        self._key = key.get_secret_value().strip()

    @property
    def credits_used(self) -> int:
        """Serper charges for queries it answered; a refused or failed one costs nothing."""
        return self.answered

    def search(self, q: str, *, gl: str, hl: str, page: int = 1) -> list[Result] | None:
        """One query's results, or None when the cap, the key or the network said no."""
        if self.stopped is not None:
            return None
        if self.sent >= self.max_queries:
            self.stopped = "cap"
            return None
        self.sent += 1
        body: dict[str, object] = {"q": q, "gl": gl, "hl": hl, "num": RESULTS_PER_QUERY, "tbs": self.window}
        if page > 1:
            body["page"] = page
        for attempt in range(MAX_TRIES):
            try:
                response = request("POST", ENDPOINT, json=body, headers={"X-API-KEY": self._key})
            except ApiError:
                raise  # demo mode or a refused host: a fact about WERA's gates, not the network
            except Exception as exc:  # noqa: BLE001 — a dropped connection is worth another try
                if attempt == MAX_TRIES - 1:
                    return self._failed(q, type(exc).__name__)
                self.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                continue
            status = response.status_code
            if status == 200:
                self.answered += 1
                return _results(response.json())
            if status == 400 and body["tbs"] != FALLBACK_WINDOW:
                # The one 400 worth a second try: a date filter Serper doesn't accept. Asked again
                # with the month, and the month kept for the rest of the run.
                logfire.warn("Serper refused the {window} window; using a month instead", window=body["tbs"])
                self.window = body["tbs"] = FALLBACK_WINDOW
                continue
            if status in (401, 403):
                self.stopped = "SERPER_AUTH"
                return self._failed(q, f"Serper refused the key (HTTP {status})")
            if status == 429 or status >= 500:
                if attempt == MAX_TRIES - 1:
                    return self._failed(q, f"HTTP {status}")
                self.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                continue
            return self._failed(q, f"HTTP {status}: {redact(response.text[:160])}")
        return self._failed(q, "no answer")

    def _failed(self, q: str, why: str) -> None:
        logfire.warn("Serper query failed: {why}", why=why, query=q)
        self.errors.append(f"search {q!r}: {why}")
        return None


def _results(data: object) -> list[Result]:
    """The organic results, LinkedIn removed. Anything malformed is skipped, not raised."""
    organic = data.get("organic") if isinstance(data, dict) else None
    out: list[Result] = []
    for item in organic if isinstance(organic, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "").strip()
        if not url.startswith(("http://", "https://")) or is_denied(url):
            continue
        out.append(
            Result(
                url=url,
                title=str(item.get("title") or "").strip(),
                snippet=str(item.get("snippet") or "").strip(),
                date=str(item["date"]).strip() if item.get("date") else None,
                position=int(item.get("position") or len(out) + 1),
            )
        )
    return out
