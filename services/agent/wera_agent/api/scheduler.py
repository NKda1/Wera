"""When intake runs on its own (SCH-01).

The watchlist once a day, early, so a shortlist is waiting in the morning. The inbox every three
hours through the day, because an alert email arrives when it arrives and a graduate vacancy can
close in a week.

**Board search is not here, and that is the point.** It is the only channel that spends Firecrawl
credits — up to `max_credits_per_run`, 150 of them — and a bill that size is a decision, not a
background task. It runs when a person starts it and at no other time. The watchlist reads the
employers' own feeds, which cost nothing, so it keeps its morning slot.

One thing that follows: board search now always takes the person-clicked budget path, so a spent
weekly budget *refuses* the run with `BUDGET_REACHED` rather than recording the refusal on a row
nobody reads (COST-01, `Store._start_runs`). That is the better way round — the person finds out
when they ask, not tomorrow.

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
    return scheduler


async def _recheck() -> None:
    """SRCH-13: ask whether the jobs WERA already found still exist. Free, and its own job
    rather than a channel — it fetches nothing new, so it has no `intake_runs` row to write."""
    from ..graphs import recheck

    try:
        await recheck.run_async()
    except Exception:  # noqa: BLE001 — logged, never raised into the scheduler
        logfire.exception("Lifecycle sweep failed")


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
        "Intake scheduled: watchlist at {hour}:{minute:02d}, inbox at {inbox}, "
        "lifecycle sweep at {recheck}:00. Board search is manual.",
        hour=WATCHLIST_HOUR,
        minute=WATCHLIST_MINUTE,
        inbox=INBOX_HOURS,
        recheck=RECHECK_HOUR,
    )
    return scheduler


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
