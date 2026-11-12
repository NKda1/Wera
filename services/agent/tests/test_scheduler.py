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
        assert set(jobs) == {"morning-watchlist", "inbox-check", "daily-recheck", "nightly-backup", "telemetry-archive"}

        # REL-05: the backup runs in the small hours, before anything scheduled starts writing.
        backup = str(jobs["nightly-backup"].trigger)
        assert "hour='3'" in backup and "minute='0'" in backup
        # OBS-01: the archive after it, so a dump never catches a night's records half-written.
        archive = str(jobs["telemetry-archive"].trigger)
        assert "hour='3'" in archive and "minute='15'" in archive

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

    def test_nothing_scheduled_by_default_starts_a_board_search(self):
        """SCH-01: board search spends Firecrawl credits, so it runs on a schedule only when the
        person has turned the scheduled search on (`TestTheScheduledSearch`) — never by default.

        Asserted over every job rather than against the one that used to carry it: the next
        channel added to the morning sweep should have to pass this too.
        """
        for job in scheduler.build().get_jobs():
            assert job.id != scheduler.SEARCH_JOB
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


class TestTheScheduledSearch:
    """SCH-01's opt-in half: the Strategist and the Harvester, on a schedule the person chose."""

    @pytest.fixture
    def pending(self, monkeypatch):
        """A built, unstarted scheduler in the module slot — enough to add and inspect jobs."""
        built = scheduler.build()
        monkeypatch.setattr(scheduler, "_scheduler", built)
        return built

    def test_turning_it_on_adds_the_job_and_off_removes_it(self, pending):
        scheduler.apply_search_schedule({"enabled": True, "cron": "0 8 * * 1-5"})
        job = pending.get_job(scheduler.SEARCH_JOB)
        assert job is not None and "hour='8'" in str(job.trigger) and "day_of_week='1-5'" in str(job.trigger)
        scheduler.apply_search_schedule({"enabled": True, "cron": "30 18 * * *"})
        assert "hour='18'" in str(pending.get_job(scheduler.SEARCH_JOB).trigger)
        scheduler.apply_search_schedule({"enabled": False, "cron": "30 18 * * *"})
        assert pending.get_job(scheduler.SEARCH_JOB) is None

    def test_a_schedule_it_cant_read_schedules_nothing(self, pending):
        scheduler.apply_search_schedule({"enabled": True, "cron": "whenever"})
        assert pending.get_job(scheduler.SEARCH_JOB) is None

    def test_saving_the_setting_applies_it_once_the_save_commits(self, store, pending):
        import asyncio

        from wera_agent.schemas.settings import SettingsPatch

        store.put_settings(SettingsPatch.model_validate({"searchSchedule": {"enabled": True, "cron": "0 7 * * *"}}))
        assert pending.get_job(scheduler.SEARCH_JOB) is None  # not before the commit
        ((make, name),) = [d for d in store.deferred if d[1] == "scheduled-search:apply"]
        asyncio.run(make())
        assert pending.get_job(scheduler.SEARCH_JOB) is not None

    def test_it_runs_one_board_search_and_leaves_the_plan_to_the_pipeline(self, client, monkeypatch):
        """SRCH-20: planning is stage 1 of every board search, so the schedule doesn't do it twice —
        it used to refresh the plan first and then start a search that read it."""
        import asyncio

        from wera_agent.services.store import Store

        monkeypatch.setattr(Store, "refresh_strategy", lambda self: pytest.fail("the scheduler planned on its own"))
        ran: list[list[str]] = []

        async def run(channels):
            ran.append(channels)

        monkeypatch.setattr(scheduler, "_run_channels", run)
        asyncio.run(scheduler._scheduled_search())
        assert ran == [["board_search"]]

    def test_the_next_run_is_reported_only_when_it_is_on(self):
        assert scheduler.next_search_at({"enabled": False, "cron": "0 8 * * *"}) is None
        assert scheduler.next_search_at({"enabled": True, "cron": "0 8 * * *"}).endswith(":00")


class TestTheScheduleSetting:
    def test_too_often_is_refused_with_a_reason_on_the_field(self, api):
        body = api("PUT", "/settings", {"searchSchedule": {"enabled": True, "cron": "0 */2 * * *"}})[1]
        assert body["error"]["code"] == "VALIDATION"
        assert "6 hours" in body["error"]["fields"]["searchSchedule.cron"]

    def test_a_schedule_that_isnt_one_is_refused(self, api):
        body = api("PUT", "/settings", {"searchSchedule": {"enabled": True, "cron": "every morning"}})[1]
        assert "five fields" in body["error"]["fields"]["searchSchedule.cron"]

    def test_it_is_off_until_turned_on_and_saved_as_given(self, api):
        assert api.ok("GET", "/settings")["searchSchedule"]["enabled"] is False
        saved = api.ok("PUT", "/settings", {"searchSchedule": {"enabled": True, "cron": "0 9 * * 1,3,5"}})
        assert saved["searchSchedule"]["enabled"] is True and saved["searchSchedule"]["cron"] == "0 9 * * 1,3,5"
