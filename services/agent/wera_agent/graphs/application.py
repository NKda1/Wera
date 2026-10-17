"""One job's journey: Gate 1 → pack → Gate 2 (ARCHITECTURE §2, SPEC §7).

One thread per job, `thread_id = "job:{id}"`, started lazily on the first decision. The two
gates are `interrupt()` calls, which is what makes them real rather than a UI convention: the
thread stops, the process can exit, and the decision still lands on the same state days later
(T-10, REL-03).

The division of labour with `services.store`: the store owns the database state machine (§7.2)
and is what the HTTP routes talk to; the graph owns the long-running work between the gates and
the pause itself. Nodes call the store's own helpers, so there is one implementation of each
transition.

Nodes are plain sync functions. LangGraph runs them in a worker thread, so they can use the
ordinary SQLAlchemy session and the blocking HTTP client without an async rewrite.
"""

from typing import Any, Literal

import logfire
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from ..db.session import transaction
from ..errors import ApiError
from ..services import drafting
from ..services.store import Store

type Gate1Action = Literal["approve", "snooze", "reject"]
type Gate2Action = Literal["approve", "request_changes", "discard"]


class AppState(BaseModel):
    """Small on purpose: the documents live in the database, not in the checkpoint."""

    job_id: str
    application_id: str | None = None
    decision: Gate1Action | None = None
    gate2: Gate2Action | None = None
    note: str | None = None
    attempt: int = 1
    hints: list[str] = []
    failed: str | None = None  # a §9.4 code, when drafting couldn't finish


def thread_id(job_id: str) -> str:
    return f"job:{job_id}"


def config_for(job_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id(job_id)}}


# ---------------------------------------------------------------------------
# Nodes


def gate1(state: AppState) -> dict[str, Any]:
    """G1-02: nothing is drafted until a person decides. Resumed with the action and, for an
    approval, the application the store has already created."""
    answer = interrupt({"gate": 1, "jobId": state.job_id})
    if isinstance(answer, str):
        answer = {"action": answer}
    return {
        "decision": answer.get("action"),
        "application_id": answer.get("applicationId") or state.application_id,
        "note": answer.get("note"),
        "attempt": 1,
        "hints": [],
        "failed": None,
    }


def after_gate1(state: AppState) -> str:
    if state.decision == "approve":
        return "tailor"
    return "snooze_wait" if state.decision == "snooze" else END


def snooze_wait(state: AppState) -> dict[str, Any]:
    """G1-02: a snooze comes back to Gate 1 when the job wakes up. The store already set the
    job's status and wake-up time; the thread just returns to waiting."""
    logfire.info("Job {job_id} snoozed; thread waits at Gate 1 again", job_id=state.job_id)
    return {"decision": None}


def _mark(state: AppState, step: str) -> str:
    """Commits the step on its own, so a polling screen sees the work that is running."""
    app_id = _app(state)
    with transaction() as session:
        Store(session).set_draft_step(app_id, step)
    return app_id


def tailor(state: AppState) -> dict[str, Any]:
    _mark(state, "cv")
    with transaction() as session:
        drafting.step_cv(session, _app(state), note=state.note, hints=state.hints, attempt=state.attempt)
    return {}


def letter(state: AppState) -> dict[str, Any]:
    _mark(state, "letter")
    with transaction() as session:
        drafting.step_letter(session, _app(state), note=state.note)
    return {}


def ats(state: AppState) -> dict[str, Any]:
    """ATSC-02/03: coverage in code, and the decision to re-tailor with hints."""
    _mark(state, "ats")
    with transaction() as session:
        hints = drafting.step_ats(session, _app(state), note=state.note, attempt=state.attempt)
    if hints:
        logfire.info("Re-tailoring {job_id} for coverage, attempt {attempt}", job_id=state.job_id, attempt=state.attempt + 1)
        return {"attempt": state.attempt + 1, "hints": hints}
    return {"hints": []}


def after_ats(state: AppState) -> str:
    return "tailor" if state.hints else "answers"


def answers(state: AppState) -> dict[str, Any]:
    _mark(state, "answers")
    with transaction() as session:
        drafting.step_answers(session, _app(state), note=state.note)
    return {}


def ready(state: AppState) -> dict[str, Any]:
    """The pack is drafted; Gate 2 is now a person's to make."""
    with transaction() as session:
        Store(session).mark_ready(_app(state))
    return {}


def gate2(state: AppState) -> dict[str, Any]:
    """G2-01: approve, ask for changes, or discard. Nothing is sent by WERA either way."""
    answer = interrupt({"gate": 2, "jobId": state.job_id, "applicationId": state.application_id})
    if isinstance(answer, str):
        answer = {"action": answer}
    return {"gate2": answer.get("action"), "note": answer.get("note"), "attempt": 1, "hints": []}


def after_gate2(state: AppState) -> str:
    return "tailor" if state.gate2 == "request_changes" else END


def _app(state: AppState) -> str:
    if not state.application_id:
        raise ApiError(409, "INVALID_STATE", "This thread has no application to draft into.")
    return state.application_id


def build() -> StateGraph:
    g = StateGraph(AppState)
    for name, fn in (
        ("gate1", gate1),
        ("snooze_wait", snooze_wait),
        ("tailor", tailor),
        ("letter", letter),
        ("ats", ats),
        ("answers", answers),
        ("ready", ready),
        ("gate2", gate2),
    ):
        g.add_node(name, fn)

    g.add_edge(START, "gate1")
    g.add_conditional_edges("gate1", after_gate1, {"tailor": "tailor", "snooze_wait": "snooze_wait", END: END})
    g.add_edge("snooze_wait", "gate1")
    g.add_edge("tailor", "letter")
    g.add_edge("letter", "ats")
    g.add_conditional_edges("ats", after_ats, {"tailor": "tailor", "answers": "answers"})
    g.add_edge("answers", "ready")
    g.add_edge("ready", "gate2")
    g.add_conditional_edges("gate2", after_gate2, {"tailor": "tailor", END: END})
    return g


# ---------------------------------------------------------------------------
# Driving the graph


async def advance(job_id: str, resume: dict[str, Any] | None) -> None:
    """Starts the thread if it doesn't exist yet, then resumes it with one gate decision.

    Called from a background task: the HTTP response has already gone, so a failure here must
    land on the application rather than on a request. `failed` is set and the person is told on
    the review screen.
    """
    from .checkpointer import checkpointer

    async with checkpointer() as saver:
        graph = build().compile(checkpointer=saver)
        config = config_for(job_id)
        snapshot = await graph.aget_state(config)
        if snapshot.created_at is None:
            # Lazily started: runs straight to Gate 1 and waits there.
            await graph.ainvoke(AppState(job_id=job_id), config)
        try:
            await graph.ainvoke(Command(resume=resume) if resume is not None else None, config)
        except ApiError as exc:
            await _record_failure(job_id, exc)
        except Exception as exc:  # noqa: BLE001 — a background task must not die silently
            logfire.exception("Drafting {job_id} failed", job_id=job_id)
            await _record_failure(job_id, exc)


async def _record_failure(job_id: str, exc: BaseException) -> None:
    code = getattr(exc, "code", "DRAFT_FAILED")
    message = getattr(exc, "message", None) or "Drafting stopped. Nothing was saved."
    with transaction() as session:
        Store(session).mark_draft_failed(job_id, code, message)
    logfire.warn("Drafting {job_id} stopped: {code}", job_id=job_id, code=code)


async def resume_unfinished() -> int:
    """REL-03: on startup, pick up threads that stopped mid-run.

    A thread with pending `next` nodes and no interrupt was interrupted by a crash, not by a
    person — so it is resumed. A thread waiting at a gate is left alone: that one is waiting for
    the person, not for WERA.
    """
    from .checkpointer import checkpointer

    with transaction() as session:
        job_ids = Store(session).drafting_job_ids()
    if not job_ids:
        return 0

    resumed = 0
    async with checkpointer() as saver:
        graph = build().compile(checkpointer=saver)
        for job_id in job_ids:
            config = config_for(job_id)
            snapshot = await graph.aget_state(config)
            if snapshot.created_at is None or not snapshot.next or snapshot.interrupts:
                continue
            logfire.info("Resuming {job_id} after restart", job_id=job_id)
            try:
                await graph.ainvoke(None, config)
                resumed += 1
            except Exception as exc:  # noqa: BLE001
                await _record_failure(job_id, exc)
    return resumed


async def pending_gate(job_id: str) -> int | None:
    """Which gate the thread is waiting at, if any — for tests and diagnostics."""
    from .checkpointer import checkpointer

    async with checkpointer() as saver:
        graph = build().compile(checkpointer=saver)
        snapshot = await graph.aget_state(config_for(job_id))
        for item in snapshot.interrupts:
            if isinstance(item.value, dict) and "gate" in item.value:
                return int(item.value["gate"])
    return None
