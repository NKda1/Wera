"""MATCH-11…13 (15 Nov): learning from a person's Gate 1 decisions.

Before this, every rejection was saved with its reason and nothing read it back.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from wera_agent.clock import iso, now_ms
from wera_agent.db.models import DecisionRow
from wera_agent.schemas.api import ApplicationPatchIn, DecisionIn
from wera_agent.schemas.jobs import Salary
from wera_agent.services import learning


def decided(store, title: str, decision: str = "reject", code: str | None = "not_my_field", *, cv_version=None, **features):
    """A Gate 1 decision as `Store.decide` records it, without needing the job to exist."""
    from wera_agent.domain import filters

    store.s.add(
        DecisionRow(
            job_id=f"job-{abs(hash((title, decision, code, len(store.s.new)))) % 10**10}",
            gate=1,
            decision=decision,
            decided_at=iso(now_ms()),
            reason_code=code if decision == "reject" else None,
            cv_version=store.cv().version if cv_version is None else cv_version,
            features={"title": title, "tokens": sorted(filters.title_tokens(title)), **features},
        )
    )
    store.s.flush()


def job(title: str, salary: Salary | None = None):
    return SimpleNamespace(title=title, salary=salary)


def learned(store):
    return learning.learn(store.s, store.cv().version, store.get_profile())


class TestRecording:
    def test_a_rejection_keeps_its_code_its_note_and_what_the_job_was(self, store):
        target = store.list_jobs("new")[0]
        store.decide(target.id, DecisionIn(action="reject", reason_code="not_my_field", reason="Mostly sales calls"))
        row = store.s.scalars(select(DecisionRow).where(DecisionRow.job_id == target.id)).one()
        assert (row.reason_code, row.note, row.cv_version) == ("not_my_field", "Mostly sales calls", store.cv().version)
        assert row.fit_total == target.fit.total
        assert row.features["title"] == target.title and row.features["scores"]["core"] == target.fit.core.score

    def test_a_preset_sent_as_text_is_coded_not_noted(self, store):
        """What an older tab sends: the chip's words as the reason."""
        target = store.list_jobs("new")[0]
        store.decide(target.id, DecisionIn(action="reject", reason="Wrong level"))
        row = store.s.scalars(select(DecisionRow).where(DecisionRow.job_id == target.id)).one()
        assert (row.reason_code, row.note) == ("wrong_level", None)

    def test_decisions_outlive_a_new_cv(self, store):
        decided(store, "Security Engineer")
        store.reset_job_world()
        assert store.s.scalars(select(DecisionRow)).first() is not None


class TestAPackThrownAway:
    """19 Nov: discarding a pack at Gate 2 changed the job's status and left no decision, so
    learning never heard the person's "no" (18 Nov review: 0 discards recorded)."""

    def pack(self, store, status: str):
        target = store.list_jobs("new")[0]
        job = store._job(store._job_row(target.id))
        store.create_application("app-t-1", job, status=status, created_at=iso(now_ms()))
        return target

    def test_a_discarded_pack_is_a_gate_2_decision_with_what_the_job_was(self, store):
        target = self.pack(store, "ready_for_review")
        store.patch_application("app-t-1", ApplicationPatchIn(status="discarded", reason_code="not_my_field"))
        row = store.s.scalars(select(DecisionRow).where(DecisionRow.job_id == target.id)).one()
        assert (row.gate, row.decision, row.reason_code) == (2, "discard", "not_my_field")
        assert row.features["title"] == target.title and row.fit_total == target.fit.total
        assert store._job(store._job_row(target.id)).status == "rejected"

    def test_a_cancelled_draft_is_recorded_but_not_a_no(self, store):
        target = self.pack(store, "drafting")
        store.patch_application("app-t-1", ApplicationPatchIn(status="discarded", reason_code="not_my_field"))
        row = store.s.scalars(select(DecisionRow).where(DecisionRow.job_id == target.id)).one()
        assert (row.gate, row.decision, row.reason_code) == (2, "cancel", None)
        assert store._job(store._job_row(target.id)).status == "new"
        assert learned(store).decisions == 0

    def test_a_discard_about_the_work_counts_against_its_words(self, store):
        target = self.pack(store, "ready_for_review")
        store.patch_application("app-t-1", ApplicationPatchIn(status="discarded", reason_code="not_my_field"))
        got = learned(store)
        assert got.decisions == 1
        assert got.rejected_titles == [(target.title, "not_my_field")]


class TestRanking:
    def test_three_rejections_as_not_my_field_move_a_like_job_down_and_say_why(self, store):
        for title in ("Security Engineer", "Application Security Engineer", "Security Analyst"):
            decided(store, title)
        moved = learning.adjust(job("Cloud Security Engineer"), learned(store))
        assert moved is not None and moved.adjustment < 0
        assert moved.reason == "Ranked lower: like 3 you rejected as not my field (security)"

    def test_the_shift_is_capped(self, store):
        for i in range(30):
            decided(store, f"Security Specialist {i}")
        moved = learning.adjust(job("Security Specialist"), learned(store))
        assert moved.adjustment == -learning.MAX_SHIFT

    def test_approvals_offset_rejections(self, store):
        for title in ("Security Engineer", "Security Analyst"):
            decided(store, title)
        for title in ("Security Platform Engineer", "Security Tooling Engineer"):
            decided(store, title, "approve")
        assert learning.adjust(job("Security Engineer"), learned(store)) is None

    def test_one_rejection_moves_nothing(self, store):
        decided(store, "Security Engineer")
        assert learning.adjust(job("Security Engineer"), learned(store)) is None

    def test_a_target_titles_own_words_are_never_learned_against(self, store):
        """They asked for those words; every job they reject carries them."""
        from wera_agent.domain.filters import title_tokens

        target_word = sorted(title_tokens(store.get_profile().titles[0]))[0]
        for i in range(4):
            decided(store, f"{target_word} Role {i}")
        assert target_word not in learned(store).words

    def test_pay_rejected_as_too_low_moves_lower_paid_jobs(self, store):
        decided(
            store, "Data Analyst", code="pay_too_low", salary={"min": 24000, "max": 28000, "currency": "GBP", "period": "year"}
        )
        moved = learning.adjust(
            job("Platform Developer", Salary(min=22000, max=26000, currency="GBP", period="year")), learned(store)
        )
        assert moved is not None and "pay" in moved.reason

    def test_the_shortlist_order_uses_it_and_turning_it_off_restores_the_plain_order(self, store):
        from wera_agent.domain.filters import title_tokens

        plain = store.list_jobs("new")
        targets = set().union(*(title_tokens(t) for t in store.get_profile().titles))
        top = next(j for j in plain if title_tokens(j.title) - targets)
        word = sorted(title_tokens(top.title) - targets)[0]
        for i in range(4):
            decided(store, f"{word} Specialist {i}")
        ranked = store.list_jobs("new")
        moved = next(j for j in ranked if j.id == top.id)
        assert moved.learned is not None and moved.learned.adjustment < 0 and word in moved.learned.reason
        assert moved.fit.total == top.fit.total  # the total is never touched (MATCH-04)
        store._save_settings({**store._settings(), "learnedRanking": False})
        assert [j.id for j in store.list_jobs("new")] == [j.id for j in plain]
        assert all(j.learned is None for j in store.list_jobs("new"))


class TestSuggestions:
    def _security(self, store, n: int = 3):
        for title in ["Security Engineer", "Application Security Engineer", "Security Analyst", "Security Lead"][:n]:
            decided(store, title)

    def test_three_rejections_suggest_an_exclusion_and_two_do_not(self, store):
        self._security(store, 2)
        assert not [s for s in store.learning_suggestions().items if s.kind == "exclude_term"]
        self._security(store, 3)
        (suggestion,) = [s for s in store.learning_suggestions().items if s.kind == "exclude_term"]
        assert suggestion.value == "Security" and suggestion.id == "exclude_term:security"

    def test_accepting_it_changes_the_profile_and_narrows_the_searches(self, store):
        self._security(store)
        profile = store.accept_suggestion("exclude_term:security")
        assert "Security" in profile.exclude
        planned, _ = store.search_queries(refresh=False, probe=False)
        assert planned and all('-intitle:"Security"' in q.q for q in planned)
        assert not [s for s in store.learning_suggestions().items if s.kind == "exclude_term"]

    def test_a_dismissed_suggestion_stays_dismissed(self, store):
        self._security(store)
        store.dismiss_suggestion("exclude_term:security")
        self._security(store, 4)
        assert "exclude_term:security" not in {s.id for s in store.learning_suggestions().items}

    def test_an_approval_vetoes_the_exclusion(self, store):
        self._security(store, 4)
        decided(store, "Security Platform Engineer", "approve")
        assert "exclude_term:security" not in {s.id for s in store.learning_suggestions().items}

    def test_titles_approved_outside_the_targets_suggest_a_title(self, store):
        for title in ("Graduate Data Engineer", "Graduate Data Engineer - London"):
            decided(store, title, "approve")
        ids = {s.id for s in store.learning_suggestions().items}
        assert any(i.startswith("add_title:") for i in ids)

    def test_places_brought_back_from_hiding_suggest_a_location(self, store):
        for _ in range(2):
            decided(store, "Junior Developer", "unhide", city="Bristol", hiddenReason="Location isn't in your search: Bristol")
        assert "add_location:bristol" in {s.id for s in store.learning_suggestions().items}

    def test_an_unknown_suggestion_is_a_404(self, store):
        from wera_agent.errors import ApiError

        with pytest.raises(ApiError) as raised:
            store.accept_suggestion("exclude_term:nothing")
        assert raised.value.status == 404


class TestOverHttp:
    def test_the_routes_answer(self, api):
        body = api.ok("GET", "/learning/suggestions")
        assert body["items"] == [] and "decisions" in body


class TestTitlesWeraNearlyMatched:
    """20 Nov: "Private Equity Associate" sat at 0.72 by meaning against the 0.80 a target title
    needs, so a finance CV's near misses kept landing in Upskills and never on the list."""

    def _signal(self, store, i: int, title: str, company: str, similarity: float = 0.78) -> None:
        from wera_agent.db.models import JobSignalRow

        store.s.add(
            JobSignalRow(
                key=f"sig-{i}", url=f"https://x.example/{i}", job_id=None, title=title, company=company, source="lead",
                similarity=similarity, keywords=[], education=None, job_hash=None, seen_at="2026-11-20T09:00:00.000Z",
            )
        )  # fmt: skip
        store.s.flush()

    def test_a_title_nearly_matched_from_three_employers_is_offered(self, store):
        for i, firm in enumerate(["Apax", "Permira", "Cinven"]):
            self._signal(store, i, "Private Equity Associate", firm)
        offered = [s for s in store.learning_suggestions().items if s.kind == "add_title"]
        assert [s.value for s in offered] == ["Private Equity Associate"] and offered[0].evidence == 3

    def test_two_employers_or_a_distant_title_is_not_enough(self, store):
        self._signal(store, 10, "Private Equity Associate", "Apax")
        self._signal(store, 11, "Private Equity Associate", "Permira")
        for i, firm in enumerate(["A", "B", "C"]):
            self._signal(store, 20 + i, "Procurement Analyst", firm, similarity=0.6)
        assert not [s for s in store.learning_suggestions().items if s.kind == "add_title"]
