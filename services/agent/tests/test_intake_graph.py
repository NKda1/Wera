"""One intake run, end to end (SRCH-08, SCH-02, SCH-04, UX-01, COST-04, T-06).

The sources are faked at their own boundaries, because what this exercises is the shape of the
run: that one channel failing doesn't take the others, that a job found twice is scored once,
that a second run costs nothing, and that a failure leaves yesterday's shortlist alone.
"""

import pytest
from sqlalchemy import select

from wera_agent.clock import iso, now_ms
from wera_agent.db.models import IntakeRunRow, JobRow, LlmCallRow
from wera_agent.db.session import transaction
from wera_agent.graphs import intake
from wera_agent.schemas.jobs import Ats, Job, JobSource, Location
from wera_agent.services.store import Store
from wera_agent.sources.ats_feeds import FeedResult

NOW = now_ms()


def job(**over) -> Job:
    base = {
        "id": over.pop("id", "job-a"),
        "dedupe_key": over.pop("dedupe_key", "url:https://x.example/1"),
        "title": "ML Engineer, Graduate",
        "company": "Fernhill AI",
        "location": Location(city="London", country="UK", raw="London, UK"),
        "remote": "hybrid",
        "description_md": "We build guardrails for LLM products. Python and FastAPI. " * 5,
        "description_status": "full",
        "apply_url": "https://x.example/1",
        "sources": [JobSource(channel="board_search", detail="gradcracker.com", seen_at=iso(NOW))],
        "language": "en",
        "first_seen": iso(NOW),
        "last_seen": iso(NOW),
        "status": "new",
        "seniority": "graduate",
    }
    return Job.model_validate(base | over)


@pytest.fixture
def runs(client):
    """Creates the intake_runs rows a run writes into, as the route does."""

    def start(channels: list[str]) -> dict[str, str]:
        with transaction() as session:
            return Store(session).start_runs(channels, scheduled=True)

    return start


@pytest.fixture
def channels(monkeypatch):
    """Replaces each channel with a fixed outcome, so the run's shape is what's under test."""

    def install(**outcomes):
        for name, outcome in outcomes.items():
            monkeypatch.setattr(intake, name, outcome)

    return install


def row_for(run_id: str) -> IntakeRunRow:
    with transaction() as session:
        return session.get(IntakeRunRow, run_id)


def job_count() -> int:
    with transaction() as session:
        return len(session.scalars(select(JobRow)).all())


def scoring_calls() -> int:
    with transaction() as session:
        return len(session.scalars(select(LlmCallRow).where(LlmCallRow.task == "fit.score")).all())


class TestOneChannelNeverTakesTheRun:
    def test_a_failed_watchlist_leaves_the_board_results(self, client, runs, channels):
        """SCH-04."""
        ids = runs(["board_search", "watchlist"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()], queries=2, results=1),
            from_watchlist=lambda *a, **k: intake.ChannelOutcome(
                channel="watchlist", errors=[("WATCHLIST_FEED", "Fernhill AI: its board didn't answer")]
            ),
        )
        before = job_count()

        summary = intake.run(["board_search", "watchlist"], ids)

        assert summary["new_jobs"] == 1
        assert job_count() == before + 1
        assert row_for(ids["board_search"]).status == "succeeded"
        failed = row_for(ids["watchlist"])
        assert failed.status == "failed"
        assert failed.errors and "didn't answer" in failed.errors[0]["message"]

    def test_a_channel_that_raises_is_recorded_not_propagated(self, client, runs, channels):
        ids = runs(["watchlist"])

        def boom(*_a, **_k):
            raise RuntimeError("feed exploded")

        channels(from_watchlist=boom)
        summary = intake.run(["watchlist"], ids)
        assert summary["new_jobs"] == 0
        assert row_for(ids["watchlist"]).status == "failed"

    def test_a_run_that_finds_nothing_leaves_the_previous_list_alone(self, client, runs, channels):
        """SCH-04: the shortlist a person looked at yesterday is still there."""
        before = job_count()
        ids = runs(["board_search"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", errors=[("BOARD_SEARCH", "down")])
        )
        intake.run(["board_search"], ids)
        assert job_count() == before


class TestDeduplication:
    def test_a_job_found_by_two_channels_is_stored_and_scored_once(self, client, runs, channels):
        """T-06, and the reason de-duplication happens before scoring."""
        ids = runs(["board_search", "watchlist"])
        shared_url = "https://job-boards.greenhouse.io/fernhillai/jobs/42"
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(
                channel="board_search", jobs=[job(id="from-board", apply_url=shared_url, dedupe_key="")], results=1
            ),
            from_watchlist=lambda *a, **k: intake.ChannelOutcome(
                channel="watchlist",
                jobs=[
                    job(
                        id="from-watchlist",
                        apply_url=shared_url,
                        dedupe_key="",
                        ats=Ats(vendor="greenhouse", board_token="fernhillai", job_id="42"),
                        sources=[JobSource(channel="watchlist", detail="fernhillai", seen_at=iso(NOW))],
                    )
                ],
                results=1,
                feed=FeedResult(answered={"greenhouse:fernhillai"}),
            ),
        )
        before = job_count()
        summary = intake.run(["board_search", "watchlist"], ids)

        assert summary["new_jobs"] == 1
        assert job_count() == before + 1
        # Selected by the URL this run used: the demo database already has Fernhill AI jobs.
        with transaction() as session:
            store = Store(session)
            stored = next(j for j in (store._job(r) for r in session.scalars(select(JobRow)).all()) if j.apply_url == shared_url)
        assert {s.channel for s in stored.sources} == {"board_search", "watchlist"}


class TestIdempotence:
    def test_a_second_run_adds_no_jobs_and_pays_for_no_new_scores(self, client, runs, channels):
        """SCH-02 and COST-04 together: the morning run after the morning run is free."""
        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()], results=1))

        intake.run(["board_search"], runs(["board_search"]))
        after_first = (job_count(), scoring_calls())

        intake.run(["board_search"], runs(["board_search"]))
        assert (job_count(), scoring_calls()) == after_first

    def test_a_second_sighting_is_a_new_source_not_a_new_job(self, client, runs, channels):
        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()], results=1))
        intake.run(["board_search"], runs(["board_search"]))

        channels(
            from_watchlist=lambda *a, **k: intake.ChannelOutcome(
                channel="watchlist",
                jobs=[job(id="same-job", sources=[JobSource(channel="watchlist", detail="fernhillai", seen_at=iso(NOW))])],
                results=1,
                feed=FeedResult(answered=set()),
            )
        )
        before = job_count()
        summary = intake.run(["watchlist"], runs(["watchlist"]))
        assert summary["new_jobs"] == 0
        assert job_count() == before


class TestCancellation:
    def test_a_cancelled_run_stops_before_scoring(self, client, runs, channels):
        """UX-01: a person who changes their mind shouldn't pay for the rest of the run."""
        ids = runs(["board_search"])
        with transaction() as session:
            Store(session).cancel_run(ids["board_search"])

        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()], results=1))
        before = (job_count(), scoring_calls())
        summary = intake.run(["board_search"], ids)

        assert summary["new_jobs"] == 0
        assert (job_count(), scoring_calls()) == before


class TestPartialRuns:
    def test_a_capped_channel_is_recorded_as_partial(self, client, runs, channels):
        """SRCH-06: stopping at a cap is a partial run, not a failure."""
        ids = runs(["board_search"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(
                channel="board_search", jobs=[job()], queries=20, credits_used=100, partial=True
            )
        )
        intake.run(["board_search"], ids)
        row = row_for(ids["board_search"])
        assert row.status == "partial"
        assert (row.queries, row.credits_used) == (20, 100)


class TestWhatTheCacheSaved:
    def test_the_saving_reaches_the_run_the_screen_reads(self, client, runs, channels):
        """SRCH-10: `credits_saved` is what makes "0 credits" legible — without it a fully
        cached run is indistinguishable on the Jobs screen from one that did nothing."""
        ids = runs(["board_search"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(
                channel="board_search", jobs=[job()], queries=20, credits_used=0, credits_saved=140
            )
        )
        summary = intake.run(["board_search"], ids)
        row = row_for(ids["board_search"])
        assert (row.credits_used, row.credits_saved) == (0, 140)
        assert summary["credits_saved"] == 140

    def test_a_run_that_paid_full_price_saved_nothing(self, client, runs, channels):
        ids = runs(["board_search"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(
                channel="board_search", jobs=[job()], queries=20, credits_used=140
            )
        )
        intake.run(["board_search"], ids)
        assert row_for(ids["board_search"]).credits_saved == 0


class TestAccountLevelFailures:
    """A rejected key or spent credit is a fact about the account, not about the job in front of
    it. Scoring stops at the first one instead of proving it forty more times."""

    def many(self, count: int) -> list[Job]:
        """Distinct titles and companies, or de-duplication quite rightly collapses them.

        On-target titles on purpose: these tests are about what happens *at* the scoring step,
        and the title filter (MATCH-01) now hides anything the profile didn't ask for long
        before a model is reached.
        """
        return [
            job(
                id=f"job-{n}",
                dedupe_key=f"url:https://x.example/{n}",
                apply_url=f"https://x.example/{n}",
                title=f"ML Engineer, Team {n}",
                company=f"Company {n}",
            )
            for n in range(count)
        ]

    def test_scoring_stops_at_the_first_credit_refusal(self, client, runs, channels, monkeypatch):
        ids = runs(["board_search"])
        found = self.many(5)
        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=found, results=5))

        attempts: list[str] = []

        def refuse(session, j, cv, *, model, now):
            attempts.append(j.id)
            return None, False, "LLM_QUOTA"

        monkeypatch.setattr(intake.fit, "evidence_for", refuse)
        summary = intake.run(["board_search"], ids)

        assert len(attempts) == 1  # not one per job
        assert summary["new_jobs"] == 5  # the jobs are kept, unscored, for when credit is back
        row = row_for(ids["board_search"])
        assert [e["code"] for e in row.errors] == ["LLM_QUOTA"]
        # All five, including the one that hit the refusal: none of them got a score.
        assert "5 job(s) were left unscored" in row.errors[0]["message"]

    def test_a_run_that_could_not_score_is_partial_not_succeeded(self, client, runs, channels, monkeypatch):
        """Finding jobs and scoring them fail separately, and "succeeded" next to a list of
        unscored jobs reads as a lie."""
        ids = runs(["board_search"])
        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()], results=1))
        monkeypatch.setattr(intake.fit, "evidence_for", lambda *a, **k: (None, False, "LLM_QUOTA"))

        intake.run(["board_search"], ids)
        assert row_for(ids["board_search"]).status == "partial"

    def test_one_job_that_will_not_score_does_not_stop_the_others(self, client, runs, channels, monkeypatch):
        """A model that answered in the wrong shape says nothing about the next job."""
        ids = runs(["board_search"])
        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=self.many(4), results=4))

        real, seen = intake.fit.evidence_for, []

        def flaky(session, j, cv, *, model, now):
            seen.append(j.id)
            if len(seen) == 1:
                return None, False, "LLM_BAD_OUTPUT"
            return real(session, j, cv, model=model, now=now)

        monkeypatch.setattr(intake.fit, "evidence_for", flaky)
        summary = intake.run(["board_search"], ids)

        assert len(seen) == 4  # every job was tried
        assert summary["scored"] == 3


class TestRealRunFeedsTheScreens:
    """Demo mode settles its own figures; a real run has to write them, or the shortlist keeps
    reporting a run that happened days ago."""

    @pytest.fixture
    def live(self, monkeypatch):
        from wera_agent.settings import get_settings

        monkeypatch.setenv("WERA_DEMO_MODE", "false")
        get_settings.cache_clear()
        # The pre-filter would otherwise load the real embedding model, and scoring would reach a
        # real provider. Neither is what this asserts.
        monkeypatch.setattr(intake.prefilter, "shortlist", lambda jobs, cv, **k: [])
        yield
        get_settings.cache_clear()

    def test_the_channel_strip_and_new_markers_come_from_the_run(self, client, runs, channels, live):
        from wera_agent.services.store import get_kv

        ids = runs(["board_search"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()], queries=7, results=1)
        )
        intake.run(["board_search"], ids)

        with transaction() as session:
            stats = get_kv(session, "stats")
            assert (stats["board"]["queries"], stats["board"]["results"]) == (7, 1)
            assert stats["board"]["ranAt"] is not None
            assert get_kv(session, "lastRunJobIds") == ["job-a"]


class TestFilteringBeforeScoring:
    def test_a_job_the_profile_excludes_is_hidden_and_never_scored(self, client, runs, channels):
        """MATCH-01 before COST: the cheapest possible rejection."""
        ids = runs(["board_search"])
        channels(
            from_board_search=lambda *a, **k: intake.ChannelOutcome(
                channel="board_search",
                jobs=[
                    job(
                        id="senior",
                        title="Senior ML Engineer",
                        apply_url="https://x.example/2",
                        dedupe_key="url:https://x.example/2",
                    )
                ],
                results=1,
            )
        )
        before = scoring_calls()
        intake.run(["board_search"], ids)

        with transaction() as session:
            stored = [Store(session)._job(r) for r in session.scalars(select(JobRow)).all()]
        hidden = next(j for j in stored if j.title == "Senior ML Engineer")
        assert hidden.status == "hidden"
        assert "Senior" in (hidden.hidden_reason or "")
        assert scoring_calls() == before  # it never reached a model


class TestWatchlistClosing:
    def test_a_job_gone_from_a_board_that_answered_is_closed(self, client, runs, channels):
        """ATS-03."""
        ats = Ats(vendor="lever", board_token="saltmarsh", job_id="gone-1")
        with transaction() as session:
            store = Store(session)
            store.upsert_jobs([job(id="job-gone", dedupe_key="ats:lever:saltmarsh:gone-1", ats=ats)])

        ids = runs(["watchlist"])
        channels(
            from_watchlist=lambda *a, **k: intake.ChannelOutcome(
                channel="watchlist", jobs=[], results=0, feed=FeedResult(jobs=[], answered={"lever:saltmarsh"})
            )
        )
        summary = intake.run(["watchlist"], ids)
        assert summary["closed"] == 1
        with transaction() as session:
            assert session.get(JobRow, "job-gone").status == "closed"

    def test_a_board_outage_closes_nothing(self, client, runs, channels):
        ats = Ats(vendor="lever", board_token="saltmarsh", job_id="gone-1")
        with transaction() as session:
            Store(session).upsert_jobs([job(id="job-gone", dedupe_key="ats:lever:saltmarsh:gone-1", ats=ats)])

        ids = runs(["watchlist"])
        channels(
            from_watchlist=lambda *a, **k: intake.ChannelOutcome(
                channel="watchlist", jobs=[], errors=[("WATCHLIST_FEED", "down")], feed=FeedResult(jobs=[], answered=set())
            )
        )
        summary = intake.run(["watchlist"], ids)
        assert summary["closed"] == 0
        with transaction() as session:
            assert session.get(JobRow, "job-gone").status == "new"


class TestProgress:
    """UX-01: what the Jobs screen can actually show while a run is working.

    `progress` existed before this but only ever moved in demo mode, where `settle()` animates
    it against a made-up `duration_ms`. A real run left it at 0 until the graph wrote 1 at the
    end — so the bar sat still for the seven minutes that scoring forty jobs takes, and the
    only thing that changed on screen was a sentence that had stopped being true.
    """

    def test_progress_is_readable_while_the_run_is_still_working(self, client, runs, channels):
        """The point of committing each heartbeat: a reader outside the run can see it.

        If scoring held one transaction, this would read 0 until the whole run was over — which
        is precisely the bug, and it is invisible unless the read happens from somewhere else.
        """
        seen: list[tuple[str, float, int, int]] = []
        ids = runs(["board_search"])

        real = intake.fit.evidence_for

        def watched(session, job_, cv, **kw):
            with transaction() as other:
                row = other.get(IntakeRunRow, ids["board_search"])
                seen.append((row.phase, row.progress, row.done, row.total))
            return real(session, job_, cv, **kw)

        intake.fit.evidence_for = watched
        try:
            channels(
                from_board_search=lambda *a, **k: intake.ChannelOutcome(
                    channel="board_search", jobs=[job(id="job-a"), job(id="job-b", dedupe_key="url:https://x.example/2")]
                )
            )
            intake.run(["board_search"], ids)
        finally:
            intake.fit.evidence_for = real

        assert seen, "scoring should report before each job, not only at the end"
        assert all(phase == "scoring" for phase, *_ in seen)
        assert [done for _, _, done, _ in seen] == list(range(len(seen)))
        assert all(total == len(seen) for *_, total in seen)
        assert seen[0][1] >= intake.SEARCH_SHARE  # searching is behind us by the time scoring starts

    def test_progress_only_rises(self, client, runs, channels):
        seen: list[float] = []
        ids = runs(["board_search"])
        real = intake.fit.evidence_for

        def watched(session, job_, cv, **kw):
            with transaction() as other:
                seen.append(other.get(IntakeRunRow, ids["board_search"]).progress)
            return real(session, job_, cv, **kw)

        intake.fit.evidence_for = watched
        try:
            channels(
                from_board_search=lambda *a, **k: intake.ChannelOutcome(
                    channel="board_search", jobs=[job(id="job-a"), job(id="job-b", dedupe_key="url:https://x.example/2")]
                )
            )
            intake.run(["board_search"], ids)
        finally:
            intake.fit.evidence_for = real
        assert seen == sorted(seen)

    def test_a_finished_run_says_done(self, client, runs, channels):
        ids = runs(["board_search"])
        channels(from_board_search=lambda *a, **k: intake.ChannelOutcome(channel="board_search", jobs=[job()]))
        intake.run(["board_search"], ids)
        row = row_for(ids["board_search"])
        assert (row.phase, row.progress) == ("done", 1)

    def test_a_heartbeat_never_takes_the_run_down(self, client, runs, channels, monkeypatch):
        """Telling someone how far along we are is not load-bearing. If the write fails — a
        locked database, a cancelled run — the scoring it describes must still finish."""

        def explode(*a, **k):
            raise RuntimeError("no database for you")

        monkeypatch.setattr(intake, "transaction", explode, raising=False)
        # `judge` needs real transactions, so only the reporter is broken here.
        intake.report({"board_search": "run-missing"}, phase="scoring", progress=0.5, done=1, total=2)
