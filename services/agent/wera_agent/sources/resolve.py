"""Finding the original posting behind a card (NORM-01, NORM-02).

An alert email gives a title, a company and a link that usually goes to the board that sent it
rather than to the employer. To score a job properly WERA needs the real posting, so it looks
for one in this order:

1. **A known ATS URL.** If the link is already a Greenhouse, Lever or Ashby posting, that is the
   original and no search is needed — and it costs nothing.
2. **One open-web search** for the title and company, LinkedIn excluded, five results.
3. **Leave it unresolved.** The card still becomes a job with no description (NORM-03).

A search result is only accepted when all three of NORM-02's conditions hold: the host is an ATS
or the employer's own domain, the company matches, and the titles are ≥ 0.85 similar. Being
wrong here is worse than being unresolved — a tailored CV written against someone else's job ad
is a confident, wasted application.
"""

import re
from dataclasses import dataclass

import logfire

from ..domain.dedupe import RESOLVE_TITLE, normalise_company, title_similarity
from ..http import DENY, host_of
from ..schemas.jobs import Ats

# The boards whose URLs carry an ATS identity WERA can read.
ATS_HOSTS = {
    "greenhouse": ("greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io"),
    "lever": ("lever.co", "jobs.lever.co"),
    "ashby": ("ashbyhq.com", "jobs.ashbyhq.com"),
}

ATS_PATTERNS = (
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?(?P<token>[\w-]+)(?:/jobs)?/(?P<job>\d+)", re.I)),
    ("lever", re.compile(r"lever\.co/(?P<token>[\w-]+)/(?P<job>[0-9a-f-]{8,})", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com/(?P<token>[\w-]+)/(?P<job>[0-9a-f-]{8,})", re.I)),
)

SEARCH_LIMIT = 5  # NORM-01


@dataclass
class Resolved:
    url: str
    ats: Ats | None
    how: str  # "ats-url" | "search" | a short reason it stayed unresolved


def ats_from_url(url: str | None) -> Ats | None:
    """The ATS identity a board URL already carries (NORM-01, step 1)."""
    if not url:
        return None
    for vendor, pattern in ATS_PATTERNS:
        match = pattern.search(url)
        if match:
            return Ats(vendor=vendor, board_token=match.group("token"), job_id=match.group("job"))
    return None


def _is_ats_host(host: str) -> bool:
    return any(host == h or host.endswith(f".{h}") for hosts in ATS_HOSTS.values() for h in hosts)


def _is_employer_host(host: str, company: str) -> bool:
    """A loose check that the domain belongs to the employer: "Fernhill AI" → fernhillai.com."""
    collapsed = re.sub(r"[^a-z0-9]", "", normalise_company(company))
    if len(collapsed) < 4:
        return False
    stem = host.removeprefix("www.").split(".")[0]
    return collapsed.startswith(re.sub(r"[^a-z0-9]", "", stem)) or re.sub(r"[^a-z0-9]", "", stem).startswith(collapsed)


def _same_company(company: str, *candidates: str) -> bool:
    """Whether any candidate names this employer, with punctuation and suffixes ignored."""
    wanted = re.sub(r"[^a-z0-9]", "", normalise_company(company))
    if len(wanted) < 4:
        return False
    for candidate in candidates:
        got = re.sub(r"[^a-z0-9]", "", normalise_company(candidate))
        if got and (wanted in got or got in wanted):
            return True
    return False


def accepts(url: str, result_title: str, *, title: str, company: str) -> bool:
    """NORM-02. All three conditions, or the result is not this job."""
    host = host_of(url)
    if not host or any(host == d or host.endswith(f".{d}") for d in DENY):
        return False

    ats = ats_from_url(url)
    if ats is not None:
        # On a board, the employer is the board token in the path — `job-boards.greenhouse.io`
        # says nothing about who is hiring, but `/fernhillai/` does.
        if not _same_company(company, ats.board_token, result_title):
            return False
    elif _is_employer_host(host, company):
        pass  # the domain itself is the employer's
    else:
        return False

    return title_similarity(title, result_title) >= RESOLVE_TITLE


def resolve(
    *,
    title: str,
    company: str,
    url: str | None,
    search_one: object = None,
) -> Resolved:
    """Finds the original posting, or says why it couldn't.

    `search_one` is a callable taking a query and returning `[(url, title)]`; it is passed in so
    this module stays testable and so the caller owns the credit budget.
    """
    known = ats_from_url(url)
    if known is not None:
        return Resolved(url=url or "", ats=known, how="ats-url")

    if search_one is None:
        return Resolved(url=url or "", ats=None, how="no search available")

    query = f'"{title}" "{company}"'
    try:
        results = search_one(query) or []  # type: ignore[operator]
    except Exception:  # noqa: BLE001 — an unresolved card is a normal outcome, not a failure
        logfire.warn("Resolving {title} at {company} failed", title=title, company=company)
        return Resolved(url=url or "", ats=None, how="search failed")

    for found_url, found_title in results[:SEARCH_LIMIT]:
        if accepts(found_url, found_title, title=title, company=company):
            return Resolved(url=found_url, ats=ats_from_url(found_url), how="search")

    return Resolved(url=url or "", ats=None, how="no confident match")
