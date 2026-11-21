"""Jobs, sub-scores and fit reports (SPEC §6.4, §6.5)."""

from typing import Literal

from .base import Model, Opt
from .learning import LearnedRank
from .profile import Seniority

type Channel = Literal["board_search", "email_alert", "watchlist", "manual"]
type JobStatus = Literal["new", "shortlisted", "in_application", "snoozed", "rejected", "hidden", "closed", "archived"]
RemoteMode = Literal["remote", "hybrid", "onsite"]

# How much a `postedAt` is worth, which is not the same across the three channels:
#
# * `exact` — an employer's own ATS feed said so (`first_published`, `createdAt`, `publishedAt`).
# * `approximate` — a board or an alert email said "3 days ago", and `normalise.parse_posted`
#   turned that into a timestamp anchored to the moment WERA read it. The value looks precise
#   and isn't: it is right to a day at best, and anything finer is an artefact.
# * `unknown` — no date at all.
#
# Recorded rather than inferred, because it can't be recovered later: once a relative phrase has
# become an ISO string, nothing downstream can tell it from a real one.
type PostedPrecision = Literal["exact", "approximate", "unknown"]

# The groups the Jobs screen offers, from `domain.scoring.URGENCY`.
type Urgency = Literal["today", "this_week", "two_weeks", "older", "unknown"]

# MATCH-10: whether WERA believes the posting is still open, and on what evidence.
#
# * `open` — an ask came back open recently.
# * `unverified` — never asked. No apply URL, LinkedIn-only, robots-disallowed, or past the
#   per-sweep cap. Not a doubt about the job, an absence of a question.
# * `unreachable` — asked, and the answer didn't confirm it. A board that blocks bots, a
#   timeout, a redirect WERA won't follow. **Flagged, never hidden**: inconclusive is not
#   evidence of withdrawal, and removing a live job is worse than showing a stale one.
# * `gone` — confirmed closed, which is a `status` change rather than a flag.
type Liveness = Literal["open", "unverified", "unreachable", "gone"]


class Location(Model):
    city: Opt[str] = None
    # ISO 3166-1 alpha-2 ("GB"), or None when WERA can't tell — set on the way in, whatever the
    # source wrote (`places.country_code`, 19 Nov). `raw` keeps the words.
    country: Opt[str] = None
    raw: str


class Salary(Model):
    min: Opt[float] = None
    max: Opt[float] = None
    currency: str
    period: Literal["year", "month", "day", "hour"]


class Ats(Model):
    # The employer's own system. The last four are international organisations' (SRCH-34), where
    # `board_token` is the organisation's id in config/organisations.yaml.
    vendor: Literal["greenhouse", "lever", "ashby", "un_careers", "taleo", "workday", "successfactors"]
    board_token: str
    job_id: str


class InternationalPost(Model):
    """What an international organisation's own system says about a post (SRCH-34)."""

    org: str  # its id in config/organisations.yaml: "un", "who"
    # The UN common system's grade as the post gives it, written one way: "P-2", "NO-B", "G-5",
    # "I-1" (an internship). None for a contract with no grade, such as a consultancy.
    grade: Opt[str] = None
    # Internationally recruited posts are open to any nationality and move people; locally
    # recruited ones (G and NO grades, "National Consultant") are for residents or nationals of
    # the duty station's country. None when the post doesn't say.
    recruitment: Opt[Literal["international", "local"]] = None
    # The organisation's own job family or network, when it gives one.
    family: Opt[str] = None


class JobSource(Model):
    channel: Channel
    detail: str
    seen_at: str


class FoundBy(Model):
    """SRCH-25: the run that first added a job — written once, when the job is inserted.

    What the Jobs screen's "New" tag and its origin label ("Search 11 Nov") read. It replaces a
    single list of the last run's job ids, which every run overwrote — so a job lost its "new"
    mark the moment any other run finished, inbox and watchlist runs included, whether or not
    anyone had looked at it.
    """

    # The intake run, when there was one. A pasted job has none.
    run_id: Opt[str] = None
    channel: Channel
    at: str


class JobFact(Model):
    """One key fact about a job, in the post's own words: "Contract: Permanent", "Experience: 7+
    years of finance experience". Read off the post in code, never written by a model."""

    label: str
    value: str


type SectionKind = Literal["intro", "role", "requirements", "nice", "benefits", "company", "process", "legal", "other"]


class DescriptionBlock(Model):
    t: Literal["p", "ul", "ol", "h"]  # `h`: a sub-heading inside a section, over the list it names
    text: Opt[str] = None  # a paragraph or a sub-heading
    items: Opt[list[str]] = None  # a list


class DescriptionSection(Model):
    heading: str | None
    kind: SectionKind
    # Folded on the card: the company's own blurb, the legal small print, how to apply. Present
    # and one click away, never deleted — the words are the employer's and the person may want them.
    fold: bool
    blocks: list[DescriptionBlock]


class DescriptionView(Model):
    """The description laid out to read (`domain.description`), computed from `description_md`
    in code. `description_md` itself stays as it arrived, because the fit score's cache key
    (`fit.job_hash`) reads it, and re-flowing it would make every score look stale."""

    version: int  # `domain.description.VERSION`: a better formatter re-runs on the next read
    # What it was laid out from (`description.source_key`): a re-sighting that brings a fuller
    # description is laid out again, rather than showing the old text beside the new one.
    of: str = ""
    facts: list[JobFact]
    sections: list[DescriptionSection]


class JobSummary(Model):
    """`job.summary` (18 Nov), written on click and kept until the post changes. Every item was
    checked against the post in code; the ones whose words or numbers aren't in it were dropped."""

    gist: str
    duties: list[str]
    must_haves: list[str]
    nice_to_haves: list[str]
    perks: list[str]
    watch_outs: list[str]
    job_hash: str  # `fit.job_hash` of the post it summarises
    model: str
    created_at: str


class Job(Model):
    id: str
    dedupe_key: str
    title: str
    company: str
    location: Location
    remote: RemoteMode | None
    salary: Opt[Salary] = None
    description_md: str | None
    description_status: Literal["full", "partial", "missing"]
    apply_url: Opt[str] = None
    ats: Opt[Ats] = None
    linkedin_url: Opt[str] = None  # MAIL-07: stored, never fetched
    sources: list[JobSource]
    language: str
    posted_at: Opt[str] = None
    # Defaulted so rows written before this field existed still load; `unknown` is also the
    # right answer for them, since nothing recorded where their date came from.
    posted_precision: PostedPrecision = "unknown"
    first_seen: str
    # LIFE-01: **`last_seen` is about WERA's attention, not about the vacancy.** It means "a
    # source mentioned this", and a source can mention a job that closed yesterday — a cached
    # card did exactly that. It is not evidence the posting is still open, which is what the
    # three fields below are for. Nothing may infer openness from `last_seen`.
    last_seen: str
    # WERA actively asked whether this posting is still there — whatever came back. Set on every
    # re-check, including the inconclusive ones, because "we asked and couldn't tell" and "we
    # never asked" are different states and the screen says so.
    last_checked_at: Opt[str] = None
    # The last time an ask came back *open*: present in a feed that answered, or a 200 with no
    # closure wording. This is the only field that is evidence the vacancy exists.
    last_confirmed_at: Opt[str] = None
    # When the status became `closed`. Separate from `last_checked_at` because ATS-03 can close a
    # job from a feed's silence without any HTTP check having happened.
    closed_at: Opt[str] = None
    # LIFE-02 (14 Nov): when applications close, when a source says — Reed's `expirationDate`,
    # USAJOBS' `ApplicationCloseDate`, a page's `validThrough`, a deadline the page states. A job
    # past it is closed without a check: the employer has said so in advance.
    closes_at: Opt[str] = None
    status: JobStatus
    snoozed_until: Opt[str] = None
    hidden_reason: Opt[str] = None
    seniority: Opt[Seniority] = None
    # SRCH-25. Absent on every job saved before tagging existed, which is right: none of those is
    # new to the person. A job is new while it has `found_by` and no `seen_at`.
    found_by: Opt[FoundBy] = None
    # When the person first opened the job's details or decided on it.
    seen_at: Opt[str] = None
    # SRCH-31/32: the job API that found it ("reed", "usajobs", "adzuna", "careerjet"), when one
    # did. Set when the job is first saved and kept through later sightings. A job found via
    # Adzuna carries Adzuna's linked label on its card (their terms).
    via: Opt[str] = None
    # 20 Nov: the agency or board that reposted this employer's job ("eFinancialCareers"), when the
    # company WERA shows is the employer its title named ("Investment Banking Analyst - Citi"). With
    # no limit on any one site, one agency's reposts could fill a search; shown under fourteen
    # banks' names, they read as what they are — fourteen employers, one of them reposting.
    posted_by: Opt[str] = None
    # SRCH-34: a post at an international organisation, with its grade and who it is open to.
    international: Opt[InternationalPost] = None
    # 18 Nov: the description to read, and a summary when the person asked for one.
    description_view: Opt[DescriptionView] = None
    summary: Opt[JobSummary] = None


class SubScore(Model):
    score: float  # 0–10
    job_quote: str
    cv_bullet_ids: list[str]


# The checkable kinds of keyword — the ones a CV can name and coverage divides by. Soft skills
# and culture are a fourth kind in the post, kept in `FitEvidence.culture` instead, so that no
# reader of `keywords` (coverage, the skills line, the citation guard) can count them by mistake.
# `qualification` (20 Nov): a certification or licence a post asks for — CFA, ACA, ACCA, CPA, PMP.
# Checkable like a skill (a CV holds it or doesn't), and shown on Upskills under its own heading.
type KeywordCategory = Literal["hard_skill", "methodology", "tool", "qualification"]


class EducationAsk(Model):
    """The degree a post asks for, as it says it (UPSK-05, 20 Nov). Degrees were keywords before
    this — "Bachelor’s Degree" and "Business Administration" were shown as skills to acquire to a
    CV with a BSc in Economics. `domain.degrees.meets` judges this against the CV's education."""

    level: Literal["none", "bachelor", "master", "phd", "professional"] = "none"
    # Fields of study the post names, in its words ("Finance", "Economics", "a related field").
    subjects: list[str] = []
    required: bool = False


class JobKeyword(Model):
    term: str
    weight: Literal["required", "nice"]
    # Absent on keywords extracted before categories existed; they are still matched and counted.
    category: Opt[KeywordCategory] = None


class FitEvidence(Model):
    """Sub-scores as produced by the scoring model. The total is never model output (MATCH-04)."""

    core: SubScore
    adjacent: SubScore
    experience: SubScore
    eligibility: SubScore
    reason: str  # one-line verdict on the job card (MATCH-03)
    job_quote: str
    cv_bullet_ids: list[str]
    keywords: list[JobKeyword]
    # Soft skills and culture the post names, in its own words ("cross-functional", "ownership").
    # Display and letter context only — never a keyword. They were once in the coverage
    # denominator, twelve of fifteen terms, and coverage read 7%.
    culture: list[str] = []
    # The degree the post asks for (UPSK-05). None on reports read before 20 Nov, and when the
    # post asks for none.
    education: Opt[EducationAsk] = None
    model: str
    # Which version of the scoring rubric judged this (`nodes.fit.RUBRIC`). A score from an older
    # rubric is stale like one from another model, and is re-scored. None: before versions.
    rubric: str | None = None


class FitReport(FitEvidence):
    job_id: str
    freshness: float  # computed from postedAt
    total: float  # computed in code, §8.2
    rough_only: bool
    # Whether this score was judged against something that has since changed — a different CV
    # version, a different scoring model, or a job whose description has been rewritten.
    #
    # `fit_reports` has carried that cache key since the start, but only the *write* path ever
    # read it: `cached_evidence` consulted it to decide whether to re-score, and nothing on the
    # read path did. So saving a new CV left every job on the shortlist showing a score computed
    # against the old one, with nothing to say so. Computed on read, like `urgency` and
    # `liveness`, and for the same reason — it is a claim about now.
    stale: bool = False


class FitWeights(Model):
    core: float
    adjacent: float
    experience: float
    eligibility: float
    freshness: float


class ScoredJob(Job):
    fit: FitReport
    # Computed on read, never stored: it is a statement about how old the job is *now*, and a
    # stored one would be wrong by morning.
    urgency: Urgency
    # Same reasoning: derived from the three lifecycle fields at read time (SRCH-13, MATCH-10).
    liveness: Liveness
    # LIFE-03: nothing has shown it open — no confirmation, no fresh sighting — for
    # `PROOF_OF_LIFE_DAYS`. Off the main list, in "Possibly closed", until something does.
    possibly_closed: bool = False
    # LIFE-04: open `EVERGREEN_DAYS` or more. Often a standing advert rather than a vacancy.
    evergreen: bool = False
    # MATCH-12: moved in the order by what the person's decisions taught, and why. Absent when it
    # wasn't moved, or learned ranking is off.
    learned: Opt[LearnedRank] = None
