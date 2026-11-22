"""Applications (SPEC §6.7, §7.2)."""

from typing import Literal

from pydantic import computed_field

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
    # The employer's own careers site, when WERA knows the system it hires through (17 Nov): the
    # company itself, beside `apply_url`, which can be a board that carried the post.
    careers_url: Opt[str] = None
    fit_total: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def employer_named(self) -> bool:
        """Whether `company` is the employer or only the board or agency that posted the job
        (22 Nov; `Job.employer_named`). The review screen, the tracker and the letter's recipient
        line say "Employer not named" rather than write to eFinancialCareers as if it were hiring."""
        from ..domain.dedupe import is_intermediary  # dedupe imports the job schemas

        return not is_intermediary(self.company)


class DraftProgress(Model):
    step: DraftStep
    progress: float


class ApplicationWithJob(Application):
    job: ApplicationJob
    draft: Opt[DraftProgress] = None


class ApplicationListItem(ApplicationWithJob):
    coverage: Opt[float] = None
