"""The Harvester: reading the postings the Strategist queued (SRCH-20, stage 3).

The doing half of the search pipeline. The Strategist (`nodes.strategist`) decides what to search
for, finds the postings and queues them in Redis; this reads them, one posting at a time, as
cheaply as each can be read, checks what it read, and hands the jobs that pass to fit scoring.

It used to be something else: a pool of browsers rendering two boards' own search pages and
taking every `/jobs/<anything>` link off them, which let category pages through as jobs. Finding
postings is now discovery's job, and only a URL classified as a posting ever reaches here.

**Reading** is the existing ladder (`firecrawl_search.scrape_card`): the employer's own feed, the
page's job markup over plain HTTP, the same after a browser render for boards that draw with
JavaScript, then the fast model on the page text — all free — and a paid Firecrawl scrape only
when none of them could. What is stored as the description is always the posting's own text.

**A posting from a full-text job API** (SRCH-31, `Target(source="feed")`) is read from the API
instead — Reed's details call, USAJOBS' own payload — and never fetched as a page, so robots.txt
and the posting classifier, which are about pages, don't apply to it. Everything below does.

**Checking** (SRCH-23) refuses, before anything is saved:

* a URL that isn't a posting — the drift guard, whatever put it in the queue;
* LinkedIn, a board the person declined, a page robots.txt disallows;
* a page that turned out to be a listing, closed, a 404 or redirected to a search page;
* a card without a title and a company, or with under 200 characters of description;
* a posting more than 21 days old (SRCH-24). One with no date is kept.

A refusal is remembered in the queue for a week (`TargetQueue.reject`), so discovery doesn't queue
the same posting again tomorrow. A posting left unread because a cap stopped paying goes back to
the front of the queue rather than being lost.
"""

import contextlib
import dataclasses
import queue as queues
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire

from ..clock import parse_ms
from ..config import Site
from ..domain.filters import DAY_MS, RECENCY_MS
from ..http import host_of, is_denied
from ..schemas.jobs import InternationalPost, Job, Salary
from . import normalise
from .firecrawl_search import CARD_SCHEMA, SearchCaps, SearchOutcome, scrape_card
from .postings import classify, is_closed_page
from .robots import Robots
from .targets import Target

if TYPE_CHECKING:
    from .browser import Fetcher
    from .crawl_cache import CrawlCache
    from .targets import TargetQueue

# Postings read per round. Small, so scoring starts on the first few while the rest wait — and so
# the 8–12 rule (SRCH-26) stops reading close to where it stops needing to. 6 since 20 Nov: two
# rounds of `READERS`.
BATCH = 6
# Postings read at once (20 Nov). Reading was 129–162 s of a 4–6 minute search, one posting after
# another, each mostly waiting on someone else's server. Three, not more: they are other people's
# sites, and the fast model reading a page is a call each. 1 on SQLite (one writer).
READERS = 3
# SRCH-23: less than this is a cookie banner or a teaser, not a posting anyone can judge.
MIN_DESCRIPTION = 200
# SRCH-24: the oldest posting worth reading.
MAX_AGE_MS = RECENCY_MS["21d"]

# Why a posting was refused, for the run's counts and Logfire. Each is a fact about the posting, so
# each is remembered for a week; a cap or a dropped connection is a fact about the run, and isn't.
NOT_A_POSTING = "not a posting"
TRANSIENT = frozenset({"capped", "unread", "firecrawl"})


@dataclass
class HarvestOutcome:
    """What one round of reading produced, and what it cost."""

    jobs: list[Job] = field(default_factory=list)
    # How each accepted posting was read: "json-ld", "model", "firecrawl", "cache", …
    by_path: Counter = field(default_factory=Counter)
    # Why each refused posting was refused.
    rejected: Counter = field(default_factory=Counter)
    put_back: int = 0


def harvest(
    targets: list[Target],
    *,
    caps: SearchCaps,
    spend: SearchOutcome,
    now: int,
    sites: list[Site],
    queue: "TargetQueue | None" = None,
    robots: Robots | None = None,
    cache: "CrawlCache | None" = None,
    fetch_html: "Fetcher | None" = None,
    parse: Callable[[str], dict[str, Any] | None] | None = None,
    redis: Any | None = None,
    on_read: Callable[[Target], None] | None = None,
    readers: int | None = None,
) -> HarvestOutcome:
    """Read and check each target. `spend` carries the run's Firecrawl accounting across rounds.
    `on_read` is told each posting as it is about to be read, for the run's progress (UX-01).

    Up to `readers` postings are read at once (20 Nov), and checked in the order given, so what is
    kept doesn't depend on which site answered first. Each read keeps its own Firecrawl tally and
    adds it to `spend` when it lands: the caps are checked against what had landed when a read
    began, so three paid reads at once can pass a cap by two pages at most — as three scoring
    workers can pass the weekly budget by two calls. `fetch_html` must be safe to call from any
    thread (a `BrowserLane`'s is)."""
    rules = robots or Robots()
    declined = {s.domain.lower().removeprefix("www.") for s in sites if s.declined_on is not None}
    out = HarvestOutcome()
    unread: list[Target] = []
    lock = threading.Lock()

    def read(target: Target) -> tuple[str | None, dict[str, Any] | None, str]:
        reason = _gate(target, rules, declined, sites)
        if reason is not None:
            return reason, None, ""
        if on_read is not None:
            on_read(target)
        if target.source == "feed":
            card, how = _from_feed(target, redis)
            return None, card, how
        with lock:
            mine = dataclasses.replace(spend, errors=[])
        before = dataclasses.replace(mine)
        card = scrape_card(target.url, CARD_SCHEMA, mine, caps, cache=cache, fetch_html=fetch_html, parse=parse)
        with lock:
            _add(spend, mine, before)
        return None, card, mine.last_how

    workers = max(1, min(readers if readers is not None else _readers(), len(targets)))
    if workers == 1:
        answers = [read(t) for t in targets]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="harvest") as pool:
            answers = list(pool.map(read, targets))

    for target, (reason, card, how) in zip(targets, answers, strict=True):
        if reason is None:
            if card is None:
                reason = how or "unreadable"
                if how == "capped":
                    unread.append(target)
                    continue
            else:
                job, reason = _checked(card, target, now)
                if job is not None:
                    _from_api(job, card, target)
                    out.jobs.append(job)
                    out.by_path[how] += 1
                    continue
        out.rejected[reason] += 1
        if queue is not None and reason not in TRANSIENT:
            queue.reject(target.url, reason)

    if unread and queue is not None:
        out.put_back = queue.put_back(unread)
    logfire.info(
        "Harvested {read} posting(s): {accepted} accepted",
        read=len(targets),
        accepted=len(out.jobs),
        by_path=dict(out.by_path),
        rejected=dict(out.rejected),
    )
    return out


def _readers() -> int:
    from ..db.session import engine

    return 1 if engine().dialect.name == "sqlite" else READERS


# `SearchOutcome`'s tallies: what one read adds to the run's.
_TALLIES = tuple(f.name for f in dataclasses.fields(SearchOutcome) if f.name not in ("errors", "capped", "last_how"))


def _add(spend: SearchOutcome, mine: SearchOutcome, before: SearchOutcome) -> None:
    """What one read spent, added to the run's: the difference its own tally made."""
    for name in _TALLIES:
        setattr(spend, name, getattr(spend, name) + getattr(mine, name) - getattr(before, name))
    spend.errors += mine.errors
    spend.capped = spend.capped or mine.capped
    spend.last_how = mine.last_how


class BrowserLane:
    """The browser on one thread of its own, so postings read at once can share it (20 Nov).

    Playwright's sync API belongs to the thread that started it (`browser.session`), and reading
    three postings at once means three threads asking it to render. Here one thread opens the
    session, renders whatever is asked, and closes it, and `fetch` hands it a URL from any thread
    and waits for the page. Renders take turns; the plain-HTTP rungs, which are most reads, don't.
    """

    def __init__(self, opener: Callable[[], Any] | None = None) -> None:
        from . import browser

        self._open = opener or browser.session
        self._asks: queues.Queue[tuple[str, Future] | None] = queues.Queue()
        self._thread = threading.Thread(target=self._serve, name="browser-lane", daemon=True)

    def _serve(self) -> None:
        with self._open() as fetch_html:
            while (ask := self._asks.get()) is not None:
                url, answer = ask
                try:
                    answer.set_result(fetch_html(url))
                except Exception as exc:  # noqa: BLE001 — the caller gets it, as if it had called
                    answer.set_exception(exc)

    def fetch(self, url: str) -> str | None:
        answer: Future = Future()
        self._asks.put((url, answer))
        return answer.result()

    def __enter__(self) -> Callable[[str], str | None]:
        self._thread.start()
        return self.fetch

    def __exit__(self, *_exc: object) -> None:
        self._asks.put(None)
        self._thread.join(timeout=30)


@contextlib.contextmanager
def browser_lane() -> Iterator[Callable[[str], str | None]]:
    """`browser.session()`, for readers on several threads."""
    with BrowserLane() as fetch:
        yield fetch


def _from_feed(target: Target, redis: Any | None) -> tuple[dict[str, Any] | None, str]:
    """A full-text API's posting, as a card. Reed's is one details call (shared through the guard,
    so a posting two people's searches queued is read once); USAJOBS' came with the search."""
    from .feeds import reed
    from .provider_guard import Guard

    payload = target.payload or {}
    if target.via == "reed" and "jobId" in payload:
        return reed.read(payload, Guard(redis, "reed")), "reed"
    if isinstance(payload.get("card"), dict):
        return payload["card"], target.via or "feed"
    return None, "unreadable"


def _from_api(job: Job, card: dict[str, Any], target: Target) -> None:
    """What a job API knows that a page doesn't say plainly: who found it, the salary as fields,
    (ReliefWeb, SRCH-35) that the employer is an international organisation, and (Himalayas,
    22 Nov) the job's page there for its label, and the country a remote job is open to."""
    if target.via:
        job.via = target.via
    if card.get("viaUrl"):
        job.via_url = str(card["viaUrl"])
    if "country" in card:
        # A card that says where it is open: the country asked, or none for a job open anywhere.
        # "Remote (United Kingdom, Netherlands)" names no city.
        city = None if card.get("remote") == "remote" else job.location.city
        job.location = job.location.model_copy(update={"city": city, "country": card["country"] or None})
    if job.salary is None and isinstance(card.get("salary"), dict):
        with contextlib.suppress(ValueError):
            job.salary = Salary(**card["salary"])
    if job.international is None and isinstance(card.get("international"), dict):
        with contextlib.suppress(ValueError):
            job.international = InternationalPost(**card["international"])


def _gate(target: Target, rules: Robots, declined: set[str], sites: list[Site]) -> str | None:
    """Why this target may not be read at all, or None. Checked before a single byte is fetched."""
    if is_denied(target.url):
        return "LinkedIn"
    if target.source == "feed":
        return None  # read from the API, not fetched as a page (SRCH-31)
    if normalise.is_tracked_link(target.url):
        return "tracked link"  # a job API's paid click, never requested (SRCH-32)
    if classify(target.url, title=target.title, sites=sites) != "posting":
        return NOT_A_POSTING
    host = host_of(target.url).removeprefix("www.")
    if any(host == d or host.endswith(f".{d}") for d in declined):
        return "declined board"
    try:
        if not rules.allows(target.url):
            return "robots.txt disallows"
    except Exception:  # noqa: BLE001 — a robots file WERA can't read is a "don't"
        return "robots.txt disallows"
    return None


def _checked(card: dict[str, Any], target: Target, now: int) -> tuple[Job | None, str]:
    """SRCH-23 on a card that was read: a job, or why not."""
    description = str(card.get("description") or "").strip()
    job = normalise.from_scrape(card, url=target.url, site=target.host, markdown=description or None, now=now)
    if job is None:
        return None, "no title or company"
    if len(description) < MIN_DESCRIPTION:
        return None, "description too short"
    if is_closed_page(job.title, description):
        return None, "closed"
    if job.posted_at:
        try:
            age = now - parse_ms(job.posted_at)
        except (ValueError, TypeError):
            age = 0
        # A day of slack for a date the board only gave as "3 weeks ago" (MATCH-08).
        slack = DAY_MS if job.posted_precision == "approximate" else 0
        if age > MAX_AGE_MS + slack:
            return None, "older than 21 days"
    return job, ""
