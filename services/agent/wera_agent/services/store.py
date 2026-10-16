"""WERA's data and workflow behind the SPEC §9.1 routes: state machines (§7), Gate 1 and
Gate 2, intake runs, documents and settings, on the database.

Ported from the web app's Phase 1 demo store. Drafts and demo intake runs still advance
with time (no background workers yet); the LangGraph application graph replaces drafting
in a later milestone, and real intake replaces the demo runs.
"""

import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import logfire
import yaml
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import iso, now_ms, parse_ms
from ..config import sites
from ..db.models import (
    ApplicationRow,
    ContactRow,
    DecisionRow,
    DocumentRow,
    FitReportRow,
    IntakeRunRow,
    JobRow,
    KvRow,
    MasterCvRow,
    ProfileRow,
    SettingsRow,
)
from ..domain.applications import ALLOWED
from ..domain.scoring import build_fit_report, js_round
from ..domain.text import is_linkedin_url, title_case
from ..domain.vocab import cv_line_index, master_cv_text, text_has_term
from ..errors import ApiError, validation_error
from ..llm.demo import PackInput, generate_pack, guard_cv, refresh_ats
from ..schemas.api import (
    ApplicationPatchIn,
    DecisionIn,
    DocumentPatchIn,
    FoundCounts,
    NavCounts,
    ShortlistMeta,
    SiteOption,
    TestResult,
)
from ..schemas.applications import Application, ApplicationJob, ApplicationListItem, ApplicationWithJob, DraftProgress
from ..schemas.cv import Contact, CvLine, MasterCV
from ..schemas.jobs import FitEvidence, FitWeights, Job, ScoredJob, SubScore
from ..schemas.pack import AnswerItem, AtsReport, Cited, CoverLetter, CvItem, CvSection, Pack, TailoredCV
from ..schemas.profile import SearchProfile
from ..schemas.runs import ChannelSummary, Estimate, IntakeRun, RunError, RunIds
from ..schemas.settings import SecretsStatus, SettingsPatch, SettingsResponse
from ..settings import get_settings, llm_provider_name
from .demo_data import DAY, MIN, first_run_seeds, seed_to_job

# Pack drafting steps (PERF-03), the same timings as the web app's progress UI.
DRAFT_STEPS = [("cv", 2500), ("letter", 4500), ("ats", 6000)]
DRAFT_TOTAL_MS = DRAFT_STEPS[-1][1]
RUN_MS = {"board_search": 6000, "email_alert": 3500, "watchlist": 2500}
PACK_KINDS = ("cv", "letter", "ats", "answers")

# The two gates are what WERA promises, so they are counted as well as logged. Ids only:
# which jobs a person looks at is theirs (SPEC SEC-05).
GATE1_DECISIONS = logfire.metric_counter("wera.gate1.decisions", unit="1", description="Gate 1 decisions, by action")
GATE2_APPROVALS = logfire.metric_counter("wera.gate2.approvals", unit="1", description="Packs a person approved at Gate 2")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Row helpers


def get_kv(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(KvRow, key)
    return default if row is None else row.value


def set_kv(session: Session, key: str, value: Any) -> None:
    row = session.get(KvRow, key)
    if row is None:
        session.add(KvRow(key=key, value=value))
    else:
        row.value = value


def save_job(
    session: Session,
    job: Job,
    evidence: FitEvidence | None = None,
    questions: list[str] | None = None,
    *,
    created_at: str | None = None,
) -> None:
    row = session.get(JobRow, job.id)
    if row is None:
        row = JobRow(id=job.id, questions=questions or [])
        session.add(row)
    row.dedupe_key = job.dedupe_key
    row.status = job.status
    row.title = job.title
    row.company = job.company
    row.posted_at = job.posted_at
    row.first_seen = job.first_seen
    row.snoozed_until = job.snoozed_until
    row.data = job.model_dump(mode="json")
    if questions is not None:
        row.questions = questions
    if evidence is not None:
        fit = session.get(FitReportRow, job.id)
        data = evidence.model_dump(mode="json")
        if fit is None:
            session.add(FitReportRow(job_id=job.id, evidence=data, model=evidence.model, created_at=created_at or iso(now_ms())))
        else:
            fit.evidence, fit.model = data, evidence.model


def _local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000).astimezone()


def _hhmm(at: str) -> str:
    return _local(parse_ms(at)).strftime("%H:%M")


def _day_month(at: str) -> str:
    d = _local(parse_ms(at))
    return f"{d.day} {MONTHS[d.month - 1]}"


def _human(status: str) -> str:
    return status.replace("_", " ")


class Store:
    """One unit of work on the database. Methods mirror the web app's API routes."""

    def __init__(self, session: Session, now: int | None = None):
        self.s = session
        self.now = now if now is not None else now_ms()
        self._cv_cache: MasterCV | None = None

    # ---- loading ----

    def cv(self) -> MasterCV:
        if self._cv_cache is None:
            row = self.s.scalars(select(MasterCvRow).order_by(MasterCvRow.version.desc()).limit(1)).first()
            if row is None:
                raise ApiError(503, "NOT_SEEDED", "WERA has no CV yet. Run `wera reset` to set up the database.")
            self._cv_cache = MasterCV.model_validate(row.data)
        return self._cv_cache

    def _settings(self) -> dict[str, Any]:
        return self.s.get(SettingsRow, 1).data  # type: ignore[union-attr]

    def _save_settings(self, data: dict[str, Any]) -> None:
        self.s.get(SettingsRow, 1).data = data  # type: ignore[union-attr]

    def _weights(self) -> FitWeights:
        return FitWeights.model_validate(self._settings()["weights"])

    def _profile(self) -> SearchProfile:
        return SearchProfile.model_validate(self.s.get(ProfileRow, 1).data)  # type: ignore[union-attr]

    def _job(self, row: JobRow) -> Job:
        return Job.model_validate(row.data)

    def _job_row(self, job_id: str) -> JobRow:
        row = self.s.get(JobRow, job_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "That job doesn't exist.")
        return row

    def _evidence(self, job_id: str) -> FitEvidence:
        return FitEvidence.model_validate(self.s.get(FitReportRow, job_id).evidence)  # type: ignore[union-attr]

    def _next_id(self) -> int:
        n = int(get_kv(self.s, "counter", 0)) + 1
        set_kv(self.s, "counter", n)
        return n

    # ---- time-based progress: drafts and demo runs advance on read ----

    def settle(self) -> None:
        now = self.now
        for row in self.s.scalars(select(JobRow).where(JobRow.status == "snoozed")).all():
            if row.snoozed_until and parse_ms(row.snoozed_until) <= now:
                job = self._job(row)
                job.status, job.snoozed_until = "new", None
                save_job(self.s, job)
        for app in self.s.scalars(select(ApplicationRow).where(ApplicationRow.status == "drafting")).all():
            if app.draft_started_at and now - parse_ms(app.draft_started_at) >= DRAFT_TOTAL_MS:
                app.status = "ready_for_review"
        for run in self.s.scalars(select(IntakeRunRow).where(IntakeRunRow.status == "running")).all():
            elapsed = now - parse_ms(run.started_at)
            run.progress = min(1, elapsed / run.duration_ms)
            if run.channel == "board_search":
                run.queries = js_round(20 * run.progress)
            if elapsed >= run.duration_ms:
                self._finish_run(run)
        self.s.flush()

    def _finish_run(self, run: IntakeRunRow) -> None:
        now = self.now
        run.status, run.progress, run.finished_at = "succeeded", 1, iso(now)
        if run.settled:
            return
        run.settled = True
        stats = get_kv(self.s, "stats")

        if run.channel == "board_search":
            run.queries = 20
            if not get_kv(self.s, "firstRunDelivered", False):
                set_kv(self.s, "firstRunDelivered", True)
                added = []
                for seed in first_run_seeds():
                    job, evidence, questions = seed_to_job(seed, now)
                    job.first_seen = iso(now)
                    save_job(self.s, job, evidence, questions, created_at=iso(now))
                    added.append(job.id)
                set_kv(self.s, "lastRunJobIds", added)
                run.results, run.new_jobs, run.credits_used = 118, len(added), 40 + len(added) * 5
            else:
                # SRCH-05: URLs already known are skipped, so a repeat run scrapes nothing.
                run.results, run.new_jobs, run.credits_used = 112, 0, 40
                set_kv(self.s, "lastRunJobIds", [])
            stats = {**stats, "board": {"queries": 20, "results": run.results, "ranAt": iso(now)}}
        elif run.channel == "email_alert":
            run.results = run.new_jobs = 0
            stats = {**stats, "inbox": {**stats["inbox"], "checkedAt": iso(now)}}
            data = self._settings()
            self._save_settings({**data, "inbox": {**data["inbox"], "lastCheckedAt": iso(now)}})
        else:
            run.queries = stats["watchlist"]["companies"]
            run.results = run.new_jobs = 0
            stats = {**stats, "watchlist": {**stats["watchlist"], "checkedAt": iso(now)}}
        set_kv(self.s, "stats", stats)

    # ---- reads ----

    def scored(self, job: Job, weights: FitWeights | None = None) -> ScoredJob:
        fit = build_fit_report(
            job.id,
            self._evidence(job.id),
            job.posted_at,
            job.description_status == "missing",
            weights or self._weights(),
            self.now,
        )
        return ScoredJob.model_validate({**job.model_dump(), "fit": fit.model_dump()})

    def nav_counts(self) -> NavCounts:
        self.settle()
        jobs = self.s.scalar(select(func.count()).select_from(JobRow).where(JobRow.status == "new")) or 0
        review = (
            self.s.scalar(select(func.count()).select_from(ApplicationRow).where(ApplicationRow.status == "ready_for_review"))
            or 0
        )
        return NavCounts(jobs=jobs, review=review, demo_mode=get_settings().wera_demo_mode)

    def list_jobs(
        self,
        status: str = "new",
        sort: str = "score",
        *,
        min_score: float = 0,
        channel: str | None = None,
        location: str | None = None,
        remote: str | None = None,
    ) -> list[ScoredJob]:
        self.settle()
        weights = self._weights()
        rows = self.s.scalars(select(JobRow).where(JobRow.status == status)).all()
        items = [self.scored(self._job(r), weights) for r in rows]
        items.sort(key=lambda j: j.posted_at or "", reverse=True)
        if sort != "date":
            items.sort(key=lambda j: j.fit.total, reverse=True)  # stable: ties stay newest-first
        loc = location.lower() if location else None
        return [
            j
            for j in items
            if j.fit.total >= min_score
            and (not channel or any(s.channel == channel for s in j.sources))
            and (not loc or loc in j.location.raw.lower())
            and (not remote or j.remote == remote)
        ]

    def get_job(self, job_id: str) -> ScoredJob:
        return self.scored(self._job(self._job_row(job_id)))

    def sites(self) -> list[SiteOption]:
        return [SiteOption(domain=s.domain, name=s.name, reviewed=s.reviewed) for s in sites()]

    def shortlist_meta(self) -> ShortlistMeta:
        self.settle()
        stats = get_kv(self.s, "stats")
        board, inbox, watch = stats["board"], stats["inbox"], stats["watchlist"]
        p = self._profile()
        board_sites = ", ".join(s.name for s in sites() if s.domain in p.sites)
        channels = [
            ChannelSummary(
                channel="board_search",
                detail=f"Firecrawl · {board_sites}",
                stats=f"{board['queries']} queries · {board['results']} results · ran {_hhmm(board['ranAt'])}",
                enabled=p.channels.board_search,
            ),
            ChannelSummary(
                channel="email_alert",
                detail="Read-only · LinkedIn, Indeed and board alerts",
                stats=f"{inbox['emails']} emails · {inbox['jobs']} jobs · checked {self._minutes_ago(inbox['checkedAt'])}",
                enabled=p.channels.inbox,
            ),
            ChannelSummary(
                channel="watchlist",
                detail=f"{watch['companies']} companies · Greenhouse, Lever, Ashby",
                stats=f"{watch['newSinceYesterday']} new since yesterday",
                enabled=p.channels.watchlist,
            ),
        ]
        return ShortlistMeta(
            found=FoundCounts(
                total=board["results"] + inbox["jobs"] + watch["newSinceYesterday"],
                board=board["results"],
                inbox=inbox["jobs"],
                watchlist=watch["newSinceYesterday"],
            ),
            channels=channels,
            new_job_ids=get_kv(self.s, "lastRunJobIds", []),
            hidden=self.list_jobs("hidden"),
            snoozed=len(self.list_jobs("snoozed")),
            rejected=len(self.list_jobs("rejected")),
            active_run_ids=list(self.s.scalars(select(IntakeRunRow.id).where(IntakeRunRow.status == "running"))),
        )

    def _minutes_ago(self, at: str) -> str:
        mins = js_round((self.now - parse_ms(at)) / MIN)
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins} min ago"
        return f"{js_round(mins / 60)} h ago"

    # ---- Gate 1 (G1-02, G1-05, MATCH-06, TRK-03) ----

    def decide(self, job_id: str, d: DecisionIn) -> ScoredJob:
        self.settle()
        row = self._job_row(job_id)
        job = self._job(row)
        now = self.now

        if d.action == "unhide":
            if job.status != "hidden":
                raise ApiError(409, "INVALID_STATE", "Only hidden jobs can be unhidden.")
            job.status, job.hidden_reason = "new", None
        else:
            if job.status != "new":
                raise ApiError(409, "INVALID_STATE", "This job has already been decided.")
            if d.action == "approve":
                self._assert_budget()
                dup = self._find_duplicate(job)
                if dup is not None and not d.force:
                    other = self._job(self._job_row(dup.job_id))
                    raise ApiError(
                        409,
                        "DUPLICATE_APPLICATION",
                        f"You already have an application for {other.title} at {other.company} "
                        f"({_human(dup.status)}, started {_day_month(dup.created_at)}).",
                    )
                job.status = "in_application"
                save_job(self.s, job)
                self.create_application(f"app-{job.id[4:]}-{self._next_id()}", job, status="drafting", created_at=iso(now))
            elif d.action == "snooze":
                job.status, job.snoozed_until = "snoozed", iso(now + (d.days or 1) * DAY)
            else:
                job.status = "rejected"
        save_job(self.s, job)
        note = d.reason or (f"{d.days}d" if d.days else None)
        self.s.add(DecisionRow(job_id=job_id, gate=1, decision=d.action, note=note, decided_at=iso(now)))
        self.s.flush()
        logfire.info("Gate 1 {action} on {job_id}", action=d.action, job_id=job_id, job_status=job.status)
        GATE1_DECISIONS.add(1, {"action": d.action})
        return self.scored(job)

    def _find_duplicate(self, job: Job) -> ApplicationRow | None:
        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

        for app in self.s.scalars(select(ApplicationRow).where(ApplicationRow.status != "discarded")).all():
            other_row = self.s.get(JobRow, app.job_id)
            if other_row is None:
                continue
            if other_row.dedupe_key == job.dedupe_key:
                return app
            recent = self.now - parse_ms(app.created_at) <= 90 * DAY
            if recent and norm(other_row.company) == norm(job.company) and norm(other_row.title) == norm(job.title):
                return app
        return None

    def _assert_budget(self) -> None:
        # COST-01: every model call checks the weekly budget first.
        s = self._settings()
        if s["spentThisWeekUsd"] >= s["weeklyBudgetUsd"]:
            raise ApiError(
                402,
                "BUDGET_REACHED",
                f"This week's model budget (${s['weeklyBudgetUsd']:.2f}) is used up. Raise it in Settings to keep going.",
            )

    # ---- Manual import (ATS-04) ----

    def import_job(self, *, url: str | None = None, text: str | None = None) -> ScoredJob:
        cv = self.cv()
        job_id = f"job-import-{self._next_id()}"

        if url is not None:
            # Checked before any request is built, so a LinkedIn URL never leaves this function.
            if is_linkedin_url(url):
                raise ApiError(
                    422,
                    "LINKEDIN_NOT_FETCHED",
                    "WERA never opens LinkedIn links. Open the job on LinkedIn and paste its description instead.",
                )
            parsed = urlparse(url.strip())
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ApiError(422, "BAD_URL", "That doesn't look like a link. Paste the full address, starting with https://")
            ats = _parse_ats_url(parsed.hostname, parsed.path)
            host = re.sub(r"^www\.|^careers\.|^jobs\.", "", parsed.hostname, count=1)
            company = title_case(ats["boardToken"]) if ats else title_case(host.split(".")[0])
            seed = {
                "id": job_id,
                "title": "Graduate Platform Engineer",
                "company": company,
                "city": "London",
                "country": "United Kingdom",
                "remote": "hybrid",
                "postedDaysAgo": 2,
                "seniority": "graduate",
                "description": f"## {company}\n{company} is building developer tooling for teams that ship LLM features.\n\n"
                "## The role\nPython and TypeScript services, REST APIs and internal tools, working with the platform team.\n\n"
                "## You have\n- Python or TypeScript\n- REST APIs and PostgreSQL\n- Interest in LLMs and agents",
                "ats": ats,
                "applyUrl": url.strip() if parsed.path else url.strip() + "/",
                "sources": [{"channel": "manual", "detail": f"{ats['vendor']}:{ats['boardToken']}" if ats else parsed.hostname}],
                "evidence": _quick_evidence(
                    cv,
                    "Python TypeScript REST APIs PostgreSQL LLMs Agents",
                    "Python and TypeScript services, REST APIs and internal tools",
                ).model_dump(),
            }
        else:
            body = text or ""
            lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
            title = re.sub(r"^#+\s*", "", lines[0])[:80]
            # "…at Acme Robotics." — capitalised words on the same line, without the full stop.
            at_line = re.search(r"\bat[ \t]+([A-Z][\w&.-]*(?:[ \t]+[A-Z][\w&.-]*){0,3})", body, re.ASCII)
            company = (
                at_line.group(1).rstrip(".,")
                if at_line
                else (re.sub(r"^#+\s*", "", lines[1]) if len(lines) > 1 and len(lines[1]) < 40 else "Unknown company")
            )
            quote = next((ln for ln in lines if len(ln) > 40 and not ln.startswith("#")), None)
            quote = quote[:90] if quote else title
            seed = {
                "id": job_id,
                "title": title,
                "company": company,
                "city": "London",
                "country": "United Kingdom",
                "remote": None,
                "postedDaysAgo": 0,
                "description": body,
                "sources": [{"channel": "manual", "detail": "pasted-text"}],
                "evidence": _quick_evidence(cv, body, quote).model_dump(),
            }

        job, evidence, questions = seed_to_job(seed, self.now)
        job.first_seen = iso(self.now)
        existing = self.s.scalars(select(JobRow).where(JobRow.dedupe_key == job.dedupe_key)).first()
        if existing is not None:
            raise ApiError(409, "ALREADY_KNOWN", f"WERA already has this job: {existing.title} at {existing.company}.")
        save_job(self.s, job, evidence, questions, created_at=iso(self.now))
        set_kv(self.s, "lastRunJobIds", [job.id])
        self.s.flush()
        return self.scored(job)

    # ---- Applications and Gate 2 (G2-01…05, TRK-01…02) ----

    def create_application(
        self, app_id: str, job: Job, *, status: str, created_at: str, note: str | None = None, **fields: Any
    ) -> ApplicationRow:
        row = ApplicationRow(
            id=app_id, job_id=job.id, status=status, created_at=created_at, draft_started_at=created_at, **fields
        )
        self.s.add(row)
        self.s.flush()
        self._save_pack(app_id, self._make_pack(job, note))
        return row

    def _make_pack(self, job: Job, note: str | None = None) -> Pack:
        questions = self._job_row(job.id).questions
        return generate_pack(
            PackInput(
                job=job,
                evidence=self._evidence(job.id),
                questions=list(questions),
                cv=self.cv(),
                cv_pages=self._settings()["cvPages"],
                note=note,
            )
        )

    def _load_pack(self, app_id: str) -> Pack | None:
        rows = self.s.scalars(select(DocumentRow).where(DocumentRow.application_id == app_id).order_by(DocumentRow.version)).all()
        latest = {r.kind: r.content for r in rows}
        if not latest:
            return None
        return Pack(
            cv=TailoredCV.model_validate(latest["cv"]),
            letter=CoverLetter.model_validate(latest["letter"]),
            ats=AtsReport.model_validate(latest["ats"]),
            answers=[AnswerItem.model_validate(a) for a in latest["answers"]["items"]],
        )

    def _save_pack(self, app_id: str, pack: Pack, kinds: tuple[str, ...] = PACK_KINDS) -> None:
        """Each save is a new version of the changed documents (G2-02); old versions stay."""
        content = {
            "cv": pack.cv.model_dump(mode="json"),
            "letter": pack.letter.model_dump(mode="json"),
            "ats": pack.ats.model_dump(mode="json"),
            "answers": {"items": [a.model_dump(mode="json") for a in pack.answers]},
        }
        for kind in kinds:
            current = (
                self.s.scalar(
                    select(func.max(DocumentRow.version)).where(DocumentRow.application_id == app_id, DocumentRow.kind == kind)
                )
                or 0
            )
            self.s.add(
                DocumentRow(
                    application_id=app_id, kind=kind, version=current + 1, content=content[kind], created_at=iso(self.now)
                )
            )
        self.s.flush()

    def _app_row(self, app_id: str) -> ApplicationRow:
        row = self.s.get(ApplicationRow, app_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "That application doesn't exist.")
        return row

    def _draft(self, app: ApplicationRow) -> DraftProgress | None:
        if app.status != "drafting" or not app.draft_started_at:
            return None
        elapsed = self.now - parse_ms(app.draft_started_at)
        step = next((s for s, ends in DRAFT_STEPS if elapsed < ends), DRAFT_STEPS[-1][0])
        return DraftProgress(step=step, progress=min(1, elapsed / DRAFT_TOTAL_MS))

    def _with_job(self, app: ApplicationRow, include_pack: bool, pack: Pack | None = None) -> ApplicationWithJob:
        job = self._job(self._job_row(app.job_id))
        fit = self.scored(job).fit
        if include_pack and app.status != "drafting" and pack is None:
            pack = self._load_pack(app.id)
        base = Application(
            id=app.id,
            job_id=app.job_id,
            status=app.status,
            created_at=app.created_at,
            draft_started_at=app.draft_started_at,
            change_note=app.change_note,
            pack=pack if include_pack and app.status != "drafting" else None,
            approved_at=app.approved_at,
            submitted_at=app.submitted_at,
            follow_up_at=app.follow_up_at,
            notes=app.notes,
        )
        return ApplicationWithJob(
            **base.model_dump(),
            job=ApplicationJob(
                id=job.id,
                title=job.title,
                company=job.company,
                location=job.location,
                remote=job.remote,
                apply_url=job.apply_url,
                linkedin_url=job.linkedin_url,
                fit_total=fit.total,
            ),
            draft=self._draft(app),
        )

    def list_applications(self) -> list[ApplicationListItem]:
        self.settle()
        rows = sorted(self.s.scalars(select(ApplicationRow)).all(), key=lambda a: a.created_at, reverse=True)
        out = []
        for app in rows:
            item = self._with_job(app, False)
            pack = None if app.status == "drafting" else self._load_pack(app.id)
            out.append(ApplicationListItem(**item.model_dump(), coverage=pack.ats.coverage if pack else None))
        return out

    def get_application(self, app_id: str) -> ApplicationWithJob:
        self.settle()
        return self._with_job(self._app_row(app_id), True)

    def regenerate(self, app_id: str, note: str) -> ApplicationWithJob:
        self.settle()
        app = self._app_row(app_id)
        if app.status not in ("ready_for_review", "changes_requested"):
            raise ApiError(409, "INVALID_STATE", "Only a pack waiting for review can be regenerated.")
        self._assert_budget()
        job = self._job(self._job_row(app.job_id))
        app.status = "drafting"
        app.change_note = note.strip() or None
        app.draft_started_at = iso(self.now)
        self._save_pack(app.id, self._make_pack(job, note))
        self.s.add(DecisionRow(job_id=app.job_id, gate=2, decision="request_changes", note=note, decided_at=iso(self.now)))
        self.s.flush()
        return self._with_job(app, True)

    def approve_pack(self, app_id: str) -> ApplicationWithJob:
        self.settle()
        app = self._app_row(app_id)
        if app.status != "ready_for_review":
            raise ApiError(409, "INVALID_STATE", "Only a pack waiting for review can be approved.")
        app.status = "approved"
        app.approved_at = iso(self.now)
        self.s.add(DecisionRow(job_id=app.job_id, gate=2, decision="approve", decided_at=app.approved_at))
        self.s.flush()
        logfire.info("Gate 2 approved {app_id}", app_id=app_id, job_id=app.job_id)
        GATE2_APPROVALS.add(1)
        return self._with_job(app, True)

    def patch_application(self, app_id: str, patch: ApplicationPatchIn) -> ApplicationWithJob:
        self.settle()
        app = self._app_row(app_id)
        if patch.status and patch.status != app.status:
            if patch.status not in ALLOWED[app.status]:
                raise ApiError(
                    409, "INVALID_TRANSITION", f"An application can't move from {_human(app.status)} to {_human(patch.status)}."
                )
            job = self._job(self._job_row(app.job_id))
            if patch.status == "discarded":
                # Cancelling a draft puts the job back on the shortlist; discarding a finished pack rejects it.
                job.status = "new" if app.status == "drafting" else "rejected"
                save_job(self.s, job)
            if patch.status == "submitted":
                app.submitted_at = iso(self.now)
                app.follow_up_at = app.follow_up_at or iso(self.now + 7 * DAY)
            app.status = patch.status
        if patch.notes is not None:
            app.notes = patch.notes
        if "follow_up_at" in patch.model_fields_set:
            app.follow_up_at = patch.follow_up_at
        self.s.flush()
        return self._with_job(app, False)

    def edit_document(self, doc_id: str, patch: DocumentPatchIn) -> ApplicationWithJob:
        """G2-02 in-place edits, TAIL-06 add-back. Edits are marked and are not re-guarded."""
        self.settle()
        app_id, kind = _split_doc_id(doc_id)
        app = self._app_row(app_id)
        pack = self._load_pack(app.id) if app.status == "ready_for_review" else None
        if pack is None:
            raise ApiError(409, "INVALID_STATE", "Only a pack waiting for review can be edited.")
        job = self._job(self._job_row(app.job_id))
        text = patch.text.strip() if patch.text is not None else None
        changed: tuple[str, ...]

        if kind == "cv":
            doc = pack.cv
            if patch.target == "summary":
                if not text:
                    raise ApiError(422, "VALIDATION", "The summary can't be empty.")
                doc.summary = Cited(text=text, source_ids=doc.summary.source_ids, edited_by_user=True)
            elif patch.target in ("add", "remove"):
                if not patch.bullet_id:
                    raise ApiError(422, "VALIDATION", "Which line?")
                if patch.target == "add":
                    self._add_bullet(doc, patch.bullet_id)
                else:
                    _remove_bullet(doc, patch.bullet_id)
                doc.guard = guard_cv(doc, self.cv(), self._evidence(job.id))
            else:
                m = re.fullmatch(r"s(\d+)\.i(\d+)\.b(\d+)", patch.target)
                bullet = None
                if m:
                    si, ii, bi = (int(g) for g in m.groups())
                    try:
                        bullet = doc.sections[si].items[ii].bullets[bi]
                    except IndexError:
                        bullet = None
                if bullet is None:
                    raise ApiError(404, "NOT_FOUND", "That line no longer exists.")
                if not text:
                    raise ApiError(422, "VALIDATION", "A line can't be empty. Remove it instead.")
                bullet.text = text
                bullet.edited_by_user = True
            pack.ats = refresh_ats(
                pack, cv=self.cv(), evidence=self._evidence(job.id), cv_pages=self._settings()["cvPages"], job=job
            )
            changed = ("cv", "ats")
        elif kind == "letter":
            m = re.fullmatch(r"p(\d+)", patch.target)
            idx = int(m.group(1)) if m else -1
            if not (0 <= idx < len(pack.letter.paragraphs)):
                raise ApiError(404, "NOT_FOUND", "That paragraph no longer exists.")
            if not text:
                raise ApiError(422, "VALIDATION", "A paragraph can't be empty.")
            para = pack.letter.paragraphs[idx]
            para.text = text
            para.edited_by_user = True
            pack.letter.word_count = sum(len([w for w in re.split(r"\s+", p.text) if w]) for p in pack.letter.paragraphs)
            changed = ("letter",)
        elif kind == "answers":
            answer = next((a for a in pack.answers if a.id == patch.target), None)
            if answer is None:
                raise ApiError(404, "NOT_FOUND", "That question no longer exists.")
            answer.answer = text or None
            changed = ("answers",)
        else:
            raise ApiError(404, "NOT_FOUND", "Unknown document.")
        self._save_pack(app.id, pack, changed)
        return self._with_job(app, True, pack)

    def _add_bullet(self, doc: TailoredCV, bullet_id: str) -> None:
        cv = self.cv()
        roles = [*cv.experience, *cv.projects]
        role = next((r for r in roles if any(b.id == bullet_id for b in r.bullets)), None)
        if role is None:
            raise ApiError(404, "NOT_FOUND", "That line isn't in your master CV any more.")
        bullet = next(b for b in role.bullets if b.id == bullet_id)
        kind = "experience" if role in cv.experience else "projects"
        section = next((s for s in doc.sections if s.kind == kind), None)
        if section is None:
            section = CvSection(kind=kind, items=[])
            if kind == "experience":
                doc.sections.insert(0, section)
            else:
                doc.sections.append(section)
        item = next((i for i in section.items if i.role_id == role.id), None)
        if item is None:
            item = CvItem(role_id=role.id, bullets=[])
            order = [r.id for r in (cv.experience if kind == "experience" else cv.projects)]
            section.items.append(item)
            section.items.sort(key=lambda i: order.index(i.role_id))
        item.bullets.append(Cited(text=bullet.text, source_ids=[bullet.id]))
        ids = [b.id for b in role.bullets]
        item.bullets.sort(key=lambda b: ids.index(b.source_ids[0]) if b.source_ids[0] in ids else -1)
        doc.omitted_bullet_ids = [i for i in doc.omitted_bullet_ids if i != bullet_id]

    def export_document(self, doc_id: str, fmt: str) -> None:
        app_id, _ = _split_doc_id(doc_id)
        app = self._app_row(app_id)
        if app.status not in ("approved", "submitted", "interview", "offer"):
            raise ApiError(403, "PACK_NOT_APPROVED", "Approve the pack to unlock downloads.")
        if fmt not in ("pdf", "docx"):
            raise ApiError(422, "VALIDATION", "Format must be pdf or docx.")
        raise ApiError(
            501, "NOT_IMPLEMENTED", "PDF and DOCX export arrives with the document renderer. Your approved pack is saved."
        )

    # ---- Intake runs (SRCH-06…08, PRF-05, UX-01) ----

    def estimate(self, channels: list[str]) -> Estimate:
        p = self._profile()
        board = "board_search" in channels
        queries = len(p.titles) * len(p.locations) if board else 0
        pages = 10 if board else 0
        stats = get_kv(self.s, "stats")
        return Estimate(
            queries=queries,
            pages=pages,
            credits=queries * 2 + pages * 5,
            usd=0.02,
            companies=stats["watchlist"]["companies"] if "watchlist" in channels else 0,
            demo=get_settings().wera_demo_mode,
        )

    def start_runs(self, channels: list[str]) -> RunIds:
        self.settle()
        if self.s.scalar(select(func.count()).select_from(IntakeRunRow).where(IntakeRunRow.status == "running")):
            raise ApiError(409, "RUN_ACTIVE", "A search is already running. Wait for it to finish or cancel it.")
        on = self._profile().channels
        enabled = [
            c for c in channels if (on.board_search if c == "board_search" else on.inbox if c == "email_alert" else on.watchlist)
        ]
        if not enabled:
            raise ApiError(422, "NO_CHANNELS", "Turn on at least one channel in your search profile.")
        self._assert_budget()
        ids = []
        for channel in enabled:
            run = IntakeRunRow(
                id=f"run-{self._next_id()}",
                channel=channel,
                status="running",
                started_at=iso(self.now),
                progress=0,
                queries=0,
                results=0,
                new_jobs=0,
                credits_used=0,
                errors=[],
                duration_ms=RUN_MS[channel],
                settled=False,
            )
            self.s.add(run)
            ids.append(run.id)
        self.s.flush()
        return RunIds(run_ids=ids)

    def get_run(self, run_id: str) -> IntakeRun:
        self.settle()
        run = self.s.get(IntakeRunRow, run_id)
        if run is None:
            raise ApiError(404, "NOT_FOUND", "That run doesn't exist.")
        return IntakeRun(
            id=run.id,
            channel=run.channel,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            progress=run.progress,
            queries=run.queries,
            results=run.results,
            new_jobs=run.new_jobs,
            credits_used=run.credits_used,
            errors=[RunError.model_validate(e) for e in run.errors],
        )

    def cancel_run(self, run_id: str) -> IntakeRun:
        self.settle()
        run = self.s.get(IntakeRunRow, run_id)
        if run is None:
            raise ApiError(404, "NOT_FOUND", "That run doesn't exist.")
        if run.status == "running":
            run.status, run.finished_at, run.settled = "cancelled", iso(self.now), True
        return self.get_run(run_id)

    # ---- CV and profile (CV-01…07, PRF-01…03) ----

    def cv_lines(self) -> dict[str, CvLine]:
        return {k: CvLine.model_validate(v) for k, v in cv_line_index(self.cv()).items()}

    def put_cv(self, cv: MasterCV) -> int:
        ids = [
            *([cv.summary.id] if cv.summary else []),
            *[x for r in [*cv.experience, *cv.projects] for x in [r.id, *(b.id for b in r.bullets)]],
            *[e.id for e in cv.education],
        ]
        dupes = sorted({i for i in ids if ids.count(i) > 1}, key=ids.index)
        if dupes:
            raise ApiError(422, "VALIDATION", f"Every id must be unique. Duplicated: {', '.join(dupes)}")
        cv.version = self.cv().version + 1
        self.s.add(MasterCvRow(version=cv.version, data=cv.model_dump(mode="json"), saved_at=iso(self.now)))
        self.s.flush()
        self._cv_cache = cv
        return cv.version

    def import_cv_yaml(self, text: str) -> MasterCV:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as err:
            first = str(err).split("\n")[0] or "syntax error"
            raise ApiError(422, "BAD_YAML", f"That YAML doesn't parse: {first}") from None
        try:
            cv = MasterCV.model_validate(data)
        except ValidationError as err:
            raise validation_error(list(err.errors())) from None
        cv.version = self.cv().version  # proposed only; saving increments it (CV-03)
        return cv

    def get_contact(self) -> Contact:
        row = self.s.get(ContactRow, 1)
        return Contact(email=row.email, phone=row.phone, address=row.address)  # type: ignore[union-attr]

    def put_contact(self, contact: Contact) -> Contact:
        row = self.s.get(ContactRow, 1)
        row.email, row.phone, row.address = contact.email, contact.phone, contact.address  # type: ignore[union-attr]
        return contact

    def get_profile(self) -> SearchProfile:
        return self._profile()

    def put_profile(self, profile: SearchProfile) -> SearchProfile:
        profile.updated_at = iso(self.now)
        self.s.get(ProfileRow, 1).data = profile.model_dump(mode="json")  # type: ignore[union-attr]
        return profile

    # ---- Settings (SET-01…07) ----

    def get_settings(self) -> SettingsResponse:
        self.settle()
        env = get_settings()
        data = self._settings()
        return SettingsResponse.model_validate(
            {
                **data,
                "demoMode": env.wera_demo_mode,
                "secrets": SecretsStatus.model_validate(env.secret_status()).model_dump(),
                "llmProvider": llm_provider_name(data["baseUrl"]),
            }
        )

    def put_settings(self, patch: SettingsPatch) -> SettingsResponse:
        data = dict(self._settings())
        changes = patch.model_dump(mode="json", exclude={"inbox"})
        data.update(changes)
        if patch.inbox is not None:
            data["inbox"] = {**data["inbox"], "senders": patch.inbox.senders}
        self._save_settings(data)
        self.s.flush()
        return self.get_settings()

    def test_provider(self, provider: str) -> TestResult:
        if provider not in ("llm", "firecrawl", "inbox"):
            raise ApiError(404, "NOT_FOUND", "Unknown provider.")
        env = get_settings()
        if env.secret_status()[provider] != "set":
            name = {"llm": "LLM_API_KEY", "firecrawl": "FIRECRAWL_API_KEY", "inbox": "ALERTS_IMAP_APP_PASSWORD"}[provider]
            return TestResult(ok=False, code=f"{provider.upper()}_KEY_MISSING", message=f"{name} isn't set on the server.")
        if env.wera_demo_mode:
            # DEMO-02: demo mode makes no outbound calls, so a live check can't run.
            return TestResult(
                ok=False, code="DEMO_MODE", message="The key is set. Demo mode makes no outbound calls, so it wasn't tested."
            )
        return TestResult(ok=False, code="NOT_IMPLEMENTED", message="Live connection tests aren't built yet.")


# ---------------------------------------------------------------------------


def _split_doc_id(doc_id: str) -> tuple[str, str]:
    i = doc_id.rfind(".")
    if i < 0:
        raise ApiError(404, "NOT_FOUND", "Unknown document.")
    return doc_id[:i], doc_id[i + 1 :]


def _remove_bullet(doc: TailoredCV, bullet_id: str) -> None:
    for section in doc.sections:
        for item in section.items:
            idx = next((i for i, b in enumerate(item.bullets) if b.source_ids[0] == bullet_id), -1)
            if idx >= 0:
                item.bullets.pop(idx)
                doc.omitted_bullet_ids.append(bullet_id)
        section.items = [it for it in section.items if it.bullets]
    doc.sections = [s for s in doc.sections if s.items]


def _parse_ats_url(host: str, path: str) -> dict[str, str] | None:
    parts = [p for p in path.split("/") if p]
    if host.endswith("greenhouse.io") and len(parts) >= 3 and parts[1] == "jobs":
        return {"vendor": "greenhouse", "boardToken": parts[0], "jobId": parts[2]}
    if host == "jobs.lever.co" and len(parts) >= 2:
        return {"vendor": "lever", "boardToken": parts[0], "jobId": parts[1]}
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return {"vendor": "ashby", "boardToken": parts[0], "jobId": parts[1]}
    return None


def _quick_evidence(cv: MasterCV, text: str, quote: str) -> FitEvidence:
    """Phase 1 quick match (§8.1) for pasted jobs: keyword coverage against the CV, scored in code."""
    vocab = [*(s.name for s in cv.skills), "LLMs", "Agents", "Docker", "Kubernetes", "AWS", "RAG", "Model evaluation"]
    terms = list(dict.fromkeys(t for t in vocab if text_has_term(text, t)))
    master = master_cv_text(cv)
    backed = [t for t in terms if text_has_term(master, t)]
    coverage = len(backed) / len(terms) if terms else 0.3
    ids = [
        b.id
        for r in [*cv.experience, *cv.projects]
        for b in r.bullets
        if any(text_has_term(f"{b.text} {' '.join(b.tags)}", t) for t in backed)
    ][:2]
    core = js_round(10 * coverage * 10) / 10
    empty = {"jobQuote": "", "cvBulletIds": []}
    return FitEvidence.model_validate(
        {
            "core": SubScore(score=core, job_quote=quote, cv_bullet_ids=ids).model_dump(),
            "adjacent": {"score": max(4, core - 1), **empty},
            "experience": {"score": 6, **empty},
            "eligibility": {"score": 7, **empty},
            "reason": f"{len(backed)} of {len(terms)} key terms are backed by your CV.",
            "jobQuote": quote,
            "cvBulletIds": ids,
            "keywords": [{"term": t, "weight": "required" if i < 5 else "nice"} for i, t in enumerate(terms)],
            "model": "qwen/qwen3.7-flash",
        }
    )
