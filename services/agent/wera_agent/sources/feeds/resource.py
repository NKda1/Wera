"""Finding a snippet API's lead where the employer posted it (SRCH-32).

An Adzuna or Careerjet lead is a title, a company and a tracked link WERA must not follow. One
Serper search for the two, quoted, finds where else the job is posted; the first result that is a
posting (`postings.classify`), isn't on an aggregator, and carries the lead's title and company is
queued as an ordinary posting, `via` the API that found it. A lead with no such result is counted
and dropped — never read through the tracked link.

These searches have their own cap (`resource_queries`, 10 a search), so finding originals never
eats into discovery's 30. They go through the same guard as discovery, so a lead two people's
searches both turned up is looked for once.
"""

import re
from urllib.parse import urlparse

from ...config import Site
from ...domain import dedupe
from ..postings import ONE_SLUG_PATH, classify
from ..serper import Result, Serper
from . import Lead

# Where a lead's own copies live: the aggregators, whose pages are someone else's posting again.
AGGREGATORS = dedupe.AGGREGATORS
# The ones a query names. Google reads about 32 words of a query, so the rest are dropped in code.
QUERY_EXCLUDES = ("adzuna.co.uk", "careerjet.co.uk", "jobviewtrack.com", "bebee.com", "jobleads.com")
# Words a company name carries that say nothing about which company it is.
COMPANY_FILLER = {"ltd", "limited", "plc", "inc", "llc", "llp", "group", "the", "uk", "co", "company", "careers", "recruitment"}


def query_for(lead: Lead, exclude: list[str]) -> str:
    title = re.sub(r'["]', "", lead.title)[:90]
    company = re.sub(r'["]', "", lead.company)[:60]
    minus = " ".join(f"-site:{host}" for host in [*QUERY_EXCLUDES, *exclude[:6]])
    return f'"{title}" "{company}" {minus}'.strip()


def original(lead: Lead, *, serper: Serper, sites: list[Site], gl: str, hl: str, exclude: list[str]) -> Result | None:
    """The lead's own posting, or None. One Serper query, answered from Redis when it can be.
    Among the results that qualify, the employer's own job system first (`dedupe.link_rank`)."""
    if not lead.title or not lead.company:
        return None
    hits = []
    for result in serper.search(query_for(lead, exclude), gl=gl, hl=hl) or []:
        host = dedupe._host(result.url)
        if any(host == a or host.endswith(f".{a}") for a in AGGREGATORS):
            continue
        kind = classify(result.url, title=result.title, sites=sites)
        if kind != "posting" and not (kind == "listing" and _slug_is_the_lead(lead, result.url)):
            continue
        if same_job(lead, result):
            hits.append(result)
    return max(hits, key=lambda r: dedupe.link_rank(_as_job(r)), default=None)


def _slug_is_the_lead(lead: Lead, url: str) -> bool:
    """An employer page with only a slug (`cudos.ch/de/jobs/junior-software-engineer-cudos-trail/`)
    is a posting when an API has just said that job exists and the slug spells its title.

    `classify` reads one slug under `/jobs/` as a category, which is right with nothing else to go
    on — Milkround's `/jobs/software-engineer` is one. A lead is the something else: on 16 Nov
    Adzuna's two Zürich roles were lost because their employers' pages are written this way.
    `same_job` still has to agree (title words, level, company) before the page is queued."""
    path = urlparse(url).path
    if not ONE_SLUG_PATH.match(path):
        return False
    slug = set(re.findall(r"[a-z0-9]+", path.lower().rstrip("/").rsplit("/", 1)[-1]))
    words = {w for w in re.findall(r"[a-z0-9]+", lead.title.lower()) if len(w) > 2}
    return len(words & slug) >= min(3, len(words))


def same_job(lead: Lead, result: Result) -> bool:
    """The lead's title, by the rule that merges copies (NORM-06 across sites): the same words in
    any order, the same level and intake — a Senior result is not a lead for a Backend role — and
    the company named in the result. A result's title often trails the company and the place
    ("… at Freetrade, London"), so those are taken off before comparing."""
    similar = max(dedupe.title_similarity_sorted(lead.title, t) for t in (result.title, _bare(result.title, lead.company)))
    if similar < dedupe.COPY_TITLE or not dedupe._same_kind(lead.title, result.title):
        return False
    words = [w for w in re.findall(r"[a-z0-9]+", lead.company.lower()) if w not in COMPANY_FILLER and len(w) > 1]
    text = f"{result.title} {result.snippet} {result.url}".lower()
    return not words or any(w in text for w in words)


def _bare(title: str, company: str) -> str:
    """'Junior Software Engineer at Abound, London | Built In' → 'Junior Software Engineer'."""
    cut = re.split(r"\s+(?:at|@)\s+|\s[|–—-]\s|,\s", title, maxsplit=1)[0]
    return re.sub(re.escape(company), "", cut, flags=re.I).strip(" -–—|,") or title


def _as_job(result: Result) -> object:
    class Link:  # just enough of a Job for `link_rank`
        ats = None
        apply_url = result.url

    return Link()
