"""When intake runs on its own (SCH-01).

The watchlist once a day, early, so a shortlist is waiting in the morning. The inbox every three
hours through the day, because an alert email arrives when it arrives and a graduate vacancy can
close in a week.

**Board search runs on its own only when the person has said so.** It is the channel that spends
Firecrawl credits, and a bill is a decision, not a background task — so it is off until they turn
on the scheduled search in Settings and choose when (`settings.searchSchedule`). Then each run is
the whole search pipeline (SRCH-20): the Strategist's plan, refreshed if the CV or profile changed
since it was made; its searches through Serper; the Harvester reading what they found; and 8–12
jobs scored. What bounds it is the weekly Firecrawl ceiling (`max_credits_per_week`) and the
Serper cap per run, which a schedule needs and a click didn't.

Changing the schedule in Settings applies at once (`apply_search_schedule`), with no restart.

Two settings do the real work:

* `coalesce` — a laptop that was asleep at 06:30 runs the morning job **once** when it wakes,
  not once for every slot it missed.
* `misfire_grace_time` — a run that starts late still counts, instead of being skipped for being
  a few minutes behind.

Off in demo mode: a demo that quietly made network calls in the background would be a lie
(DEMO-02).
"""

from typing import Any

import logfire
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..settings import get_settings

# SCH-01. Local time, because "06:30" means the person's morning.
WATCHLIST_HOUR, WATCHLIST_MINUTE = 6, 30
INBOX_HOURS = "7-22/3"

# SRCH-13: the lifecycle sweep, after the watchlist rather than before it — the watchlist's fresh
# feed answers confirm a chunk of the estate for free, which is that many fewer HTTP checks.
RECHECK_HOUR, RECHECK_MINUTE = 7, 0

# REL-05: the nightly database backup, before the morning's jobs start writing.
BACKUP_HOUR, BACKUP_MINUTE = 3, 0

# OBS-01: the Logfire archive, after the backup rather than during it, so a dump is never taken
# halfway through a night's worth of records.
ARCHIVE_HOUR, ARCHIVE_MINUTE = 3, 15
ARCHIVE_JOB = "telemetry-archive"
CATCH_UP_JOB = "telemetry-catch-up"
# Long enough for the app to finish starting; the catch-up is a background read, not a gate.
CATCH_UP_DELAY_SECONDS = 60

# A missed run is still worth doing when the machine comes back.
GRACE_SECONDS = 60 * 60

_scheduler: AsyncIOScheduler | None = None


async def _run_channels(channels: list[str]) -> None:
    """One scheduled pass. Failures are swallowed deliberately: a scheduled run that raises
    would kill the job, and the next morning would be silent."""
    from ..db.session import transaction
    from ..graphs import intake
    from ..services.store import Store

    try:
        with transaction() as session:
            store = Store(session)
            ids = store.start_runs(channels, scheduled=True)
        import asyncio

        await asyncio.to_thread(intake.run, channels, ids)
    except Exception:  # noqa: BLE001 — logged, never raised into the scheduler
        logfire.exception("Scheduled intake for {channels} failed", channels=channels)


def build() -> AsyncIOScheduler:
    """The schedule, configured but not running.

    Separate from `start` because starting an asyncio scheduler needs a running event loop,
    which the app's lifespan has and a test asserting on cron fields shouldn't need.
    """
    scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "misfire_grace_time": GRACE_SECONDS, "max_instances": 1})
    scheduler.add_job(
        _run_channels,
        CronTrigger(hour=WATCHLIST_HOUR, minute=WATCHLIST_MINUTE),
        # The watchlist alone. `board_search` is deliberately absent — see the module docstring.
        args=[["watchlist"]],
        id="morning-watchlist",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_channels,
        CronTrigger(hour=INBOX_HOURS, minute=0),
        args=[["email_alert"]],
        id="inbox-check",
        replace_existing=True,
    )
    scheduler.add_job(
        _recheck,
        CronTrigger(hour=RECHECK_HOUR, minute=RECHECK_MINUTE),
        id="daily-recheck",
        replace_existing=True,
    )
    scheduler.add_job(
        _backup,
        CronTrigger(hour=BACKUP_HOUR, minute=BACKUP_MINUTE),
        id="nightly-backup",
        replace_existing=True,
    )
    scheduler.add_job(
        _archive,
        CronTrigger(hour=ARCHIVE_HOUR, minute=ARCHIVE_MINUTE),
        id=ARCHIVE_JOB,
        replace_existing=True,
    )
    return scheduler


SEARCH_JOB = "scheduled-search"


async def _scheduled_search() -> None:
    """The person's scheduled job search: one pass of the search pipeline (SRCH-20).

    Nothing to do here but start it. Stage 1 of the pipeline refreshes the Strategist's plan when
    the CV or profile has changed, so a schedule that runs every weekday pays for a plan once, not
    five times — and a plan that can't be made (budget, provider) doesn't stop the search: the
    queries are built from the CV and profile alone.
    """
    with logfire.span("scheduled search"):
        await _run_channels(["board_search"])


def apply_search_schedule(schedule: dict[str, Any]) -> None:
    """Add, move or remove the scheduled search to match the person's setting. Applies at once;
    a no-op in demo mode, where nothing is scheduled at all."""
    if _scheduler is None:
        return
    if _scheduler.get_job(SEARCH_JOB) is not None:
        _scheduler.remove_job(SEARCH_JOB)
    if not schedule.get("enabled"):
        logfire.info("Scheduled search is off")
        return
    cron = str(schedule.get("cron") or "")
    try:
        trigger = CronTrigger.from_crontab(cron)
    except ValueError:
        logfire.warn("Scheduled search not set: {cron!r} isn't a schedule", cron=cron)
        return
    job = _scheduler.add_job(_scheduled_search, trigger, id=SEARCH_JOB, replace_existing=True)
    logfire.info("Scheduled search set: {cron}, next at {next}", cron=cron, next=str(getattr(job, "next_run_time", None)))


def next_search_at(schedule: dict[str, Any]) -> str | None:
    """When the scheduled search next runs, for the Settings screen. Computed from the setting
    itself, so it is right whether or not this process's scheduler is the one running it."""
    from datetime import datetime

    if not schedule.get("enabled"):
        return None
    try:
        trigger = CronTrigger.from_crontab(str(schedule.get("cron") or ""))
    except ValueError:
        return None
    now = datetime.now().astimezone()
    at = trigger.get_next_fire_time(None, now)
    return at.isoformat() if at else None


async def _recheck() -> None:
    """SRCH-13: ask whether the jobs WERA already found still exist. Free, and its own job
    rather than a channel — it fetches nothing new, so it has no `intake_runs` row to write."""
    from ..graphs import recheck

    try:
        await recheck.run_async()
    except Exception:  # noqa: BLE001 — logged, never raised into the scheduler
        logfire.exception("Lifecycle sweep failed")


async def _backup() -> None:
    """REL-05: tonight's `pg_dump`. Off the event loop, since a dump can take a while."""
    import asyncio

    from ..db.backup import backup

    try:
        await asyncio.to_thread(backup)
    except Exception:  # noqa: BLE001 — logged, never raised into the scheduler
        logfire.exception("Nightly database backup failed")


async def _archive() -> None:
    """OBS-01: copy Logfire's new records into `telemetry.records`. Without a read token there is
    nothing to do, and that is said once rather than raised."""
    import asyncio

    from ..services import telemetry_archive

    reason = telemetry_archive.refusal()
    if reason:
        logfire.info("Telemetry archive skipped: {reason}", reason=reason)
        return
    try:
        await asyncio.to_thread(telemetry_archive.archive)
    except Exception:  # noqa: BLE001 — logged, never raised into the scheduler
        logfire.exception("Telemetry archive failed")


def _archive_due() -> bool:
    """A laptop asleep at 03:15 for a week still archives that week: on the next start, if the
    last archive is more than a day old. Logfire keeps thirty days, so a missed night is only
    lost if the machine stays off for a month."""
    from ..services import telemetry_archive

    try:
        return telemetry_archive.due()
    except Exception:  # noqa: BLE001 — a schedule that can't read its state still starts
        logfire.exception("Couldn't read the telemetry archive's state")
        return False


def start() -> AsyncIOScheduler | None:
    """Starts the schedule, or doesn't, in demo mode. Idempotent."""
    global _scheduler
    if get_settings().wera_demo_mode:
        logfire.info("Demo mode: intake is not scheduled")
        return None
    if _scheduler is not None:
        return _scheduler
    scheduler = build()
    scheduler.start()
    _scheduler = scheduler
    logfire.info(
        "Intake scheduled: watchlist at {hour}:{minute:02d}, inbox at {inbox}, lifecycle sweep at {recheck}:00, "
        "backup at {backup}:00, telemetry archive at {archive}:{archive_minute:02d}.",
        hour=WATCHLIST_HOUR,
        minute=WATCHLIST_MINUTE,
        inbox=INBOX_HOURS,
        recheck=RECHECK_HOUR,
        backup=BACKUP_HOUR,
        archive=ARCHIVE_HOUR,
        archive_minute=ARCHIVE_MINUTE,
    )
    apply_search_schedule(_stored_search_schedule())
    if _archive_due():
        from datetime import datetime, timedelta

        scheduler.add_job(
            _archive,
            "date",
            run_date=datetime.now().astimezone() + timedelta(seconds=CATCH_UP_DELAY_SECONDS),
            id=CATCH_UP_JOB,
            replace_existing=True,
        )
    return scheduler


def _stored_search_schedule() -> dict[str, Any]:
    """The person's saved schedule, or "off" if it can't be read — a schedule that can't be read
    is not one to guess at."""
    from ..db import state
    from ..db.session import transaction

    try:
        with transaction() as session:
            return dict(state.settings_data(session).get("searchSchedule") or {})
    except Exception:  # noqa: BLE001
        logfire.exception("Couldn't read the scheduled search setting")
        return {}


def stop() -> None:
    """Shut the schedule down, and forget it even if shutting down failed.

    `AsyncIOScheduler.shutdown` posts to the loop it was started on, so a scheduler outliving
    that loop — a test, or a reload — raises "Event loop is closed". The global is cleared either
    way: leaving a scheduler bound to a dead loop in place means every later `start()` hands back
    the dead one, and one failed shutdown becomes every subsequent failure.
    """
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001 — a scheduler we can't reach is one we must still drop
        logfire.info("Scheduler was already gone with its event loop")
    finally:
        _scheduler = None


def describe() -> list[dict[str, Any]]:
    """What is scheduled and when it next fires, for diagnostics and tests."""
    if _scheduler is None:
        return []
    return [{"id": job.id, "next": str(getattr(job, "next_run_time", None))} for job in _scheduler.get_jobs()]
