"""Finding employers' boards from the titles you want (ATS-05).

The watchlist is WERA's best source — an employer publishing their own vacancies, no scraping,
no terms, and a real ATS identity that makes de-duplication exact. Its weakness was that
somebody had to know which companies to watch, and keep knowing: hand-maintaining thirty board
tokens is the kind of chore that silently stops happening, and a stale watchlist looks exactly
like a working one.

So the list is grown from the target titles instead. One Firecrawl search per title, restricted
to the three vendors' public board domains, turns "AI Engineer" into a set of board tokens;
each one is confirmed by actually fetching the feed, and the survivors are written into
`config/companies.yaml`. From then on they are polled free, daily, by `ats_feeds`.

The split is deliberate, and it is what makes this cheap:

* **Discovery is paid and occasional.** Firecrawl credits, once a week by default.
* **Watching is free and daily.** Public JSON APIs, no key, no limit worth worrying about.

Searching the ATS domains on *every* run instead would cost 7 credits per job found and lose
ATS-03 — a job that vanishes from a feed can be marked closed, but a job that stops appearing
in search results has only stopped appearing in search results.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import logfire

from ..config import Company
from ..domain.text import title_case
from ..http import host_of
from ..logging import redact

# The public board domains, per vendor. `boards.greenhouse.io` is the older host and still
# serves plenty of live boards, so both are searched and both parse to the same vendor.
VENDOR_DOMAINS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("job-boards.greenhouse.io", "boards.greenhouse.io"),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
}

SEARCH_DOMAINS = [d for domains in VENDOR_DOMAINS.values() for d in domains]
HOST_VENDOR = {domain: vendor for vendor, domains in VENDOR_DOMAINS.items() for domain in domains}

# The board token is the first path segment on every one of these hosts:
#   job-boards.greenhouse.io/monzo/jobs/4471023
#   jobs.lever.co/monzo/8f3c…         jobs.ashbyhq.com/monzo/8f3c…
_TOKEN = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9._-]{1,62})(?:/|$)")

# First segments that are the vendor's own pages rather than an employer's board.
RESERVED = frozenset({"embed", "api", "v1", "jobs", "job", "search", "privacy", "terms", "app", "static", "assets", "_next"})

LIMIT_PER_QUERY = 10


@dataclass
class Candidate:
    vendor: str
    token: str
    matched: str  # the watch title whose search turned this up


@dataclass
class DiscoveryOutcome:
    found: list[Company] = field(default_factory=list)
    queries: int = 0
    credits_used: int = 0
    checked: int = 0  # candidate boards whose feed was fetched to confirm them
    rejected: int = 0  # candidates whose feed didn't answer, or had no jobs
    errors: list[str] = field(default_factory=list)


def board_from_url(url: str) -> tuple[str, str] | None:
    """`(vendor, token)` from a board URL, or None when it isn't one."""
    host = host_of(url)
    vendor = HOST_VENDOR.get(host)
    if vendor is None:
        return None
    match = _TOKEN.match(re.sub(r"^https?://[^/]+", "", url.split("?")[0].split("#")[0]) or "/")
    if match is None:
        return None
    token = match.group(1)
    return None if token.lower() in RESERVED else (vendor, token)


def queries_for(titles: list[str], locations: list[str]) -> list[str]:
    """One query per title, with the locations as context rather than a hard filter.

    A title × location grid would multiply the credits for very little: a board's URL is the
    same whichever office the vacancy is in, and the location filtering happens later anyway,
    on the jobs themselves (`domain.filters`). Naming the locations in the query text still
    nudges the results towards the right country.
    """
    where = " ".join(f'"{loc}"' for loc in locations[:3] if loc.strip())
    return [f'"{title}" {where}'.strip() for title in titles if title.strip()]


def _client():  # noqa: ANN202 — the SDK's own type, imported lazily
    from firecrawl.v2 import FirecrawlClient

    from ..settings import get_settings

    env = get_settings()
    return FirecrawlClient(api_key=env.firecrawl_api_key.get_secret_value() if env.firecrawl_api_key else "")


def search_boards(
    titles: list[str], locations: list[str], *, max_queries: int, credits_per_search: int
) -> tuple[list[Candidate], DiscoveryOutcome]:
    """Searches the ATS domains for each title and returns the board tokens found."""
    outcome = DiscoveryOutcome()
    queries = queries_for(titles, locations)[:max_queries]
    if not queries:
        return [], outcome

    client = _client()
    seen: set[tuple[str, str]] = set()
    candidates: list[Candidate] = []

    for title, query in zip([t for t in titles if t.strip()], queries, strict=False):
        try:
            result = client.search(query, sources=["web"], include_domains=SEARCH_DOMAINS, limit=LIMIT_PER_QUERY)
        except Exception as exc:  # noqa: BLE001 — one title failing is not the run failing
            detail = redact(" ".join(str(exc).split()))[:200] or type(exc).__name__
            logfire.warn("Board discovery for {title} failed: {error}", title=title, error=detail)
            outcome.errors.append(f"discover {title}: {detail}")
            outcome.queries += 1
            outcome.credits_used += credits_per_search
            continue

        outcome.queries += 1
        outcome.credits_used += credits_per_search
        for item in getattr(result, "web", None) or []:
            board = board_from_url(getattr(item, "url", None) or "")
            if board is None or board in seen:
                continue
            seen.add(board)
            candidates.append(Candidate(vendor=board[0], token=board[1], matched=title))

    logfire.info(
        "Board discovery found {count} candidate board(s) from {queries} query/queries",
        count=len(candidates),
        queries=outcome.queries,
    )
    return candidates, outcome


def confirm(candidate: Candidate, today: date) -> Company | None:
    """Fetches the board once to prove it exists, and to learn the employer's real name.

    A search result is evidence a URL existed when it was indexed, not that the board answers
    today. Confirming costs nothing — these are free public APIs — and it keeps dead tokens out
    of a file that is otherwise fetched every morning for ever.
    """
    from . import ats_feeds

    provisional = Company(name=title_case(candidate.token), vendor=candidate.vendor, token=candidate.token)  # type: ignore[arg-type]
    jobs, error = ats_feeds.fetch_company(provisional)
    if error or not jobs:
        return None
    # Greenhouse puts the employer's own name on every posting; the others don't, so the token
    # title-cased is the best available and the person can correct it in the file. Stripped
    # because a feed's own name routinely carries trailing whitespace, and it would be written
    # into a config file and quoted by the YAML dumper to preserve it.
    name = next(
        (j.company.strip() for j in jobs if j.company.strip() and j.company.strip() != provisional.name), provisional.name
    )
    return Company(
        name=name,
        vendor=candidate.vendor,  # type: ignore[arg-type]
        token=candidate.token,
        discovered_on=today,
        matched=candidate.matched,
    )


def merge(existing: list[Company], found: list[Company], *, max_companies: int) -> list[Company]:
    """The new watchlist: every manual entry, then the newest discoveries, capped.

    Three rules, in order:

    1. **A manual entry is never touched.** No `discovered_on`, or `pinned: true`, means a
       person put it there; discovery has no business removing or reordering it.
    2. **A board already known stays known**, keeping the date it was first found rather than
       being re-dated on every run — the list should read as a history, not a timestamp.
    3. **What's left is filled with the newest finds**, up to the cap, because a watchlist that
       grows without bound turns every morning run into a hundred HTTP requests.
    """
    manual = [c for c in existing if c.manual]
    by_board = {c.board: c for c in existing}
    kept_boards = {c.board for c in manual}

    discovered: list[Company] = []
    for company in found:
        if company.board in kept_boards:
            continue
        kept_boards.add(company.board)
        previous = by_board.get(company.board)
        # Rule 2: keep the original date, and the original name if a person edited it.
        discovered.append(
            company.model_copy(update={"discovered_on": previous.discovered_on, "name": previous.name}) if previous else company
        )

    room = max(max_companies - len(manual), 0)
    return [*manual, *discovered[:room]]


def discover(
    titles: list[str],
    locations: list[str],
    existing: list[Company],
    *,
    max_queries: int,
    max_companies: int,
    credits_per_search: int,
    today: date,
) -> tuple[list[Company], DiscoveryOutcome]:
    """One discovery pass: search, confirm, merge. Returns the new list and what it cost."""
    candidates, outcome = search_boards(titles, locations, max_queries=max_queries, credits_per_search=credits_per_search)

    known = {c.board for c in existing}
    confirmed: list[Company] = []
    for candidate in candidates:
        board = f"{candidate.vendor}:{candidate.token}"
        if board in known:
            # Already watched: no need to spend a request proving it again.
            confirmed.append(next(c for c in existing if c.board == board))
            continue
        outcome.checked += 1
        company = confirm(candidate, today)
        if company is None:
            outcome.rejected += 1
            continue
        confirmed.append(company)

    outcome.found = confirmed
    merged = merge(existing, confirmed, max_companies=max_companies)
    logfire.info(
        "Watchlist now {total} company/companies ({new} newly discovered, {rejected} rejected)",
        total=len(merged),
        new=len([c for c in merged if c.discovered_on == today]),
        rejected=outcome.rejected,
    )
    return merged, outcome
