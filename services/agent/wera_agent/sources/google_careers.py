"""careers.google.com, read directly (one employer, no credits).

**Why it isn't a Firecrawl search.** Google is an employer with its own listing pages, so it
belongs with the free daily channels — the watchlist — rather than with the paid board search.
It is not an ATS vendor either: there is no token-based JSON feed, so it can't join
`config/companies.yaml` and `ats_feeds`. It sits in `config/sites.yaml` because that file is the
terms gate, marked `direct: true` so `firecrawl_search.usable_sites` can never send a query to it.

**It needs the browser.** The results page is a client-rendered application: plain HTTP returns a
shell with no postings in it. So this rung only produces jobs on an install that has the `browser`
extra *and* `playwright install chromium`; without either it finds nothing and says so, which is
the same shape as a board that didn't answer.

**Why a regex and not selectors.** The listing markup carries generated class names that change
without notice, so matching on them would break silently and look like Google having no jobs.
Job URLs are matched on their path instead — `/about/careers/applications/jobs/results/<id>` —
which is the part of the page that is also a permalink someone could paste, and therefore the
part least free to change.

**Google's own Jobs API is discontinued**, and there is no public API for its careers listings.
What exists is this site, so this reads this site: one render per query to collect links, then
`sources.extract` per posting. Nothing here guesses — a page that can't be read becomes no job.
"""

import re
from urllib.parse import quote_plus

import logfire

from ..schemas.jobs import Job
from . import extract, normalise
from .browser import Fetcher

HOST = "careers.google.com"
BASE = "https://www.google.com/about/careers/applications"
RESULTS = f"{BASE}/jobs/results"

# A posting's permalink: `/jobs/results/<id>-<slug>`. The numeric id is captured apart from the
# slug because the id is the job's identity and the slug is decoration — two links to the same
# posting can carry different slugs, so de-duplicating on the whole path would read one job
# twice. The *fetched* URL keeps the slug, because that is the link the page actually publishes
# and the one a person could paste.
JOB_PATH = re.compile(r"/about/careers/applications/jobs/results/(\d+)([\w-]*)")

# Per run. Google lists thousands of roles and WERA wants the ones matching the watch titles, so
# the bound is on titles × pages rather than on jobs — and it exists because each posting is a
# render, which is seconds rather than milliseconds.
MAX_QUERIES = 4
MAX_JOBS_PER_QUERY = 10


def search_url(title: str, location: str | None) -> str:
    """One results page for a title, optionally narrowed to a place."""
    query = f"?q={quote_plus(title)}"
    return f"{RESULTS}{query}&location={quote_plus(location)}" if location else f"{RESULTS}{query}"


def job_urls(html: str, *, limit: int = MAX_JOBS_PER_QUERY) -> list[str]:
    """The postings linked from a rendered results page, de-duplicated, in page order."""
    out: list[str] = []
    seen: set[str] = set()
    for match in JOB_PATH.finditer(html or ""):
        job_id, slug = match.group(1), match.group(2)
        if job_id in seen:
            continue
        seen.add(job_id)
        out.append(f"{RESULTS}/{job_id}{slug}")
        if len(out) >= limit:
            break
    return out


def list_jobs(
    titles: list[str],
    locations: list[str],
    *,
    fetch_html: Fetcher,
    now: int,
    parse: object = None,
) -> tuple[list[Job], list[str]]:
    """Google's open roles for these titles. Returns `(jobs, errors)` and never raises.

    `errors` carries one line when the page couldn't be read at all, because "Google has no
    graduate engineering roles" and "WERA has no browser installed" are opposite facts and the
    channel strip should not show the first when it means the second.
    """
    wanted = [t.strip() for t in titles if t.strip()][:MAX_QUERIES]
    if not wanted:
        return [], []
    place = next((loc.strip() for loc in locations if loc.strip()), None)

    jobs: list[Job] = []
    seen: set[str] = set()
    rendered_any = False

    for title in wanted:
        page = fetch_html(search_url(title, place))
        if not page:
            continue
        rendered_any = True
        for url in job_urls(page):
            if url in seen:
                continue
            seen.add(url)
            card, how = extract.card(url, fetch_html=fetch_html, parse=parse)  # type: ignore[arg-type]
            if card is None:
                continue
            job = normalise.from_scrape(card, url=url, site=HOST, markdown=card.get("description"), now=now)
            if job is not None:
                jobs.append(job)
                logfire.info("Read a Google posting with {how}", how=how)

    if not rendered_any:
        # Said once, not per query: four identical lines about a missing browser is noise.
        return jobs, [
            "Google Careers needs a headless browser to read. Install the `browser` extra and run "
            "`uv run playwright install chromium`."
        ]
    return jobs, []
