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


class TestReScoringAfterACvChange:
    """CV-08: saving a CV sets off a pass that re-judges what WERA already has.

    The path a multi-user install runs, and the reason `reset_on_cv_upload` is a flag rather than
    a rewrite of `put_cv`: a user fixing a typo in their CV must keep their shortlist. Pinned on
    purpose — the shipped config currently chooses the other path (see `TestClearingTheBacklog`),
    and these tests would otherwise pass or fail on a setting someone flipped for an afternoon.
    """

    @pytest.fixture(autouse=True)
    def _rescore(self, cv_upload_mode):
        cv_upload_mode("rescore")

    def new_cv(self, store) -> None:
        cv = store.cv()
        cv.summary.text = f"{cv.summary.text} And a line that changes the document."
        store.put_cv(cv)

    def test_saving_a_cv_starts_a_rescore_run(self, client):
        with transaction() as session:
            self.new_cv(Store(session))
        with transaction() as session:
            started = session.scalars(select(IntakeRunRow).where(IntakeRunRow.channel == "rescore")).all()
        assert len(started) == 1
        # Straight into scoring: this run never searches for anything, so reporting a `searching`
        # phase would describe work it doesn't do.
        assert started[0].phase == "scoring"

    def test_it_re_scores_the_stale_jobs_and_finishes(self, client):
        with transaction() as session:
            store = Store(session)
            expected = len(store.list_jobs("new"))
            self.new_cv(store)
            run_id = session.scalars(select(IntakeRunRow.id).where(IntakeRunRow.channel == "rescore")).one()

        intake.rescore(run_id)

        row = row_for(run_id)
        assert row.status == "succeeded"
        assert row.progress == 1 and row.phase == "done"
        with transaction() as session:
            store = Store(session)
            assert store.jobs_needing_score(cv_version=store.cv().version, model=store.scoring_model()) == []
            assert not any(j.fit.stale for j in store.list_jobs("new"))
            assert len(store.list_jobs("new")) == expected

    def test_saving_a_cv_never_fails_because_a_search_is_running(self, client, runs):
        """A busy run costs the re-score, never the CV. The next ordinary run picks the work up,
        because `judge` asks what needs scoring rather than scoring only what it found."""
        runs(["watchlist"])  # leaves a row with status "running"
        with transaction() as session:
            store = Store(session)
            version = store.cv().version
            self.new_cv(store)
            assert store.cv().version == version + 1
        with transaction() as session:
            assert session.scalars(select(IntakeRunRow).where(IntakeRunRow.channel == "rescore")).all() == []

    def test_nothing_to_score_means_no_run(self, client, monkeypatch):
        """A progress bar for no work is worse than no progress bar — a fresh install shouldn't
        get one just for saving its first CV.

        Note what this does *not* claim: that re-saving an unedited CV is free. `put_cv` is
        append-only and bumps the version whatever the content, so pressing Save without editing
        really does invalidate every score and start a pass. Comparing content before bumping
        would change what a CV version means, so it isn't done here.
        """
        monkeypatch.setattr(Store, "jobs_needing_score", lambda *_a, **_k: [])
        with transaction() as session:
            self.new_cv(Store(session))
        with transaction() as session:
            assert session.scalars(select(IntakeRunRow).where(IntakeRunRow.channel == "rescore")).all() == []


class TestClearingTheBacklog:
    """`reset_on_cv_upload`: saving a CV throws away everything built from the old one.

    The testing-phase path, and the one the shipped config currently chooses. The loop it serves
    is "upload a CV, search, read the shortlist and the generated documents, do it again with a
    different CV" — which only measures anything if each pass starts from nothing.
    """

    @pytest.fixture(autouse=True)
    def _reset(self, cv_upload_mode):
        cv_upload_mode("reset")

    def new_cv(self, store) -> None:
        cv = store.cv()
        cv.summary.text = f"{cv.summary.text} And a line that changes the document."
        store.put_cv(cv)

    def test_it_clears_the_jobs_and_starts_no_rescore(self, client):
        with transaction() as session:
            store = Store(session)
            assert store.list_jobs("new"), "the demo seed should have left a backlog"
            self.new_cv(store)
        with transaction() as session:
            assert session.scalars(select(JobRow)).all() == []
            # Nothing is left to score, so a run row here would be a progress bar for no work —
            # and would spend the scoring tier on jobs that no longer exist.
            assert session.scalars(select(IntakeRunRow).where(IntakeRunRow.channel == "rescore")).all() == []

    def test_the_applications_and_packs_go_with_them(self, client):
        """A pack cites master-CV bullet ids, and ids are derived from employer names
        (`domain/cv_build`), so a pack written against a different person's CV doesn't merely
        show stale scores — it cites lines that no longer exist."""
        from wera_agent.db.models import ApplicationRow, DocumentRow

        with transaction() as session:
            assert session.scalars(select(ApplicationRow)).all(), "the demo seed should have applications"
            assert session.scalars(select(DocumentRow)).all(), "the demo seed should have packs"
        with transaction() as session:
            self.new_cv(Store(session))
        with transaction() as session:
            assert session.scalars(select(ApplicationRow)).all() == []
            assert session.scalars(select(DocumentRow)).all() == []

    def test_it_keeps_the_cv_the_contact_details_and_the_profile(self, client):
        """The person, not their job list. The CV being saved is the whole point of the call."""
        with transaction() as session:
            store = Store(session)
            before = store.cv().version
            contact, profile = store.get_contact(), store.get_profile()
            self.new_cv(store)
        with transaction() as session:
            store = Store(session)
            assert store.cv().version == before + 1
            assert store.get_contact() == contact
            assert store.get_profile().titles == profile.titles

    def test_it_keeps_the_cost_ledger(self, client):
        """COST-01: `llm_calls` is money that really left the account. Clearing it would hand
        back the spend and let an afternoon of testing run past the weekly cap."""
        with transaction() as session:
            session.add(LlmCallRow(task="fit.score", tier="scoring", model="qwen/test", cost_usd=0.02, called_at=iso(now_ms())))
            before = len(session.scalars(select(LlmCallRow)).all())
        with transaction() as session:
            self.new_cv(Store(session))
        with transaction() as session:
            assert len(session.scalars(select(LlmCallRow)).all()) == before

    def test_it_never_reuses_an_application_id(self, client):
        """`kv` holds `_next_id`'s counter and survives the wipe. Resetting it would make a new
        application inherit a dead one's id, and with it a dead one's paused graph thread."""
        with transaction() as session:
            first = Store(session)._next_id()
            self.new_cv(Store(session))
        with transaction() as session:
            assert Store(session)._next_id() > first

    def test_it_clears_the_crawl_cache(self, client):
        """SRCH-10: a cached search comes back without running, so leaving the cache would mean
        the first thing tested after a reset is the cache rather than the search."""
        from wera_agent.db.models import CrawlCacheRow

        with transaction() as session:
            session.add(
                CrawlCacheRow(
                    key="search:x", kind="search", payload={}, stored_at=iso(now_ms()), expires_at=iso(now_ms() + 10**7)
                )
            )
        with transaction() as session:
            self.new_cv(Store(session))
        with transaction() as session:
            assert session.scalars(select(CrawlCacheRow)).all() == []

    def test_a_running_search_is_cancelled_rather_than_left_to_block(self, client, runs):
        """Its jobs are gone, so it has nothing left to do — and both `start_rescore_if_idle` and
        `start_score_more` refuse while a run is active, so one abandoned here would block the
        next search instead of finishing."""
        runs(["watchlist"])  # leaves a row with status "running"
        with transaction() as session:
            self.new_cv(Store(session))
        with transaction() as session:
            assert session.scalars(select(IntakeRunRow).where(IntakeRunRow.status == "running")).all() == []

    def test_the_route_says_how_many_jobs_it_cleared(self, api):
        """A shortlist that empties itself without a word reads as a bug, whoever asked for it —
        and "search again" is not obvious from an empty screen."""
        cv = api.ok("GET", "/cv")
        before = len(api.ok("GET", "/jobs?status=new")["items"])
        assert before, "the demo seed should have left a backlog"

        result = api.ok("PUT", "/cv", cv)

        assert result["clearedJobs"] >= before
        assert api.ok("GET", "/jobs?status=new")["items"] == []

    def test_a_fresh_search_can_find_the_same_postings_again(self, client):
        """Why this deletes rather than archives: `known_apply_urls` reads every job row whatever
        its status, so an archived job's URL is one `firecrawl_search` still skips (SRCH-05).
        Archiving would leave a clean screen behind which the next search quietly found nothing.
        """
        with transaction() as session:
            store = Store(session)
            assert store.known_apply_urls(), "the demo seed should have apply URLs"
            self.new_cv(store)
        with transaction() as session:
            assert Store(session).known_apply_urls() == set()


class TestARunSaysWhatItIsDoing:
    """UX-01: *"Progress must move while the slow work is happening, not between the phases
    around it."* 0005 made the bar move; this is the half that names the stage."""

    def phases_seen(self, monkeypatch) -> list[tuple[str, str | None]]:
        """Every (phase, detail) a run publishes, in order, by watching the heartbeat."""
        seen: list[tuple[str, str | None]] = []
        real = intake.report

        def spy(run_ids, **kw):
            seen.append((kw["phase"], kw.get("detail")))
            return real(run_ids, **kw)

        monkeypatch.setattr(intake, "report", spy)
        return seen

    def test_the_shared_stages_between_fetching_and_scoring_are_named(self, client, runs, channels, monkeypatch):
        """`collapsing` and `closing` are seconds of silence between two long phases, and silence
        between stages is what a frozen bar is made of."""
        ids = runs(["watchlist"])
        channels(from_watchlist=lambda *a, **k: intake.ChannelOutcome(channel="watchlist", jobs=[job()], results=1))
        seen = self.phases_seen(monkeypatch)

        intake.run(["watchlist"], ids)

        phases = [p for p, _ in seen]
        assert "collapsing" in phases
        assert "filtering" in phases
        assert "closing" in phases
        # In order, and never going backwards.
        from wera_agent.schemas.runs import PHASE_ORDER

        order = [PHASE_ORDER.index(p) for p in phases]
        assert order == sorted(order), phases

    def test_scoring_names_the_job_it_is_waiting_on(self, client, runs, channels, monkeypatch):
        """Scoring is ~98% of a real run's wall clock at roughly eleven seconds a job, so a
        count alone leaves the longest phase in the product with nothing changing."""
        ids = runs(["watchlist"])
        channels(from_watchlist=lambda *a, **k: intake.ChannelOutcome(channel="watchlist", jobs=[job()], results=1))
        seen = self.phases_seen(monkeypatch)

        intake.run(["watchlist"], ids)

        scoring = [detail for phase, detail in seen if phase == "scoring"]
        assert scoring, "scoring never reported"
        assert any(d and "ML Engineer, Graduate" in d and "Fernhill AI" in d for d in scoring), scoring

    def test_a_stale_detail_is_cleared_rather_than_left_behind(self, client, runs, channels):
        """A sentence from the previous phase is worse than no sentence: it says WERA is reading a
        board it finished with minutes ago."""
        ids = runs(["watchlist"])
        channels(from_watchlist=lambda *a, **k: intake.ChannelOutcome(channel="watchlist", jobs=[job()], results=1))
        run_id = ids["watchlist"]

        intake.report({"watchlist": run_id}, phase="feeds", progress=0.1, detail="32 employers")
        assert row_for(run_id).detail == "32 employers"
        intake.report({"watchlist": run_id}, phase="collapsing", progress=0.25)
        assert row_for(run_id).detail is None

    def test_a_finished_run_is_doing_nothing_and_says_so(self, client, runs, channels):
        ids = runs(["watchlist"])
        channels(from_watchlist=lambda *a, **k: intake.ChannelOutcome(channel="watchlist", jobs=[job()], results=1))

        intake.run(["watchlist"], ids)

        row = row_for(ids["watchlist"])
        assert row.phase == "done"
        assert row.detail is None

    def test_a_detail_too_long_for_the_column_is_truncated_not_refused(self, client, runs):
        """A heartbeat must never be the thing that fails a run."""
        run_id = runs(["watchlist"])["watchlist"]
        intake.report({"watchlist": run_id}, phase="scoring", progress=0.5, detail="x" * 500)
        assert len(row_for(run_id).detail) == intake.DETAIL_LIMIT


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
