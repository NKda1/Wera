"""The application graph: Gate 1 → pack → Gate 2 (T-10, T-05, ATSC-03, ANS-04).

These drive the graph directly rather than through HTTP, because what matters here is what
survives a restart. Each test builds a fresh checkpointer, so "tear the process down" is a
real teardown of everything the graph was holding.
"""

import asyncio

import pytest
from sqlalchemy import select

from wera_agent.db.models import ApplicationRow, DocumentRow
from wera_agent.db.session import transaction
from wera_agent.domain.questions import kind
from wera_agent.graphs import application as graph
from wera_agent.llm import demo
from wera_agent.schemas.pack import AnswerItem, CoverLetter, TailoredCV
from wera_agent.services.store import Store

JOB = "job-fernhill-mle"


def run(coro):
    """Each call is its own event loop: nothing is shared between the "processes"."""
    return asyncio.run(coro)


@pytest.fixture
def approved(client):
    """A Gate 1 approval saved the way the route saves it, with the thread not yet advanced."""

    def decide(job_id: str = JOB) -> str:
        with transaction() as session:
            store = Store(session)
            from wera_agent.schemas.api import DecisionIn

            store.decide(job_id, DecisionIn(action="approve"))
            return store.started_application_id  # type: ignore[return-value]

    return decide


def status_of(app_id: str) -> str:
    with transaction() as session:
        return Store(session)._app_row(app_id).status


def documents(app_id: str) -> set[str]:
    with transaction() as session:
        rows = session.scalars(select(DocumentRow).where(DocumentRow.application_id == app_id)).all()
        return {r.kind for r in rows}


class TestGatesSurviveARestart:
    def test_gate_1_is_still_pending_after_a_restart(self, client):
        """T-10, first half: a job sent to Gate 1 waits there across processes."""
        run(graph.advance(JOB, None))
        assert run(graph.pending_gate(JOB)) == 1
        # A whole new process: new loop, new checkpointer, new compiled graph.
        assert run(graph.pending_gate(JOB)) == 1

    def test_approving_after_a_restart_drafts_the_pack(self, client, approved):
        app_id = approved()
        run(graph.advance(JOB, None))  # the thread reaches Gate 1 and stops
        assert run(graph.pending_gate(JOB)) == 1
        assert status_of(app_id) == "drafting"

        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))

        assert documents(app_id) == {"cv", "letter", "ats", "answers"}
        assert status_of(app_id) == "ready_for_review"

    def test_gate_2_is_still_pending_after_a_restart(self, client, approved):
        """T-10, second half: the same holds for the gate that matters most."""
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        assert run(graph.pending_gate(JOB)) == 2

        # Tear everything down and come back to it.
        assert run(graph.pending_gate(JOB)) == 2
        run(graph.advance(JOB, {"action": "approve"}))
        assert run(graph.pending_gate(JOB)) is None  # the thread is finished

    def test_a_rejection_ends_the_thread(self, client):
        run(graph.advance(JOB, None))
        run(graph.advance(JOB, {"action": "reject"}))
        assert run(graph.pending_gate(JOB)) is None

    def test_a_snooze_comes_back_to_gate_1(self, client):
        run(graph.advance(JOB, None))
        run(graph.advance(JOB, {"action": "snooze"}))
        # G1-02: the thread is waiting at Gate 1 again, ready for the job to wake up.
        assert run(graph.pending_gate(JOB)) == 1


class TestRequestChanges:
    def test_request_changes_redrafts_from_gate_2(self, client, approved):
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        before = _versions(app_id, "cv")

        run(graph.advance(JOB, {"action": "request_changes", "note": "Keep it to one page"}))

        # G2-02: a new version of the CV, with the old one still there.
        assert _versions(app_id, "cv") > before
        assert status_of(app_id) == "ready_for_review"
        assert run(graph.pending_gate(JOB)) == 2


def _versions(app_id: str, kind_: str) -> int:
    with transaction() as session:
        rows = session.scalars(select(DocumentRow).where(DocumentRow.application_id == app_id, DocumentRow.kind == kind_)).all()
        return len(rows)


class TestRestartRecovery:
    def test_a_thread_waiting_at_a_gate_is_left_alone(self, client, approved):
        """REL-03: waiting for a person is not the same as crashed."""
        approved()
        run(graph.advance(JOB, None))
        assert run(graph.resume_unfinished()) == 0
        assert run(graph.pending_gate(JOB)) == 1

    def test_nothing_to_resume_when_no_application_is_drafting(self, client):
        assert run(graph.resume_unfinished()) == 0

    def test_a_thread_that_died_mid_draft_is_picked_up(self, client, approved, monkeypatch):
        """REL-03, the case that matters: the process went away while the letter was being
        written. The application is still drafting, the thread still has work pending, and no
        person is being waited on — so it carries on where it stopped."""
        app_id = approved()
        real = demo.respond

        class Crash(BaseException):
            """Not an Exception: `advance` must not treat this as a drafting failure."""

        def die_on_letter(task, variables, context, demo_input=None):
            if task == "letter.write":
                raise Crash
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", die_on_letter)
        with pytest.raises(Crash):
            run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))

        # The CV survived; the letter never happened; the application is still drafting.
        assert documents(app_id) == {"cv"}
        assert status_of(app_id) == "drafting"
        assert run(graph.pending_gate(JOB)) is None  # stopped mid-run, not at a gate

        # WERA restarts.
        monkeypatch.setattr(demo, "respond", real)
        assert run(graph.resume_unfinished()) == 1
        assert documents(app_id) == {"cv", "letter", "ats", "answers"}
        assert status_of(app_id) == "ready_for_review"


class TestCitationGuard:
    def test_a_fabricated_skill_is_dropped_and_flagged(self, client, approved, monkeypatch):
        """T-05: a line the CV can't back doesn't reach the review screen unmarked."""
        real = demo.respond

        def fabricate(task, variables, context, demo_input=None):
            out = real(task, variables, context, demo_input)
            if task == "cv.tailor":
                # Cites a real CV line, but claims something it doesn't say.
                first = out["experience"][0]["bullets"][0]
                first["text"] = "Ran the Kubernetes platform for 40 teams."
            return out

        monkeypatch.setattr(demo, "respond", fabricate)
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))

        with transaction() as session:
            doc = Store(session).load_document(app_id, "cv", TailoredCV)
        texts = [b.text for s in doc.sections for i in s.items for b in i.bullets]
        assert "Ran the Kubernetes platform for 40 teams." not in texts
        # The drop is shown, not swallowed.
        assert doc.guard.violations
        assert any("Left out" in v.detail for v in doc.guard.violations)

    def test_a_quote_from_the_job_post_is_not_treated_as_a_claim(self, client, approved):
        """LTR-03: the letter quotes the employer's own words, numbers and all. Those are not
        claims about the applicant, so the guard must not drop the paragraph carrying them."""
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        with transaction() as session:
            letter = Store(session).load_document(app_id, "letter", CoverLetter)
        assert letter.guard.pass_, [v.detail for v in letter.guard.violations]
        assert len(letter.paragraphs) >= 3  # LTR-01: nothing was dropped
        if letter.company_fact.quote:
            assert any(letter.company_fact.quote in p.text for p in letter.paragraphs)

    def test_a_line_the_cv_backs_is_kept(self, client, approved):
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        with transaction() as session:
            doc = Store(session).load_document(app_id, "cv", TailoredCV)
        assert doc.guard.pass_
        assert all(b.source_ids for s in doc.sections for i in s.items for b in i.bullets)


class TestLetterRedraft:
    """LTR-01: a letter is 3 or 4 paragraphs, so one that the guard cuts below three is asked
    for again rather than shipped short."""

    @staticmethod
    def _spoil(count: int):
        """A responder that makes `count` letter paragraphs unsupportable on the first attempt."""
        real = demo.respond
        calls: list[str] = []

        def respond(task, variables, context, demo_input=None):
            out = real(task, variables, context, demo_input)
            if task == "letter.write":
                calls.append(variables["note"])
                if len(calls) == 1:
                    for para in out["paragraphs"][:count]:
                        para["text"] = f"{para['text']} I ran the Kubernetes platform for 40 teams."
            return out

        return respond, calls

    def test_a_letter_cut_below_three_paragraphs_is_redrafted(self, client, approved, monkeypatch):
        respond, calls = self._spoil(2)
        monkeypatch.setattr(demo, "respond", respond)
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))

        assert len(calls) == 2, "the short letter should have been asked for again"
        # The second attempt was told what went wrong, in terms of its own output.
        assert "citation check" in calls[1]
        assert "paragraph" in calls[1]

        with transaction() as session:
            letter = Store(session).load_document(app_id, "letter", CoverLetter)
        assert len(letter.paragraphs) >= 3
        assert letter.guard.pass_

    def test_one_dropped_paragraph_out_of_four_is_not_redrafted(self, client, approved, monkeypatch):
        """Dropping one from four still leaves a letter worth sending, so it isn't paid for twice."""
        respond, calls = self._spoil(1)
        monkeypatch.setattr(demo, "respond", respond)
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        with transaction() as session:
            letter = Store(session).load_document(app_id, "letter", CoverLetter)
        assert len(calls) == 1
        assert len(letter.paragraphs) >= 3
        # The drop is still shown to the person.
        assert letter.guard.violations

    def test_a_letter_that_stays_short_keeps_the_fuller_attempt_and_shows_why(self, client, approved, monkeypatch):
        """Two tries is the cap. Rather than loop, WERA keeps the better letter with its
        violations visible, so the person sees the problem at Gate 2."""
        real = demo.respond
        calls: list[int] = []

        def always_bad(task, variables, context, demo_input=None):
            out = real(task, variables, context, demo_input)
            if task == "letter.write":
                calls.append(1)
                for para in out["paragraphs"][: 3 if len(calls) == 1 else 2]:
                    para["text"] = f"{para['text']} I ran the Kubernetes platform for 40 teams."
            return out

        monkeypatch.setattr(demo, "respond", always_bad)
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))

        assert len(calls) == 2  # asked twice, never a third time
        with transaction() as session:
            letter = Store(session).load_document(app_id, "letter", CoverLetter)
        assert len(letter.paragraphs) == 2  # the fuller of the two attempts
        assert not letter.guard.pass_
        assert letter.guard.violations


class TestAtsRetryCap:
    def test_tailoring_is_not_retried_for_ever(self, client, approved, monkeypatch):
        """ATSC-03: at most two extra attempts, however poor the coverage stays."""
        attempts: list[int] = []
        real = demo.respond

        def count(task, variables, context, demo_input=None):
            if task == "cv.tailor":
                attempts.append(1)
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", count)

        # The retry condition is coverage below the threshold *and* a missing keyword the CV
        # does back (§8.3) — so both have to hold for the loop to run at all.
        from wera_agent.nodes import ats as ats_node

        def stuck(doc, **kwargs):
            report = ats_node.build_ats(doc, **kwargs)
            report.coverage = 0.1
            for keyword in report.keywords:
                keyword.matched, keyword.in_master_cv = False, True
            return report

        monkeypatch.setattr("wera_agent.services.drafting.build_ats", stuck)

        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        assert len(attempts) == 3  # the first pass plus two retries, then it stops
        assert status_of(app_id) == "ready_for_review"


class TestAnswerPack:
    def test_sensitive_questions_are_never_drafted(self, client, approved, monkeypatch):
        """ANS-04: the model is not asked, and the answer is left for the person."""
        asked: list[str] = []
        real = demo.respond

        def watch(task, variables, context, demo_input=None):
            if task == "answers.draft":
                asked.append(variables["questions"])
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", watch)
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))

        with transaction() as session:
            rows = session.scalars(
                select(DocumentRow).where(DocumentRow.application_id == app_id, DocumentRow.kind == "answers")
            ).all()
            items = [AnswerItem.model_validate(a) for a in rows[-1].content["items"]]

        sensitive = [a for a in items if a.sensitive]
        assert sensitive, "the demo job has sensitive questions to protect"
        for item in sensitive:
            assert item.answer is None
            assert kind(item.question) == "sensitive"
            # The question text never reached the prompt.
            assert all(item.question not in prompt for prompt in asked)

    def test_an_answer_with_no_evidence_is_left_null(self, client, approved):
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        with transaction() as session:
            rows = session.scalars(
                select(DocumentRow).where(DocumentRow.application_id == app_id, DocumentRow.kind == "answers")
            ).all()
            items = [AnswerItem.model_validate(a) for a in rows[-1].content["items"]]
        for item in items:
            # ANS-02/03: an answer either cites the CV, is a link copied from it, or is null.
            assert item.answer is None or item.source_ids or kind(item.question) == "links"


class TestProgress:
    def test_the_draft_step_follows_the_graph(self, client, approved, monkeypatch):
        """PERF-03: the review screen's progress comes from the node that is running."""
        seen: list[tuple[str, str | None]] = []
        real = demo.respond

        def record(task, variables, context, demo_input=None):
            with transaction() as session:
                row = session.scalars(select(ApplicationRow).where(ApplicationRow.job_id == JOB)).first()
                seen.append((task, row.draft_step if row else None))
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", record)
        app_id = approved()
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        # Whatever the model is asked, the step shown is the document being built.
        steps = dict(seen)
        assert steps["cv.tailor"] == "cv"
        assert steps["letter.write"] == "letter"
        assert steps["answers.draft"] == "answers"
        with transaction() as session:
            assert Store(session)._app_row(app_id).draft_step is None
