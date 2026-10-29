"""Request bodies and composite responses for the agent's HTTP API (SPEC §9.1)."""

from typing import Annotated, Literal

from pydantic import Field

from .applications import ApplicationListItem, ApplicationStatus
from .base import Model, Opt, check, required
from .jobs import Job, ScoredJob
from .profile import SearchProfile
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
    # Plain `Job`, not `ScoredJob`: MATCH-01 hides these before any model call, so a hidden job
    # has a reason and never a fit. A sample, capped by `Store.HIDDEN_SHOWN` — `hidden_total` is
    # the real figure.
    hidden: list[Job]
    hidden_total: int
    # New jobs with no fit report yet: past the day's scoring cap (MATCH-05), or left unscored
    # by a run that stopped. They are not on the shortlist, and saying so beats their absence.
    awaiting_score: int
    snoozed: int
    rejected: int
    active_run_ids: list[str]


class SiteOption(Model):
    domain: str
    name: str
    reviewed: bool
    # SRCH-11: where this board's jobs are, so the profile screen can offer countries and
    # regions rather than asking someone to know which domain covers Amsterdam. Empty
    # `countries` means the board makes no geographic claim and is searched for anywhere.
    countries: list[str] = []
    region: str = "global"
    # SRCH-12: a board WERA proposed and nobody has decided on yet. `proposed` is derived rather
    # than stored, so the screen and the file can't disagree about what is waiting.
    proposed: bool = False
    declined: bool = False
    discovered_on: Opt[str] = None
    matched: Opt[str] = None  # the target title whose search turned this board up
    robots_ok: Opt[bool] = None  # None means "couldn't check", which the card says out loud
    examples: list[str] = []  # real postings to look at before deciding


class SiteReviewIn(Model):
    """SRCH-04: a recorded human decision about a board WERA proposed.

    `approve` means the person read the site's terms and robots.txt. Nothing in the code can
    assert that on their behalf, which is why this is an explicit action rather than a flag.
    """

    action: Literal["approve", "decline"]


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


class ListDiff(Model):
    """What a regenerated profile would do to one list field (PRF-04)."""

    added: list[str]
    removed: list[str]
    kept: list[str]


class ScalarDiff(Model):
    from_: str = Field(alias="from")
    to: str


class ProfileProposalResult(Model):
    """A proposed profile and what it would change. Nothing is saved until the person PUTs it."""

    proposed: SearchProfile
    diff: dict[str, ListDiff | ScalarDiff]
    cv_version: int


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


class CvParseIn(Model):
    """Text pulled out of an uploaded CV (CV-03).

    The web app does the PDF and DOCX extraction, because that is where the libraries are, and
    sends plain text. So this route never handles a file, and a malformed document fails in the
    browser's own process rather than in the agent.
    """

    text: str = ""


class RunIn(Model):
    channels: Opt[list[str]] = None
