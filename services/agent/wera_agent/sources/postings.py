"""What a URL is, before anything is paid to read it (SRCH-21).

Board search used to send every search result to the scrape ladder, and a search engine returns
whatever it has indexed for a site: results pages (`gradcracker.com/search/…?page=10`), category
pages (`builtinlondon.uk/jobs/remote`), company pages and blog posts. The ladder read each as if
it were one posting, the model reported the first job on the page as "the job", and WERA saved
it with the results page as its apply link. 22 of the first 304 board-search jobs were exactly
that, one of them far enough along to have an application.

So every URL is sorted first, from its shape alone:

* `posting` — one job. The only kind the Harvester reads.
* `listing` — a page of jobs (a search, a category, a company's jobs). Not read as a job; its
  posting links are collected instead.
* `closed` — the URL or its title already says the job is gone.
* `other` — anything else: a blog, a company profile, a login, an unknown shape.

The order of evidence: an employer's job system (its URLs are unambiguous), then the board's own
`posting_pattern` from `config/sites.yaml` (taken from real URLs in the database, never guessed),
then a generic rule for hosts WERA has no pattern for. A generic posting needs an id in its path;
a slug alone is too often a category ("/jobs/software-engineer" is Milkround's category page).
"""

import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, urljoin, urlparse

from ..domain.dedupe import canonical_url
from ..http import is_denied
from . import resolve
from .liveness import _GONE_IN_PATH, CLOSED_PHRASES

if TYPE_CHECKING:
    from ..config import Site

type Kind = Literal["posting", "listing", "closed", "other"]

# Employer job systems beyond the three `resolve` reads feeds from. Host, then the path a single
# posting has there; a vendor URL that doesn't match is that employer's board, so a listing.
VENDOR_POSTINGS = (
    ("myworkdayjobs.com", re.compile(r"/job/[^/]+/[^/?#]+", re.I)),
    ("smartrecruiters.com", re.compile(r"^/[^/]+/\d{6,}", re.I)),
    ("workable.com", re.compile(r"/j/[0-9A-F]{6,}", re.I)),
    ("teamtailor.com", re.compile(r"^/jobs/\d+", re.I)),
    ("recruitee.com", re.compile(r"^/o/[^/?#]+", re.I)),
    ("personio.de", re.compile(r"^/job/\d+", re.I)),
    ("personio.com", re.compile(r"^/job/\d+", re.I)),
    ("bamboohr.com", re.compile(r"/careers/\d+", re.I)),
    ("greenhouse.io", None),
    ("lever.co", None),
    ("ashbyhq.com", None),
)

# Query parameters that name one job on an employer's own careers page. `gh_jid` is Greenhouse's
# embed: 6,000 of the watchlist's jobs live at URLs like `databricks.com/…/job?gh_jid=8593713002`.
POSTING_PARAMS = {"gh_jid", "ashby_jid", "jobid", "job_id", "vacancyid"}

# The generic posting: a job-ish segment with an id somewhere after it. An id is a run of five
# digits, a UUID, a long hex string, or a requisition number with a short letter prefix
# ("R0133741", "JR12345") — never a year, never a bare slug.
#
# The segments include how German, French, Dutch, Spanish and Italian boards write "jobs" (16 Nov:
# jobs.ch's `/de/stellenangebote/detail/<uuid>/` was a posting read as "other", so a search of
# Switzerland could not keep it).
JOB_WORDS = (
    r"jobs?|vacanc(?:y|ies)|positions?|roles?|openings?|careers?|graduate-jobs?"
    r"|stellenangebote?|stellen|stelle|jobangebote?|emplois?|offres?(?:-d-emploi)?|vacatures?|empleos?|ofertas?|offerte"
)
JOB_SEGMENT = re.compile(rf"/(?:{JOB_WORDS}|o)/", re.I)
ID = re.compile(
    r"(?:^|[/_.=-])(?:\d{5,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{12,}|[a-z]{1,3}\d{5,})"
    r"(?:$|[/_.?&-])",
    re.I,
)

# A single slug under a jobs index, with nothing after it: `/jobs/software-engineer`, `/de/jobs/<slug>/`.
ONE_SLUG = rf"^/(?:[a-z]{{2}}/)?(?:{JOB_WORDS})/[^/]+/?$"
ONE_SLUG_PATH = re.compile(ONE_SLUG, re.I)


# Pages that are never a posting, whatever else their path says.
NOT_A_JOB = re.compile(
    r"/(?:blog|news|articles?|insights|resources|events?|about(?:-us)?|press|privacy|terms|cookies?|"
    r"log-?in|sign-?in|sign-?up|register|help|faq|salar(?:y|ies)|advice|career-advice|guides?|"
    r"companies-v1|apply|application|workflow)(?:/|$)",
    re.I,
)

# A page of jobs: a search, a category, a place, a page of results, a bare jobs index.
LISTING_PATH = re.compile(
    r"/(?:search|job-search|browse|categor(?:y|ies)|sectors?|locations?)(?:/|$)"
    r"|/in-[a-z0-9-]+/?$"
    r"|-jobs(?:-in-[a-z0-9-]+)?/?$"
    rf"|/(?:{JOB_WORDS}|opportunities)/?$"
    # One word under a jobs index is a category: builtinlondon `/jobs/remote`, Milkround
    # `/jobs/software-engineer`. Reached only after the posting rules, so a posting with an id
    # in that position has already been claimed.
    rf"|{ONE_SLUG}",
    re.I,
)
LISTING_PARAMS = {"page", "q", "query", "keywords", "search", "location", "order", "sort"}

# A company page, and a company's own jobs list on a board.
COMPANY = re.compile(r"^/(?:[a-z]{2}/)?compan(?:y|ies)/[^/]+/?$", re.I)
COMPANY_JOBS = re.compile(r"^/(?:[a-z]{2}/)?compan(?:y|ies)/[^/]+/jobs/?$", re.I)

# Titles a dead page wears. Checked on the title only: "not found" in a description is a sentence,
# in a title it is the page.
CLOSED_TITLE = re.compile(
    r"^\s*(?:404\b|(?:page|job|posting|job posting|vacancy|position)?\s*not found\b|.*\b(?:expired|no longer available)\b)",
    re.I,
)


def classify(url: str, *, title: str = "", sites: "list[Site] | None" = None) -> Kind:
    """What `url` is, from its shape (and, when there is one, the title it was found with).

    `sites` defaults to `config/sites.yaml`; tests pass their own. Pure apart from that read.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "other"
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or is_denied(url):
        return "other"
    path = parsed.path or "/"
    params = {k.lower() for k in parse_qs(parsed.query)}

    if _GONE_IN_PATH.search(path) or (title and _closed_title(title)):
        return "closed"

    vendor = _vendor(url, host, path)
    if vendor is not None:
        return vendor

    # An application form names its vacancy too (`wise.jobs/Workflow?…&vacancyId=4404`, seen
    # live), but it is a form, not the posting: there is nothing on it to read a job from.
    if params & POSTING_PARAMS and not NOT_A_JOB.search(path):
        return "posting"

    site = _site_for(host, sites)
    if site is not None and site.posting_pattern:
        if re.search(site.posting_pattern, path):
            return "posting"
        return _not_posting(path, params)

    if NOT_A_JOB.search(path) or COMPANY.match(path):
        return "other"
    segment = JOB_SEGMENT.search(path)
    if segment and ID.search(path, segment.end() - 1):
        return "posting"
    return _not_posting(path, params)


def is_closed_page(title: str, text: str) -> bool:
    """Whether a page that was read says the job is gone: its title, or the top of its text."""
    if _closed_title(title):
        return True
    head = " ".join(text[:1500].lower().split())
    return any(phrase in head for phrase in CLOSED_PHRASES)


def _closed_title(title: str) -> bool:
    return bool(CLOSED_TITLE.match(title))


def _vendor(url: str, host: str, path: str) -> Kind | None:
    """An employer job system's URL: a posting when it names one job, its board otherwise."""
    for vendor_host, pattern in VENDOR_POSTINGS:
        if host != vendor_host and not host.endswith(f".{vendor_host}"):
            continue
        if pattern is None:
            return "posting" if resolve.ats_from_url(url) is not None else "listing"
        return "posting" if pattern.search(path) else "listing"
    return None


def _not_posting(path: str, params: set[str]) -> Kind:
    """A URL that didn't match a posting: a listing if it looks like one, otherwise nothing."""
    if NOT_A_JOB.search(path) or COMPANY.match(path):
        return "other"
    if COMPANY_JOBS.match(path) or LISTING_PATH.search(path) or params & LISTING_PARAMS:
        return "listing"
    return "other"


def _site_for(host: str, sites: "list[Site] | None") -> "Site | None":
    if sites is None:
        from ..config import sites as configured

        sites = configured()
    bare = host.removeprefix("www.")
    for site in sites:
        domain = site.domain.lower().removeprefix("www.")
        if bare == domain or bare.endswith(f".{domain}"):
            return site
    return None


def posting_links(html: str, page_url: str, domain: str, limit: int) -> list[tuple[str, str]]:
    """The posting links on a page of results, in page order, as (url, link text).

    Same site only — a board's results page links to its own postings, and anything else on it is
    an advert, a footer or a social link. Deduped on the canonical form, so the same posting
    reached through two tracking parameters is one link — but returned as the page wrote it,
    because the canonical form drops `www.` and is a key, not an address to open.
    """
    from selectolax.lexbor import LexborHTMLParser

    site = domain.lower().removeprefix("www.")
    page = canonical_url(page_url) or page_url
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for anchor in LexborHTMLParser(html).css("a[href]"):
        href = urljoin(page_url, anchor.attributes.get("href") or "")
        parsed = urlparse(href)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in ("http", "https") or not (host == site or host.endswith(f".{site}")):
            continue
        # SRCH-21. A loose `/jobs/<anything>` rule used to stand here, and it let builtinlondon's
        # category pages (`/jobs/remote`, `/jobs/sales/search/account-executive`) through as
        # postings: the model then reported the first job on each as "the job".
        if classify(href) != "posting":
            continue
        key = canonical_url(href) or href
        if key == page or key in seen:
            continue
        seen.add(key)
        out.append((href.split("#")[0], _link_text(anchor)[:200]))
        if len(out) >= limit:
            break
    return out


# Where a job card keeps its title inside the link, first match wins. 21 Nov: jobs.ch's links are
# whole cards, and their text run together read "3 weeks agoBusiness Analyst TraineePlace of
# work:Zürich" — a title MATCH-01 can't judge and the pool can't rank.
_TITLE_NODES = ("h1", "h2", "h3", "h4", "h5", "[class*='title' i]", "[data-cy*='title' i]", "strong")


def _link_text(anchor: Any) -> str:
    """A link's job title: its label when it has one of a few words, the card's heading when the
    link is a card, else all its text with the pieces kept apart."""
    for attr in ("aria-label", "title"):
        label = " ".join((anchor.attributes.get(attr) or "").split())
        if len(label.split()) >= 2:
            return label
    for selector in _TITLE_NODES:
        node = anchor.css_first(selector)
        if node is not None and (text := " ".join((node.text(separator=" ") or "").split())):
            return text
    return " ".join((anchor.text(separator=" ") or "").split())


# SRCH-22 (21 Nov): how a results page names its next page. `rel="next"` first — a `<link>` in the
# head or an `<a>` in the pager — then a pager link whose words or label say "next", then a link
# that is this page's own address with its page number one higher. Nothing looser: a link called
# "More jobs" is as often a category as a second page, and following it reads a different list.
_NEXT_WORDS = re.compile(
    r"^(?:next(?:\s+page)?|next\s*[›»>→]|[›»>→]|page\s+suivante|weiter|nächste(?:\s+seite)?|volgende|siguiente)$", re.I
)
PAGE_PARAMS = ("page", "pg", "p", "pagenumber", "page_number", "pageno")


def next_page(html: str, page_url: str) -> str | None:
    """The page after this one, on the same site, or None when the page names none.

    Same host only (a pager never sends you to another site), never this page again, and never a
    URL `classify` calls a posting or a closed page — a "next" that is really the first job is
    not a page of results.
    """
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(html)
    here = urlparse(page_url)
    site = (here.hostname or "").lower().removeprefix("www.")
    page = canonical_url(page_url) or page_url

    def usable(href: str | None) -> str | None:
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return None
        url = urljoin(page_url, href).split("#")[0]
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in ("http", "https") or not (host == site or host.endswith(f".{site}")):
            return None
        if (canonical_url(url) or url) == page or classify(url) in ("posting", "closed"):
            return None
        return url

    for node in tree.css('link[rel~="next"], a[rel~="next"]'):
        if url := usable(node.attributes.get("href")):
            return url
    for anchor in tree.css("a[href]"):
        label = " ".join((anchor.attributes.get("aria-label") or anchor.attributes.get("title") or anchor.text() or "").split())
        if label and _NEXT_WORDS.match(label) and (url := usable(anchor.attributes.get("href"))):
            return url
    # The same address with the page number one higher: `?page=2` from `?page=1` (or from no page
    # number at all, which is page 1).
    params = {k.lower(): v for k, v in parse_qs(here.query).items()}
    name = next((p for p in PAGE_PARAMS if p in params), None)
    try:
        current = int(params[name][0]) if name else 1
    except ValueError:
        return None
    for anchor in tree.css("a[href]"):
        url = usable(anchor.attributes.get("href"))
        if url is None:
            continue
        there = urlparse(url)
        if there.path.rstrip("/") != here.path.rstrip("/"):
            continue
        theirs = {k.lower(): v for k, v in parse_qs(there.query).items()}
        key = name or next((p for p in PAGE_PARAMS if p in theirs), None)
        if key and theirs.get(key, [""])[0] == str(current + 1):
            rest = {k: v for k, v in theirs.items() if k != key}
            if rest == {k: v for k, v in params.items() if k != key}:
                return url
    return None


def links_from_urls(urls: list[str], page_url: str, domain: str, limit: int) -> list[tuple[str, str]]:
    """`posting_links` for a bare list of URLs — what a links-only render returns — with a title
    read off each URL's last words, since there is no link text: `/job/graduate-analyst-12345`
    is "Graduate Analyst". Enough for MATCH-01's title rules and the pool's ranking to work on."""
    html = "".join(f'<a href="{u.replace(chr(34), "%22")}">{_slug_title(u)}</a>' for u in urls if isinstance(u, str))
    return posting_links(html, page_url, domain, limit)


def _slug_title(url: str) -> str:
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    words = [w for seg in path.split("/") for w in re.split(r"[-_+.]", seg) if w.isalpha() and len(w) > 1]
    tail = [
        w
        for w in words
        if w.lower() not in {"job", "jobs", "vacancy", "vacancies", "careers", "career", "en", "gb", "uk", "html"}
    ]
    return " ".join(w.capitalize() for w in tail[-8:])
