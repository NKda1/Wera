"""WERA's data and workflow behind the SPEC §9.1 routes: state machines (§7), Gate 1 and
Gate 2, intake runs, documents and settings, on the database.

The store owns the database state machine and is what the HTTP routes talk to. Drafting a pack
is the application graph's job (graphs/application.py): its nodes call the helpers under "what
the application graph's nodes call" below, so each transition has one implementation.

Demo intake runs advance with time (`_finish_run`); a real install runs the intake graph
instead, and that graph records its own figures.
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
from ..db import state
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
)
from ..domain import cv_build, cv_extract, dedupe
from ..domain.applications import ALLOWED
from ..domain.profile_diff import profile_diff
from ..domain.scoring import build_fit_report, js_round, liveness, urgency
from ..domain.text import is_linkedin_url, title_case
from ..domain.vocab import cv_line_index, master_cv_text, text_has_term
from ..errors import ApiError, validation_error
from ..llm.demo import PackInput, guard_cv, refresh_ats
from ..llm.gateway import structured
from ..llm.pricing import spent_this_week_usd
from ..llm.prompts import cv_text, profile_text, untrusted
from ..pii import scrub_contacts
from ..schemas.api import (
    ApplicationPatchIn,
    DecisionIn,
    DocumentPatchIn,
    FoundCounts,
    NavCounts,
    ProfileProposalResult,
    ShortlistMeta,
    SiteOption,
    TestResult,
)
from ..schemas.applications import Application, ApplicationJob, ApplicationListItem, ApplicationWithJob, DraftProgress
from ..schemas.cv import Contact, CvLine, MasterCV
from ..schemas.jobs import Ats, FitEvidence, FitWeights, Job, ScoredJob, SubScore
from ..schemas.llm_outputs import ParsedCv, ProfileProposal
from ..schemas.pack import AnswerItem, AtsReport, Cited, CoverLetter, CvItem, CvSection, Pack, TailoredCV
from ..schemas.profile import SearchProfile
from ..schemas.runs import ChannelSummary, Estimate, IntakeRun, RunError, RunIds
from ..schemas.settings import SecretsStatus, SettingsPatch, SettingsResponse
from ..settings import get_settings, llm_provider_name
from . import providers
from .demo_data import DAY, MIN, first_run_seeds, seed_to_job

# The order the graph drafts a pack in, which the review screen shows as progress (PERF-03).
# Must match the nodes in `graphs/application.py` and `schemas.applications.DraftStep`. An
# unrecognised step falls back to the first one (see `_draft`), so a step missing here shows the
# person a pack that went backwards.
DRAFT_ORDER = ("cv", "letter", "ats", "answers")
RUN_MS = {"board_search": 6000, "email_alert": 3500, "watchlist": 2500}
PACK_KINDS = ("cv", "letter", "ats", "answers")

# The two gates are what WERA promises, so they are counted as well as logged. Ids only:
# which jobs a person looks at is theirs (SPEC SEC-05).
GATE1_DECISIONS = logfire.metric_counter("wera.gate1.decisions", unit="1", description="Gate 1 decisions, by action")
GATE2_APPROVALS = logfire.metric_counter("wera.gate2.approvals", unit="1", description="Packs a person approved at Gate 2")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Row helpers


def _latest_ms(*stamps: str | None) -> int | None:
    """The most recent of some ISO stamps, ignoring the missing and the unreadable.

    `None` when none of them can be read, which callers must treat as "no evidence" rather than
    as "long ago" — the difference between those two is a job archived for having a bad date.
    """
    found = []
    for stamp in stamps:
        if not stamp:
            continue
        try:
            found.append(parse_ms(stamp))
        except (ValueError, TypeError):
            continue
    return max(found) if found else None


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


def _required(app_id: str | None) -> str:
    """A graph node always has an application by the time it drafts; this says so out loud."""
    if not app_id:
        raise ApiError(409, "INVALID_STATE", "This thread has no application to draft into.")
    return app_id


class Store:
    """One unit of work on the database. Methods mirror the web app's API routes."""

    def __init__(self, session: Session, now: int | None = None):
        self.s = session
        self.now = now if now is not None else now_ms()
        self._cv_cache: MasterCV | None = None
        # Set by `decide` when an approval creates an application, so the route can hand it to
        # the graph thread it resumes.
        self.started_application_id: str | None = None
        # Work to start once this unit of work is committed; see wera_agent/tasks.py.
        self.deferred: list[tuple[Any, str]] = []

    def defer(self, coro: Any, *, name: str) -> None:
        """Run this after the transaction closes, not inside it."""
        self.deferred.append((coro, name))

    # ---- loading ----

    def cv(self) -> MasterCV:
        if self._cv_cache is None:
            row = self.s.scalars(select(MasterCvRow).order_by(MasterCvRow.version.desc()).limit(1)).first()
            if row is None:
                raise ApiError(503, "NOT_SEEDED", "WERA has no CV yet. Run `wera reset` to set up the database.")
            self._cv_cache = MasterCV.model_validate(row.data)
        return self._cv_cache

    def _settings(self) -> dict[str, Any]:
        return state.settings_data(self.s)

    def _save_settings(self, data: dict[str, Any]) -> None:
        state.save_settings_data(self.s, data)

    def _weights(self) -> FitWeights:
        return FitWeights.model_validate(self._settings()["weights"])

    def _profile_row(self) -> ProfileRow:
        """The one profile row. `seed()` creates it for real and demo databases alike, so its
        absence means an un-seeded or hand-edited database — which is worth saying out loud
        rather than raising `AttributeError` from somewhere three screens away."""
        row = self.s.get(ProfileRow, 1)
        if row is None:
            raise ApiError(500, "NOT_SEEDED", "This database has no search profile. Run `wera reset` to rebuild it.")
        return row

    def _profile(self) -> SearchProfile:
        return SearchProfile.model_validate(self._profile_row().data)

    def _job(self, row: JobRow) -> Job:
        return Job.model_validate(row.data)

    def _job_row(self, job_id: str) -> JobRow:
        row = self.s.get(JobRow, job_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "That job doesn't exist.")
        return row

    def _evidence(self, job_id: str) -> FitEvidence:
        found = self._evidence_or_none(job_id)
        if found is None:
            raise ApiError(409, "NOT_SCORED", "That job hasn't been scored yet.")
        return found

    def _evidence_or_none(self, job_id: str) -> FitEvidence | None:
        """A job's fit evidence, or None when it has none.

        Not every stored job has one, and two of the reasons are by design rather than failure:
        a job hidden by the profile filters (MATCH-01) was filtered *before* any model call and
        will never have one, and a job past the day's scoring cap (MATCH-05) does not have one
        yet. A third is ordinary failure — the run stopped on budget, or one job's scoring call
        failed. Treating any of these as a missing attribute is what made the Jobs screen 500
        on a real database while demo mode, whose hidden jobs all carry fit reports, stayed green.
        """
        row = self.s.get(FitReportRow, job_id)
        return FitEvidence.model_validate(row.evidence) if row else None

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
        # Drafting is no longer time-based: the application graph moves an application to
        # ready_for_review when its nodes have actually finished (see graphs/application.py).
        #
        # Intake runs are only simulated in demo mode. A real run is finished by the intake
        # graph when its channels have actually answered, so advancing it on a timer here would
        # overwrite real results with demo seeds.
        if not get_settings().wera_demo_mode:
            self.s.flush()
            return
        for run in self.s.scalars(select(IntakeRunRow).where(IntakeRunRow.status == "running")).all():
            elapsed = now - parse_ms(run.started_at)
            run.progress = min(1, elapsed / run.duration_ms)
            if run.channel == "board_search":
                run.queries = js_round(20 * run.progress)
                # `done`/`total` are what the screen reads while a run is searching, so the demo
                # animates them as well — otherwise the demo's bar has no label under it.
                run.phase, run.done, run.total = "searching", run.queries, 20
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
                # SRCH-05: URLs already known are skipped, so a repeat run scrapes nothing — and
                # SRCH-10: the searches themselves come from the crawl cache, which is the 40
                # credits this used to spend anyway. A repeat run inside the window is free.
                run.results, run.new_jobs = 112, 0
                run.credits_used, run.credits_saved = 0, 40
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

    def set_last_run_jobs(self, job_ids: list[str]) -> None:
        """What the shortlist marks as new since the last run."""
        set_kv(self.s, "lastRunJobIds", job_ids)

    def record_channel_stats(self, channel: str, *, queries: int, results: int, new_jobs: int, emails: int = 0) -> None:
        """The channel strip's figures after a real run.

        Demo mode has `_finish_run` for this; a real run has to write them itself, or the
        shortlist would keep showing the state of a run that happened days ago.
        """
        stats = get_kv(self.s, "stats")
        at = iso(self.now)
        if channel == "board_search":
            stats = {**stats, "board": {"queries": queries, "results": results, "ranAt": at}}
        elif channel == "email_alert":
            stats = {**stats, "inbox": {**stats["inbox"], "emails": emails, "jobs": results, "checkedAt": at}}
            data = self._settings()
            self._save_settings({**data, "inbox": {**data["inbox"], "lastCheckedAt": at}})
        else:
            stats = {**stats, "watchlist": {**stats["watchlist"], "newSinceYesterday": new_jobs, "checkedAt": at}}
        set_kv(self.s, "stats", stats)

    # ---- reads ----

    def scored_or_none(self, job: Job, weights: FitWeights | None = None) -> ScoredJob | None:
        """The job with its fit report, or None when it hasn't been scored.

        There is deliberately no placeholder score here. A zero would sort it to the bottom of
        a ranked list as though a model had judged it and found nothing, which is a claim about
        the job rather than about WERA's progress through the queue.
        """
        return self.scored(job, weights) if self._evidence_or_none(job.id) else None

    def count_jobs(self, status: str) -> int:
        """How many jobs hold a status, counted in the database rather than by building them."""
        return self.s.scalar(select(func.count()).select_from(JobRow).where(JobRow.status == status)) or 0

    def awaiting_score(self) -> int:
        """New jobs with no fit report: past the day's cap (MATCH-05), or left by a failed run."""
        stmt = (
            select(func.count())
            .select_from(JobRow)
            .outerjoin(FitReportRow, FitReportRow.job_id == JobRow.id)
            .where(JobRow.status == "new", FitReportRow.job_id.is_(None))
        )
        return self.s.scalar(stmt) or 0

    def list_unscored(self, status: str, *, limit: int) -> list[Job]:
        """Jobs as stored, with no fit report attached.

        For `hidden` this is the only correct shape: MATCH-01 filters before any model call, so
        a hidden job has a `hiddenReason` and never a score. The limit is because a profile that
        filters hard produces thousands of these, and the screen shows a count and a sample.
        """
        rows = self.s.scalars(select(JobRow).where(JobRow.status == status).limit(limit)).all()
        return [self._job(r) for r in rows]

    def scored(self, job: Job, weights: FitWeights | None = None) -> ScoredJob:
        fit = build_fit_report(
            job.id,
            self._evidence(job.id),
            job.posted_at,
            job.description_status == "missing",
            weights or self._weights(),
            self.now,
        )
        return ScoredJob.model_validate(
            {
                **job.model_dump(),
                "fit": fit.model_dump(),
                "urgency": urgency(job.posted_at, self.now),
                "liveness": liveness(job, self.now),
            }
        )

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
        # A job with no fit report is not ready for a ranked list — it is waiting for tomorrow's
        # scoring run (MATCH-05) or it failed. It is counted in `awaiting_score` rather than
        # dropped silently, because a job that vanishes with no explanation is its own bug.
        items = [scored for r in rows if (scored := self.scored_or_none(self._job(r), weights))]
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
        return [
            SiteOption(
                domain=s.domain,
                name=s.name,
                reviewed=s.reviewed,
                countries=[c.upper() for c in s.countries],
                region=s.region,
                proposed=s.proposed,
                declined=s.declined_on is not None,
                discovered_on=s.discovered_on.isoformat() if s.discovered_on else None,
                matched=s.matched,
                robots_ok=s.robots_ok,
                examples=s.examples,
            )
            for s in sites()
        ]

    # How many hidden jobs the Jobs screen is sent. A real profile filters thousands — one run
    # against 15 boards left 3,223 — and the screen shows a count and a sample, not all of them.
    HIDDEN_SHOWN = 50

    def shortlist_meta(self) -> ShortlistMeta:
        self.settle()
        stats = get_kv(self.s, "stats")
        board, inbox, watch = stats["board"], stats["inbox"], stats["watchlist"]
        p = self._profile()
        board_sites = ", ".join(s.name for s in sites() if s.domain in p.sites)
        # A channel that has never run says so, rather than reporting a confident "0 results".
        channels = [
            ChannelSummary(
                channel="board_search",
                detail=f"Firecrawl · {board_sites}",
                stats=f"{board['queries']} queries · {board['results']} results · ran {_hhmm(board['ranAt'])}"
                if board["ranAt"]
                else "not run yet",
                enabled=p.channels.board_search,
            ),
            ChannelSummary(
                channel="email_alert",
                detail="Read-only · LinkedIn, Indeed and board alerts",
                stats=f"{inbox['emails']} emails · {inbox['jobs']} jobs · checked {self._minutes_ago(inbox['checkedAt'])}"
                if inbox["checkedAt"]
                else "not checked yet",
                enabled=p.channels.inbox,
            ),
            ChannelSummary(
                channel="watchlist",
                detail=f"{watch['companies']} companies · Greenhouse, Lever, Ashby",
                stats=f"{watch['newSinceYesterday']} new since yesterday" if watch["checkedAt"] else "not checked yet",
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
            hidden=self.list_unscored("hidden", limit=self.HIDDEN_SHOWN),
            hidden_total=self.count_jobs("hidden"),
            awaiting_score=self.awaiting_score(),
            snoozed=self.count_jobs("snoozed"),
            rejected=self.count_jobs("rejected"),
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
                app_id = f"app-{job.id[4:]}-{self._next_id()}"
                self.create_application(app_id, job, status="drafting", created_at=iso(now))
                self.started_application_id = app_id
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
        """ATS-04. A real install fetches the posting it was given; the demo makes one up,
        because a demo that reached out to a job board would not be a demo (DEMO-02)."""
        if not get_settings().wera_demo_mode:
            return self._import_live(url=url, text=text)
        return self._import_demo(url=url, text=text)

    def _import_live(self, *, url: str | None, text: str | None) -> ScoredJob:
        """The real thing: fetch or take the posting, then score it like any other job.

        An imported job is scored straight away — the person asked for this one by name, so the
        model call is the point of the click rather than a cost they didn't choose. Scoring that
        fails takes the import with it: a job on the list with no fit would never be scored
        later (a run only scores what that run found), so a clear failure beats a job that sits
        there unranked for good.
        """
        from ..llm.gateway import MESSAGES
        from ..nodes import fit
        from ..sources import manual

        job = manual.imported(url=url, text=text, now=self.now)
        existing = self.s.scalars(select(JobRow).where(JobRow.dedupe_key == job.dedupe_key)).first()
        if existing is not None:
            raise ApiError(409, "ALREADY_KNOWN", f"WERA already has this job: {existing.title} at {existing.company}.")

        save_job(self.s, job, None, [], created_at=iso(self.now))
        evidence, _, code = fit.evidence_for(self.s, job, self.cv(), model=self._settings()["models"]["scoring"], now=self.now)
        if evidence is None:
            raise ApiError(
                424,
                code or "SCORE_FAILED",
                f"WERA read the job but couldn't score it, so nothing was added. "
                f"{MESSAGES.get(code or '', 'Try again in a moment.')}",
            )
        set_kv(self.s, "lastRunJobIds", [job.id])
        self.s.flush()
        return self.scored(job)

    def _import_demo(self, *, url: str | None = None, text: str | None = None) -> ScoredJob:
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
        """The row only. The documents arrive from the application graph, node by node."""
        row = ApplicationRow(
            id=app_id,
            job_id=job.id,
            status=status,
            created_at=created_at,
            draft_started_at=created_at,
            draft_step="cv" if status == "drafting" else None,
            change_note=note,
            **fields,
        )
        self.s.add(row)
        self.s.flush()
        return row

    # ---- what the application graph's nodes call ----

    def pack_input(self, job_id: str, *, note: str | None = None) -> PackInput:
        """Everything a writing node needs, read fresh from the database."""
        job = self._job(self._job_row(job_id))
        return PackInput(
            job=job,
            evidence=self._evidence(job_id),
            questions=list(self._job_row(job_id).questions),
            cv=self.cv(),
            cv_pages=self._settings()["cvPages"],
            note=note,
            # LTR-06: so the letter's company paragraph has something true to connect to.
            profile=self.get_profile(),
        )

    def set_draft_step(self, app_id: str | None, step: str) -> None:
        """Records which document is being drafted (PERF-03).

        The graph commits this on its own before the model call, so a screen polling in another
        request sees the step that is running — not the one that just finished.
        """
        app = self._app_row(_required(app_id))
        app.draft_step = step
        app.draft_started_at = app.draft_started_at or iso(self.now)
        self.s.flush()

    def draft_input(self, app_id: str | None, *, note: str | None) -> PackInput:
        """What a writing step needs, with the person's own change note if they left one."""
        app = self._app_row(_required(app_id))
        return self.pack_input(app.job_id, note=note or app.change_note)

    def save_document(self, app_id: str | None, kind: str, document: Any) -> None:
        """One document, as a new version (G2-02); earlier versions stay."""
        self._save_document(_required(app_id), kind, document.model_dump(mode="json"))

    def save_answers(self, app_id: str | None, items: list[AnswerItem]) -> None:
        self._save_document(_required(app_id), "answers", {"items": [a.model_dump(mode="json") for a in items]})

    def _save_document(self, app_id: str, kind: str, content: dict[str, Any]) -> None:
        current = (
            self.s.scalar(
                select(func.max(DocumentRow.version)).where(DocumentRow.application_id == app_id, DocumentRow.kind == kind)
            )
            or 0
        )
        self.s.add(DocumentRow(application_id=app_id, kind=kind, version=current + 1, content=content, created_at=iso(self.now)))
        self.s.flush()

    def load_document[T](self, app_id: str | None, kind: str, model: type[T]) -> T:
        row = self.s.scalars(
            select(DocumentRow)
            .where(DocumentRow.application_id == _required(app_id), DocumentRow.kind == kind)
            .order_by(DocumentRow.version.desc())
            .limit(1)
        ).first()
        if row is None:
            raise ApiError(409, "INVALID_STATE", f"This application has no {kind} yet.")
        return model.model_validate(row.content)  # type: ignore[attr-defined]

    def mark_ready(self, app_id: str | None) -> None:
        """The pack is drafted and Gate 2 is waiting on a person."""
        app = self._app_row(_required(app_id))
        app.status, app.draft_step, app.change_note = "ready_for_review", None, None
        self.s.flush()

    def clear_draft_step(self, app_id: str | None) -> None:
        self._app_row(_required(app_id)).draft_step = None
        self.s.flush()

    def mark_draft_failed(self, job_id: str, code: str, message: str) -> None:
        """Drafting stopped. The job goes back on the shortlist so the person can retry, and the
        reason is kept on the application rather than lost in a log."""
        app = self.s.scalars(
            select(ApplicationRow)
            .where(ApplicationRow.job_id == job_id, ApplicationRow.status == "drafting")
            .order_by(ApplicationRow.created_at.desc())
            .limit(1)
        ).first()
        if app is None:
            return
        app.status, app.draft_step = "changes_requested", None
        app.notes = f"{message} ({code})"
        self.s.flush()

    # ---- what the intake graph's steps call ----

    def weights(self) -> FitWeights:
        return self._weights()

    def known_apply_urls(self) -> set[str]:
        """Every apply URL WERA already has, so a search doesn't scrape them again (SRCH-05).

        **Canonicalised**, because the raw string isn't the posting's identity. This compared raw
        strings while `dedupe.dedupe_key` compares `canonical_url`, so the same vacancy arriving
        with a `?utm_source=` tag, a `www.`, or a trailing slash read as a URL WERA had never
        seen: 5 credits to re-scrape it, and — worse — a fresh sighting that refreshed the job's
        `last_seen`, which is the clock NORM-07 archives on.
        """
        urls: set[str] = set()
        for row in self.s.scalars(select(JobRow)).all():
            url = dedupe.canonical_url((row.data or {}).get("applyUrl"))
            if url:
                urls.add(url)
        return urls

    def ats_by_job(self) -> dict[str, Ats]:
        """Job id → ATS identity, for every open job that has one (ATS-03, ATS-06).

        Renamed from `watchlist_ats_by_job`, which it never was: it is keyed on a job *having* an
        ATS identity, not on where the job came from. The old name is why ATS-06 reads as a new
        feature — the data to close an email-resolved or manually imported job has been here all
        along, and only `fetch_all`'s watchlist-shaped input stopped it being used.
        """
        out: dict[str, Ats] = {}
        for row in self.s.scalars(select(JobRow).where(JobRow.status.not_in(("closed", "archived")))).all():
            ats = (row.data or {}).get("ats")
            if ats:
                out[row.id] = Ats.model_validate(ats)
        return out

    # Statuses the lifecycle sweep may touch. A job someone is applying to keeps its job whatever
    # a board says (the `close_jobs` rule), and the two terminal states are already terminal.
    RECHECKABLE = ("new", "shortlisted")

    def recheck_candidates(self, limit: int) -> list[Job]:
        """Which open jobs to ask about this sweep, worst-informed first (SRCH-13).

        Ordered so a cap spends its budget where WERA knows least: never asked before anything
        else, then longest since it was asked. Sorted in Python because `last_checked_at` lives in
        `JobRow.data` rather than a column — the same reason `known_apply_urls` scans.
        """
        rows = self.s.scalars(select(JobRow).where(JobRow.status.in_(self.RECHECKABLE))).all()
        jobs = [self._job(row) for row in rows]
        jobs.sort(key=lambda j: (j.last_checked_at is not None, j.last_checked_at or ""))
        return jobs[:limit]

    def record_liveness(self, job_id: str, *, confirmed: bool) -> None:
        """One job's re-check result. `confirmed` is the only thing that is evidence of openness.

        An unconfirmed job is *flagged, not hidden* (MATCH-10): nothing here touches `status`,
        `hidden_reason` or the fit, so the job keeps its place on the shortlist and the screen
        says when it was last confirmed. Inconclusive is not evidence of withdrawal.
        """
        row = self.s.get(JobRow, job_id)
        if row is None:
            return
        job = self._job(row)
        job.last_checked_at = iso(self.now)
        if confirmed:
            job.last_confirmed_at = job.last_checked_at
        save_job(self.s, job)
        self.s.flush()

    def archive_stale(self, after_days: int) -> int:
        """NORM-07: archive non-watchlist jobs nobody has seen for `after_days`.

        **The clock is `max(last_seen, last_confirmed_at)`, and that is not a refinement — a plain
        `last_seen` sweep would archive every board-search job on day 30, including ones confirmed
        open that morning.** `firecrawl_search.search` drops a hit whose URL is already known
        *before* scraping it (SRCH-05), so a stored board-search job is never re-sighted and its
        `last_seen` is frozen at the day it was found. `last_seen` measures WERA's attention;
        `last_confirmed_at` measures the vacancy, and only the second can clear a job of being
        stale. This is why the liveness check had to land first.

        Watchlist-sourced jobs are excluded, as NORM-07 says: ATS-03 governs those from their
        employer's own feed, and a timer would fight it.
        """
        cutoff = self.now - after_days * 24 * 60 * 60 * 1000
        archived = 0
        for row in self.s.scalars(select(JobRow).where(JobRow.status.in_(self.RECHECKABLE))).all():
            job = self._job(row)
            if any(s.channel == "watchlist" for s in job.sources):
                continue
            latest = _latest_ms(job.last_seen, job.last_confirmed_at)
            # An unreadable or missing date is never a reason to archive: the sweep acts on
            # evidence that time has passed, and it has none here.
            if latest is None or latest > cutoff:
                continue
            job.status = "archived"
            save_job(self.s, job)
            archived += 1
        self.s.flush()
        return archived

    def close_jobs(self, job_ids: list[str]) -> int:
        """ATS-03: the vacancy is gone, so the job is closed — not deleted. An application
        already in flight keeps its job."""
        closed = 0
        for job_id in job_ids:
            row = self.s.get(JobRow, job_id)
            if row is None or row.status in ("in_application", "closed", "archived"):
                continue
            job = self._job(row)
            job.status = "closed"
            # LIFE-01: when, not just whether. Without this the Jobs screen can say a job is
            # closed but never "closed three days ago", and nothing can tell a job closed this
            # morning from one closed a month ago.
            job.closed_at = iso(self.now)
            job.last_checked_at = job.closed_at
            save_job(self.s, job)
            closed += 1
        self.s.flush()
        return closed

    def upsert_jobs(self, jobs: list[Job]) -> tuple[list[Job], set[str]]:
        """Saves a run's jobs. Returns `(as stored, the ids that are new)`.

        A job WERA already had keeps its own id, status and decisions — a second sighting is a
        new source and a fresher `lastSeen`, not a new job (SCH-02). The stored jobs come back
        because a merged sighting takes the id it already had, and anything downstream (a fit
        report, for one) has to point at that id and not at the one this run invented.
        """
        new_ids: set[str] = set()
        stored: list[Job] = []
        by_key = {row.dedupe_key: row for row in self.s.scalars(select(JobRow)).all() if row.dedupe_key}
        for job in jobs:
            existing = self.s.get(JobRow, job.id) or by_key.get(job.dedupe_key)
            if existing is None:
                save_job(self.s, job, questions=[])
                new_ids.add(job.id)
                stored.append(job)
                continue
            merged = dedupe.merge_sources(self._job(existing), job)
            merged.last_seen = iso(self.now)
            save_job(self.s, merged)
            stored.append(merged)
        self.s.flush()
        return stored, new_ids

    def inbox_last_uid(self) -> int | None:
        """MAIL-09: the highest UID already processed."""
        value = get_kv(self.s, "inboxLastUid")
        return int(value) if isinstance(value, int | float) else None

    def set_inbox_last_uid(self, uid: int) -> None:
        set_kv(self.s, "inboxLastUid", int(uid))
        self.s.flush()

    def ats_discovered_at(self) -> str | None:
        """ATS-05: when WERA last searched for new boards. None means never."""
        value = get_kv(self.s, "atsDiscoveredAt")
        return value if isinstance(value, str) else None

    def set_ats_discovered_at(self, at: str) -> None:
        """Written whether or not the search found anything, so a provider outage doesn't make
        WERA retry every single run and spend the credits it was trying to save."""
        set_kv(self.s, "atsDiscoveredAt", at)
        self.s.flush()

    def sites_discovered_at(self) -> str | None:
        """SRCH-12: when WERA last looked for new boards. None means never."""
        value = get_kv(self.s, "sitesDiscoveredAt")
        return value if isinstance(value, str) else None

    def set_sites_discovered_at(self, at: str) -> None:
        """Written whether or not anything was found, like `set_ats_discovered_at`: a provider
        outage must not make every later run retry and spend the credits it was saving."""
        set_kv(self.s, "sitesDiscoveredAt", at)
        self.s.flush()

    def review_site(self, domain: str, *, approve: bool) -> list[SiteOption]:
        """SRCH-04: record a person's decision about a board WERA proposed.

        Approving does **two** things, and forgetting the second is the difference between a
        working feature and one that visibly does nothing: it sets `terms_reviewed_on`, *and* it
        adds the domain to the search profile. `usable_sites` requires both — a reviewed board
        the profile doesn't list is still never searched.

        Never creates an entry. A route that could invent a site would be a route that could be
        talked into searching anything.
        """
        from datetime import date

        from ..config import sites_file, write_sites

        file = sites_file()
        found = next((s for s in file.sites if s.domain == domain), None)
        if found is None:
            raise ApiError(404, "NOT_FOUND", f"WERA doesn't know a board called {domain}.")
        if is_linkedin_url(f"https://{domain}") or domain == "linkedin.com":
            raise ApiError(422, "LINKEDIN_BLOCKED", "WERA never searches LinkedIn.")
        if found.terms_reviewed_on or found.declined_on:
            raise ApiError(409, "INVALID_STATE", f"{domain} has already been decided on.")

        today = date.fromisoformat(iso(self.now)[:10])
        updated = found.model_copy(update={"terms_reviewed_on": today} if approve else {"declined_on": today})
        write_sites(file.model_copy(update={"sites": [updated if s.domain == domain else s for s in file.sites]}))

        profile = self.get_profile()
        enabled = [d for d in profile.sites if d != domain]
        if approve:
            enabled.append(domain)
        self.put_profile(profile.model_copy(update={"sites": enabled}))
        return self.sites()

    def rechecked_at(self) -> str | None:
        """SRCH-13: when the lifecycle sweep last ran. None means never."""
        value = get_kv(self.s, "recheckedAt")
        return value if isinstance(value, str) else None

    def set_recheck(self, summary: dict[str, Any]) -> None:
        """Records the sweep and its figures, whether or not it found anything.

        Written unconditionally, for the same reason `set_ats_discovered_at` is: a sweep that
        answered nothing must not make the next request retry it immediately. The figures ride
        along so the Jobs screen can say what left and why, instead of jobs quietly vanishing.
        """
        set_kv(self.s, "recheckedAt", iso(self.now))
        set_kv(self.s, "recheck", {"at": iso(self.now), **summary})
        self.s.flush()

    def last_recheck(self) -> dict[str, Any]:
        value = get_kv(self.s, "recheck")
        return value if isinstance(value, dict) else {}

    def job_questions(self, job_id: str) -> list[str]:
        return list(self._job_row(job_id).questions)

    def set_job_questions(self, job_id: str, questions: list[str]) -> None:
        self._job_row(job_id).questions = questions
        self.s.flush()

    def application_job_id(self, app_id: str) -> str:
        """Which job an application belongs to — the graph's threads are keyed by job."""
        return self._app_row(app_id).job_id

    def drafting_job_ids(self) -> list[str]:
        """Jobs whose application is mid-draft — the candidates for restart recovery (REL-03)."""
        return list(self.s.scalars(select(ApplicationRow.job_id).where(ApplicationRow.status == "drafting")).all())

    def _load_pack(self, app_id: str) -> Pack | None:
        """The pack, or None when there isn't a showable one yet.

        A half-written pack is an expected state, not corruption: the graph writes one document
        per step, and a draft that fails partway is parked as `changes_requested` with the
        reason (see `fail_draft`) while keeping what it managed to build. A dev-server restart
        mid-draft leaves the same thing behind.

        `answers` is the one kind that is genuinely optional — a job with no application
        questions has none to draft — so its absence is an empty list rather than a missing
        pack. The other three are what a pack *is*; without any one of them there is nothing to
        show, and the screen says the draft didn't finish instead of answering 500 for every
        application on it.
        """
        rows = self.s.scalars(select(DocumentRow).where(DocumentRow.application_id == app_id).order_by(DocumentRow.version)).all()
        latest = {r.kind: r.content for r in rows}
        if not all(kind in latest for kind in ("cv", "letter", "ats")):
            return None
        return Pack(
            cv=TailoredCV.model_validate(latest["cv"]),
            letter=CoverLetter.model_validate(latest["letter"]),
            ats=AtsReport.model_validate(latest["ats"]),
            answers=[AnswerItem.model_validate(a) for a in latest.get("answers", {}).get("items", [])],
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
            self._save_document(app_id, kind, content[kind])

    def _app_row(self, app_id: str) -> ApplicationRow:
        row = self.s.get(ApplicationRow, app_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "That application doesn't exist.")
        return row

    def _draft(self, app: ApplicationRow) -> DraftProgress | None:
        """Which document the graph is on, and how far through the pack that is (PERF-03)."""
        if app.status != "drafting":
            return None
        step = app.draft_step if app.draft_step in DRAFT_ORDER else DRAFT_ORDER[0]
        return DraftProgress(step=step, progress=(DRAFT_ORDER.index(step) + 1) / (len(DRAFT_ORDER) + 1))

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
        # Back to drafting. The graph redrafts from Gate 2 with the note as hints; the previous
        # versions of each document stay, so nothing the person saw is lost (G2-02).
        app.status = "drafting"
        app.change_note = note.strip() or None
        app.draft_started_at = iso(self.now)
        app.draft_step = DRAFT_ORDER[0]
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
        # `source_ids` is allowed to be empty by the contract — the guard normally drops an
        # uncited line, but a user-edited one is exempt (G2-02) — so this sorts on "the first
        # source if there is one" rather than indexing and hoping.
        item.bullets.sort(key=lambda b: ids.index(b.source_ids[0]) if b.source_ids and b.source_ids[0] in ids else -1)
        doc.omitted_bullet_ids = [i for i in doc.omitted_bullet_ids if i != bullet_id]

    # ---- Intake runs (SRCH-06…08, PRF-05, UX-01) ----

    def estimate(self, channels: list[str]) -> Estimate:
        from ..config import sites
        from ..sources.firecrawl_search import JSON_SCRAPE_CREDITS, SEARCH_CREDITS, queries_for

        p = self._profile()
        board = "board_search" in channels
        # Counted by the same function the run uses, not re-derived. It used to be
        # `len(titles) * len(locations)`, which stopped being the grid the moment SRCH-11 made it
        # title × board — and COST-05 requires the dialog's arithmetic and the cap's to be the
        # same one. Asking `queries_for` means they cannot drift again.
        queries = len(queries_for(p, sites())) if board else 0
        pages = 10 if board else 0
        stats = get_kv(self.s, "stats")
        return Estimate(
            queries=queries,
            pages=pages,
            # The same rates the cap counts in, from one place, so the dialog can't promise one
            # number while the run enforces another.
            credits=queries * SEARCH_CREDITS + pages * JSON_SCRAPE_CREDITS,
            usd=0.02,
            companies=stats["watchlist"]["companies"] if "watchlist" in channels else 0,
            demo=get_settings().wera_demo_mode,
        )

    def start_runs(self, channels: list[str], *, scheduled: bool = False) -> dict[str, str]:
        """Creates one `intake_runs` row per channel and returns channel → run id (SRCH-08).

        A scheduled run doesn't refuse when the budget is spent the way a person's click does;
        it records the refusal on its own row instead, so tomorrow's run still happens.
        """
        return self._start_runs(channels, scheduled=scheduled)

    def start_runs_for_api(self, channels: list[str]) -> RunIds:
        """What the route returns: just the ids."""
        return RunIds(run_ids=list(self._start_runs(channels, scheduled=False).values()))

    def _start_runs(self, channels: list[str], *, scheduled: bool) -> dict[str, str]:
        self.settle()
        if self.s.scalar(select(func.count()).select_from(IntakeRunRow).where(IntakeRunRow.status == "running")):
            raise ApiError(409, "RUN_ACTIVE", "A search is already running. Wait for it to finish or cancel it.")
        on = self._profile().channels
        enabled = [
            c for c in channels if (on.board_search if c == "board_search" else on.inbox if c == "email_alert" else on.watchlist)
        ]
        if not enabled:
            raise ApiError(422, "NO_CHANNELS", "Turn on at least one channel in your search profile.")
        if not scheduled:
            self._assert_budget()
        ids: dict[str, str] = {}
        for channel in enabled:
            run = IntakeRunRow(
                id=f"run-{self._next_id()}",
                channel=channel,
                status="running",
                started_at=iso(self.now),
                progress=0,
                phase="searching",
                done=0,
                total=0,
                queries=0,
                results=0,
                new_jobs=0,
                credits_used=0,
                credits_saved=0,
                errors=[],
                duration_ms=RUN_MS[channel],
                settled=False,
            )
            self.s.add(run)
            ids[channel] = run.id
        self.s.flush()
        return ids

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
            phase=run.phase,  # type: ignore[arg-type]
            done=run.done,
            total=run.total,
            queries=run.queries,
            results=run.results,
            new_jobs=run.new_jobs,
            credits_used=run.credits_used,
            credits_saved=run.credits_saved,
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
        # An imported CV may carry a date WERA couldn't read (see `Role.start`). It is allowed
        # to arrive that way and not to be stored that way: freshness and the experience
        # sub-score both read these, so a blank start would quietly distort every later score.
        # The path matches what the editor keys its field errors on, so it lands on the row.
        blank = {
            f"{kind}.{i}.start": "Add the month this started, as YYYY-MM"
            for kind, roles in (("experience", cv.experience), ("projects", cv.projects))
            for i, r in enumerate(roles)
            if not r.start
        }
        if blank:
            raise validation_error([{"loc": tuple(path.split(".")), "msg": msg} for path, msg in blank.items()])
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

    # How much of an uploaded document is sent to a model. A CV is one or two pages; anything
    # past this is a portfolio, a publication list or a PDF that extracted badly, and paying to
    # read 60,000 characters of it helps nobody. Truncating is better than refusing: the person
    # gets their roles and skills and fixes the tail by hand.
    CV_TEXT_LIMIT = 24_000

    def parse_cv_text(self, text: str) -> MasterCV:
        """CV-03: an uploaded CV's text, read into the contract. Saves nothing.

        `domain.cv_extract` reads it first, without a model. A conventionally laid out CV comes
        back from there complete, and the `cv.parse` call below never happens — which is most of
        them, and the whole point: section headings, date ranges and bullet markers are not
        things that need a language model, and this is the one document every later search is
        built on. The model is the fallback for layouts the parser declines to guess at.

        The ids come from `domain.cv_build`, never from the model — see that module for why.
        """
        body = text.strip()
        if not body:
            raise ApiError(
                422, "VALIDATION", "That file had no readable text in it. If it's a scan, the text has to be typed in."
            )
        if len(body) < 120:
            raise ApiError(
                422,
                "VALIDATION",
                "That file had almost no text in it — it's probably a scanned image rather than a text PDF. "
                "Export it from your word processor, or paste the text instead.",
            )
        clipped = body[: self.CV_TEXT_LIMIT]
        read = cv_extract.extract(clipped)
        if read.cv is not None:
            logfire.info(
                "cv.parse read without a model ({ambiguous} ambiguous header(s))",
                ambiguous=len(read.ambiguous),
                headers=read.ambiguous,
            )
            parsed = read.cv
        else:
            logfire.info("cv.parse fell back to the model: {reason}", reason=read.decline)
            # PRIV-01 for this task is `scrub_contacts`, not the stored-contact redactor — see
            # `pii.scrub_contacts` for why the usual one is the wrong instrument on an upload.
            document = untrusted("uploaded CV", scrub_contacts(clipped))
            parsed = structured(self.s, "cv.parse", ParsedCv, {"document": document}, redact=False)
        cv = cv_build.to_master_cv(parsed, version=self.cv().version)
        if not cv.experience and not cv.projects and not cv.education:
            raise ApiError(
                422,
                "VALIDATION",
                "WERA couldn't find any roles, projects or education in that file. Check it's the right document, "
                "or import a WERA YAML file instead.",
            )
        return cv

    def get_contact(self) -> Contact:
        return state.contact(self.s)

    def put_contact(self, contact: Contact) -> Contact:
        row = self.s.get(ContactRow, 1)
        if row is None:  # same reasoning as `_profile_row`
            raise ApiError(500, "NOT_SEEDED", "This database has no contact row. Run `wera reset` to rebuild it.")
        row.email, row.phone, row.address = contact.email, contact.phone, contact.address
        return contact

    def get_profile(self) -> SearchProfile:
        return self._profile()

    def put_profile(self, profile: SearchProfile) -> SearchProfile:
        profile.updated_at = iso(self.now)
        self._profile_row().data = profile.model_dump(mode="json")
        return profile

    def generate_profile(self) -> ProfileProposalResult:
        """PRF-01/PRF-04: propose a profile from the master CV and show what would change.

        Saves nothing — the person reviews the diff field by field and then PUTs what they want.
        """
        cv = self.cv()
        current = self._profile()
        proposal = structured(
            self.s,
            "profile.generate",
            ProfileProposal,
            {"cv": cv_text(cv), "current": profile_text(current)},
            context={"cv": cv},
        )
        # The model is told to use the CV's spellings and the schema enforces it; this keeps the
        # order and casing the CV uses, so a proposal never renames a skill.
        by_name = {s.name.casefold(): s.name for s in cv.skills}
        skills = [by_name.get(s.casefold(), s) for s in proposal.skills]
        proposed = SearchProfile(
            titles=proposal.titles,
            skills=skills,
            locations=proposal.locations,
            remote=proposal.remote,
            seniority=proposal.seniority,
            exclude=proposal.exclude,
            # The person's own choices, not the model's: which boards, which countries, which
            # channels. `countries` has to be carried explicitly — it was omitted when SRCH-11
            # added it, and because the field defaults to `[]` the proposal silently emptied it:
            # the diff showed nothing (it isn't a proposed field), and accepting the proposal
            # made SRCH-11 fail open to every reviewed board. A default that means "anywhere" is
            # exactly the wrong thing to arrive at by accident.
            sites=current.sites,
            countries=current.countries,
            recency=proposal.recency,
            channels=current.channels,
            generated_from_cv_version=cv.version,
            updated_at=iso(self.now),
        )
        return ProfileProposalResult(proposed=proposed, diff=profile_diff(current, proposed), cv_version=cv.version)

    # ---- Settings (SET-01…07) ----

    def get_settings(self) -> SettingsResponse:
        self.settle()
        env = get_settings()
        data = self._settings()
        return SettingsResponse.model_validate(
            {
                **data,
                # SET-06: the real figure, from the rows the gateway writes (not a stored number).
                "spentThisWeekUsd": spent_this_week_usd(self.s, self.now),
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
        result = providers.check(provider, env)
        if result.ok and provider == "llm":
            self._save_settings({**self._settings(), "llmLastCheckedAt": iso(self.now)})
        return result


# ---------------------------------------------------------------------------


def _split_doc_id(doc_id: str) -> tuple[str, str]:
    i = doc_id.rfind(".")
    if i < 0:
        raise ApiError(404, "NOT_FOUND", "Unknown document.")
    return doc_id[:i], doc_id[i + 1 :]


def _remove_bullet(doc: TailoredCV, bullet_id: str) -> None:
    for section in doc.sections:
        for item in section.items:
            idx = next((i for i, b in enumerate(item.bullets) if b.source_ids and b.source_ids[0] == bullet_id), -1)
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
