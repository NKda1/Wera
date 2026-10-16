"""Jobs, sub-scores and fit reports (SPEC §6.4, §6.5)."""

from typing import Literal

from .base import Model, Opt
from .profile import Seniority

type Channel = Literal["board_search", "email_alert", "watchlist", "manual"]
type JobStatus = Literal["new", "shortlisted", "in_application", "snoozed", "rejected", "hidden", "closed", "archived"]
RemoteMode = Literal["remote", "hybrid", "onsite"]


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
    first_seen: str
    last_seen: str
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
