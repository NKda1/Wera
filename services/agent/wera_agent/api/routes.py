"""HTTP routes (SPEC §9.1). The web app's route handlers forward to these one to one; the
browser never calls this service directly."""

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status

from ..db.session import transaction
from ..errors import ApiError
from ..graphs.application import advance
from ..graphs.intake import run_intake, run_rescore
from ..llm.gateway import flush_unrecorded
from ..schemas.api import (
    ApplicationList,
    ApplicationPatchIn,
    CvFileSaved,
    CvImportIn,
    CvParseIn,
    CvVersion,
    DecisionIn,
    DocumentPatchIn,
    ImportTextIn,
    ImportUrlIn,
    JobList,
    JobsCleared,
    NavCounts,
    PlaceOption,
    PreviewRead,
    ProfileProposalResult,
    RegenerateIn,
    RunIn,
    ShortlistMeta,
    SiteOption,
    SiteReviewIn,
    Started,
    TestResult,
)
from ..schemas.applications import ApplicationWithJob
from ..schemas.cv import Contact, CvLine, MasterCV
from ..schemas.cv_file import CvFile, CvFileIn, CvPreview
from ..schemas.cv_profile import CvProfile
from ..schemas.jobs import Job, ScoredJob
from ..schemas.learning import SuggestionList
from ..schemas.pack import AtsCompare, WritingReport
from ..schemas.profile import SavedProfile, SearchProfile
from ..schemas.runs import Estimate, IntakeRun, RunIds
from ..schemas.settings import SettingsPatch, SettingsResponse
from ..schemas.strategy import TargetMatrix
from ..schemas.upskills import UpskillsReport, UpskillsVersion
from ..services.store import Store

# Aliased: this module already has a `get_settings` *route*, which would shadow it.
from ..settings import get_settings as environment
from ..tasks import spawn

router = APIRouter()

CHANNELS = ["board_search", "email_alert", "watchlist"]
# What `GET /jobs` can rank. `hidden` is deliberately absent: MATCH-01 hides a job before any
# model call, so it has no fit report and no place in a list sorted by score. Hidden jobs are
# served by `GET /jobs/meta`, with their reason, which is what the Jobs screen reads.
STATUSES = ["new", "shortlisted", "in_application", "snoozed", "rejected", "closed", "archived"]


def store() -> Iterator[Store]:
    deferred: list[tuple[Any, str]] = []
    try:
        with transaction() as session:
            s = Store(session)
            deferred = s.deferred
            yield s
    finally:
        # Runs even when the request failed, because a model call that failed still cost money
        # and its row was rolled back with the rest of the work (see gateway._record).
        flush_unrecorded()
    # Committed by now, so background work sees the decision it is acting on and doesn't
    # contend with this request for the write lock (see wera_agent/tasks.py).
    for make, name in deferred:
        spawn(make(), name=name)


# `scope="function"`: the transaction commits **before** the response is sent. FastAPI's default
# for a dependency with `yield` is the other way round — the response goes out and the commit
# follows — so a screen that refreshed on the 200 could read the state from before its own
# change (seen live: "Clear jobs" answered, and a read straight after still counted every job).
S = Annotated[Store, Depends(store, scope="function")]


# ---- shell ----


@router.get("/nav")
def nav(s: S) -> NavCounts:
    return s.nav_counts()


@router.get("/sites")
def list_sites(s: S) -> list[SiteOption]:
    return s.sites()


@router.get("/places")
def list_places(s: S) -> list[PlaceOption]:
    """SRCH-11: the countries the profile can target, with what searches each. Free."""
    return s.places()


@router.post("/sites/{domain}/review")
def review_site(s: S, domain: str, body: SiteReviewIn) -> list[SiteOption]:
    """SRCH-04/12: record that a person decided about a board WERA proposed.

    A `POST .../review` rather than a `PATCH` on the site, following `POST /jobs/{id}/decision`:
    this is a decision with a meaning attached — "I have read this site's terms and robots.txt" —
    not a generic config edit that happens to be reachable over HTTP.
    """
    return s.review_site(domain, approve=body.action == "approve")


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
    possibly_closed: Annotated[str | None, Query(alias="possiblyClosed")] = None,
) -> JobList:
    """`possiblyClosed`: absent for the main list, which leaves those jobs out (LIFE-03); `only`
    for just them; `all` for both."""
    if status_ == "hidden":
        raise ApiError(400, "VALIDATION", "Hidden jobs have no score to rank. Read them from /jobs/meta instead.")
    st = status_ if status_ in STATUSES else "new"
    which = {"only": True, "all": None}.get(possibly_closed or "", False)
    filters = {"min_score": min_score, "channel": channel, "location": location, "remote": remote}
    return JobList(
        items=s.list_jobs(st, "date" if sort == "date" else "score", **filters, possibly_closed=which),
        possibly_closed=len(s.list_jobs(st, "score", **filters, possibly_closed=True)) if which is False else 0,
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


@router.get("/jobs/clear")
def clear_preview(s: S) -> JobsCleared:
    """SRCH-27: what "Clear jobs" would remove, for the confirm to name. Gated like the clear."""
    return s.clear_preview()


@router.post("/jobs/clear")
def clear_jobs(s: S) -> JobsCleared:
    """SRCH-27: delete every job, application and pack, so the next search starts from nothing.

    A testing control (`allow_clear_jobs`). The CV, profile, settings and both spend ledgers stay.
    """
    return s.clear_jobs()


@router.get("/jobs/{job_id}")
def get_job(s: S, job_id: str) -> ScoredJob:
    return s.get_job(job_id)


@router.post("/jobs/{job_id}/summary")
def summarise_job(s: S, job_id: str) -> ScoredJob:
    """The post in a minute's reading, written on click and kept until the post changes."""
    return s.summarise_job(job_id)


@router.post("/jobs/{job_id}/read")
def read_preview(s: S, job_id: str) -> PreviewRead:
    """21 Nov: "Read the full ad" on a preview — one lookup for the employer's own posting, the
    read ladder on it, and a score, which counts towards the day's scoring limit (MATCH-05). With
    no original it is scored on the job API's summary, rough-only (MATCH-04). Never requests the
    job API's tracked link (SRCH-32)."""
    return s.read_preview(job_id)


@router.post("/jobs/{job_id}/decision")
def decide(s: S, job_id: str, body: DecisionIn) -> ScoredJob | Job:
    """Gate 1: approve, snooze (1, 3 or 7 days) or reject (G1-02). Every decision is logged (MATCH-06).

    The decision is saved before the response goes out; the pack is drafted after it, by the
    application graph, so the person isn't left waiting on a model call.
    """
    if body.action == "approve" and not environment().wera_demo_mode and s.get_job_status(job_id) != "preview":
        # LIFE-05: a pack is model calls and the person's time — make sure the job is still there
        # first. Free (a feed or one page), and only a definite answer stops it: "gone" closes the
        # job and `decide` then refuses with JOB_CLOSED; anything less goes ahead. A preview
        # (21 Nov) is read in full by `decide` instead, and its only link may be a job API's
        # tracked one, which is never requested (SRCH-32).
        from ..graphs import recheck

        if recheck.check_one(job_id, force=True) == "gone":
            s.s.expire_all()
    job = s.decide(job_id, body)
    if body.action != "unhide":
        resume = {"action": body.action, "applicationId": s.started_application_id, "note": body.reason}
        s.defer(lambda: advance(job_id, resume), name=f"gate1:{job_id}")
    return job


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
    job_id = s.application_job_id(app_id)
    out = s.patch_application(app_id, body)
    if body.status == "discarded":
        # Gate 2, the third option: the thread ends rather than being left waiting.
        s.defer(lambda: advance(job_id, {"action": "discard"}), name=f"gate2:{job_id}")
    return out


@router.post("/applications/{app_id}/regenerate")
def regenerate(s: S, app_id: str, body: RegenerateIn) -> ApplicationWithJob:
    """Gate 2 "Request changes": the note goes to the writer and the pack is drafted again."""
    note = body.note[:1000]
    job_id = s.application_job_id(app_id)
    out = s.regenerate(app_id, note)
    s.defer(lambda: advance(job_id, {"action": "request_changes", "note": note}), name=f"gate2:{job_id}")
    return out


@router.post("/applications/{app_id}/approve")
def approve(s: S, app_id: str) -> ApplicationWithJob:
    """Gate 2 approval. Only a person can move an application to approved (§7.2)."""
    job_id = s.application_job_id(app_id)
    out = s.approve_pack(app_id)
    s.defer(lambda: advance(job_id, {"action": "approve"}), name=f"gate2:{job_id}")
    return out


@router.patch("/documents/{doc_id}")
def edit_document(s: S, doc_id: str, body: DocumentPatchIn) -> ApplicationWithJob:
    """G2-02: in-place edits are saved as a new version, marked "edited by you", and not re-guarded."""
    return s.edit_document(doc_id, body)


@router.post("/applications/{app_id}/company-brief")
def company_brief(s: S, app_id: str) -> ApplicationWithJob:
    """Learn about the company: its post and its own About page, each item quoted and checked."""
    return s.company_brief(app_id)


@router.post("/applications/{app_id}/letter/rewrite")
def rewrite_letter(s: S, app_id: str) -> ApplicationWithJob:
    """Redraft the letter alone, with the company brief. The review screen polls until it's back."""
    return s.rewrite_letter(app_id)


@router.get("/applications/{app_id}/compare")
def compare_application(s: S, app_id: str) -> AtsCompare:
    """ATSC-08 (20 Nov): the job ad beside the CV, each of the post's keywords highlighted where it
    is — found by the ATS report's own matcher, on the text the report measures. Read only."""
    from ..domain.vocab import master_cv_text
    from ..nodes.ats import compare

    pack, job, _, text = s.ats_view(app_id)
    return compare(job, pack.ats.keywords, text, master=master_cv_text(s.cv()))


@router.get("/applications/{app_id}/writing")
def writing_checks(s: S, app_id: str) -> WritingReport:
    """ATSC-09 (20 Nov): action verbs, numbers, summary length and skills count. Suggestions only;
    the "add a figure" prompts are `cv.profile`'s, read from its cache, so no model is called."""
    from ..domain.writing import writing_report

    _, _, doc, _ = s.ats_view(app_id)
    implied = {a.bullet_id: a.phrase for role in s.cv_profile_cached().professional_history for a in role.implied_achievements}
    return writing_report(doc, s.cv(), implied=implied)


# ---- the CV file (EXP-01, 18 Nov) ----
#
# The CV PDF is rendered here, by RenderCV, from the pack's CV file; the web app renders the
# cover letter and both Word files, and holds the approval gate for every download (G2-03).


@router.get("/applications/{app_id}/cv-file")
def get_cv_file(s: S, app_id: str) -> CvFile:
    """The RenderCV file the CV PDF is made from, with what it prints. No contact details."""
    return s.cv_file(app_id)


@router.put("/applications/{app_id}/cv-file")
def put_cv_file(s: S, app_id: str, body: CvFileIn) -> CvFileSaved:
    """Save the person's file (or reset it to WERA's); the ATS report is measured against it."""
    file, application = s.save_cv_file(app_id, body)
    return CvFileSaved(file=file, application=application)


@router.post("/applications/{app_id}/cv-file/preview")
def preview_cv_file(s: S, app_id: str, body: CvFileIn) -> CvPreview:
    """The file as page images, for the editor. Saves nothing."""
    return s.preview_cv_file(app_id, body)


@router.get("/applications/{app_id}/cv.pdf")
def cv_pdf(s: S, app_id: str) -> Response:
    """The CV PDF with contact details (EXP-02). Only the web app calls this, and it gates and
    lints the file before a person receives it."""
    return Response(s.cv_pdf(app_id), media_type="application/pdf", headers={"cache-control": "no-store"})


# ---- intake ----


@router.get("/intake/estimate")
def estimate(s: S, channels: str | None = None) -> Estimate:
    chosen = [c for c in channels.split(",") if c in CHANNELS] if channels else CHANNELS
    return s.estimate(chosen)


def _hand_person(request: Request, run_ids: list[str]) -> None:
    """SRCH-32: who started this search, for Careerjet, handed to the run through Redis (an hour).
    Only what the web proxy forwards; without it Careerjet is skipped and says why."""
    from ..sources import feeds, targets

    ip, agent = request.headers.get("x-wera-person-ip"), request.headers.get("x-wera-person-ua")
    if not ip or not agent or environment().wera_demo_mode:
        return
    try:
        client = targets.connect()
    except ApiError:
        return
    for run_id in run_ids:
        feeds.remember_person(client, run_id, feeds.Person(ip=ip, user_agent=agent))


@router.post("/intake/run")
def start_run(s: S, body: RunIn, request: Request) -> RunIds:
    """UX-01: the run's rows exist before the response, so the UI can poll immediately; the
    fetching happens afterwards, outside this request's transaction."""
    chosen = [c for c in (body.channels if body.channels is not None else CHANNELS) if c in CHANNELS]
    if not chosen:
        raise ApiError(422, "VALIDATION", "Choose at least one channel.")
    ids = s.start_runs(chosen)
    if "board_search" in ids:
        _hand_person(request, [ids["board_search"]])
    if not environment().wera_demo_mode:
        # DEMO-02: demo runs are simulated against the fixtures and make no outbound request.
        s.defer(lambda: run_intake(chosen, ids), name=f"intake:{','.join(chosen)}")
    return RunIds(run_ids=list(ids.values()))


@router.get("/upskills")
def get_upskills(s: S, days: int = Query(60, ge=7, le=365), family: str | None = Query(None, max_length=32)) -> UpskillsReport:
    """UPSK-01: the skills the person's jobs ask for that their CV doesn't show. No model call."""
    from ..services import upskills
    from ..services.store import get_kv

    field = (get_kv(s.s, "target_matrix") or {}).get("field")
    # UPSK-07 (21 Nov): the target titles say the person's own role family; `family` filters to one.
    titles = s.get_profile().titles
    cv = s.cv()
    out = upskills.report(s.s, cv, now=s.now, days=days, field=field, titles=titles, family=family or None)
    out.version = upskills.version(s.s, cv, titles=titles)
    return out


@router.get("/upskills/version")
def get_upskills_version(s: S) -> UpskillsVersion:
    """22 Nov: what the report would be built from, cheaply — the Upskills page polls it and
    refreshes when it moves (a search's scores, a near miss read after it, a decision)."""
    from ..services import upskills

    return UpskillsVersion(version=upskills.version(s.s, s.cv(), titles=s.get_profile().titles))


@router.get("/learning/suggestions")
def learning_suggestions(s: S) -> SuggestionList:
    """MATCH-13: profile changes the person's Gate 1 decisions point at. Nothing is applied."""
    return s.learning_suggestions()


# `:path` (22 Nov): a suggested title can carry a slash ("Equity Analyst/in …"), and so can its id;
# the browser encodes it, the server decodes it before routing, and a plain segment then 404'd.
@router.post("/learning/suggestions/{suggestion_id:path}/accept")
def accept_suggestion(s: S, suggestion_id: str) -> SearchProfile:
    return s.accept_suggestion(suggestion_id)


@router.post("/learning/suggestions/{suggestion_id:path}/dismiss")
def dismiss_suggestion(s: S, suggestion_id: str) -> SuggestionList:
    return s.dismiss_suggestion(suggestion_id)


@router.post("/jobs/seen")
def mark_all_seen(s: S) -> dict[str, int]:
    """SRCH-25: "Mark all seen" — every job tagged New loses the tag."""
    return {"marked": s.mark_all_seen()}


@router.post("/jobs/{job_id}/seen")
def mark_seen(s: S, job_id: str) -> dict[str, bool]:
    """SRCH-25: the person opened this job's details, so it is no longer new.

    A body rather than a 204: the web proxy passes responses through as text, and a 204 with a
    body — even an empty string — is a response `fetch` refuses to construct.
    """
    s.mark_seen(job_id)
    # LIFE-05: the person is looking at it, so find out whether it is still there — in the
    # background, after the response, and only if nobody has asked in the last day.
    if not environment().wera_demo_mode:
        from ..graphs import recheck

        s.defer(lambda: recheck.check_one_async(job_id), name=f"check:{job_id}")
    return {"seen": True}


@router.post("/jobs/recheck")
def recheck_jobs(s: S) -> Started:
    """SRCH-13: ask whether the shortlist's postings still exist, now rather than at 07:00.

    Spends no Firecrawl credits and makes no model calls — employers' own feeds, plain HTTP, and
    a headless browser for the pages that render their closure client-side. So there is no budget
    to check and no reason to make anyone wait for it: it runs in the background and the Jobs
    screen shows what the last sweep found.

    `force`, because a person pressing this has decided it is worth doing whatever the
    twenty-hour rule thinks.
    """
    from ..graphs import recheck

    if environment().wera_demo_mode:
        # DEMO-02: the demo makes no outbound request, so there is nothing honest to check.
        raise ApiError(409, "DEMO_MODE", "Demo mode makes no outbound requests, so there's nothing to check.")
    s.defer(lambda: recheck.run_async(force=True), name="recheck:manual")
    return Started()


@router.post("/intake/score-more")
def score_more(s: S, request: Request) -> RunIds:
    """MATCH-05: call up the next batch from the broad search's pool.

    No channels and no fetching — the jobs are already stored, they just have no score yet. Like
    `/intake/run`, the row exists before the response so the screen can poll it immediately.
    """
    ids = s.start_score_more()
    run_id = ids.run_ids[0]
    _hand_person(request, [run_id])
    if not environment().wera_demo_mode:
        # DEMO-02: a demo run is animated by `settle()` against the fixtures instead.
        s.defer(lambda: run_rescore(run_id, "score_more"), name=f"score_more:{run_id}")
    return ids


@router.post("/intake/rescore")
def rescore_board(s: S, scope: str = "board") -> RunIds:
    """CV-08 (18 Nov): re-score the board's stale scores, offered once the review list is done."""
    ids = s.start_rescore("review" if scope == "review" else "board")
    run_id, chosen = ids.run_ids[0], "review" if scope == "review" else "board"
    if not environment().wera_demo_mode:
        s.defer(lambda: run_rescore(run_id, "rescore", scope=chosen), name=f"rescore:{run_id}")
    return ids


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
def put_cv(s: S, body: MasterCV, replace: bool = False, fresh: bool | None = None) -> CvVersion:
    """An edit by default, which never deletes anything. `replace` is a new CV from an upload or
    import, where `fresh` chooses between keeping the jobs (re-scored) and starting over (18 Nov)."""
    version = s.put_cv(body, replace=replace, fresh=fresh)
    return CvVersion(version=version, cleared_jobs=s.cleared_jobs)


@router.get("/cv/lines")
def cv_lines(s: S) -> dict[str, CvLine]:
    return s.cv_lines()


@router.get("/cv/profile")
def cv_profile(s: S) -> CvProfile:
    """The candidate rubric for the current CV. The first read after a save makes one model call."""
    return s.cv_profile()


@router.get("/strategy")
def get_strategy(s: S) -> TargetMatrix | None:
    """The Strategist's current matrix, or null when there is none for this CV and profile. Free."""
    return s.strategy()


@router.post("/strategy")
def refresh_strategy(s: S) -> TargetMatrix:
    """Run the Strategist: one scoring-tier call, plus a robots.txt fetch per board it proposes."""
    return s.refresh_strategy()


@router.post("/cv/import")
def import_cv(s: S, body: CvImportIn) -> MasterCV:
    """Returns a proposed CV; nothing is saved until PUT /cv (CV-03)."""
    if not body.yaml.strip():
        raise ApiError(422, "VALIDATION", "Paste or choose a YAML file.")
    return s.import_cv_yaml(body.yaml)


@router.post("/cv/parse")
def parse_cv(s: S, body: CvParseIn) -> MasterCV:
    """CV-03: a CV read out of an uploaded PDF or DOCX. Returns a proposal only (CV-03)."""
    if not body.text.strip():
        raise ApiError(422, "VALIDATION", "Choose a file, or paste your CV's text.")
    return s.parse_cv_text(body.text)


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
def put_profile(s: S, body: SearchProfile) -> SavedProfile:
    saved = s.put_profile(body)
    return SavedProfile(**saved.model_dump(), refiltered=s.where_change)


@router.post("/profile/generate")
def generate_profile(s: S) -> ProfileProposalResult:
    """PRF-01, PRF-04: propose a profile from the CV and show the diff. Saves nothing."""
    return s.generate_profile()


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
