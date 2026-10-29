"""The lifecycle sweep: asking whether the jobs WERA already found still exist.

Intake finds jobs. Nothing re-examined them. A posting a person was still deciding about could
have been taken down weeks earlier and WERA would have gone on ranking it, because closure
(ATS-03) needed an ATS identity that `board_search` jobs never have, and that was the only
re-check there was.

This is the other half of intake, and it is **free** — the employers' own public feeds and plain
HTTP, never Firecrawl (SRCH-13, ATS-06, NORM-07).

Deliberately **not** an intake channel. `CHANNELS` maps one-to-one onto `intake_runs` rows and
onto `ProfileChannels`, a validated user-facing toggle; a fourth channel would mean a contract
change, three more checkboxes, and a run row for something that fetched no jobs. It also has to
run when the watchlist channel is switched off, because jobs go stale regardless of how they were
found. So: its own graph, its own cron, and its figures in `kv` rather than in a phantom run.

Order matters and is cheapest-first:

1. **Feeds**, one fetch per employer. Positive evidence, exact dates, and it costs one request
   per board however many jobs sit behind it.
2. **HTTP**, for everything a feed can't answer for — the board-search jobs that were previously
   uncheckable at all.
3. **Archive**, which only now has an honest clock to read (see `Store.archive_stale`).
"""

from typing import Any

import logfire

from ..clock import iso, now_ms, parse_ms
from ..db.session import transaction
from ..settings import get_settings

# How long before the sweep will run again. SPEC §11.3 permits polling "at most daily", so the
# floor is the compliance boundary, not a performance tweak — and it is enforced here rather than
# by the cron being the only caller, so `wera recheck` is safe to run by hand.
MIN_HOURS_BETWEEN = 20

# Committed per board and per batch of HTTP checks rather than once at the end. A sweep that dies
# halfway must keep the answers it already spent wall-clock time on, which is `judge`'s reasoning
# about scoring applied to the same problem.
HTTP_BATCH = 25


def due(last: str | None, now: int) -> bool:
    """Whether the sweep may run. Demo mode never sweeps: it makes no outbound request (DEMO-02)."""
    if get_settings().wera_demo_mode:
        return False
    if not last:
        return True
    try:
        return now - parse_ms(last) >= MIN_HOURS_BETWEEN * 60 * 60 * 1000
    except (ValueError, TypeError):
        return True


def run(*, force: bool = False) -> dict[str, Any]:
    """One pass. Returns what it did; raises nothing a caller needs to handle."""
    from ..config import wera
    from ..services.store import Store

    now = now_ms()
    cfg = wera().lifecycle

    with transaction() as session:
        store = Store(session, now)
        if not force and not due(store.rechecked_at(), now):
            return {"skipped": "not due"}

    summary = {"checked": 0, "closed": 0, "confirmed": 0, "unconfirmed": 0, "archived": 0}

    summary["closed"] += _by_feed(now, cfg.max_boards_per_sweep, summary)
    _by_http(now, cfg.max_http_checks, summary)

    with transaction() as session:
        store = Store(session, now)
        summary["archived"] = store.archive_stale(cfg.archive_after_days)
        store.set_recheck(summary)

    logfire.info(
        "Lifecycle sweep: {checked} checked, {closed} closed, {unconfirmed} unconfirmed, {archived} archived",
        **summary,
    )
    return summary


def _by_feed(now: int, max_boards: int, summary: dict[str, int]) -> int:
    """ATS-03 + ATS-06: close what an employer's own feed no longer lists, and date the rest.

    `closed_tokens` needs no change — only the `FeedResult` handed to it gets wider, because
    `fetch_boards` is given every board a stored job names rather than only the watched ones.
    """
    from ..services.store import Store
    from ..sources import ats_feeds

    with transaction() as session:
        known = Store(session, now).ats_by_job()
    if not known:
        return 0

    # Worst-informed first, so a capped sweep still makes progress round the estate.
    boards = [f"{a.vendor}:{a.board_token}" for a in known.values()]
    result = ats_feeds.fetch_boards(boards, limit=max_boards)
    if result.errors:
        logfire.info("{count} board(s) didn't answer; nothing closed for them", count=len(result.errors))

    gone = ats_feeds.closed_tokens(result, known)
    live = {j for j in known if j not in gone and _board_of(known[j]) in result.answered}

    with transaction() as session:
        store = Store(session, now)
        closed = store.close_jobs(gone)
        for job_id in live:
            store.record_liveness(job_id, confirmed=True)
    summary["checked"] += len(live) + len(gone)
    summary["confirmed"] += len(live)
    return closed


def _board_of(ats: Any) -> str:
    return f"{ats.vendor}:{ats.board_token}"


def _by_http(now: int, max_checks: int, summary: dict[str, int]) -> None:
    """SRCH-13, for the jobs no feed can speak for — which is most board-search jobs.

    A `gone` verdict closes; `likely_gone` and `unknown` only record that the question was asked.
    That asymmetry is the whole policy: a board that blocks a bot must never prune a shortlist.
    """
    from ..services.store import Store
    from ..sources import liveness as live

    with transaction() as session:
        store = Store(session, now)
        # Jobs a feed already answered for were confirmed moments ago, so they sort last anyway.
        candidates = [j for j in store.recheck_candidates(max_checks) if j.ats is None and j.apply_url]

    verdicts: list[tuple[str, str]] = []
    for job in candidates:
        verdicts.append((job.id, live.check(job.apply_url)))
        if len(verdicts) >= HTTP_BATCH:
            _apply(now, verdicts, summary)
            verdicts = []
    if verdicts:
        _apply(now, verdicts, summary)


def _apply(now: int, verdicts: list[tuple[str, str]], summary: dict[str, int]) -> None:
    from ..services.store import Store

    with transaction() as session:
        store = Store(session, now)
        gone = [job_id for job_id, verdict in verdicts if verdict == "gone"]
        summary["closed"] += store.close_jobs(gone)
        for job_id, verdict in verdicts:
            if verdict == "gone":
                continue
            store.record_liveness(job_id, confirmed=verdict == "open")
            summary["confirmed" if verdict == "open" else "unconfirmed"] += 1
        summary["checked"] += len(verdicts)


async def run_async(*, force: bool = False) -> dict[str, Any]:
    """On a worker thread, so the scheduler's event loop isn't blocked by HTTP."""
    import asyncio

    return await asyncio.to_thread(run, force=force)


def describe(last: dict[str, Any]) -> str:
    """One readable line for the Jobs screen, or empty when there is nothing to say."""
    if not last or not last.get("checked"):
        return ""
    parts = [f"Checked {last['checked']} posting(s)"]
    if last.get("closed"):
        parts.append(f"{last['closed']} closed")
    if last.get("unconfirmed"):
        parts.append(f"{last['unconfirmed']} couldn't be confirmed")
    if last.get("archived"):
        parts.append(f"{last['archived']} archived")
    return ", ".join(parts) + "."


__all__ = ["describe", "due", "iso", "run", "run_async"]
