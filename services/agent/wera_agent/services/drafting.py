"""The four steps that turn an approved job into a pack.

One implementation, two callers: the application graph runs these as separate nodes, so a
person sees progress and a crash resumes from the step it reached; the demo seed runs them
straight through to fill a ready-for-review application.

Each step is its own unit of work. A step saves what it produced before the next one starts,
which is why a half-drafted pack is still a readable pack.
"""

from dataclasses import replace

from sqlalchemy.orm import Session

from ..config import wera
from ..errors import ApiError
from ..nodes.answers import answers as draft_answers
from ..nodes.ats import build_ats, reconcile_skills
from ..nodes.tailor import letter as write_letter
from ..nodes.tailor import tailor as tailor_cv
from ..schemas.pack import TailoredCV
from ..sources.greenhouse import fetch_questions
from .store import Store


def step_cv(session: Session, app_id: str, *, note: str | None) -> None:
    """Select the lines, then cover the job's keywords from the CV's own skills (ATSC-02/03).

    Reconciling here rather than in the ATS step is what makes the ATS report a *measurement*
    again: by the time it runs, the skills line is already final.
    """
    store = Store(session)
    inp = store.draft_input(app_id, note=note)
    doc = tailor_cv(session, inp)
    doc = reconcile_skills(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, threshold=wera().ats_threshold)
    store.save_document(app_id, "cv", doc)


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
    report = build_ats(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, job=inp.job, attempt=1)
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
