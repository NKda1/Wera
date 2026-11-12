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

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire

from ..clock import parse_ms
from ..config import Site
from ..domain.filters import DAY_MS, RECENCY_MS
from ..http import host_of, is_denied
from ..schemas.jobs import Job
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
# the 8–12 rule (SRCH-26) stops reading close to where it stops needing to.
BATCH = 4
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
) -> HarvestOutcome:
    """Read and check each target. `spend` carries the run's Firecrawl accounting across rounds."""
    rules = robots or Robots()
    declined = {s.domain.lower().removeprefix("www.") for s in sites if s.declined_on is not None}
    out = HarvestOutcome()
    unread: list[Target] = []

    for target in targets:
        reason = _gate(target, rules, declined, sites)
        if reason is None:
            card = scrape_card(target.url, CARD_SCHEMA, spend, caps, cache=cache, fetch_html=fetch_html, parse=parse)
            how = spend.last_how
            if card is None:
                reason = how or "unreadable"
                if how == "capped":
                    unread.append(target)
                    continue
            else:
                job, reason = _checked(card, target, now)
                if job is not None:
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


def _gate(target: Target, rules: Robots, declined: set[str], sites: list[Site]) -> str | None:
    """Why this target may not be read at all, or None. Checked before a single byte is fetched."""
    if is_denied(target.url):
        return "LinkedIn"
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
