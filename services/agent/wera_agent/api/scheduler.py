"""When intake runs on its own (SCH-01).

Board search and the watchlist once a day, early, so a shortlist is waiting in the morning. The
inbox every three hours through the day, because an alert email arrives when it arrives and a
graduate vacancy can close in a week.

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
BOARD_HOUR, BOARD_MINUTE = 6, 30
INBOX_HOURS = "7-22/3"

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
        CronTrigger(hour=BOARD_HOUR, minute=BOARD_MINUTE),
        args=[["board_search", "watchlist"]],
        id="morning-intake",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_channels,
        CronTrigger(hour=INBOX_HOURS, minute=0),
        args=[["email_alert"]],
        id="inbox-check",
        replace_existing=True,
    )
    return scheduler


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
        "Intake scheduled: boards at {hour}:{minute:02d}, inbox at {inbox}",
        hour=BOARD_HOUR,
        minute=BOARD_MINUTE,
        inbox=INBOX_HOURS,
    )
    return scheduler


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def describe() -> list[dict[str, Any]]:
    """What is scheduled and when it next fires, for diagnostics and tests."""
    if _scheduler is None:
        return []
    return [{"id": job.id, "next": str(getattr(job, "next_run_time", None))} for job in _scheduler.get_jobs()]
