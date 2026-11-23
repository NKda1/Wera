"""Arbeitnow's job board API (Europe; SRCH-31, 22 Nov): no search, a feed of the latest jobs.

Arbeitnow lists jobs from employers' own job systems, mostly in Germany and its neighbours, with
each posting's whole text (`description`, HTML). It has no key and no search: `GET
/api/job-board-api?page=N` gives the newest jobs a page at a time, and `links.next` says whether
there is another. The UK's are a second feed, on arbeitnow.co.uk, read when GB is ticked.

So WERA reads the feed and matches it here, to the plan's titles (by their words or, with the real
embedder, their meaning — MATCH-01's own rules) and to the ticked countries (the job's `location`,
or `remote: true` for someone who doesn't say no to remote work). "Once a day" is the guard: each
page is one call shared by every search for 1,440 minutes (`guard.cache_minutes_by.arbeitnow`), so
the day's first search pays for at most `max_pages` pages a feed and the rest of the day's are
free. No scheduler. Postings older than 21 days are dropped here (SRCH-24); at most `max_leads`
matches become leads, newest first, as the feed has them.

**Cheaper by day (22 Nov, SPEC 3.30).** Matching by meaning embedded every title the feed carried:
31.3 s of the 49.5 s job-API step on 22 Nov, for 3,785 titles. Now only a title that could be one
of the plan's goes to the meaning match — one sharing a word stem with a target title (level words
included, first five letters, so "Analytics" meets "Analyst"), or in the profile's own role family
(`role_family.own_family`) — and only a fresh one in a ticked place, since those are the only ones
`matches` would ask about. On the 22 Nov feed (2,907 distinct titles) against six careers' titles
that keeps every title the full match keeps (`tests/test_arbeitnow_prefilter.py`). And the day's
matches are kept in Redis for 24 hours per titles, countries and feed day, so the day's second
search matches nothing at all.

**Terms:** none stated on its API page (arbeitnow.com/blog/job-board-api, read 22 Nov) beyond
"requires no API key". Read them before setting `terms_reviewed_on`.
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import logfire

from ...clock import iso
from ...domain import filters, places, role_family
from ..ats_feeds import html_to_text
from ..provider_guard import Guard
from . import Lead, call

FEED = "https://www.arbeitnow.com/api/job-board-api"
FEED_UK = "https://www.arbeitnow.co.uk/api/job-board-api"
MAX_AGE = timedelta(days=21)
# The day's matches (above). Not under `wera:answer:`, which holds what the API itself answered.
MATCHED = "wera:arbeitnow:matched:"
MATCHED_TTL_SECONDS = 24 * 60 * 60
# The day's matches found kept, or made (`evaluate search-latency`).
STATS: Counter[str] = Counter()
# How much of a word the pre-filter compares: "analy" for "Analyst" and "Analytics".
STEM = 5


def stems(title: str) -> set[str]:
    """A title's words, cut to their first `STEM` letters. Level words stay: "Account Manager
    Junior" is close in meaning to "Junior Financial Modeller", and only "junior" says so."""
    return {w[:STEM] for w in re.findall(r"[a-z0-9+#]+", filters.normalise_title(title)) if len(w) >= 2}


@dataclass(frozen=True)
class Near:
    """Whether a title could be one of `titles` by meaning, judged by words alone (no embedding):
    the meaning match is only asked about titles this passes."""

    stems: frozenset[str]
    family: str | None

    @classmethod
    def of(cls, titles: list[str]) -> "Near":
        return cls(frozenset().union(*(stems(t) for t in titles)), role_family.own_family(titles))

    def __call__(self, title: str) -> bool:
        if not filters.title_tokens(title) or stems(title) & self.stems:
            return True
        return self.family is not None and role_family.family_of(title) == self.family


@dataclass
class Read:
    """What one search took from the feed: the matched leads, how the pages were answered, and
    how many jobs were read to find them ("40 matched of 1,000 read")."""

    leads: list[Lead]
    how: str
    read: int


def read(
    guard: Guard,
    *,
    targets: list[str],
    countries: list[str],
    remote_ok: bool,
    max_pages: int,
    max_leads: int,
    now: datetime | None = None,
) -> Read:
    """The latest jobs matching `targets` (titles) in `countries` (lower case). `remote_ok`: the
    person doesn't say no to remote work, so a remote job counts wherever its employer is."""
    at = now or datetime.now(UTC)
    wanted = {c.lower() for c in countries}
    titles = [t for t in dict.fromkeys(t.strip() for t in targets) if t]
    key = _matched_key(titles, wanted, remote_ok=remote_ok, max_pages=max_pages, max_leads=max_leads, day=at.date().isoformat())
    if (known := _recall(guard, key)) is not None:
        STATS["kept"] += 1
        return known
    STATS["matched"] += 1
    urls = [FEED, *([FEED_UK] if "gb" in wanted else [])]
    items: list[dict[str, Any]] = []
    hows: list[str] = []
    for url in urls:
        for page in range(1, max(max_pages, 0) + 1):
            data, how = call(guard, "arbeitnow", url, params={"page": page})
            hows.append(how)
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            items += [row for row in rows if isinstance(row, dict)]
            if not (data.get("links") or {}).get("next"):
                break
    near = Near.of(titles)
    if titles and items:
        from ...nodes import prefilter  # meaning, judged in one batch rather than a call a title

        # Only the titles `matches` will ask the meaning of: fresh, in a ticked place, not already
        # one of the titles by its words, and near one by `Near`.
        asked = [
            title
            for item in items
            if (title := str(item.get("title") or "").strip())
            and _fresh(item, at)
            and _placed(item, countries=wanted, remote_ok=remote_ok)
            and not filters.matches_a_target(title, titles)
            and near(title)
        ]
        with logfire.span("arbeitnow match", read=len(items), asked=len(asked)):
            prefilter.warm_titles([*titles, *asked])
    leads: list[Lead] = []
    seen: set[str] = set()
    for item in items:
        if len(leads) >= max_leads:
            break
        card = card_of(item, countries=wanted)
        if not card["url"] or not card["title"] or card["url"] in seen:
            continue
        if not _fresh(item, at) or not matches(item, card, titles=titles, countries=wanted, remote_ok=remote_ok, near=near):
            continue
        seen.add(card["url"])
        leads.append(
            Lead(
                url=card["url"],
                title=card["title"],
                company=card["company"],
                location=card["locationRaw"],
                date=card["postedAt"],
                snippet=card["description"][:300],
                payload={"card": card},
            )
        )
    got = Read(leads, _how(hows), len(items))
    if items and all(how in ("fetched", "cached", "coalesced") for how in hows):
        _keep(guard, key, got)
    return got


def _matched_key(titles: list[str], countries: set[str], *, remote_ok: bool, max_pages: int, max_leads: int, day: str) -> str:
    """Everything that decides the day's matches: what was asked, the feed's day, and the meaning
    rule (its embedder and bar), so a change to any of them is a new key."""
    from ...nodes import prefilter
    from ...settings import get_settings

    blob = json.dumps(
        [
            sorted(titles),
            sorted(countries),
            remote_ok,
            max_pages,
            max_leads,
            day,
            prefilter.MODEL_NAME,
            prefilter.TITLE_MEANING_MIN,
            get_settings().wera_demo_mode,
        ],
        separators=(",", ":"),
    )
    return f"{MATCHED}{hashlib.sha256(blob.encode()).hexdigest()[:24]}"


def _recall(guard: Guard, key: str) -> Read | None:
    """The day's matches for this question, when another search has made them."""
    if guard.redis is None:
        return None
    try:
        raw = guard.redis.get(key)
        if raw is None:
            return None
        kept = json.loads(raw)
        return Read([Lead(**lead) for lead in kept["leads"]], "cached", int(kept["read"]))
    except Exception:  # noqa: BLE001 — a bad or missing value is a miss: the feed is matched again
        return None


def _keep(guard: Guard, key: str, got: Read) -> None:
    if guard.redis is None:
        return
    try:
        guard.redis.set(
            key, json.dumps({"leads": [asdict(lead) for lead in got.leads], "read": got.read}), ex=MATCHED_TTL_SECONDS
        )
    except Exception:  # noqa: BLE001 — the next search matches again
        logfire.warn("Couldn't keep the day's Arbeitnow matches")


def matches(
    item: dict[str, Any],
    card: dict[str, Any],
    *,
    titles: list[str],
    countries: set[str],
    remote_ok: bool,
    near: Near | None = None,
) -> bool:
    """One of the plan's titles, in a ticked country (or remote, for someone open to it). `near`,
    when given, decides which titles are asked about by meaning at all."""
    if not titles:
        return False
    title = card["title"]
    if not filters.matches_a_target(title, titles):
        if near is not None and not near(title):
            return False
        if not filters.close_in_meaning(title, titles):
            return False
    return _placed(item, countries=countries, remote_ok=remote_ok)


def _placed(item: dict[str, Any], *, countries: set[str], remote_ok: bool) -> bool:
    if item.get("remote") is True and remote_ok:
        return True
    code = places.country_code(str(item.get("location") or ""))
    return code is not None and code.lower() in countries


def card_of(item: dict[str, Any], *, countries: set[str]) -> dict[str, Any]:
    """A job as a card (`harvest._checked` reads it like Himalayas'). Its country is the one its
    location names when that is ticked; a remote job elsewhere belongs to none."""
    where = str(item.get("location") or "").strip()
    remote = item.get("remote") is True
    code = places.country_code(where)
    posted = _when(item.get("created_at"))
    card: dict[str, Any] = {
        "title": str(item.get("title") or "").strip(),
        "company": str(item.get("company_name") or "").strip(),
        "locationRaw": (f"Remote ({where})" if where else "Remote") if remote else (where or "Not stated"),
        "description": html_to_text(str(item.get("description") or "")),
        "postedAt": iso(posted.timestamp() * 1000) if posted else None,
        "url": str(item.get("url") or "").strip(),
        "country": code.upper() if code and code.lower() in countries else None,
    }
    if remote:
        card["remote"] = "remote"
    return card


def _fresh(item: dict[str, Any], now: datetime) -> bool:
    posted = _when(item.get("created_at"))
    return posted is None or posted >= now - MAX_AGE


def _how(hows: list[str]) -> str:
    """One word for the pages' answers: "fetched" when any page cost a call."""
    for word in ("fetched", "coalesced", "cached"):
        if word in hows:
            return word
    return hows[0] if hows else "failed"


def _when(value: object) -> datetime | None:
    """Arbeitnow's `created_at` is Unix seconds."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC) if seconds > 0 else None
