"""HTTP routes (SPEC §9.1). The web app's route handlers forward to these one to one; the
browser never calls this service directly."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status

from ..db.session import transaction
from ..errors import ApiError
from ..schemas.api import (
    ApplicationList,
    ApplicationPatchIn,
    CvImportIn,
    CvVersion,
    DecisionIn,
    DocumentPatchIn,
    ImportTextIn,
    ImportUrlIn,
    JobList,
    NavCounts,
    RegenerateIn,
    RunIn,
    ShortlistMeta,
    SiteOption,
    TestResult,
)
from ..schemas.applications import ApplicationWithJob
from ..schemas.cv import Contact, CvLine, MasterCV
from ..schemas.jobs import ScoredJob
from ..schemas.profile import SearchProfile
from ..schemas.runs import Estimate, IntakeRun, RunIds
from ..schemas.settings import SettingsPatch, SettingsResponse
from ..services.store import Store

router = APIRouter()

CHANNELS = ["board_search", "email_alert", "watchlist"]
STATUSES = ["new", "shortlisted", "in_application", "snoozed", "rejected", "hidden", "closed", "archived"]


def store() -> Iterator[Store]:
    with transaction() as session:
        yield Store(session)


S = Annotated[Store, Depends(store)]


# ---- shell ----


@router.get("/nav")
def nav(s: S) -> NavCounts:
    return s.nav_counts()


@router.get("/sites")
def list_sites(s: S) -> list[SiteOption]:
    return s.sites()


# ---- jobs and Gate 1 ----


@router.get("/jobs")
def list_jobs(
    s: S,
    status_: Annotated[str, Query(alias="status")] = "new",
    sort: str = "score",
    min_score: Annotated[float, Query(alias="minScore")] = 0,
    channel: str | None = None,
    location: str | None = None,
    remote: str | None = None,
) -> JobList:
    st = status_ if status_ in STATUSES else "new"
    return JobList(
        items=s.list_jobs(
            st, "date" if sort == "date" else "score", min_score=min_score, channel=channel, location=location, remote=remote
        )
    )


@router.get("/jobs/meta")
def shortlist_meta(s: S) -> ShortlistMeta:
    return s.shortlist_meta()


@router.post("/jobs/import", status_code=status.HTTP_201_CREATED)
def import_job(s: S, body: Annotated[ImportUrlIn | ImportTextIn, Body()]) -> ScoredJob:
    """ATS-04: a URL or a pasted description. LinkedIn URLs are refused without any request."""
    if isinstance(body, ImportUrlIn):
        return s.import_job(url=body.url)
    return s.import_job(text=body.text)


@router.get("/jobs/{job_id}")
def get_job(s: S, job_id: str) -> ScoredJob:
    return s.get_job(job_id)


@router.post("/jobs/{job_id}/decision")
def decide(s: S, job_id: str, body: DecisionIn) -> ScoredJob:
    """Gate 1: approve, snooze (1, 3 or 7 days) or reject (G1-02). Every decision is logged (MATCH-06)."""
    return s.decide(job_id, body)


# ---- applications and Gate 2 ----


@router.get("/applications")
def list_applications(s: S) -> ApplicationList:
    return ApplicationList(items=s.list_applications())


@router.get("/applications/{app_id}")
def get_application(s: S, app_id: str) -> ApplicationWithJob:
    return s.get_application(app_id)


@router.patch("/applications/{app_id}")
def patch_application(s: S, app_id: str, body: ApplicationPatchIn) -> ApplicationWithJob:
    """Tracker updates: status (only §7.2 transitions), notes and follow-up date."""
    return s.patch_application(app_id, body)


@router.post("/applications/{app_id}/regenerate")
def regenerate(s: S, app_id: str, body: RegenerateIn) -> ApplicationWithJob:
    """Gate 2 "Request changes": the note goes to the writer and the pack is drafted again."""
    return s.regenerate(app_id, body.note[:1000])


@router.post("/applications/{app_id}/approve")
def approve(s: S, app_id: str) -> ApplicationWithJob:
    """Gate 2 approval. Only a person can move an application to approved (§7.2)."""
    return s.approve_pack(app_id)


@router.patch("/documents/{doc_id}")
def edit_document(s: S, doc_id: str, body: DocumentPatchIn) -> ApplicationWithJob:
    """G2-02: in-place edits are saved as a new version, marked "edited by you", and not re-guarded."""
    return s.edit_document(doc_id, body)


# Exports have no agent route: the web app renders PDF and DOCX from the pack, the master CV
# and the contact details it fetches here (ARCHITECTURE §4, EXP-02).


# ---- intake ----


@router.get("/intake/estimate")
def estimate(s: S, channels: str | None = None) -> Estimate:
    chosen = [c for c in channels.split(",") if c in CHANNELS] if channels else CHANNELS
    return s.estimate(chosen)


@router.post("/intake/run")
def start_run(s: S, body: RunIn) -> RunIds:
    chosen = [c for c in (body.channels if body.channels is not None else CHANNELS) if c in CHANNELS]
    if not chosen:
        raise ApiError(422, "VALIDATION", "Choose at least one channel.")
    return s.start_runs(chosen)


@router.get("/intake/runs/{run_id}")
def get_run(s: S, run_id: str) -> IntakeRun:
    return s.get_run(run_id)


@router.post("/intake/runs/{run_id}/cancel")
def cancel_run(s: S, run_id: str) -> IntakeRun:
    return s.cancel_run(run_id)


# ---- CV and profile ----


@router.get("/cv")
def get_cv(s: S) -> MasterCV:
    """The master CV without contact details (those are fetched separately; CV-06)."""
    return s.cv()


@router.put("/cv")
def put_cv(s: S, body: MasterCV) -> CvVersion:
    return CvVersion(version=s.put_cv(body))


@router.get("/cv/lines")
def cv_lines(s: S) -> dict[str, CvLine]:
    return s.cv_lines()


@router.post("/cv/import")
def import_cv(s: S, body: CvImportIn) -> MasterCV:
    """Returns a proposed CV; nothing is saved until PUT /cv (CV-03)."""
    if not body.yaml.strip():
        raise ApiError(422, "VALIDATION", "Paste or choose a YAML file.")
    return s.import_cv_yaml(body.yaml)


@router.get("/cv/contact")
def get_contact(s: S) -> Contact:
    return s.get_contact()


@router.put("/cv/contact")
def put_contact(s: S, body: Contact) -> Contact:
    return s.put_contact(body)


@router.get("/profile")
def get_profile(s: S) -> SearchProfile:
    return s.get_profile()


@router.put("/profile")
def put_profile(s: S, body: SearchProfile) -> SearchProfile:
    return s.put_profile(body)


# ---- settings ----


@router.get("/settings")
def get_settings(s: S) -> SettingsResponse:
    """Non-secret settings plus key status (set / missing). Key values never leave (SET-01, SET-07)."""
    return s.get_settings()


@router.put("/settings")
def put_settings(s: S, body: SettingsPatch) -> SettingsResponse:
    return s.put_settings(body)


@router.post("/settings/test/{provider}")
def test_provider(s: S, provider: str) -> TestResult:
    return s.test_provider(provider)
