"""SRCH-12: finding job boards worth searching, and asking before searching them.

Six hand-curated boards is a ceiling, not a configuration. This looks for more — but it cannot
simply start searching what it finds, and the reason is a requirement rather than a technical
limit. **SRCH-04**: a site is searchable only once `terms_reviewed_on` is set, which SPEC §11.3
defines as a person having read that site's terms of service and its robots.txt. That is the same
reasoning that hard-excludes LinkedIn. So discovery *proposes*: it appends what it finds with
`terms_reviewed_on: null`, which is a state the whole stack already handles — `otta.com` has sat
in that slot since the beginning, loaded, shown disabled in the UI, and excluded from search.

**How a board is recognised, which is the only interesting problem here.**

Not by asking a model, and not by searching for "best job boards in X" — that returns listicles
whose hosts are blogs, and reading one costs a 5-credit scrape. Instead this runs the same kind of
query a real board search runs, minus the domain restriction, and lets the *results* say which
hosts carry postings for these titles in these places. A host then has to earn its place:

* **two distinct result paths**, because a company's own careers page contributes exactly one;
* **matched by two different titles**, because a single employer's portal can usually answer one
  of a person's target titles and a multi-employer board answers several;
* **a posting-shaped path** somewhere among them.

That is why the two queries deliberately use *different* titles. It is a cheap test and it will
still occasionally propose a large employer's careers portal — which is tolerable precisely
because a human decides, the card shows the real URLs it matched on, and a decline is remembered.

"Relevant to the master CV" needs no extra work: `profile.titles` is generated from the CV
(PRF-01/CV-03) and nothing else sets it, so querying from the profile *is* querying from the CV.

The robots.txt check is free and is the one piece of SRCH-04 a machine can honestly do: a host
that blanket-disallows crawling is rejected outright and never reaches the review queue. Judging
a site's *terms* is not automatable and is not attempted.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import logfire

from ..config import Site, SitesFile
from ..http import host_of, is_denied, request
from ..logging import redact
from ..schemas.profile import SearchProfile

LIMIT_PER_QUERY = 10

# How many queries one pass may spend. Two, which is 4 credits — and two is also what makes the
# "matched by different titles" test possible at all.
MAX_QUERIES = 2

# How many proposals one pass may add. The constraint is the human, not the credits: a queue
# nobody clears is indistinguishable from a broken feature.
MAX_PROPOSALS = 2

# A path that looks like it holds a posting rather than an article about hiring.
_JOB_PATH = re.compile(r"/(jobs?|vacanc|stelle|emploi|graduate|internship|careers)", re.I)

# Hosts that are never a job board worth adding: search, social, wikis, publishing. Suffix
# matched, so `news.google.com` is covered by `google.`.
NOT_A_BOARD = (
    "google.",
    "bing.com",
    "duckduckgo.com",
    "yahoo.",
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "reddit.com",
    "youtube.com",
    "tiktok.com",
    "wikipedia.org",
    "medium.com",
    "substack.com",
    "quora.com",
    "github.com",
    "pinterest.com",
)

# Hosts whose terms are known to forbid this, or that WERA reaches another way. Left out of the
# queue rather than offered and declined, because offering them would invite the wrong click.
ALREADY_HANDLED = ("indeed.", "glassdoor.")

# Single-employer boards. A hit here is a signal for ATS-05, which discovers *employers*, not for
# this module, which discovers *boards*. Wider than `ats_discovery.SEARCH_DOMAINS` on purpose:
# WERA only reads three vendors' feeds, but it should recognise the rest as not-a-board.
ATS_VENDOR_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "workday.com",
    "smartrecruiters.com",
    "teamtailor.com",
    "personio.de",
    "personio.com",
    "recruitee.com",
    "workable.com",
    "breezy.hr",
    "bamboohr.com",
    "jobvite.com",
    "icims.com",
    "successfactors.com",
    "taleo.net",
)


@dataclass
class Candidate:
    """A host that might be a job board, and the evidence for it."""

    domain: str
    matched: set[str] = field(default_factory=set)  # the titles whose queries turned it up
    paths: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)

    @property
    def looks_like_a_board(self) -> bool:
        """Two distinct postings, found via two different titles, at least one posting-shaped.

        Each clause removes a different false positive: one path is a careers page, one title is
        one employer, and no job-shaped path at all is an article about the industry.
        """
        return len(self.paths) >= 2 and len(self.matched) >= 2 and any(_JOB_PATH.search(p) for p in self.paths)


@dataclass
class DiscoveryOutcome:
    found: list[Site] = field(default_factory=list)
    queries: int = 0
    credits_used: int = 0
    checked: int = 0  # candidates whose robots.txt was fetched
    rejected: int = 0  # candidates a robots.txt turned away
    errors: list[str] = field(default_factory=list)


def _client() -> Any:
    from ..sources.firecrawl_search import _client as shared

    return shared()


def known_hosts(sites: list[Site]) -> set[str]:
    """Every domain already in the file, in any state — including declined ones.

    A declined board must not come back next week; that is what makes a decline cost one click
    once rather than one click a week for ever.
    """
    return {s.domain.lower() for s in sites}


def search_hosts(profile: SearchProfile, sites: list[Site], outcome: DiscoveryOutcome) -> list[Candidate]:
    """Ask the open web which hosts carry postings for these titles, and in these places."""
    from ..sources.firecrawl_search import SEARCH_CREDITS, _retrying, _where

    titles = [t.strip() for t in profile.titles if t.strip()]
    if not titles:
        return []
    # Different titles on purpose: `looks_like_a_board` needs two of them to agree.
    chosen = titles[:MAX_QUERIES] if len(titles) >= MAX_QUERIES else titles
    where = _where(profile)
    skip = known_hosts(sites)
    found: dict[str, Candidate] = {}
    client = _client()

    for title in chosen:
        query = f'"{title}" jobs{where}'
        # No `include_domains` — that is the whole point — and so, for once, SRCH-03 can be sent
        # as an `exclude_domains` argument. The main search path can't: Firecrawl refuses a
        # request carrying both lists, and there an allow-list is the stronger of the two.
        result = _retrying(
            lambda q=query: client.search(q, sources=["web"], exclude_domains=["linkedin.com"], limit=LIMIT_PER_QUERY),
            f"site discovery {query}",
            outcome.errors,
        )
        outcome.queries += 1
        outcome.credits_used += SEARCH_CREDITS
        if result is None:
            continue
        for item in getattr(result, "web", None) or []:
            url = (getattr(item, "url", None) or "").strip()
            host = host_of(url)
            if not _worth_considering(url, host, skip):
                continue
            candidate = found.setdefault(host, Candidate(domain=host))
            candidate.matched.add(title)
            path = _path_of(url)
            if path and path not in candidate.paths:
                candidate.paths.add(path)
                if len(candidate.examples) < 2:
                    candidate.examples.append(url)

    return [c for c in found.values() if c.looks_like_a_board]


def _worth_considering(url: str, host: str, skip: set[str]) -> bool:
    if not url or not host or is_denied(url):
        return False  # SRCH-03, before anything else
    if host in skip or host.removeprefix("www.") in skip:
        return False
    suffixes = (*NOT_A_BOARD, *ALREADY_HANDLED, *ATS_VENDOR_HOSTS)
    return not any(host == s.rstrip(".") or host.endswith(s) or s in host for s in suffixes)


def _path_of(url: str) -> str | None:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).path or "/").rstrip("/") or "/"
    except ValueError:
        return None


# A robots.txt that forbids everyone, which is the one thing a machine can read off it honestly.
_DISALLOW_ALL = re.compile(r"^\s*user-agent:\s*\*\s*$(?:(?!^\s*user-agent:).)*?^\s*disallow:\s*/\s*$", re.I | re.M | re.S)


def confirm(candidate: Candidate, outcome: DiscoveryOutcome) -> bool | None:
    """Fetch the host's robots.txt. `False` means reject; `None` means "couldn't check".

    Free, and it proves DNS and HTTP as a side effect, which a search index cannot. A host that
    blanket-disallows crawling never reaches the review queue — that keeps the half of SRCH-04 a
    machine *can* judge honest, and keeps the queue short. The terms of service are not read
    here and are not readable here; that is the human's half.
    """
    outcome.checked += 1
    try:
        response = request("GET", f"https://{candidate.domain}/robots.txt")
    except Exception as exc:  # noqa: BLE001 — unreachable is not the same as unwelcoming
        outcome.errors.append(f"{candidate.domain}: couldn't read robots.txt ({redact(type(exc).__name__)})")
        return None
    if response.status_code == 404:
        return True  # no robots.txt disallows nothing
    if response.status_code != 200:
        return None
    try:
        body = response.text[:20_000]
    except Exception:  # noqa: BLE001
        return None
    if _DISALLOW_ALL.search(body):
        outcome.rejected += 1
        logfire.info("Site discovery rejected {domain}: robots.txt disallows all", domain=candidate.domain)
        return False
    return True


def discover(profile: SearchProfile, file: SitesFile, *, today: date) -> tuple[SitesFile, DiscoveryOutcome]:
    """Propose up to `MAX_PROPOSALS` new boards. Writes nothing — the caller writes.

    Append-only: the returned file is the one passed in plus whatever was proposed, in order.
    Nothing existing is touched, so a discovery pass can never cost someone a board they chose.
    """
    outcome = DiscoveryOutcome()
    live = [s for s in file.sites if s.declined_on is None]
    if len(live) >= file.max_sites:
        logfire.info("Site discovery skipped: {count} sites already", count=len(live))
        return file, outcome

    candidates = search_hosts(profile, file.sites, outcome)
    # Best evidence first: more distinct postings, then more titles agreeing.
    candidates.sort(key=lambda c: (-len(c.paths), -len(c.matched), c.domain))

    room = min(MAX_PROPOSALS, file.max_sites - len(live))
    for candidate in candidates:
        if len(outcome.found) >= room:
            break
        robots = confirm(candidate, outcome)
        if robots is False:
            continue
        outcome.found.append(
            Site(
                domain=candidate.domain,
                name=candidate.domain,
                # SRCH-04: unreviewed, and therefore unsearchable, until a person says otherwise.
                terms_reviewed_on=None,
                # No country claim. Tagging from the profile's countries would look
                # better-evidenced than it is — one GB-flavoured query answered by a global board
                # would pin it to GB and SRCH-11 would then skip it for a Berlin search, a false
                # negative indistinguishable from a broken board. `[]` means "anywhere"; the
                # approval card is where a person narrows it.
                countries=[],
                discovered_on=today,
                matched=sorted(candidate.matched)[0],
                robots_ok=robots,
                examples=candidate.examples[:2],
            )
        )

    if outcome.found:
        logfire.info("Site discovery proposes {count} board(s)", count=len(outcome.found))
    return file.model_copy(update={"sites": [*file.sites, *outcome.found]}), outcome
