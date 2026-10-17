"""The watchlist: employers' own public job boards (ATS-01, ATS-03).

This is the best source WERA has. A board API is the employer publishing their own vacancies —
no scraping, no terms to accept, no key, and the job carries its real ATS identity, which makes
de-duplication exact rather than a guess (NORM-05).

Each vendor's shape was read from a live board rather than from documentation:

* Greenhouse `/v1/boards/{token}/jobs?content=true` → `{"jobs": [...]}`, HTML in `content`.
* Lever `/v0/postings/{token}?mode=json` → a bare list, plain text in `descriptionPlain`.
* Ashby `/posting-api/job-board/{token}` → `{"jobs": [...]}`, plain text in `descriptionPlain`.

A feed that won't answer costs that company's jobs for this run and nothing else: the others
still go through, and `closed_tokens` only reports a company whose feed *did* answer, so an
outage can never mark someone's jobs closed by mistake.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any

import logfire

from ..clock import iso
from ..config import Company
from ..http import request
from ..schemas.jobs import Ats, Location

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
LEVER = "https://api.lever.co/v0/postings/{token}?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{token}"

TAGS = re.compile(r"<[^>]+>")
BLANKS = re.compile(r"\n{3,}")


@dataclass
class FeedJob:
    """One posting as its own board describes it, before WERA normalises anything."""

    ats: Ats
    title: str
    company: str
    location: Location
    apply_url: str
    description: str
    posted_at: str | None
    remote: str | None = None


@dataclass
class FeedResult:
    jobs: list[FeedJob] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Only companies whose feed actually answered, so an outage can't close a job (ATS-03).
    answered: set[str] = field(default_factory=set)


def _text(raw: str | None) -> str:
    """Board descriptions arrive as HTML or as text; both end up as readable plain text.

    Unescaping comes first. Greenhouse sends its HTML escaped, so stripping tags before
    unescaping strips nothing and then reveals the markup — which is how `<div class=…>` ends
    up in a cover letter.
    """
    if not raw:
        return ""
    cleaned = html.unescape(raw)
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", cleaned)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", cleaned)
    cleaned = re.sub(r"(?i)<li[^>]*>", "- ", cleaned)
    cleaned = TAGS.sub("", cleaned)
    # A second pass: entities inside the markup are only reachable once the tags are gone.
    cleaned = html.unescape(cleaned).replace("\xa0", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return BLANKS.sub("\n\n", cleaned).strip()


def _location(raw: str | None) -> Location:
    value = (raw or "").strip()
    if not value:
        return Location(raw="Not stated")
    # "New York City, NY; San Francisco, CA | New York City, NY" — the first entry is enough.
    first = re.split(r"[;|]", value)[0].strip()
    parts = [p.strip() for p in first.split(",") if p.strip()]
    city = parts[0] if parts else None
    country = parts[-1] if len(parts) > 1 else None
    return Location(city=city, country=country, raw=value)


def _remote(*hints: Any) -> str | None:
    blob = " ".join(str(h) for h in hints if h).lower()
    if "remote" in blob:
        return "remote"
    if "hybrid" in blob:
        return "hybrid"
    return "onsite" if "onsite" in blob or "on-site" in blob else None


def _greenhouse(company: Company, body: dict[str, Any]) -> list[FeedJob]:
    out = []
    for job in body.get("jobs") or []:
        job_id = str(job.get("id") or "")
        title = (job.get("title") or "").strip()
        if not job_id or not title:
            continue
        where = (job.get("location") or {}).get("name")
        out.append(
            FeedJob(
                ats=Ats(vendor="greenhouse", board_token=company.token, job_id=job_id),
                title=title,
                company=job.get("company_name") or company.name,
                location=_location(where),
                apply_url=job.get("absolute_url") or "",
                description=_text(job.get("content")),
                posted_at=job.get("first_published") or job.get("updated_at"),
                remote=_remote(where),
            )
        )
    return out


def _lever(company: Company, body: list[dict[str, Any]]) -> list[FeedJob]:
    out = []
    for job in body or []:
        job_id = str(job.get("id") or "")
        title = (job.get("text") or "").strip()
        if not job_id or not title:
            continue
        categories = job.get("categories") or {}
        created = job.get("createdAt")
        out.append(
            FeedJob(
                ats=Ats(vendor="lever", board_token=company.token, job_id=job_id),
                title=title,
                company=company.name,
                location=_location(categories.get("location")),
                apply_url=job.get("hostedUrl") or job.get("applyUrl") or "",
                description=_text(job.get("descriptionPlain") or job.get("description")),
                posted_at=iso(int(created)) if isinstance(created, int | float) else None,
                remote=_remote(job.get("workplaceType"), categories.get("location")),
            )
        )
    return out


def _ashby(company: Company, body: dict[str, Any]) -> list[FeedJob]:
    out = []
    for job in body.get("jobs") or []:
        job_id = str(job.get("id") or "")
        title = (job.get("title") or "").strip()
        if not job_id or not title or job.get("isListed") is False:
            continue
        out.append(
            FeedJob(
                ats=Ats(vendor="ashby", board_token=company.token, job_id=job_id),
                title=title,
                company=company.name,
                location=_location(job.get("location")),
                apply_url=job.get("jobUrl") or job.get("applyUrl") or "",
                description=_text(job.get("descriptionPlain") or job.get("descriptionHtml")),
                posted_at=job.get("publishedAt"),
                remote="remote" if job.get("isRemote") else _remote(job.get("workplaceType"), job.get("location")),
            )
        )
    return out


URLS = {"greenhouse": GREENHOUSE, "lever": LEVER, "ashby": ASHBY}
PARSERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}


def fetch_company(company: Company) -> tuple[list[FeedJob], str | None]:
    """One company's open jobs, or a short reason why not."""
    url = URLS[company.vendor].format(token=company.token)
    try:
        response = request("GET", url)
        response.raise_for_status()
        body = response.json()
    except Exception as exc:  # noqa: BLE001 — one board being down is not the run failing
        logfire.warn("{vendor} feed for {company} didn't answer", vendor=company.vendor, company=company.name)
        return [], f"{company.name}: its {company.vendor} board didn't answer ({type(exc).__name__})"
    try:
        return PARSERS[company.vendor](company, body), None
    except Exception:  # noqa: BLE001 — a shape WERA doesn't recognise is the same as no jobs
        logfire.warn("{vendor} feed for {company} had an unexpected shape", vendor=company.vendor, company=company.name)
        return [], f"{company.name}: its {company.vendor} board returned something WERA couldn't read"


def fetch_all(companies: list[Company]) -> FeedResult:
    """Every watched company, one at a time. Public boards are small and this runs daily."""
    result = FeedResult()
    for company in companies:
        jobs, error = fetch_company(company)
        if error:
            result.errors.append(error)
            continue
        result.answered.add(f"{company.vendor}:{company.token}")
        result.jobs.extend(jobs)
    return result


def closed_tokens(result: FeedResult, known: dict[str, Ats]) -> list[str]:
    """ATS-03: job ids that have gone from a feed that answered.

    `known` maps a job id to the ATS identity WERA stored for it. A company whose board failed
    this run is skipped entirely — absence of evidence isn't evidence the job closed.
    """
    seen = {f"{j.ats.vendor}:{j.ats.board_token}:{j.ats.job_id}" for j in result.jobs}
    gone = []
    for job_id, ats in known.items():
        board = f"{ats.vendor}:{ats.board_token}"
        if board in result.answered and f"{board}:{ats.job_id}" not in seen:
            gone.append(job_id)
    return gone
