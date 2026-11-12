"""MATCH-05 and SRCH-26: scoring is handed out a search's worth at a time, best first.

A job takes about eleven seconds, so the forty a run used to work through was seven minutes
during which the shortlist showed nothing new — the broad search finished in the first quarter of
that and then sat unreadable. A run now scores up to `surface.max` (12) and leaves the rest in the
pool, where it is listed and waiting for "Find more".

What these cover is the arithmetic, because that is where it can go quietly wrong: the batch
boundary, the ordering across batches, and the day's cap — which only became load-bearing when
the allowance started going out in instalments.
"""

import pytest
from sqlalchemy import select

from wera_agent.clock import iso, now_ms
from wera_agent.config import wera
from wera_agent.db.models import IntakeRunRow, LlmCallRow
from wera_agent.db.session import transaction
from wera_agent.graphs import intake
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.services.store import Store
from wera_agent.sources.harvest import BATCH

NOW = now_ms()


def job(i: int) -> Job:
    """One plausible job. Distinct titles so `dedupe.collapse` keeps them apart."""
    return Job.model_validate(
        {
            "id": f"job-batch-{i}",
            "dedupe_key": f"url:https://x.example/{i}",
            "title": f"Graduate Machine Learning Engineer {i}",
            "company": f"Fernhill AI {i}",
            "location": Location(city="London", country="UK", raw="London, UK"),
            "remote": "hybrid",
            "description_md": "We build guardrails for LLM products. Python and FastAPI. " * 5,
            "description_status": "full",
            "apply_url": f"https://x.example/{i}",
            "sources": [JobSource(channel="watchlist", detail="greenhouse", seen_at=iso(NOW))],
            "language": "en",
            "first_seen": iso(NOW),
            "last_seen": iso(NOW),
            "status": "new",
            "seniority": "graduate",
        }
    )


@pytest.fixture
def pool(client):
    """A broad search's worth of jobs: more than one run will score."""

    def fill(count: int) -> int:
        found = [job(i) for i in range(count)]
        with transaction() as session:
            store = Store(session, NOW)
            before = len(store.list_jobs("new")) + store.awaiting_score()
            store.upsert_jobs(found)
            kept = store.awaiting_score()
        assert kept >= count, f"the profile filtered the fixtures out ({kept} of {count} kept)"
        return before

    return fill


def scored_count() -> int:
    with transaction() as session:
        return Store(session, NOW).scored_today()


def awaiting() -> int:
    with transaction() as session:
        return Store(session, NOW).awaiting_score()


class TestARunScoresTwoBatchesAndStops:
    def test_it_scores_the_batch_budget_and_leaves_the_rest(self, client, pool):
        pool(30)
        per_run = wera().scored_per_run

        judged = intake.judge([], now=NOW, run_ids={})

        assert judged.scored == per_run
        assert judged.pool > 0, "a 30-job pool should have something left after two batches"
        assert awaiting() > 0

    def test_the_rest_stay_in_the_pool_rather_than_being_dropped(self, client, pool):
        pool(30)
        before = awaiting()

        intake.judge([], now=NOW, run_ids={})

        # Exactly the scored ones left the queue. A job WERA found and filtered *in* is never
        # discarded for being past a batch boundary.
        assert awaiting() == before - wera().scored_per_run

    def test_a_pool_smaller_than_a_batch_is_scored_whole(self, client, pool):
        """The batch is a ceiling, not a quota: three jobs is three calls, not eight."""
        pool(3)
        judged = intake.judge([], now=NOW, run_ids={})
        assert judged.scored == 3
        assert awaiting() == 0


class TestOrderingAcrossBatches:
    def test_the_pool_is_ranked_as_a_whole_before_it_is_sliced(self, client, pool, monkeypatch):
        """Ranking only the slice would make each batch the best of an arbitrary subset rather
        than the best of what's left — which is the one thing that makes stopping early safe."""
        seen: list[int] = []
        real = intake.prefilter.shortlist

        def spy(jobs, cv, **kw):
            seen.append(len(jobs))
            return real(jobs, cv, **kw)

        monkeypatch.setattr(intake.prefilter, "shortlist", spy)
        pool(30)

        intake.judge([], now=NOW, run_ids={})

        assert seen and seen[0] >= 30, f"ranked {seen} jobs, not the whole pool"

    def test_a_second_pass_scores_different_jobs(self, client, pool):
        pool(30)
        intake.judge([], now=NOW, run_ids={})
        with transaction() as session:
            first = {j.id for j in Store(session, NOW).list_jobs("new")}

        intake.judge([], now=NOW, run_ids={})
        with transaction() as session:
            second = {j.id for j in Store(session, NOW).list_jobs("new")}

        assert second > first, "the second pass re-scored the first batch instead of moving on"


class TestTheDailyCapStillBounds:
    def test_batches_cannot_add_up_to_more_than_the_day_allows(self, client, pool):
        """The cap used to be enforced by handing a whole day's allowance to one run's
        pre-filter, so nothing had to remember anything. Batching made that insufficient."""
        pool(60)
        cap = wera().max_scored_per_day

        total = 0
        for _ in range(20):  # more passes than the cap can possibly allow
            total += intake.judge([], now=NOW, run_ids={}).scored
            if awaiting() == 0:
                break

        assert total <= cap, f"scored {total} jobs against a cap of {cap}"
        assert scored_count() <= cap

    def test_a_capped_pass_says_so_rather_than_looking_like_an_empty_pool(self, client, pool):
        pool(60)
        while intake.judge([], now=NOW, run_ids={}).scored:
            pass

        judged = intake.judge([], now=NOW, run_ids={})

        assert judged.scored == 0
        assert any(code == "DAILY_CAP" for code, _ in judged.errors), judged.errors


class TestScoreMore:
    def test_it_picks_up_where_the_run_stopped(self, client, pool):
        pool(30)
        intake.judge([], now=NOW, run_ids={})
        before = awaiting()

        with transaction() as session:
            ids = Store(session, NOW).start_score_more()
        intake.rescore(ids.run_ids[0], "score_more")

        assert awaiting() == before - wera().surface.max

    def test_find_more_brings_one_search_worth_and_stops(self, client, pool):
        """SRCH-26: the backlog first, and no more than a search's 8–12 — and with that many
        waiting, nothing is searched for at all."""
        pool(30)
        with transaction() as session:
            ids = Store(session, NOW).start_score_more()

        before = awaiting()
        intake.rescore(ids.run_ids[0], "score_more")

        assert before - awaiting() == wera().surface.max
        with transaction() as session:
            row = session.get(IntakeRunRow, ids.run_ids[0])
            assert (row.status, row.queries, row.total) == ("succeeded", 0, wera().surface.max)

    def test_it_refuses_when_there_is_nothing_waiting(self, client):
        from wera_agent.errors import ApiError

        with transaction() as session, pytest.raises(ApiError) as raised:
            Store(session, NOW).start_score_more()
        assert raised.value.code == "NOTHING_TO_SCORE"

    def test_it_refuses_rather_than_queueing_behind_a_running_search(self, client, pool):
        from wera_agent.errors import ApiError

        pool(30)
        with transaction() as session:
            Store(session, NOW).start_runs(["watchlist"], scheduled=True)
        with transaction() as session, pytest.raises(ApiError) as raised:
            Store(session, NOW).start_score_more()
        assert raised.value.code == "RUN_ACTIVE"
        assert "search" in raised.value.message.lower()

    def test_it_refuses_once_the_day_is_spent(self, client, pool):
        from wera_agent.errors import ApiError

        pool(60)
        while intake.judge([], now=NOW, run_ids={}).scored:
            pass

        with transaction() as session, pytest.raises(ApiError) as raised:
            Store(session, NOW).start_score_more()
        assert raised.value.code == "DAILY_CAP"

    def test_the_run_is_labelled_as_scoring_not_as_a_search(self, client, pool):
        pool(30)
        with transaction() as session:
            ids = Store(session, NOW).start_score_more()
        with transaction() as session:
            row = session.get(IntakeRunRow, ids.run_ids[0])
            assert row.channel == "score_more"
            # Straight into scoring: this run never searches for anything.
            assert row.phase == "scoring"


class TestTheScreenKnowsWhereABatchEnds:
    def test_the_batch_size_travels_with_the_run(self, client, pool):
        """The panel re-reads the shortlist on a batch boundary, so it has to know where one is —
        and scoring reports after every job, not every batch."""
        pool(30)
        with transaction() as session:
            ids = Store(session, NOW).start_score_more()
        with transaction() as session:
            assert Store(session, NOW).get_run(ids.run_ids[0]).batch_size == BATCH

    def test_the_meta_says_how_many_find_more_brings(self, client, pool):
        pool(30)
        with transaction() as session:
            assert Store(session, NOW).shortlist_meta().find_more_size == wera().surface.max


def test_scoring_calls_are_what_the_cap_counts(client, pool):
    """`scored_today` reads `llm_calls` rather than a tally WERA keeps, so it cannot drift from
    what was actually spent. One `fit.score` call is one job."""
    assert scored_count() == 0, "the demo seed should not have spent any scoring calls"
    pool(10)

    judged = intake.judge([], now=NOW, run_ids={})

    with transaction() as session:
        rows = session.scalars(select(LlmCallRow).where(LlmCallRow.task == "fit.score")).all()
    assert len(rows) == judged.scored


class TestTheSearchScoresOnlyWhatItRead:
    def test_only_these_leaves_the_backlog_alone(self, client, pool):
        """SRCH-20, stage 4. A round of the search pipeline scores the postings it just read, in
        discovery's order — never the best few of a backlog of hundreds instead."""
        pool(30)
        before = awaiting()
        fresh = [job(100), job(101)]
        judged = intake.judge(fresh, now=NOW, run_ids={}, only_these=True, limit=12)
        assert judged.surfaced == 2 and judged.new_ids == {"job-batch-100", "job-batch-101"}
        assert awaiting() == before  # the 30 waiting are still waiting
