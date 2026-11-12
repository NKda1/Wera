"""Firecrawl: the last resort for reading a posting, and the engine behind board discovery.

Board search used to start here — one Firecrawl search per title × board, every result paid for
and read — and a diagnosis of the real database (11 Nov) found it reading results pages, category
pages and blog posts as jobs, and storing Firecrawl's summary of the real ones instead of their
text. Discovery now belongs to the Strategist (Serper, SRCH-20) and reading to the Harvester, so
two things are left here:

* **`scrape_card`** — one posting read as cheaply as it can be: the free rungs in
  `sources.extract` first, and a paid Firecrawl scrape only when none of them could (SRCH-05).
  A paid read keeps the page's own text as the description, never Firecrawl's summary of it.
* **Site discovery's searches** (SRCH-12, `sources.site_discovery`), which still use Firecrawl
  search and borrow `_client`, `_retrying` and `_where` from here.

The caps are the point as much as the reading is. Firecrawl charges per credit, so a run carries a
budget for pages and credits, and stops paying cleanly as `partial` when it reaches one (SRCH-06).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire

from ..config import Site
from ..config import usable_sites as config_usable_sites
from ..http import is_denied
from ..logging import redact
from ..schemas.profile import SearchProfile
from . import extract

if TYPE_CHECKING:
    from .browser import Fetcher
    from .crawl_cache import CrawlCache

# SRCH-02: how "posted within" maps to a search-time filter.
# Firecrawl's search windows are a day, a week or a month, so 21 days asks for the month and
# `filters.too_old` cuts at three weeks on the way in.
TBS = {"24h": "qdr:d", "7d": "qdr:w", "21d": "qdr:m"}

LIMIT_PER_QUERY = 10  # SRCH-02
MAX_TRIES = 3  # SRCH-07
BACKOFF_SECONDS = (1.0, 3.0)

# What Firecrawl actually charges, checked against its published pricing on 2026-10-20. A
# search is 2 credits per 10 results, and `LIMIT_PER_QUERY` is 10, so one query is 2. A basic
# scrape is 1 credit and the JSON format adds 4 — and WERA always scrapes with the JSON format
# (SRCH-05), so every page is 5.
#
# These were both 1 until now, which made the per-run cap (SRCH-06) wrong by the factor that
# matters: a run allowed 100 "credits" could spend 500 real ones. The cap exists so a bill is
# never a surprise, so it has to count in the units the invoice is in. Re-check these after a
# Firecrawl pricing change, and keep `Store.estimate` in step — the dialog's figure and the cap
# have to be the same arithmetic or one of them is lying.
SEARCH_CREDITS = 2
JSON_SCRAPE_CREDITS = 5

# How much of a paid read's markdown is kept as the description. A posting is a few thousand
# characters; this leaves room for a long one and cuts a page that went on into footers.
MAX_DESCRIPTION = 20_000


@dataclass
class SearchCaps:
    """What one run may spend (SRCH-06). Credits are Firecrawl's own unit."""

    max_queries: int = 20
    max_pages: int = 30
    max_credits: int = 100
    # SRCH-20: Serper's own unit, one per query, bounded separately from Firecrawl's credits.
    max_serper_queries: int = 30


# SRCH-05: the shape asked of a scraped page. Shared with the manual import (ATS-04), which
# scrapes a single page the same way.
CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "company": {"type": "string"},
        "location": {"type": "string"},
        "remote": {"type": "string"},
        "postedAt": {"type": "string"},
        "description": {"type": "string"},
    },
    "required": ["title", "company"],
}


@dataclass
class SearchOutcome:
    # Work actually sent to Firecrawl. These two are what the caps are checked against, which is
    # why they stay "sent" rather than "answered": a cached answer cost nothing, so it must never
    # be the thing that stops a run (SRCH-10, rule 1).
    queries_run: int = 0
    pages_scraped: int = 0
    credits_used: int = 0
    # Work the cache answered. `credits_saved` is what it would have cost at Firecrawl's rates.
    cached_queries: int = 0
    cached_cards: int = 0
    credits_saved: int = 0
    # Pages read locally (`sources.extract`) against pages that still needed Firecrawl. The point
    # of counting both is that "the local path works" stops being an assumption: the ratio is the
    # only honest measure of whether the ladder is earning its keep, and a drift upwards in
    # `fallback_pages` is a board changing shape, which is worth knowing before the invoice says so.
    local_pages: int = 0
    fallback_pages: int = 0
    errors: list[str] = field(default_factory=list)
    capped: str | None = None  # which cap ended the run, if any
    # How the last `scrape_card` answered: "cache", a free rung ("json-ld", "model", …),
    # "firecrawl", or why it read nothing ("listing", "closed", "not-a-job", "capped", "unread").
    # The Harvester records it per posting, so "read for free" is a count, not a hope.
    last_how: str = ""

    @property
    def partial(self) -> bool:
        return self.capped is not None

    @property
    def queries_answered(self) -> int:
        """Queries whose results this run actually has, paid for or not.

        What a person means by "how many queries did it run". `queries_run` is the narrower
        accounting figure and is not the one to show anyone.
        """
        return self.queries_run + self.cached_queries


# Moved to `config` so the Strategist can ask the same question without importing this module;
# kept under its old name here for the callers that already use it.
usable_sites = config_usable_sites


def direct_sites(profile: SearchProfile, sites: list[Site]) -> list[Site]:
    """The boards WERA reads itself: reviewed, serving a target country, and `direct`.

    The same gates as `usable_sites` minus the one that sends a query to Firecrawl. Being in this
    list is what makes a board free rather than what makes it allowed — `reviewed` still decides
    that, and an unreviewed `direct` board is loaded, shown and never fetched.
    """
    return [s for s in sites if s.direct and s.reviewed and s.domain in profile.sites and s.serves(profile.countries)]


def _where(profile: SearchProfile) -> str:
    """The geography clause of a query, from the cities if there are any, else the countries.

    Cities first because they are what a posting's own text says — an ad for a London job says
    "London", not "GB". The country codes are only a fallback so a country-only profile still
    asks a question with a place in it.
    """
    places = [p.strip() for p in profile.locations if p.strip()]
    if not places:
        places = [COUNTRY_NAMES.get(c, c) for c in profile.countries]
    if not places:
        return ""
    if len(places) == 1:
        return f' "{places[0]}"'
    return " (" + " OR ".join(f'"{p}"' for p in places) + ")"


# Enough to turn a profile's country codes into words a job ad would actually use. Only the
# countries the boards in `config/sites.yaml` cover; an unknown code falls through as itself,
# which is a poor search term but an honest one.
COUNTRY_NAMES = {
    "GB": "United Kingdom",
    "IE": "Ireland",
    "NL": "Netherlands",
    "DE": "Germany",
    "FR": "France",
    "BE": "Belgium",
    "ES": "Spain",
    "CH": "Switzerland",
    "CZ": "Czechia",
    "US": "United States",
}


# Firecrawl's own scrape runs an LLM over the page, so it inherits LLM-grade latency — and the
# client was built with no timeout at all, which meant `_retrying` could only ever catch a
# timeout the SDK chose to raise. A page WERA is prepared to wait 20s for in a browser is not
# worth more than this from a paid API.
CLIENT_TIMEOUT_MS = 120_000


def _client() -> Any:
    from firecrawl.v2 import FirecrawlClient

    from ..settings import get_settings

    env = get_settings()
    key = env.firecrawl_api_key.get_secret_value() if env.firecrawl_api_key else ""
    try:
        return FirecrawlClient(api_key=key, timeout=CLIENT_TIMEOUT_MS)
    except TypeError:
        # The SDK's constructor signature is not something WERA controls. An unbounded client is
        # worse than a warning, but it is much better than a channel that can't be built at all.
        logfire.warn("Firecrawl client doesn't accept a timeout; requests are unbounded")
        return FirecrawlClient(api_key=key)


def _retrying(call: Any, what: str, errors: list[str]) -> Any | None:
    """SRCH-07: retry a rate limit or a server error, up to three tries, then give up on this
    one piece of work rather than the whole run."""
    for attempt in range(MAX_TRIES):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — the SDK raises its own hierarchy
            name = type(exc).__name__
            retryable = "RateLimit" in name or "InternalServer" in name or "Timeout" in name
            if not retryable or attempt == MAX_TRIES - 1:
                # The class name alone hid a rejected request behind a bare "ValidationError"
                # for a whole channel. The message says what was wrong with it; it is redacted
                # and truncated because it echoes the request back.
                detail = redact(" ".join(str(exc).split()))[:200] or name
                logfire.warn("Firecrawl {what} failed: {error}", what=what, error=detail)
                errors.append(f"{what}: {detail}")
                return None
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    return None


def scrape_card(
    url: str,
    schema: dict[str, Any],
    outcome: SearchOutcome,
    caps: SearchCaps,
    *,
    cache: "CrawlCache | None" = None,
    fetch_html: "Fetcher | None" = None,
    parse: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """SRCH-05: one job read off one new URL, as cheaply as it can be read.

    The ladder lives in `sources.extract` and is tried first; this function is what owns the
    credit caps, so Firecrawl stays here as the last rung. `fetch_html` and `parse` are the two
    rungs that need something from outside — a browser and a database session — and a caller that
    supplies neither still gets the free JSON-LD and ATS-feed rungs.

    Returns None when a cap is reached or no rung could read the page, so the caller treats it the
    same as a card it couldn't resolve.
    """
    # SRCH-03 comes first — ahead of the cache, because a denied page is read from nowhere, and
    # ahead of the caps, because a page that was never going to be fetched must not spend a cap
    # or mark the run partial. (It used to sit last, so asking for a LinkedIn URL while at the
    # page cap set `capped="pages"` on a run that had done nothing wrong.)
    if is_denied(url):
        outcome.last_how = "unread"
        return None

    if cache is not None:
        found, card = cache.card(url, schema)
        if found:
            # The bool is the whole point: `(True, None)` means "WERA read this page and there was
            # no job on it", which is a different fact from "WERA has never read this page" — and
            # confusing the two is how the 5 credits get spent twice.
            outcome.cached_cards += 1
            outcome.credits_saved += JSON_SCRAPE_CREDITS
            outcome.last_how = "cache"
            return card

    # The free rungs, before any cap is consulted: they cost no credits, so a run that has hit its
    # page or credit cap can still read pages this way. That is the whole point of the cap moving
    # from "stop reading" to "stop paying".
    local, how = extract.card(url, fetch_html=fetch_html, parse=parse)
    if how in extract.NOT_JOB_ANSWERS:
        # Read and understood: an advert slot, a listing, a closed page. Paying Firecrawl to read
        # it again would buy the same answer, so it stops here — and is remembered on the
        # unreadable TTL, so the next run doesn't ask the model either.
        logfire.info("{url} isn't an open job posting ({how}); not paying to read it again", url=url, how=how)
        if cache is not None:
            cache.put_card(url, schema, None)
        outcome.last_how = how
        return None
    if local is not None:
        outcome.local_pages += 1
        outcome.credits_saved += JSON_SCRAPE_CREDITS
        logfire.info("Read {url} with {how}, no credits spent", url=url, how=how)
        if cache is not None:
            cache.put_card(url, schema, _dated_card(local, cache.now))
        outcome.last_how = how
        return local

    if outcome.pages_scraped >= caps.max_pages:
        outcome.capped = outcome.capped or "pages"
        outcome.last_how = "capped"
        return None
    if outcome.credits_used >= caps.max_credits:
        outcome.capped = outcome.capped or "credits"
        outcome.last_how = "capped"
        return None

    outcome.fallback_pages += 1
    outcome.last_how = "firecrawl"
    client = _client()
    document = _retrying(
        lambda: client.scrape(
            url,
            # Markdown as well as the fields, at no extra credit: the JSON format's `description`
            # is Firecrawl's summary of the posting (a median 1,231 characters across the jobs
            # read this way, against 5,994 for the same boards read directly), and every later
            # prompt quotes the description as the employer's own words (LTR-02).
            formats=["markdown", {"type": "json", "schema": schema, "prompt": "Extract this job posting's fields."}],
            only_main_content=True,
        ),
        f"scrape {url}",
        outcome.errors,
    )
    outcome.pages_scraped += 1
    outcome.credits_used += JSON_SCRAPE_CREDITS
    if document is None:
        return None  # a transport failure is never cached: nothing was learnt about the page
    data = getattr(document, "json", None)
    card = data if isinstance(data, dict) else None
    markdown = getattr(document, "markdown", None)
    if card is not None and isinstance(markdown, str) and markdown.strip():
        card = {**card, "description": markdown.strip()[:MAX_DESCRIPTION]}
    # Remembered either way, and this is the half that closes the expensive leak. Three cases end
    # up here: a good card; a page whose JSON wasn't a dict (stored as `None`, on the unreadable
    # TTL); and — the common one — a dict that `normalise.from_scrape` will reject for want of a
    # title or a company, which is stored as the card it is. That last page will never become a
    # job, so it never enters `known_urls`, so before this it was re-read at 5 credits on every
    # run for the rest of time.
    if cache is not None:
        cache.put_card(url, schema, _dated_card(card, cache.now))
    return card


def _dated_card(card: dict[str, Any] | None, read_at: int) -> dict[str, Any] | None:
    """Resolve a relative `postedAt` to an absolute date **before the card is stored**.

    A posting's date is relative to when the page was *read*, not to when a cache served it. A
    card saying "2 days ago" was being stored verbatim and re-resolved against each later run's
    clock, so every cache hit moved the posting two days closer to today — a withdrawn job got
    younger each time it was served, and its freshness score and urgency group moved with it.
    MATCH-08's whole claim is that a date records where it came from; a date that drifts records
    nothing.

    Resolved here rather than re-based on read because it makes the stored card self-consistent:
    whenever it is served, it means the day it meant when it was written. Precision is unaffected
    — `normalise.dated` reads an ISO string back as `approximate`, which is what it was, and only
    an employer's own feed ever claims `exact`.
    """
    from .normalise import dated

    if not card or not card.get("postedAt"):
        return card
    when, _precision = dated(str(card["postedAt"]), read_at)
    return {**card, "postedAt": when} if when else card


def credits_remaining() -> int | None:
    """What Firecrawl says is left, for the estimate on the search dialog (PRF-05)."""
    try:
        usage = _client().get_credit_usage()
    except Exception:  # noqa: BLE001 — an estimate is not worth failing a request for
        return None
    value = getattr(usage, "remaining_credits", None)
    return int(value) if isinstance(value, int | float) else None
