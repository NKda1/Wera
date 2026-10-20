"""Board search through Firecrawl (SRCH-01…08).

One query per target title × location, restricted to the boards the person enabled, with
LinkedIn excluded from every call whatever the config says (SRCH-03). Results that WERA already
knows are skipped before anything is scraped, which is what makes a repeat run nearly free.

The caps are the point of this module as much as the searching is. Firecrawl charges per credit,
so a run carries a budget for queries, pages and credits, and stops cleanly as `partial` when it
reaches one (SRCH-06). A run that can't finish is better than a bill nobody expected.
"""

import time
from dataclasses import dataclass, field
from typing import Any

import logfire

from ..config import Site
from ..http import DENY, host_of
from ..logging import redact
from ..schemas.profile import SearchProfile

# SRCH-02: how "posted within" maps to a search-time filter.
TBS = {"24h": "qdr:d", "7d": "qdr:w", "30d": "qdr:m"}

LIMIT_PER_QUERY = 10  # SRCH-02
MAX_TRIES = 3  # SRCH-07
BACKOFF_SECONDS = (1.0, 3.0)


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
    queries_run: int = 0
    pages_scraped: int = 0
    credits_used: int = 0
    errors: list[str] = field(default_factory=list)
    capped: str | None = None  # which cap ended the run, if any

    @property
    def partial(self) -> bool:
        return self.capped is not None


def queries_for(profile: SearchProfile, sites: list[Site]) -> list[str]:
    """SRCH-01: one query per title × location. The site restriction is a search argument, not
    part of the text, so the query stays readable."""
    usable = [s for s in sites if s.reviewed and s.domain in profile.sites]  # SRCH-04
    if not usable:
        return []
    return [f'"{title}" "{location}"' for title in profile.titles for location in profile.locations]


def enabled_domains(profile: SearchProfile, sites: list[Site]) -> list[str]:
    """Only sites whose terms the person has reviewed, and never LinkedIn (SRCH-03/04)."""
    return [s.domain for s in sites if s.reviewed and s.domain in profile.sites and s.domain not in DENY]


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


def search(profile: SearchProfile, sites: list[Site], caps: SearchCaps, known_urls: set[str]) -> SearchOutcome:
    """Runs the profile's queries and returns the results WERA hasn't seen before."""
    outcome = SearchOutcome()
    domains = enabled_domains(profile, sites)
    queries = queries_for(profile, sites)
    if not queries or not domains:
        return outcome

    client = _client()
    tbs = TBS.get(profile.recency)
    seen: set[str] = set()

    for query in queries:
        if outcome.queries_run >= caps.max_queries:
            outcome.capped = "queries"
            break
        if outcome.credits_used >= caps.max_credits:
            outcome.capped = "credits"
            break

        result = _retrying(
            # SRCH-03 is not enforced here: Firecrawl refuses a request carrying both an
            # include list and an exclude list, and sending both failed *every* query. An
            # allow-list is the stronger of the two anyway — nothing outside it can come back —
            # and the deny list is still applied by `enabled_domains` (which never puts a
            # denied domain in this list), `_hits_from` (which drops a denied host from the
            # results) and `scrape_card` (which refuses to fetch one).
            lambda q=query: client.search(
                q,
                sources=["web"],
                include_domains=domains,
                limit=LIMIT_PER_QUERY,
                tbs=tbs,
            ),
            f"search {query}",
            outcome.errors,
        )
        outcome.queries_run += 1
        outcome.credits_used += 1  # a search costs one credit
        if result is None:
            continue

        for hit in _hits_from(result, domains):
            # SRCH-05: a URL already known, or already seen this run, is not a new result.
            if hit.url in known_urls or hit.url in seen:
                continue
            seen.add(hit.url)
            outcome.hits.append(hit)

    logfire.info(
        "Board search ran {queries} query/queries and found {new} new result(s)",
        queries=outcome.queries_run,
        new=len(outcome.hits),
    )
    return outcome


def scrape_card(url: str, schema: dict[str, Any], outcome: SearchOutcome, caps: SearchCaps) -> dict[str, Any] | None:
    """SRCH-05: one scrape per new URL, asking for the job's fields as JSON.

    Returns None when a cap is reached or the page can't be read, so the caller treats it the
    same as a card it couldn't resolve.
    """
    if outcome.pages_scraped >= caps.max_pages:
        outcome.capped = outcome.capped or "pages"
        return None
    if outcome.credits_used >= caps.max_credits:
        outcome.capped = outcome.capped or "credits"
        return None
    if any(host_of(url) == d or host_of(url).endswith(f".{d}") for d in DENY):
        return None  # SRCH-03, again: never scraped, whatever asked for it

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
    outcome.credits_used += 1
    if document is None:
        return None
    data = getattr(document, "json", None)
    return data if isinstance(data, dict) else None


def credits_remaining() -> int | None:
    """What Firecrawl says is left, for the estimate on the search dialog (PRF-05)."""
    try:
        usage = _client().get_credit_usage()
    except Exception:  # noqa: BLE001 — an estimate is not worth failing a request for
        return None
    value = getattr(usage, "remaining_credits", None)
    return int(value) if isinstance(value, int | float) else None


def estimate(profile: SearchProfile, sites: list[Site]) -> tuple[int, int]:
    """(queries, credits) a run would use, before it runs (PRF-05)."""
    queries = len(queries_for(profile, sites))
    # One credit per search, plus one per page WERA expects to scrape.
    return queries, queries * 2
