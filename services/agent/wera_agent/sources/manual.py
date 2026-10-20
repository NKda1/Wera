"""ATS-04: one job, imported by hand.

Three paths, in order of how much WERA can trust what it gets:

1. **An ATS link** (Greenhouse, Lever, Ashby) resolves to the vendor's own JSON, so the title,
   company, location and description are the employer's own fields.
2. **Any other link** is scraped once — one page, one credit, the same extraction the board
   search uses.
3. **A pasted description** is taken as given. Nothing is fetched, nothing is guessed about
   where the job is: a paste with no location says "Not stated" rather than inventing one.

LinkedIn is refused before a request is built (SPEC §11.2). The deny list inside `scrape_card`
is a second line of defence, not the first.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..clock import iso
from ..config import Company
from ..domain.text import is_linkedin_url, title_case
from ..errors import ApiError
from ..schemas.jobs import Job, JobSource
from . import ats_feeds, firecrawl_search, normalise, resolve
from .firecrawl_search import SearchCaps, SearchOutcome

# One page, and a credit ceiling that cannot run away with a person's balance on a single click.
IMPORT_CAPS = SearchCaps(max_queries=0, max_pages=1, max_credits=5)

LINKEDIN_REFUSAL = "WERA never opens LinkedIn links. Open the job on LinkedIn and paste its description instead."


def checked_url(raw: str) -> tuple[str, str]:
    """The URL and its host, or an `ApiError` explaining why not. Called before any request."""
    url = raw.strip()
    if is_linkedin_url(url):
        raise ApiError(422, "LINKEDIN_NOT_FETCHED", LINKEDIN_REFUSAL)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError(422, "BAD_URL", "That doesn't look like a link. Paste the full address, starting with https://")
    return url, parsed.hostname


def _source(detail: str, now: int) -> JobSource:
    """Imported by hand, whatever fetched it: the person asked for this one by name."""
    return JobSource(channel="manual", detail=detail, seen_at=iso(now))


def from_ats(url: str, now: int) -> Job:
    """The employer's own posting, read from the vendor's feed."""
    ats = resolve.ats_from_url(url)
    if ats is None:  # pragma: no cover — callers check first
        raise ApiError(422, "BAD_URL", "That isn't a Greenhouse, Lever or Ashby link.")
    company = Company(name=title_case(ats.board_token), vendor=ats.vendor, token=ats.board_token)
    jobs, error = ats_feeds.fetch_company(company)
    if error is not None:
        raise ApiError(502, "IMPORT_FAILED", f"{company.name}'s job board didn't answer. Try again in a minute.")
    found = next((j for j in jobs if j.ats.job_id == ats.job_id), None)
    if found is None:
        raise ApiError(
            404,
            "IMPORT_NOT_FOUND",
            f"That posting isn't on {company.name}'s board any more. It may have closed.",
        )
    job = normalise.from_feed(found, now)
    job.sources = [_source(f"{ats.vendor}:{ats.board_token}", now)]
    return job


def from_page(url: str, host: str, now: int) -> Job:
    """Any other link: scraped once."""
    outcome = SearchOutcome()
    card = firecrawl_search.scrape_card(url, firecrawl_search.CARD_SCHEMA, outcome, IMPORT_CAPS)
    if card is None:
        raise ApiError(
            502,
            "IMPORT_FAILED",
            "WERA couldn't read that page. Paste the job's description instead and it will be used as-is.",
        )
    job = normalise.from_scrape(card, url=url, site=host, markdown=card.get("description"), now=now)
    if job is None:
        raise ApiError(
            422,
            "IMPORT_NO_JOB",
            "WERA couldn't find a job posting on that page. Paste the description instead.",
        )
    job.sources = [_source(host, now)]
    return job


def title_and_company(text: str) -> tuple[str, str]:
    """The two things a job needs, read off a pasted description.

    Deliberately simple and deliberately honest: when the company can't be read, it says so
    rather than guessing, and the person can correct it on the job's page.
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        raise ApiError(422, "EMPTY_PASTE", "There's nothing there. Paste the job's description.")
    title = re.sub(r"^#+\s*", "", lines[0])[:120]
    # "…at Acme Robotics." — capitalised words on one line, without the trailing full stop.
    at_line = re.search(r"\bat[ \t]+([A-Z][\w&.-]*(?:[ \t]+[A-Z][\w&.-]*){0,3})", text, re.ASCII)
    if at_line:
        return title, at_line.group(1).rstrip(".,")
    second = re.sub(r"^#+\s*", "", lines[1]) if len(lines) > 1 else ""
    return title, second if 0 < len(second) < 40 else "Unknown company"


def _location_in(text: str) -> str | None:
    """A "Location: …" line, if the paste has one. Nothing is inferred beyond that."""
    found = re.search(r"^\s*(?:location|based in|office)\s*[:\-—]\s*(.{2,60})$", text, re.IGNORECASE | re.MULTILINE)
    return found.group(1).strip() if found else None


def from_text(text: str, now: int) -> Job:
    """A pasted description, taken as given."""
    body = text.strip()
    title, company = title_and_company(body)
    return normalise.from_pasted(body, title=title, company=company, location_raw=_location_in(body), now=now)


def imported(*, url: str | None, text: str | None, now: int) -> Job:
    """ATS-04's entry point: whichever of the three paths the input calls for."""
    if url:
        checked, host = checked_url(url)
        return from_ats(checked, now) if resolve.ats_from_url(checked) else from_page(checked, host, now)
    if text and text.strip():
        return from_text(text, now)
    raise ApiError(422, "NOTHING_TO_IMPORT", "Give WERA a link to the job, or paste its description.")
