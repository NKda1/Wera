"""Previews, read in full on demand (21 Nov, SRCH-26, SRCH-32).

A preview is a job API's lead that a search kept rather than read (`normalise.from_lead`): a title,
a company, 270–500 characters of the ad, and a tracked link WERA never requests. It is ranked by CV
similarity alone and costs nothing until the person asks — "Read the full ad", or shortlisting it —
or "Find more" runs out of searches to send. Then it is read the way the search would have read it:

1. **One lookup** for where the employer posted it (`feeds.resource.original`, one Serper query
   through the provider guard, answered from Redis when any search asked it in the last six hours).
2. **The normal read ladder** on that page (`harvest.harvest`: the employer's feed, the page's job
   data, a browser render, the fast model, and a paid scrape only within the caller's caps), with
   the same checks a search's postings get (SRCH-23).
3. With no original found, or none readable, the preview is scored on its summary — `partial`,
   so the score is rough-only and capped (MATCH-04).

Either way the job keeps the preview's id, so its decisions, its "New" tag and the run that found
it stay with it (`absorb`), and it becomes `new`: on the shortlist once it is scored.

**The tracked link is never requested** — not to read the ad, not to check it is open. A preview
with no original keeps it as its apply URL, for the person to open themselves.

**A hunted link is already the posting** (22 Nov, `Job.found_on.kind == "posting"`): it is read
where it is, through the same ladder and checks (robots.txt, LinkedIn, a declined board, not a
posting, closed, too old), with no lookup. Its "summary" is only its link's text, so one that
can't be read — refused, disallowed, gone — is not scored on it: it stays a preview, and `Read`
says why (`unread`). An aggregator's copy (`kind == "copy"`) is looked for like a job API's lead.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire

from ..config import Site
from ..domain.dedupe import canonical_url
from ..http import host_of
from ..schemas.jobs import Job
from .feeds import FEEDS, Lead
from .firecrawl_search import SearchCaps, SearchOutcome
from .normalise import is_tracked_link
from .targets import Target

if TYPE_CHECKING:
    from ..schemas.profile import SearchProfile
    from .browser import Fetcher
    from .crawl_cache import CrawlCache
    from .serper import Serper

# A person's click reads one page, so one paid scrape at most when every free rung fails — the
# manual import's ceiling (ATS-04), for the same reason: one click must not run away with credits.
CLICK_CAPS = SearchCaps(max_queries=0, max_pages=1, max_credits=5)


@dataclass
class Read:
    """What reading some previews came to. `jobs` are the previews as read, under their own ids,
    status `new` (or `hidden`, by the filters, once the caller applies them)."""

    jobs: list[Job] = field(default_factory=list)
    # How many had their original ad found and read (a hunted link read where it is counts: it is
    # the original); the rest are scored on their summary.
    originals: int = 0
    looked: int = 0
    # 22 Nov: hunted links that couldn't be read, by id, and why — left previews, never scored on
    # their link's text.
    unread: dict[str, str] = field(default_factory=dict)


def is_direct(job: Job) -> bool:
    """A preview whose apply URL is the posting itself — a hunted link — read with no lookup."""
    return job.found_on is not None and job.found_on.kind == "posting"


def lead_of(job: Job) -> Lead:
    """The preview as the lead it was, for `feeds.resource.original`. The employer and the title as
    WERA saved them — an agency's repost is looked for under the employer it named (20 Nov), which
    is where the employer's own posting is."""
    return Lead(
        url=job.apply_url or "",
        title=job.title,
        company=job.company,
        location=job.location.raw,
        date=(job.posted_at or "")[:10] or None,
        snippet=job.description_md or "",
    )


def absorb(preview: Job, read: Job) -> Job:
    """The job as read, under the preview's identity.

    What the posting said wins — its title, place, description, salary and link are the employer's,
    where the preview's were a job API's summary of them. What is about WERA and the person stays:
    the id and key (so a decision or a fit report already pointing at it still does), when it was
    first seen and by which run (SRCH-25), whether it was opened, and the match it was ranked by.
    """
    seen = {(s.channel, s.detail) for s in read.sources}
    return read.model_copy(
        update={
            "id": preview.id,
            "dedupe_key": preview.dedupe_key,
            "found_by": preview.found_by or read.found_by,
            "first_seen": min(preview.first_seen, read.first_seen),
            "last_seen": max(preview.last_seen, read.last_seen),
            "seen_at": preview.seen_at or read.seen_at,
            "preview_match": preview.preview_match,
            "via": read.via or preview.via,
            "posted_by": read.posted_by or preview.posted_by,
            "posted_at": read.posted_at or preview.posted_at,
            "salary": read.salary or preview.salary,
            "sources": [*read.sources, *[s for s in preview.sources if (s.channel, s.detail) not in seen]],
        }
    )


def unread(preview: Job) -> Job:
    """A preview with no original to read: on the shortlist on its summary, scored rough-only."""
    return preview.model_copy(update={"status": "new"})


def read_many(
    previews: list[Job],
    *,
    serper: "Serper | None",
    sites: list[Site],
    profile: "SearchProfile | None",
    caps: SearchCaps,
    spend: SearchOutcome,
    now: int,
    cache: "CrawlCache | None" = None,
    fetch_html: "Fetcher | None" = None,
    parse: Callable[[str], dict[str, Any] | None] | None = None,
    redis: Any | None = None,
    lookups_at_once: int = 4,
    no_lookup: "set[str] | None" = None,
) -> Read:
    """Each preview read in full where its original can be found, else as it is, in the order given.

    The lookups run `lookups_at_once` at a time (H4, 21 Nov) and the pages `harvest.READERS` at a
    time; neither changes which job is which, since each answer is matched to its preview by
    position (a lookup) or by its link (a page). `serper` None — no key, or the day's Serper spent —
    reads none in full: every preview is scored on its summary. So is each in `no_lookup` (22 Nov):
    a good match a short search had no lookup left for, scored on its summary, not looked for.
    """
    from ..nodes.strategist import _excluded
    from . import harvest
    from .feeds import resource as finding

    out = Read()
    if not previews:
        return out
    leads = [lead_of(p) for p in previews]
    skip = no_lookup or set()

    def look(i: int) -> object:
        if is_direct(previews[i]):
            return None  # the posting itself: nothing to look for
        if previews[i].id in skip:
            return None  # no lookup left for it: scored on its summary
        if serper is None or getattr(serper, "stopped", None) or not leads[i].title or not leads[i].company:
            return None
        gl = (previews[i].location.country or "gb").lower()
        hit = finding.original(leads[i], serper=serper, sites=sites, gl=gl, hl="en", exclude=[])
        if hit is not None and profile is not None and _excluded(hit.title, profile):
            return None  # its own posting names a title the person said no to
        return hit

    with ThreadPoolExecutor(max_workers=max(1, min(lookups_at_once, len(previews)))) as pool:
        hits = list(pool.map(look, range(len(previews))))
    out.looked = sum(
        1 for p in previews if not is_direct(p) and p.id not in skip and serper is not None and p.title and p.company
    )

    targets: list[Target] = []
    for i, (preview, hit) in enumerate(zip(previews, hits, strict=True)):
        if is_direct(preview):
            url = preview.apply_url or ""
            if not url or is_tracked_link(url):
                continue  # never a job API's tracked link, whatever the row says
            targets.append(
                Target(
                    url,
                    host_of(url).removeprefix("www."),
                    "open",
                    f"Found on {preview.found_on.site}: {preview.title}",  # type: ignore[union-attr]
                    i,
                    preview.title,
                    "",
                    None,
                    country=(preview.location.country or "").lower() or None,
                    listing=preview.found_on.page,  # type: ignore[union-attr]
                )
            )
            continue
        if hit is None:
            continue
        url = hit.url  # type: ignore[attr-defined]
        name = FEEDS[preview.via].name if preview.via in FEEDS else "A job API"
        targets.append(
            Target(
                url,
                host_of(url).removeprefix("www."),
                "open",
                f"{name}: {preview.title}",
                i,
                hit.title,  # type: ignore[attr-defined]
                hit.snippet,  # type: ignore[attr-defined]
                leads[i].date,
                via=preview.via,
                country=(preview.location.country or "").lower() or None,
            )
        )
    got = (
        harvest.harvest(
            targets, caps=caps, spend=spend, now=now, sites=sites, cache=cache, fetch_html=fetch_html, parse=parse, redis=redis
        )
        if targets
        else harvest.HarvestOutcome()
    )
    by_link = {canonical_url(j.apply_url) or j.apply_url: j for j in got.jobs}
    for i, preview in enumerate(previews):
        if is_direct(preview):
            page = by_link.get(canonical_url(preview.apply_url) or preview.apply_url or "")
            if page is None:
                out.unread[preview.id] = _why_unread(preview, got)
                continue
            out.jobs.append(absorb(preview, page))
            out.originals += 1
            continue
        hit = hits[i]
        page = by_link.get(canonical_url(hit.url) or hit.url) if hit is not None else None  # type: ignore[attr-defined]
        if page is not None:
            if preview.via and not page.via:
                page.via = preview.via
            out.jobs.append(absorb(preview, page))
            out.originals += 1
        else:
            out.jobs.append(unread(preview))
    logfire.info(
        "Read {read} preview(s) in full: {originals} had their original ad",
        read=len(previews),
        originals=out.originals,
        refused=dict(got.rejected),
        unread=len(out.unread),
    )
    return out


def _why_unread(preview: Job, got: "Any") -> str:
    """Why a hunted link wasn't read, in words for a person: the reader's reason when it was the
    only page this round, else a general one."""
    if is_tracked_link(preview.apply_url):
        return "it is a job API's tracked link, which WERA never opens"
    reasons = list(got.rejected) if got is not None else []
    if len(reasons) == 1:
        return str(reasons[0]).replace("-", " ")
    return "the page couldn't be read"
