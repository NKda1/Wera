"""The four steps that turn an approved job into a pack.

One implementation, two callers: the application graph runs these as separate nodes, so a
person sees progress and a crash resumes from the step it reached; the demo seed runs them
straight through to fill a ready-for-review application.

Each step is its own unit of work. A step saves what it produced before the next one starts,
which is why a half-drafted pack is still a readable pack.
"""

from dataclasses import replace

import logfire
from sqlalchemy.orm import Session

from ..config import wera
from ..errors import ApiError
from ..llm.demo import PackInput
from ..nodes.analyse import analyse as analyse_job
from ..nodes.answers import answers as draft_answers
from ..nodes.ats import build_ats, reconcile_skills
from ..nodes.tailor import letter as write_letter
from ..nodes.tailor import tailor as tailor_cv
from ..schemas.pack import CoverLetter, JobAnalysis, TailoredCV
from ..sources.greenhouse import fetch_questions
from .store import Store


def step_cv(session: Session, app_id: str, *, note: str | None) -> None:
    """Select the lines, then cover the job's keywords from the CV's own skills (ATSC-02/03).

    Reconciling here rather than in the ATS step is what makes the ATS report a *measurement*
    again: by the time it runs, the skills line is already final.
    """
    store = Store(session)
    inp = store.draft_input(app_id, note=note)
    analysis = _analyse(session, store, app_id, inp)
    doc = tailor_cv(session, inp)
    doc = reconcile_skills(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, threshold=wera().ats_threshold)
    # Both saved after the model calls, never between them: a save takes SQLite's one write lock,
    # and saving the analysis first held it through the tailoring call.
    if analysis is not None:
        store.save_document(app_id, "analysis", analysis)
    store.save_document(app_id, "cv", doc)


def _analyse(session: Session, store: Store, app_id: str, inp: PackInput) -> JobAnalysis | None:
    """The job-description rubric, once per application (`nodes.analyse`), or None to keep what
    the application already has.

    First, because the letter is written from it; and only once, because a redraft is a change
    to the documents, not to the job — so it reuses the analysis it already has.

    A failure here doesn't stop the pack. The analysis is context the letter draws on when it is
    there, and a letter without it is the letter WERA wrote before the rubric existed: weaker on
    the company paragraph, not wrong. Failing the whole draft over it would trade a good CV for
    a better sentence.
    """
    if store.has_document(app_id, "analysis"):
        return None
    try:
        return analyse_job(session, inp.job, inp.evidence)
    except ApiError as exc:
        logfire.warn("Couldn't analyse {job_id} for its pack: {code}", job_id=inp.job.id, code=exc.code)
        return None


def step_letter(session: Session, app_id: str, *, note: str | None) -> None:
    store = Store(session)
    inp = store.draft_input(app_id, note=note)
    doc = store.load_document(app_id, "cv", TailoredCV)
    store.save_document(app_id, "letter", write_letter(session, _with_doc(inp, doc), doc))


def step_ats(session: Session, app_id: str, *, note: str | None) -> None:
    """Saves the ATS report. Purely a measurement now — `step_cv` has already done the covering,
    so there is nothing here that can send the draft back for another pass."""
    store = Store(session)
    inp = store.draft_input(app_id, note=note)
    doc = store.load_document(app_id, "cv", TailoredCV)
    letter = store.load_document(app_id, "letter", CoverLetter)
    report = build_ats(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, job=inp.job, letter=letter)
    store.save_document(app_id, "ats", report)


def step_answers(session: Session, app_id: str, *, note: str | None) -> None:
    store = Store(session)
    inp = store.draft_input(app_id, note=note)
    questions = _questions(store, inp.job)
    doc = store.load_document(app_id, "cv", TailoredCV)
    store.save_answers(app_id, draft_answers(session, replace(_with_doc(inp, doc), questions=questions)))


def _questions(store: Store, job) -> list[str]:  # noqa: ANN001 — a Job
    """ANS-01: a Greenhouse job's questions come from its board; anything else uses what intake
    already found. A live fetch is cached on the job, so a redraft doesn't ask again."""
    stored = list(store.job_questions(job.id))
    if stored or job.ats is None or job.ats.vendor != "greenhouse":
        return stored
    try:
        found = fetch_questions(job)
    except ApiError:
        return stored  # demo mode, or a host WERA won't fetch
    if found:
        store.set_job_questions(job.id, found)
    return found


def draft_pack(session: Session, app_id: str, *, note: str | None = None, ready: bool = True) -> None:
    """All four steps in order. Used by the demo seed; the graph runs the same steps one node at
    a time. Straight through — the ATS retry loop that used to wrap this is gone (see
    `nodes.ats.reconcile_skills`)."""
    store = Store(session)
    store.set_draft_step(app_id, "cv")
    step_cv(session, app_id, note=note)
    store.set_draft_step(app_id, "letter")
    step_letter(session, app_id, note=note)
    store.set_draft_step(app_id, "ats")
    step_ats(session, app_id, note=note)
    store.set_draft_step(app_id, "answers")
    step_answers(session, app_id, note=note)
    if ready:
        store.mark_ready(app_id)
    else:
        # An application seeded further along its life already has its status; it just isn't
        # drafting any more.
        store.clear_draft_step(app_id)


def _with_doc(inp, doc: TailoredCV):  # noqa: ANN001, ANN202 — a PackInput dataclass
    return replace(inp, doc=doc)
