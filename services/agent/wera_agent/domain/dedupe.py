"""One job, however many places it was found (NORM-05, NORM-06, T-06).

The same posting reaches WERA from a board search, an alert email and a watchlist feed, often
with a different title each time. Showing it three times wastes the person's attention and
their model budget, so the three become one job that remembers all three sources.

Two mechanisms, in this order:

* **An exact key** (NORM-05) — the ATS identity if there is one, else the canonical apply URL,
  else a hash of company, title and city. Two cards with the same key are the same job.
* **A near match** (NORM-06) — same company and city, titles ≥ 0.9 similar, first seen within
  30 days. This is what catches "ML Engineer, Graduate" and "Graduate ML Engineer".

Similarity is a normalised token-set ratio, so word order doesn't matter and "Sr" expands to
"Senior" before comparing (§8.4).
"""

import hashlib
import re
from urllib.parse import urlparse, urlunparse

from rapidfuzz import fuzz

from ..clock import parse_ms
from ..schemas.jobs import Ats, Job

DAY_MS = 24 * 60 * 60 * 1000
NEAR_WINDOW_MS = 30 * DAY_MS
NEAR_TITLE = 0.9  # NORM-06
RESOLVE_TITLE = 0.85  # NORM-02

# §8.4: expanded before comparing, so "Sr SWE" and "Senior Software Engineer" match.
ABBREVIATIONS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "swe": "software engineer",
    "sde": "software engineer",
    "mle": "machine learning engineer",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "eng": "engineer",
    "dev": "developer",
    "grad": "graduate",
}

# How much a posted date is worth, for deciding which of two sightings' dates to keep.
_PRECISION = {"unknown": 0, "approximate": 1, "exact": 2}

# Query and fragment are tracking, not identity: the same job arrives with different utm tags.
TRACKING = re.compile(r"^(utm_|gh_|ref|source|src|lever-|ashby_)", re.I)


def normalise_title(title: str) -> str:
    words = re.sub(r"[^a-z0-9+#/ ]+", " ", title.lower()).split()
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)


def normalise_company(company: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", company.lower())
    cleaned = re.sub(r"\b(ltd|limited|inc|llc|plc|gmbh|sa|bv|corp|co|the)\b", " ", cleaned)
    return " ".join(cleaned.split())


def title_similarity(left: str, right: str) -> float:
    """0–1, word order and abbreviations ignored (§8.4)."""
    return fuzz.token_set_ratio(normalise_title(left), normalise_title(right)) / 100


def canonical_url(raw: str | None) -> str | None:
    """The apply URL without tracking, so one posting has one URL."""
    if not raw:
        return None
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None
    if not parsed.hostname:
        return None
    kept = [p for p in parsed.query.split("&") if p and not TRACKING.match(p.split("=")[0])]
    host = parsed.hostname.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower() or "https", host, path, "", "&".join(sorted(kept)), ""))


def dedupe_key(*, ats: Ats | None, apply_url: str | None, company: str, title: str, city: str | None) -> str:
    """NORM-05, in priority order. The ATS identity is the strongest: it is the employer's own."""
    if ats is not None:
        return f"ats:{ats.vendor}:{ats.board_token}:{ats.job_id}"
    canonical = canonical_url(apply_url)
    if canonical:
        return f"url:{canonical}"
    seed = f"{normalise_company(company)}|{normalise_title(title)}|{(city or '').lower().strip()}"
    return f"hash:{hashlib.sha256(seed.encode()).hexdigest()[:32]}"


def key_for(job: Job) -> str:
    return dedupe_key(ats=job.ats, apply_url=job.apply_url, company=job.company, title=job.title, city=job.location.city)


def is_near_duplicate(left: Job, right: Job) -> bool:
    """NORM-06: the same job posted under a slightly different title."""
    if normalise_company(left.company) != normalise_company(right.company):
        return False
    if (left.location.city or "").lower() != (right.location.city or "").lower():
        return False
    if title_similarity(left.title, right.title) < NEAR_TITLE:
        return False
    return abs(parse_ms(left.first_seen) - parse_ms(right.first_seen)) <= NEAR_WINDOW_MS


def merge_sources(into: Job, other: Job) -> Job:
    """Keeps every source and the fullest description, so nothing is lost to a merge."""
    seen = {(s.channel, s.detail) for s in into.sources}
    into.sources = [*into.sources, *[s for s in other.sources if (s.channel, s.detail) not in seen]]
    into.first_seen = min(into.first_seen, other.first_seen)
    into.last_seen = max(into.last_seen, other.last_seen)
    # A card from an email has no description; one scraped from the board does.
    if len(other.description_md or "") > len(into.description_md or ""):
        into.description_md, into.description_status = other.description_md, other.description_status
    into.apply_url = into.apply_url or other.apply_url
    into.ats = into.ats or other.ats
    into.linkedin_url = into.linkedin_url or other.linkedin_url  # MAIL-07: stored, never fetched
    into.salary = into.salary or other.salary
    # Not "first one wins": the same job arrives from an alert email saying "3 days ago" and
    # from the employer's own feed with a real timestamp, and which order they arrive in is an
    # accident of scheduling. The better-evidenced date should win either way, or a job's
    # position in the urgency groups depends on which channel happened to see it first.
    #
    # No `or into.posted_at is None` here: a job with no date always has precision `unknown`,
    # which is the lowest rank, so "I have no date and they do" is already this comparison.
    if _PRECISION[other.posted_precision] > _PRECISION[into.posted_precision]:
        into.posted_at, into.posted_precision = other.posted_at, other.posted_precision
    return into


def collapse(jobs: list[Job]) -> list[Job]:
    """Folds a batch down to one job per posting, keeping the order they arrived in."""
    out: list[Job] = []
    by_key: dict[str, Job] = {}
    for job in jobs:
        key = key_for(job)
        if key in by_key:
            merge_sources(by_key[key], job)
            continue
        near = next((k for k in out if is_near_duplicate(k, job)), None)
        if near is not None:
            merge_sources(near, job)
            continue
        by_key[key] = job
        job.dedupe_key = key
        out.append(job)
    return out
