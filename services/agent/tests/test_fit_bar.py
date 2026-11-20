"""MATCH-01 after scoring (19 Nov): a job scored under `surface.min_fit` is hidden, not listed.

On 18 Nov, 30 of the 59 jobs on the list scored under 5 — 15 of them one agency's reposts at 2–3.6.
They are hidden now with the score as the reason, Unhide brings one back, and only jobs at the
bar or above count towards the 8 a search aims for.
"""

import pytest

from wera_agent.config import wera
from wera_agent.db.session import transaction
from wera_agent.domain import filters
from wera_agent.graphs import intake
from wera_agent.schemas.api import DecisionIn
from wera_agent.services.store import Store, save_job

from .test_intake_graph import job


@pytest.fixture
def bar(monkeypatch):
    def set_to(value: float) -> None:
        monkeypatch.setattr(wera().surface, "min_fit", value)

    return set_to


def status(store: Store, job_id: str) -> tuple[str, str | None]:
    found = store._job(store._job_row(job_id))
    return found.status, found.hidden_reason


class TestTheBar:
    def test_a_job_under_the_bar_is_hidden_with_its_score_and_one_at_it_is_kept(self, store, bar):
        bar(4)
        low, high = store.list_jobs("new")[:2]
        under = store.fit_bar([(low.id, 3.2), (high.id, 4.0)])
        assert [j.id for j, _ in under] == [low.id]
        assert status(store, low.id) == ("hidden", "Fit 3.2, below your bar of 4")
        assert status(store, high.id)[0] == "new"

    def test_a_job_the_person_brought_back_stays_back(self, store, bar):
        bar(4)
        low = store.list_jobs("new")[0]
        store.fit_bar([(low.id, 3.0)])
        store.decide(low.id, DecisionIn(action="unhide"))
        assert store.fit_bar([(low.id, 3.0)]) == []
        assert status(store, low.id) == ("new", None)

    def test_a_job_being_applied_for_is_never_hidden(self, store, bar):
        bar(4)
        target = store.list_jobs("new")[0]
        save_job(store.s, store._job(store._job_row(target.id)).model_copy(update={"status": "in_application"}))
        assert store.fit_bar([(target.id, 1.0)]) == []

    def test_a_bar_of_zero_is_off(self, store, bar):
        bar(0)
        assert store.fit_bar([(store.list_jobs("new")[0].id, 0.5)]) == []

    def test_the_filters_never_bring_a_fit_hidden_job_back(self, store, bar):
        """`refilter_hidden` brings back what today's filters keep. A job hidden for its score
        passes the filters — that is how it got scored — so it must be left alone."""
        bar(4)
        low = store.list_jobs("new")[0]
        store.fit_bar([(low.id, 2.0)])
        assert low.id not in {j.id for j in store.refilter_hidden(apply=True)}
        assert status(store, low.id)[1].startswith(filters.FIT_BELOW)

    def test_the_list_saved_before_the_bar_can_be_put_under_it(self, store, bar):
        listed = store.list_jobs("new")
        bar(max(j.fit.total for j in listed) + 0.1)
        assert len(store.apply_fit_bar(apply=False)) == len(listed)
        assert store.list_jobs("new") != []  # a dry run hides nothing
        store.apply_fit_bar(apply=True)
        assert store.list_jobs("new") == []


class TestASearchReadsOn:
    def test_a_job_scored_under_the_bar_neither_counts_nor_is_new_on_the_list(self, client, bar):
        bar(10.1)  # above any score, so whatever the demo scorer says, the job is under it
        judged = intake.judge([job()], now=intake.now_ms(), only_these=True)
        assert judged.scored == 1
        assert judged.surfaced == 0
        assert judged.visible_new == set()
        with transaction() as session:
            found = Store(session)._job(Store(session)._job_row("job-a"))
        assert found.status == "hidden" and found.hidden_reason.startswith(filters.FIT_BELOW)

    def test_a_job_over_the_bar_counts(self, client, bar):
        bar(0.1)
        judged = intake.judge([job()], now=intake.now_ms(), only_these=True)
        assert (judged.surfaced, judged.visible_new) == (1, {"job-a"})
