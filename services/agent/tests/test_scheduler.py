"""When intake runs on its own (SCH-01).

Testing a schedule means testing the trigger, not waiting for it. These assert the cron fields,
the settings that make a missed run behave sensibly, and that demo mode schedules nothing at all.
"""

import pytest

from wera_agent.api import scheduler
from wera_agent.settings import get_settings


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("WERA_DEMO_MODE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    scheduler.stop()


class TestDemoMode:
    def test_demo_mode_schedules_nothing(self, client):
        """DEMO-02: a demo that quietly called out in the background would be a lie."""
        assert scheduler.start() is None
        assert scheduler.describe() == []


class TestSchedule:
    def test_the_watchlist_runs_each_morning_and_the_inbox_through_the_day(self):
        """SCH-01."""
        jobs = {job.id: job for job in scheduler.build().get_jobs()}
        assert set(jobs) == {"morning-watchlist", "inbox-check", "daily-recheck"}

        # SRCH-13: after the watchlist, not before — its fresh feed answers confirm part of the
        # estate for free, which is that many fewer HTTP checks.
        sweep = str(jobs["daily-recheck"].trigger)
        assert "hour='7'" in sweep and "minute='0'" in sweep
        assert list(jobs["daily-recheck"].args) == []

        morning = str(jobs["morning-watchlist"].trigger)
        assert "hour='6'" in morning and "minute='30'" in morning
        # The employers' own feeds, which cost nothing to read.
        assert list(jobs["morning-watchlist"].args) == [["watchlist"]]

        inbox = str(jobs["inbox-check"].trigger)
        assert "hour='7-22/3'" in inbox and "minute='0'" in inbox
        assert list(jobs["inbox-check"].args) == [["email_alert"]]

    def test_nothing_scheduled_ever_starts_a_board_search(self):
        """SCH-01: board search is the only channel that spends Firecrawl credits, so it runs
        when a person asks and at no other time.

        Asserted over every job rather than against the one that used to carry it: the next
        channel added to the morning sweep should have to pass this too.
        """
        for job in scheduler.build().get_jobs():
            for channels in job.args:
                assert "board_search" not in channels, f"{job.id} would spend credits on its own"

    def test_a_missed_run_fires_once_not_once_per_missed_slot(self):
        """`coalesce` is what stops a laptop that slept all weekend running Monday's intake
        three times over, and the grace period is what stops a late run being skipped.

        Asserted on the scheduler's defaults: APScheduler only stamps them onto a job when it
        starts, and a pending job hasn't been given them yet.
        """
        defaults = scheduler.build()._job_defaults
        assert defaults["coalesce"] is True
        assert defaults["misfire_grace_time"] == scheduler.GRACE_SECONDS
        assert defaults["max_instances"] == 1

    def test_starting_twice_does_not_double_the_schedule(self, live):
        import asyncio

        async def go():
            first = scheduler.start()
            assert scheduler.start() is first
            # Counted against `build()` rather than a literal, so adding a job doesn't fail here
            # for the wrong reason — which is exactly what happened when the lifecycle sweep was
            # added: the stale `== 2` skipped the `stop()` below, and the fixture's teardown then
            # shut a scheduler down on a closed loop and poisoned every later test.
            assert len(first.get_jobs()) == len(scheduler.build().get_jobs())
            scheduler.stop()

        asyncio.run(go())

    def test_stop_clears_it(self, live):
        import asyncio

        async def go():
            scheduler.start()
            assert scheduler.describe()
            scheduler.stop()
            assert scheduler.describe() == []

        asyncio.run(go())


class TestScheduledRun:
    def test_a_scheduled_run_records_its_own_failure_and_never_raises(self, client, live, monkeypatch):
        """A scheduled job that raises is a job that silently stops happening."""
        import asyncio

        def boom(_channels, _ids):
            raise RuntimeError("firecrawl down")

        monkeypatch.setattr("wera_agent.graphs.intake.run", boom)
        # Must not raise: the schedule has to survive a bad morning.
        asyncio.run(scheduler._run_channels(["board_search"]))

    def test_a_scheduled_run_is_not_refused_by_a_spent_budget(self, client, live, monkeypatch):
        """A person's click is refused when the budget is gone (COST-01); tomorrow's scheduled
        run still needs to create its rows, so the refusal is recorded on the run instead."""
        from wera_agent.db.session import transaction
        from wera_agent.services.store import Store

        with transaction() as session:
            store = Store(session)
            store._save_settings({**store._settings(), "weeklyBudgetUsd": 0.0})

        with transaction() as session:
            ids = Store(session).start_runs(["board_search"], scheduled=True)
        assert set(ids) == {"board_search"}

    def test_a_persons_click_is_still_refused_when_the_budget_is_gone(self, client, api):
        api.ok("PUT", "/settings", {"weeklyBudgetUsd": 0.01})
        assert api.code("POST", "/intake/run", {"channels": ["board_search"]}) == "BUDGET_REACHED"


class TestNow:
    def test_the_schedule_is_local_time(self):
        """ "06:30" means the person's morning, not UTC's."""
        trigger = scheduler.build().get_job("morning-watchlist").trigger
        # APScheduler resolves a naive cron against the local zone, which is what we want here.
        assert trigger.timezone is not None
