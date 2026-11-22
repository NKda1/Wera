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

**Terms:** none stated on its API page (arbeitnow.com/blog/job-board-api, read 22 Nov) beyond
"requires no API key". Read them before setting `terms_reviewed_on`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ...clock import iso
from ...domain import filters, places
from ..ats_feeds import html_to_text
from ..provider_guard import Guard
from . import Lead, call

FEED = "https://www.arbeitnow.com/api/job-board-api"
FEED_UK = "https://www.arbeitnow.co.uk/api/job-board-api"
MAX_AGE = timedelta(days=21)


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
    titles = [t for t in dict.fromkeys(t.strip() for t in targets) if t]
    if titles and items:
        from ...nodes import prefilter  # meaning, judged in one batch rather than a call a title

        prefilter.warm_titles([*titles, *(str(i.get("title") or "") for i in items)])
    leads: list[Lead] = []
    seen: set[str] = set()
    for item in items:
        if len(leads) >= max_leads:
            break
        card = card_of(item, countries=wanted)
        if not card["url"] or not card["title"] or card["url"] in seen:
            continue
        if not _fresh(item, at) or not matches(item, card, titles=titles, countries=wanted, remote_ok=remote_ok):
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
    return Read(leads, _how(hows), len(items))


def matches(item: dict[str, Any], card: dict[str, Any], *, titles: list[str], countries: set[str], remote_ok: bool) -> bool:
    """One of the plan's titles, in a ticked country (or remote, for someone open to it)."""
    if not titles or not (filters.matches_a_target(card["title"], titles) or filters.close_in_meaning(card["title"], titles)):
        return False
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
