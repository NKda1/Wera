"""The company's own site, and its About page, for the company brief (18 Nov).

What this module will and won't do, because a company's site is not a job board and WERA reads it
on the person's behalf:

* **Only the employer's own site.** The apply link's host when it is the employer's, else one
  Serper search for the company's site, skipping boards, aggregators, directories and every ATS
  vendor's host (a Greenhouse page is Greenhouse's, not the employer's). **Never LinkedIn**
  (`http.check` refuses it anyway).
* **Not an agency's post.** When the "company" is an agency or a board (eFinancialCareers, Reed, a
  recruiter), the employer isn't named, so there is nothing of theirs to read; the brief says so.
* **One page, plain HTTP, after robots.txt.** About, then About us, then the home page — the first
  that robots.txt allows and that reads as a page of text. No Firecrawl, no browser, nothing that
  would get past a site that said no. Redirects are followed only within the same site.
* **Only the page's own words.** Scripts, navigation, headers, footers and forms are dropped, and
  what is left is capped (`company_brief.max_chars`): enough for an About page, and stored so a
  quote from it can be checked again later.
"""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import logfire

from ..config import wera
from ..domain import dedupe
from ..errors import ApiError
from ..http import host_of, is_denied, request
from ..schemas.jobs import Job
from .robots import USER_AGENT, Robots
from .site_discovery import ATS_VENDOR_HOSTS

# Hosts that talk about companies without being them.
NOT_THE_COMPANY = (
    *dedupe.AGGREGATORS,
    *ATS_VENDOR_HOSTS,
    "linkedin.com", "indeed.com", "indeed.co.uk", "glassdoor.com", "glassdoor.co.uk", "reed.co.uk", "totaljobs.com",
    "cv-library.co.uk", "monster.com", "ziprecruiter.com", "wikipedia.org", "crunchbase.com", "bloomberg.com",
    "companieshouse.gov.uk", "find-and-update.company-information.service.gov.uk", "facebook.com", "instagram.com",
    "x.com", "twitter.com", "youtube.com", "tiktok.com", "trustpilot.com", "dnb.com", "zoominfo.com", "pitchbook.com",
    "wellfound.com", "builtin.com", "theorg.com", "craft.co", "owler.com", "comparably.com", "nhs.uk",
)  # fmt: skip

ABOUT_PATHS = ("/about", "/about-us", "/")
_DROP = ("script", "style", "noscript", "svg", "nav", "header", "footer", "form", "iframe", "button", "select", "template")


@dataclass
class Page:
    url: str
    text: str

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:32]


def _excluded(host: str) -> bool:
    host = host.removeprefix("www.")
    return not host or any(host == h or host.endswith(f".{h}") for h in NOT_THE_COMPANY)


_JOBS_SUBDOMAIN = re.compile(r"^(?:careers?|jobs?|apply|hire|hiring|work|recruiting|talent)\.(?=[^.]+\.)")


def _root(url: str) -> str:
    """The site's home: `careers.example.com/job/1` is `https://example.com`, where About lives."""
    p = urlparse(url)
    return f"https://{_JOBS_SUBDOMAIN.sub('', p.hostname or '')}"


def site_from_links(job: Job) -> str | None:
    """The employer's site from the job's own link, when the link is on the employer's host."""
    if job.apply_url and not _excluded(host_of(job.apply_url)) and not is_denied(job.apply_url):
        return _root(job.apply_url)
    return None


def site_from_search(company: str) -> str | None:
    """One Serper search for the company's site; the first result that is the company's own."""
    from .serper import Serper

    queries = wera().company_brief.serper_queries
    if queries < 1:
        return None
    try:
        client = Serper(max_queries=queries, window="", guard=_guard())
    except ApiError:
        return None  # no Serper key: the brief is written from the post alone, and says so
    for result in client.search(f'"{company}" official site', gl="uk", hl="en") or []:
        host = host_of(result.url)
        if not _excluded(host) and not is_denied(result.url):
            return _root(result.url)
    return None


def _guard():  # noqa: ANN202 — a provider_guard.Guard, or None without Redis
    from . import targets
    from .provider_guard import Guard

    try:
        return Guard(targets.connect(), "serper")
    except ApiError:
        return None


def main_text(html: str, limit: int) -> str:
    """A page's own words: `main` or `article` when it has one, the body otherwise, without the
    furniture around it. Lines kept in order, repeats dropped."""
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(html)
    for tag in _DROP:
        for node in tree.css(tag):
            node.decompose()
    root = tree.css_first("main") or tree.css_first("article") or tree.body
    if root is None:
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    for raw in root.text(separator="\n").split("\n"):
        line = " ".join(raw.split())
        if len(line) < 3 or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    text = "\n".join(lines)
    return text[:limit].rsplit("\n", 1)[0] if len(text) > limit else text


def _get(url: str) -> tuple[int, str, str]:
    """GET, following redirects within the same site (http://→https://, bare→www.). Returns the
    status, the final URL and the body."""
    site = host_of(url).removeprefix("www.")
    for _ in range(4):
        response = request("GET", url, headers={"user-agent": USER_AGENT, "accept": "text/html"})
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
            nxt = urljoin(url, response.headers["location"])
            if host_of(nxt).removeprefix("www.") != site:
                return response.status_code, url, ""
            url = nxt
            continue
        kind = response.headers.get("content-type", "")
        return response.status_code, url, response.text if "html" in kind else ""
    return 0, url, ""


def about_page(site: str, robots: Robots | None = None) -> tuple[Page | None, str | None]:
    """The first About-like page robots.txt allows and that reads as text, or why there is none."""
    robots = robots or Robots()
    limit = wera().company_brief.max_chars
    refused = unreadable = 0
    for path in ABOUT_PATHS:
        url = urljoin(site + "/", path.lstrip("/"))
        verdict = robots.verdict(url)
        if verdict != "allowed":
            refused += verdict == "disallowed"
            unreadable += verdict == "unreadable"
            continue
        try:
            status, final, body = _get(url)
        except Exception as err:  # a network failure on one page isn't a reason to stop trying
            logfire.info("Company page {url} didn't answer: {err}", url=url, err=type(err).__name__)
            continue
        text = main_text(body, limit) if status == 200 and body else ""
        if len(text) >= 200:
            return Page(url=final, text=text), None
    host = host_of(site)
    if refused:
        return None, f"{host}'s robots.txt asks automated readers not to, so WERA didn't."
    if unreadable:
        return None, f"WERA couldn't read {host}'s robots.txt, so it didn't read the site."
    return None, f"WERA couldn't find a page of text about the company on {host}."


def read(job: Job) -> tuple[Page | None, str | None]:
    """The company's About page for this job, or why there is none."""
    from ..settings import get_settings

    if not wera().company_brief.enabled:
        return None, "Reading company sites is turned off (company_brief.enabled)."
    if get_settings().wera_demo_mode:
        return None, "Demo mode reads no web pages, so this brief is from the post alone."
    if dedupe.is_agency(job.company):
        return None, f"This post is {job.company}'s, an agency or job board, so the employer isn't named."
    site = site_from_links(job) or site_from_search(job.company)
    if site is None:
        return None, f"WERA couldn't find {job.company}'s own website."
    return about_page(site)
