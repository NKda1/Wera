"""Request bodies and composite responses for the agent's HTTP API (SPEC §9.1)."""

from typing import Annotated, Literal

from pydantic import Field

from .applications import ApplicationListItem, ApplicationStatus
from .base import Model, Opt, check, required
from .jobs import ScoredJob
from .runs import ChannelSummary


class ErrorDetail(Model):
    code: str
    message: str
    fields: Opt[dict[str, str]] = None


class ApiErrorBody(Model):
    error: ErrorDetail


class NavCounts(Model):
    jobs: int
    review: int
    demo_mode: bool


class FoundCounts(Model):
    total: int
    board: int
    inbox: int
    watchlist: int


class ShortlistMeta(Model):
    found: FoundCounts
    channels: list[ChannelSummary]
    new_job_ids: list[str]
    hidden: list[ScoredJob]
    snoozed: int
    rejected: int
    active_run_ids: list[str]


class SiteOption(Model):
    domain: str
    name: str
    reviewed: bool


class JobList(Model):
    items: list[ScoredJob]
    next: None = None


class ApplicationList(Model):
    items: list[ApplicationListItem]


class TestResult(Model):
    ok: bool
    code: Opt[str] = None
    message: str


class CvVersion(Model):
    version: int


# ---- Request bodies ----


class DecisionIn(Model):
    action: Literal["approve", "snooze", "reject", "unhide"]
    days: Opt[Literal[1, 3, 7]] = None
    reason: Opt[Annotated[str, Field(max_length=200)]] = None
    force: Opt[bool] = None


class ApplicationPatchIn(Model):
    status: Opt[ApplicationStatus] = None
    notes: Opt[Annotated[str, Field(max_length=4000)]] = None
    follow_up_at: str | None = None  # explicit null clears it; absent leaves it alone


class DocumentPatchIn(Model):
    target: Annotated[str, required("Which part?")]
    text: Opt[Annotated[str, Field(max_length=4000)]] = None
    bullet_id: Opt[str] = None


class ImportUrlIn(Model):
    url: Annotated[str, required("Paste a link")]


class ImportTextIn(Model):
    text: Annotated[str, check(lambda v: len(v) >= 20, "too_short", "Paste the full description")]


class RegenerateIn(Model):
    note: str = ""


class CvImportIn(Model):
    yaml: str = ""


class RunIn(Model):
    channels: Opt[list[str]] = None
