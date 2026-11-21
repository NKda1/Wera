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
COPY_TITLE = 0.8  # NORM-06 across sites: the same words, in any order

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
# `cid` is the careers-site campaign tag (Intuit's `?cid=directBookmarked_…` made one posting two
# jobs on 14 Nov); the click ids are the ad networks'. Whole names, so `cid` doesn't take `cidx`.
TRACKING = re.compile(r"^(utm_|gh_|ref|source|src|lever-|ashby_|mc_|(cid|gclid|fbclid|msclkid|trk)(=|$))", re.I)


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


def title_similarity_sorted(left: str, right: str) -> float:
    """0–1 like `title_similarity`, but counting the words one title has and the other hasn't."""
    return fuzz.token_sort_ratio(normalise_title(left), normalise_title(right)) / 100


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
    kept = [p for p in parsed.query.split("&") if p and not TRACKING.match(p.split("=")[0] + "=")]
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
    """NORM-06: the same job posted under a slightly different title — or reposted by an agency
    under the employer's name (20 Nov): eFinancialCareers' "Investment Banking Analyst, M&A - Citi"
    is Citi's "Investment Banking Analyst, M&A" (`employer_key`, `bare_title`)."""
    if employer_key(left.company, left.title) != employer_key(right.company, right.title):
        return False
    if (left.location.city or "").lower() != (right.location.city or "").lower():
        return False
    if title_similarity(bare_title(left.company, left.title), bare_title(right.company, right.title)) < NEAR_TITLE:
        return False
    return abs(parse_ms(left.first_seen) - parse_ms(right.first_seen)) <= NEAR_WINDOW_MS


# Sites that copy other sites' postings. A link there is the weakest a job can have: the employer's
# own system is where an application goes, and a board is at least where the employer posted it.
AGGREGATORS = (
    "bebee.com", "adzuna.co.uk", "adzuna.com", "careerjet.co.uk", "careerjet.com", "jobviewtrack.com",
    "simplyhired.co.uk", "simplyhired.com", "jooble.org", "talent.com", "efinancialcareers.com", "efinancialcareers.co.uk",
    "jobleads.com", "devglobaljobs.com", "jobrapido.com", "neuvoo.com", "whatjobs.com", "learn4good.com", "trabajo.org",
    "jobright.ai", "bandana.com", "jobs.fox8.com", "apply4u.co.uk", "startup.jobs", "simplify.jobs",
)  # fmt: skip


# Job boards and agencies that a feed names as the "company" when they posted on an employer's
# behalf. Reed returned eFinancialCareers as the employer of two quant roles on 14 Nov, and the
# letter, not knowing who the employer was, wrote "Your firm…". Compared after `normalise_company`.
INTERMEDIARIES = frozenset(
    {
        "efinancialcareers", "reed", "reed uk", "hays", "michael page", "robert half", "randstad",
        "harnham", "hamilton barnes", "jobleads", "bebee", "adzuna", "careerjet", "totaljobs", "cv library",
        "jobserve", "glassdoor", "indeed", "monster", "linkedin",
        # 20 Nov: finance's recruiters, named in eFinancialCareers' titles as if they were employers
        # ("Analyst, Portfolio Valuation - Goodman Masson").
        "goodman masson", "morgan mckinley", "selby jennings", "hays specialist recruitment", "page group",
        "pagegroup", "robert walters", "korn ferry", "oliver james", "sanderson", "investigo", "barclay simpson",
    }
)  # fmt: skip


def is_intermediary(company: str) -> bool:
    """Whether the named "company" is the board or agency that posted the job, not its employer."""
    return normalise_company(company) in INTERMEDIARIES


# A recruiter posting on an employer's behalf, by how agencies name themselves.
_AGENCY = re.compile(
    r"\b(recruitment|recruiting|recruiters?|staffing|resourcing|personnel|headhunt\w*|talent (?:solutions|partners|acquisition)|"
    r"search (?:group|partners)|employment (?:agency|services))\b",
    re.I,
)


def is_agency(company: str) -> bool:
    """A board or a recruiter rather than the employer: a named one, or one named like an agency."""
    return is_intermediary(company) or bool(_AGENCY.search(company))


# The end of an agency's title that names the employer: "… - Citi", "… | Barings", "… – PIMCO".
_EMPLOYER_TAIL = re.compile(r"\s+(?:-|–|—|\|)\s+([^-–—|]+?)\s*$")
# A tail that is about the job rather than who it is for: "… - Summer 2027", "… - Graduate Scheme".
# A year or a bare number is the job's; a digit inside a name is not (20 Nov: "Educate2trade Ltd").
_NOT_AN_EMPLOYER = re.compile(
    r"\b(?:19|20)\d{2}\b|(?<![A-Za-z])\d+(?![A-Za-z])|\b(intern(ship)?s?|graduate|grad|junior|senior|entry|level|programme|program|scheme|placement|summer|"
    r"analyst|associate|engineer|developer|manager|remote|hybrid|contract|permanent|temporary|part[- ]time|full[- ]time|"
    r"london|new york|toronto|uk|usa?|canada|england|europe|emea)\b",
    re.I,
)


def employer_named(company: str, title: str) -> str | None:
    """The employer an agency's title names, or None (20 Nov).

    Only for an agency (`is_agency`): an employer's own "Analyst - Capital Solutions" names a team,
    not a company. The tail must read as a name — a few words, no digits, no level, place or job
    word — so "Analyst - Summer 2027" and "Engineer - London" name no one.
    """
    if not is_agency(company):
        return None
    found = _EMPLOYER_TAIL.search(title)
    if found is None or found.start() == 0:
        return None
    name = found.group(1).strip()
    if not name or len(name.split()) > 5 or _NOT_AN_EMPLOYER.search(name) or is_agency(name):
        return None
    return name


def employer_key(company: str, title: str) -> str:
    """Whom a job is for, as `normalise_company` writes it: the employer an agency's title names,
    else the company the source gave."""
    return normalise_company(employer_named(company, title) or company)


def bare_title(company: str, title: str) -> str:
    """The title without the employer an agency added to it: "Investment Banking Analyst, M&A".
    An employer's own title that ends in its own name ("… - Barings", from Adzuna) loses it too."""
    found = _EMPLOYER_TAIL.search(title)
    if found is None or found.start() == 0:
        return title
    if employer_named(company, title) or normalise_company(found.group(1)) == normalise_company(company):
        return title[: found.start()].strip()
    return title


def same_posting_key(company: str, title: str) -> str:
    """One key for one job, whoever posted it: the employer and the title without its name, so the
    job APIs' copies of a posting (an agency's repost through Reed, the employer's own through
    Adzuna) are looked at once (SRCH-31, 20 Nov)."""
    return f"{employer_key(company, title)}|{normalise_title(bare_title(company, title))}"


def _host(url: str | None) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def link_rank(job: Job) -> int:
    """2 for the employer's own job system, 0 for an aggregator's copy, 1 for anything else."""
    from ..sources.site_discovery import ATS_VENDOR_HOSTS  # the list lives with the boards it isn't

    host = _host(job.apply_url)
    if job.ats is not None or any(host == h or host.endswith(f".{h}") for h in ATS_VENDOR_HOSTS):
        return 2
    if any(host == a or host.endswith(f".{a}") for a in AGGREGATORS):
        return 0
    return 1


# What a near-identical title can still differ in and be another job: its level, and its intake.
# `title_similarity` is a token-set measure, so "Junior Backend Software Engineer" and "Senior
# Software Engineer, Backend" score as the same title; the 2026 and 2027 intakes do too.
_LEVEL = re.compile(
    r"\b(intern(?:ship)?|graduate|grad|junior|jr|entry|associate|mid|senior|sr|staff|lead|principal|head|director|manager|new grad)\b",
    re.I,
)
_YEAR = re.compile(r"\b20\d\d\b")


def _same_kind(left: str, right: str) -> bool:
    def marks(title: str) -> tuple[set[str], set[str]]:
        return {m.lower() for m in _LEVEL.findall(title)}, set(_YEAR.findall(title))

    return marks(left) == marks(right)


def is_copy_elsewhere(left: Job, right: Job) -> bool:
    """NORM-06 across sites: the same job posted on another site (an aggregator's copy of an
    employer's posting, say), which a link can't tell apart because the links differ.

    Two postings on the *same* site are never merged by this, however alike: an employer with two
    "Software Engineer" openings in London has two jobs, with two links on its own system. Nor are
    two jobs from the employer's own feeds with different ids.
    """
    if left.ats is not None and right.ats is not None:
        return False  # equal ATS identities share a key and never get here; unequal are two jobs
    left_host, right_host = _host(left.apply_url), _host(right.apply_url)
    if not left_host or not right_host or left_host == right_host:
        return False
    # And the same words, not one title's words inside the other's: `title_similarity` scores
    # "Graduate Machine Learning Engineer 2027" against "Graduate Software Engineer (AI ML Test
    # Systems) 2027" at 1.0. Sorted-token similarity counts the extra words (63 there; 85–100
    # for the real copies seen on 13 Nov).
    # An agency's copy is compared without the employer's name it adds to the title (20 Nov).
    left_title, right_title = bare_title(left.company, left.title), bare_title(right.company, right.title)
    same_words = title_similarity_sorted(left_title, right_title) >= COPY_TITLE
    return same_words and _same_kind(left_title, right_title) and is_near_duplicate(left, right)


def merge_sources(into: Job, other: Job) -> Job:
    """Keeps every source and the fullest description, so nothing is lost to a merge."""
    seen = {(s.channel, s.detail) for s in into.sources}
    into.sources = [*into.sources, *[s for s in other.sources if (s.channel, s.detail) not in seen]]
    into.first_seen = min(into.first_seen, other.first_seen)
    into.last_seen = max(into.last_seen, other.last_seen)
    # A card from an email has no description; one scraped from the board does.
    if len(other.description_md or "") > len(into.description_md or ""):
        into.description_md, into.description_status = other.description_md, other.description_status
    # The employer's own link wins over a board's, and a board's over an aggregator's copy, so a
    # job first seen on bebee and then found at the employer applies at the employer.
    if other.apply_url and link_rank(other) > link_rank(into):
        into.apply_url, into.ats = other.apply_url, other.ats or into.ats
    else:
        into.apply_url = into.apply_url or other.apply_url
        into.ats = into.ats or other.ats
    into.via = into.via or other.via
    into.linkedin_url = into.linkedin_url or other.linkedin_url  # MAIL-07: stored, never fetched
    into.salary = into.salary or other.salary
    into.closes_at = into.closes_at or other.closes_at  # LIFE-02: whichever source gave a date
    into.international = into.international or other.international  # SRCH-34: the grade, from its own system
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
