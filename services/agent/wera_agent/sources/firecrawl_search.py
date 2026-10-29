"""Board search through Firecrawl (SRCH-01…11).

**One query per target title × board** (SRCH-01/11), with LinkedIn excluded from every call
whatever the config says (SRCH-03). One board per query is a correction, not a detail: a single
query carrying four `include_domains` and `limit: 10` lets the search engine's ranking decide how
the ten slots are shared, and it does not share them — a real run came back almost entirely from
the one board with the best SEO while three reviewed boards went unread and still cost their part
of the bill. Ten slots each, or it isn't coverage.

Which boards those are comes from the countries the profile targets (SRCH-11), because most
boards are national and asking a UK graduate board about Berlin spends credits it cannot answer.
Cities are named in the query text rather than multiplied into the grid, and what a job's
location is actually allowed to be is decided afterwards by `filters.wrong_location`, which reads
the posting instead of trusting a search engine to have honoured a word.

Results WERA already knows are skipped before anything is scraped (SRCH-05), and what the last
crawl paid for is remembered for a few hours (SRCH-10) — together, that is what makes a repeat
run nearly free.

The caps are the point of this module as much as the searching is. Firecrawl charges per credit,
so a run carries a budget for queries, pages and credits, and stops cleanly as `partial` when it
reaches one (SRCH-06). A run that can't finish is better than a bill nobody expected.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire

from ..config import Site
from ..domain.dedupe import canonical_url
from ..http import DENY, host_of, is_denied
from ..logging import redact
from ..schemas.profile import SearchProfile

if TYPE_CHECKING:
    from .crawl_cache import CrawlCache

# SRCH-02: how "posted within" maps to a search-time filter.
TBS = {"24h": "qdr:d", "7d": "qdr:w", "30d": "qdr:m"}

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


@dataclass
class SearchCaps:
    """What one run may spend (SRCH-06). Credits are Firecrawl's own unit."""

    max_queries: int = 20
    max_pages: int = 30
    max_credits: int = 100


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
class Hit:
    """A search result, before anything is scraped."""

    url: str
    title: str
    snippet: str
    site: str


@dataclass
class SearchOutcome:
    hits: list[Hit] = field(default_factory=list)
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
    errors: list[str] = field(default_factory=list)
    capped: str | None = None  # which cap ended the run, if any

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


def usable_sites(profile: SearchProfile, sites: list[Site]) -> list[Site]:
    """The boards this search may actually ask (SRCH-03/04/11).

    Three gates, in order of how non-negotiable they are: LinkedIn is never searched whatever
    anything says; a site's terms must have been reviewed; and the board has to serve somewhere
    the person is looking. A profile naming no countries passes the last gate for everything,
    which is what keeps a profile written before SRCH-11 working.
    """
    return [s for s in sites if s.domain not in DENY and s.reviewed and s.domain in profile.sites and s.serves(profile.countries)]


def enabled_domains(profile: SearchProfile, sites: list[Site]) -> list[str]:
    """Only sites whose terms the person has reviewed, that serve a target country, and never
    LinkedIn (SRCH-03/04/11)."""
    return [s.domain for s in usable_sites(profile, sites)]


@dataclass
class Query:
    """One search: the text to ask, and the single board to ask it of."""

    text: str
    domain: str


def queries_for(profile: SearchProfile, sites: list[Site]) -> list[Query]:
    """SRCH-01/11: one query per title × board, with the places named in the text.

    **One board per query, which is the whole change here.** Sending one query with four
    `include_domains` and `limit: 10` meant the search engine's ranking decided the split, and
    it does not split — in a real run nearly every result came back from the single board with
    the best SEO, while three reviewed boards went unread and still cost their share of the
    credits. A board with its own query gets its own ten slots and cannot be crowded out.

    It is also *cheaper* than what it replaces, because the location grid collapses into the
    query text: four titles × five locations was twenty queries, and four titles × four boards
    is sixteen. Where a job is allowed to be is enforced afterwards by `filters.wrong_location`,
    which reads the posting rather than trusting a search engine to have honoured a city name.

    The places go in as a bare `OR` group rather than one query each. A board search is a
    keyword match, not a database query, so naming the cities is a hint either way — and the
    filter behind it is the thing that actually decides.
    """
    boards = usable_sites(profile, sites)
    titles = [t.strip() for t in profile.titles if t.strip()]
    if not boards or not titles:
        return []
    where = _where(profile)
    return [Query(text=f'"{title}"{where}', domain=s.domain) for title in titles for s in boards]


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


def _client() -> Any:
    from firecrawl.v2 import FirecrawlClient

    from ..settings import get_settings

    env = get_settings()
    key = env.firecrawl_api_key.get_secret_value() if env.firecrawl_api_key else ""
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


def _hits_from(result: Any, allowed: list[str]) -> list[Hit]:
    """Firecrawl returns its own objects; only web results with an allowed host are kept."""
    out: list[Hit] = []
    for item in getattr(result, "web", None) or []:
        url = getattr(item, "url", None) or ""
        host = host_of(url)
        if not url or not host:
            continue
        # Belt and braces: the exclude list is sent too, but a result from LinkedIn is dropped
        # here as well, because SRCH-03 is not a preference.
        if any(host == d or host.endswith(f".{d}") for d in DENY):
            continue
        site = next((d for d in allowed if host == d or host.endswith(f".{d}")), host)
        out.append(
            Hit(
                url=url,
                title=(getattr(item, "title", None) or "").strip(),
                snippet=(getattr(item, "description", None) or "").strip(),
                site=site,
            )
        )
    return out


def search(
    profile: SearchProfile,
    sites: list[Site],
    caps: SearchCaps,
    known_urls: set[str],
    *,
    cache: "CrawlCache | None" = None,
    on_query: Callable[["SearchOutcome", int, int], None] | None = None,
) -> SearchOutcome:
    """Runs the profile's queries and returns the results WERA hasn't seen before.

    `on_query(outcome, answered, planned)` fires after every query, cached or paid. Twenty
    queries is minutes of wall clock, and without this the screen had nothing to say for any of
    it — a silent process holding a terminal is read as a hung one, and so is a frozen bar.
    """
    outcome = SearchOutcome()
    queries = queries_for(profile, sites)
    if not queries:
        return outcome

    tbs = TBS.get(profile.recency)
    # Canonicalised on the way in as well as on the way out, so the comparison is right whether
    # the caller handed over canonical URLs or raw ones. `Store.known_apply_urls` canonicalises,
    # but a set of URLs is the kind of argument that acquires a second caller.
    known = {canonical_url(u) or u for u in known_urls}
    # Built on the first query that is actually sent. A fully-cached run shouldn't construct a
    # client it never uses — with no key that can raise and take the whole channel down.
    client: Any | None = None
    seen: set[str] = set()

    for position, query in enumerate(queries):
        # One board per query (SRCH-11), so the allow-list is a list of one. The cache key is
        # built from the same list, which means re-pointing a query at another board is a miss
        # rather than a stale hit.
        domains = [query.domain]
        cached = cache.hits(query.text, domains, tbs, LIMIT_PER_QUERY) if cache is not None else None
        if cached is not None:
            # `[]` is an answer — "nothing matched this query" — so a miss has to be `None`.
            outcome.cached_queries += 1
            outcome.credits_saved += SEARCH_CREDITS
            hits = _hits_from_cache(cached)
        else:
            # The caps are checked here and nowhere else: only a request about to be *sent* can
            # be stopped by one (SRCH-06). And a cap no longer ends the loop — the queries after
            # it may be free, and declining a free answer buys nobody anything.
            if outcome.queries_run >= caps.max_queries:
                outcome.capped = outcome.capped or "queries"
                continue
            if outcome.credits_used >= caps.max_credits:
                outcome.capped = outcome.capped or "credits"
                continue

            if client is None:
                client = _client()
            result = _retrying(
                # SRCH-03 is not enforced here: Firecrawl refuses a request carrying both an
                # include list and an exclude list, and sending both failed *every* query. An
                # allow-list is the stronger of the two anyway — nothing outside it can come back —
                # and the deny list is still applied by `usable_sites` (which never puts a denied
                # domain in a query), `_hits_from` (which drops a denied host from the results)
                # and `scrape_card` (which refuses to fetch one).
                lambda q=query.text, d=domains, c=client: c.search(
                    q,
                    sources=["web"],
                    include_domains=d,
                    limit=LIMIT_PER_QUERY,
                    tbs=tbs,
                ),
                f"search {query.text}",
                outcome.errors,
            )
            outcome.queries_run += 1
            outcome.credits_used += SEARCH_CREDITS
            if result is None:
                # Nothing was learnt about this query, so nothing is cached: remembering a rate
                # limit would turn one bad minute into six hours of empty runs.
                continue
            hits = _hits_from(result, domains)
            if cache is not None:
                # After the `result is None` guard, deliberately. `_hits_from` returns `[]` both
                # for a genuinely empty result and for a reply missing `.web`, and that guard is
                # the only thing telling "nothing matched" from "the SDK returned something odd".
                cache.put_hits(query.text, domains, tbs, LIMIT_PER_QUERY, [vars(h) for h in hits])

        for hit in hits:
            # SRCH-05, applied *after* the cache: `known_urls` changes between runs, so what gets
            # remembered is the search's answer, not this run's filtering of it.
            #
            # Compared canonically, matching `Store.known_apply_urls` and `dedupe.dedupe_key`: a
            # posting that comes back with a tracking tag is the same posting, and treating it as
            # new costs 5 credits and backdates the job's `last_seen`.
            identity = canonical_url(hit.url) or hit.url
            if identity in known or identity in seen:
                continue
            seen.add(identity)
            outcome.hits.append(hit)

        if on_query is not None:
            # Position in the plan, not `queries_answered`: a query refused by a cap advances the
            # loop without answering, so counting answers would leave the bar stuck short of the
            # end on exactly the runs a person most wants to watch.
            on_query(outcome, position + 1, len(queries))

    logfire.info(
        "Board search answered {queries} query/queries ({cached} from cache) and found {new} new result(s)",
        queries=outcome.queries_answered,
        cached=outcome.cached_queries,
        new=len(outcome.hits),
    )
    return outcome


def _hits_from_cache(rows: list[dict[str, Any]]) -> list[Hit]:
    """Rehydrate stored hits. A payload written by an older build must never raise.

    Field by field rather than `Hit(**row)`: a cache is not a contract, and a `TypeError` here
    would take down a channel over an entry that could simply have been ignored.
    """
    out: list[Hit] = []
    for row in rows:
        url = str(row.get("url") or "")
        if not url:
            continue
        out.append(
            Hit(
                url=url,
                title=str(row.get("title") or ""),
                snippet=str(row.get("snippet") or ""),
                site=str(row.get("site") or host_of(url)),
            )
        )
    return out


def scrape_card(
    url: str,
    schema: dict[str, Any],
    outcome: SearchOutcome,
    caps: SearchCaps,
    *,
    cache: "CrawlCache | None" = None,
) -> dict[str, Any] | None:
    """SRCH-05: one scrape per new URL, asking for the job's fields as JSON.

    Returns None when a cap is reached or the page can't be read, so the caller treats it the
    same as a card it couldn't resolve.
    """
    # SRCH-03 comes first — ahead of the cache, because a denied page is read from nowhere, and
    # ahead of the caps, because a page that was never going to be fetched must not spend a cap
    # or mark the run partial. (It used to sit last, so asking for a LinkedIn URL while at the
    # page cap set `capped="pages"` on a run that had done nothing wrong.)
    if is_denied(url):
        return None

    if cache is not None:
        found, card = cache.card(url, schema)
        if found:
            # The bool is the whole point: `(True, None)` means "WERA read this page and there was
            # no job on it", which is a different fact from "WERA has never read this page" — and
            # confusing the two is how the 5 credits get spent twice.
            outcome.cached_cards += 1
            outcome.credits_saved += JSON_SCRAPE_CREDITS
            return card

    if outcome.pages_scraped >= caps.max_pages:
        outcome.capped = outcome.capped or "pages"
        return None
    if outcome.credits_used >= caps.max_credits:
        outcome.capped = outcome.capped or "credits"
        return None

    client = _client()
    document = _retrying(
        lambda: client.scrape(
            url,
            formats=[{"type": "json", "schema": schema, "prompt": "Extract this job posting's fields."}],
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


def estimate(profile: SearchProfile, sites: list[Site]) -> tuple[int, int]:
    """(queries, credits) a run would use, before it runs (PRF-05).

    One new page per query is the assumption. It is a guess either way — a first run scrapes
    almost everything it finds and a tenth run scrapes almost nothing, because a known URL is
    never scraped twice and SRCH-10 remembers the rest — so the estimate deliberately errs high.
    """
    queries = len(queries_for(profile, sites))
    return queries, queries * (SEARCH_CREDITS + JSON_SCRAPE_CREDITS)
