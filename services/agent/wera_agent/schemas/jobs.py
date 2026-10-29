"""Jobs, sub-scores and fit reports (SPEC §6.4, §6.5)."""

from typing import Literal

from .base import Model, Opt
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
    country: Opt[str] = None
    raw: str


class Salary(Model):
    min: Opt[float] = None
    max: Opt[float] = None
    currency: str
    period: Literal["year", "month", "day", "hour"]


class Ats(Model):
    vendor: Literal["greenhouse", "lever", "ashby"]
    board_token: str
    job_id: str


class JobSource(Model):
    channel: Channel
    detail: str
    seen_at: str


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
    status: JobStatus
    snoozed_until: Opt[str] = None
    hidden_reason: Opt[str] = None
    seniority: Opt[Seniority] = None


class SubScore(Model):
    score: float  # 0–10
    job_quote: str
    cv_bullet_ids: list[str]


class JobKeyword(Model):
    term: str
    weight: Literal["required", "nice"]


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
    model: str


class FitReport(FitEvidence):
    job_id: str
    freshness: float  # computed from postedAt
    total: float  # computed in code, §8.2
    rough_only: bool


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
