"""Applications (SPEC §6.7, §7.2)."""

from typing import Literal

from .base import Model, Opt
from .jobs import Location, RemoteMode
from .pack import Pack

type ApplicationStatus = Literal[
    "drafting",
    "ready_for_review",
    "changes_requested",
    "approved",
    "submitted",
    "interview",
    "offer",
    "rejected",
    "discarded",
    "withdrawn",
]
# The four steps `graphs/application.py` actually runs. `answers` was missing here while the
# graph marked it anyway, and `Store._draft` maps an unrecognised step back to the first one — so
# the progress indicator jumped from "ATS check" to "Tailoring CV" on the last step of every pack.
type DraftStep = Literal["cv", "letter", "ats", "answers"]


class Application(Model):
    id: str
    job_id: str
    status: ApplicationStatus
    created_at: str
    draft_started_at: Opt[str] = None
    change_note: Opt[str] = None
    pack: Opt[Pack] = None
    approved_at: Opt[str] = None
    submitted_at: Opt[str] = None
    follow_up_at: Opt[str] = None
    notes: Opt[str] = None


class ApplicationJob(Model):
    id: str
    title: str
    company: str
    location: Location
    remote: RemoteMode | None
    apply_url: Opt[str] = None
    linkedin_url: Opt[str] = None
    fit_total: float


class DraftProgress(Model):
    step: DraftStep
    progress: float


class ApplicationWithJob(Application):
    job: ApplicationJob
    draft: Opt[DraftProgress] = None


class ApplicationListItem(ApplicationWithJob):
    coverage: Opt[float] = None
