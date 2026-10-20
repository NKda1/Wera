"""One intake run: find jobs, judge them, rank them (SRCH-08, SCH-02, SCH-04, UX-01).

The shape of the run matters more than any single step:

1. **Each channel is fetched on its own** and writes its own `intake_runs` row, so one channel
   failing never takes the others with it (SCH-04). A board that's down costs that board.
2. **Everything is collapsed together** before anything is scored, so a job found three ways is
   scored once (NORM-05/06, T-06).
3. **The cheap rules run before the expensive ones**: hard filters, then the embedding
   pre-filter, then the model. By the time a model is asked, the list is short.
4. **The previous list survives a failure.** Jobs are only written once the run has something
   to write, so a failed run leaves yesterday's shortlist exactly as it was (SCH-04).

Cancellation is checked between steps (UX-01): a person who changes their mind shouldn't have
to wait for a model call they no longer want.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import logfire

from ..clock import iso, now_ms
from ..config import companies, senders, sites, wera
from ..db.session import transaction
from ..domain import dedupe, filters
from ..errors import ApiError
from ..llm.gateway import AUTH, BUDGET, MESSAGES, QUOTA, RATE
from ..nodes import fit, prefilter
from ..schemas.jobs import Job
from ..schemas.llm_outputs import JobCardList
from ..schemas.profile import SearchProfile
from ..settings import get_settings
from ..sources import ats_feeds, firecrawl_search, inbox, normalise, resolve
from ..sources.firecrawl_search import SearchCaps

CHANNELS = ("board_search", "email_alert", "watchlist")

# Conditions that belong to the account rather than to the job in front of them: a rejected key,
# spent credit, a spent budget, a provider saying "slow down". Scoring stops at the first one.
STOPS_THE_RUN = frozenset({AUTH, QUOTA, BUDGET, RATE})


@dataclass
class ChannelOutcome:
    """What one channel produced, and what it cost."""

    channel: str
    jobs: list[Job] = field(default_factory=list)
    queries: int = 0
    results: int = 0
    credits_used: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    partial: bool = False
    last_uid: int | None = None
    # Messages read, for the inbox's line on the channel strip — distinct from `results`, which
    # counts the job cards those messages contained.
    emails: int = 0
    # The watchlist's own result, kept so closed-job detection can tell "gone from a feed that
    # answered" from "a feed that didn't answer" (ATS-03).
    feed: ats_feeds.FeedResult | None = None


class Cancelled(Exception):
    """Raised between steps when the person cancelled the run (UX-01)."""


# ---------------------------------------------------------------------------
# Channels


def from_watchlist(now: int) -> ChannelOutcome:
    """ATS-01: the employers' own boards."""
    out = ChannelOutcome(channel="watchlist")
    watched = companies()
    if not watched:
        return out
    result = ats_feeds.fetch_all(watched)
    out.results = len(result.jobs)
    out.jobs = [normalise.from_feed(feed, now) for feed in result.jobs]
    out.errors = [("WATCHLIST_FEED", message) for message in result.errors]
    out.feed = result
    return out


def from_board_search(profile: SearchProfile, known_urls: set[str], caps: SearchCaps, now: int) -> ChannelOutcome:
    """SRCH-01…07: search the enabled boards, then scrape only what's new."""
    out = ChannelOutcome(channel="board_search")
    outcome = firecrawl_search.search(profile, sites(), caps, known_urls)
    out.queries = outcome.queries_run
    out.results = len(outcome.hits)
    out.credits_used = outcome.credits_used
    out.errors = [("BOARD_SEARCH", message) for message in outcome.errors]

    for hit in outcome.hits:
        card = firecrawl_search.scrape_card(hit.url, firecrawl_search.CARD_SCHEMA, outcome, caps)
        if card is None:
            continue
        job = normalise.from_scrape(card, url=hit.url, site=hit.site, markdown=card.get("description"), now=now)
        if job is not None:
            out.jobs.append(job)
    out.credits_used = outcome.credits_used
    out.partial = outcome.partial
    return out


def from_inbox(session: Any, profile: SearchProfile, last_uid: int | None, now: int) -> ChannelOutcome:
    """MAIL-01…10: read the alerts folder, then resolve what it found."""
    out = ChannelOutcome(channel="email_alert")
    env = get_settings()
    password = env.alerts_imap_app_password.get_secret_value() if env.alerts_imap_app_password else ""
    if not (env.alerts_imap_host and env.alerts_imap_user and password):
        out.errors.append(("INBOX_NOT_CONFIGURED", "Set the inbox details to use the alert channel."))
        return out

    def extract(text: str) -> list[dict[str, Any]]:
        """MAIL-04's fallback, on the fast tier, with no tools and no URLs in the prompt."""
        from ..llm.gateway import structured
        from ..llm.prompts import untrusted

        reply = structured(
            session,
            "email.extract",
            JobCardList,
            {"email": untrusted("job alert email", text)},
            context={},
        )
        return [c.model_dump(by_alias=True) for c in reply.cards]

    result = inbox.check(
        host=env.alerts_imap_host,
        user=env.alerts_imap_user,
        password=password,
        folder=env.alerts_folder,
        senders=senders(),
        last_uid=last_uid,
        now=datetime.fromtimestamp(now / 1000, tz=UTC),
        extract=extract,
    )
    out.results = len(result.cards)
    out.emails = result.checked
    out.last_uid = result.last_uid
    out.errors = [("INBOX", message) for message in result.errors]
    if result.rejected_unauthenticated:
        logfire.info("{count} alert(s) failed sender authentication", count=result.rejected_unauthenticated)

    for meta, card in result.cards:
        found = resolve.resolve(title=card.title, company=card.company, url=card.url or card.linkedin_url)
        job = normalise.from_card(
            title=card.title,
            company=card.company,
            location_raw=card.location_raw,
            url=found.url if found.ats else card.url,
            detail=f"{meta.sender}-alert",
            now=now,
        )
        if card.linkedin_url:
            job.linkedin_url = card.linkedin_url  # MAIL-07
        if found.ats is not None:
            job.ats = found.ats
            job.dedupe_key = dedupe.key_for(job)
            job.id = normalise.job_id(job.dedupe_key)
        out.jobs.append(job)
    return out


# ---------------------------------------------------------------------------
# The run


def _check_cancelled(session: Any, run_ids: list[str]) -> None:
    from ..db.models import IntakeRunRow

    for run_id in run_ids:
        row = session.get(IntakeRunRow, run_id)
        if row is not None and row.status == "cancelled":
            raise Cancelled


@dataclass
class Judged:
    new_ids: set[str] = field(default_factory=set)
    scored: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def judge(session: Any, jobs: list[Job], *, now: int) -> Judged:
    """Filter, save, pre-filter, score, rank — in that order, and the order matters.

    The filters run first because they are free (MATCH-01). The jobs are **saved next**, before
    anything is scored: a fit report belongs to a job, so the job has to exist first, and it
    means a run whose scoring fails still leaves the jobs on the list rather than losing them.
    Only then is the expensive step reached, on a pre-filtered shortlist (MATCH-05).
    """
    from ..db import state
    from ..services.store import Store

    store = Store(session, now)
    cv = store.cv()
    profile = store.get_profile()
    saved = state.settings_data(session)
    weights = store.weights()
    model = saved["models"]["scoring"]
    demo_mode = get_settings().wera_demo_mode
    out = Judged()

    kept, hidden = filters.apply(jobs, profile)  # MATCH-01, before any model call
    saved_jobs, out.new_ids = store.upsert_jobs([*kept, *hidden])
    # Score what was stored, not what was found: a merged sighting kept the id it already had.
    scoreable = [j for j in saved_jobs if j.status == "new"]

    chosen = prefilter.shortlist(scoreable, cv, demo_mode=demo_mode, top_n=wera().max_scored_per_day)  # MATCH-05
    scored: list[tuple[Job, Any]] = []
    for position, job in enumerate(chosen):
        evidence, was_scored, code = fit.evidence_for(session, job, cv, model=model, now=now)
        if evidence is None:
            if code in STOPS_THE_RUN:
                # The key, the credit and the budget are the same for every job in the list, so
                # the other forty would fail identically. Stopping saves forty pointless round
                # trips and leaves one legible error instead of forty copies of it.
                remaining = len(chosen) - position
                out.errors.append((code, f"{MESSAGES[code]} {remaining} job(s) were left unscored."))
                break
            out.errors.append((code or "SCORE_FAILED", f"{job.title} at {job.company} couldn't be scored."))
            continue
        out.scored += 1 if was_scored else 0
        scored.append((job, fit.report_for(job, evidence, weights, now)))

    # Ranking is read back from the fit reports when the shortlist is listed, so the order here
    # only needs to exist — the store sorts on `fit.total`.
    fit.rank(scored)
    return out


async def run_intake(channels: list[str], run_ids: dict[str, str]) -> dict[str, Any]:
    """`run` on a worker thread, so a request can set a run off and return (UX-01)."""
    import asyncio

    try:
        return await asyncio.to_thread(run, channels, run_ids)
    finally:
        from ..llm.gateway import flush_unrecorded

        flush_unrecorded()


def run(channels: list[str], run_ids: dict[str, str]) -> dict[str, Any]:
    """One intake pass over the given channels. Each channel's row is updated as it finishes."""
    from ..db.models import IntakeRunRow
    from ..services.store import Store

    now = now_ms()
    cfg = wera()
    caps = SearchCaps(
        max_queries=cfg.max_queries,
        max_pages=cfg.max_pages,
        max_credits=cfg.max_credits_per_run,
    )
    summary: dict[str, Any] = {"new_jobs": 0, "scored": 0, "errors": []}

    with transaction() as session:
        store = Store(session, now)
        profile = store.get_profile()
        known_urls = store.known_apply_urls()
        last_uid = store.inbox_last_uid()

    outcomes: list[ChannelOutcome] = []
    for channel in channels:
        try:
            with transaction() as session:
                _check_cancelled(session, list(run_ids.values()))
            if channel == "watchlist":
                outcomes.append(from_watchlist(now))
            elif channel == "board_search":
                outcomes.append(from_board_search(profile, known_urls, caps, now))
            else:
                with transaction() as session:
                    outcomes.append(from_inbox(session, profile, last_uid, now))
        except Cancelled:
            logfire.info("Intake cancelled before {channel}", channel=channel)
            break
        except ApiError as exc:
            outcomes.append(ChannelOutcome(channel=channel, errors=[(exc.code, exc.message)]))
        except Exception as exc:  # noqa: BLE001 — one channel never takes the run with it
            logfire.exception("Channel {channel} failed", channel=channel)
            outcomes.append(ChannelOutcome(channel=channel, errors=[("CHANNEL_FAILED", type(exc).__name__)]))

    # Everything together, so a job found twice is scored once.
    found = dedupe.collapse([job for outcome in outcomes for job in outcome.jobs])

    with transaction() as session:
        store = Store(session, now)
        try:
            _check_cancelled(session, list(run_ids.values()))
            judged = judge(session, found, now=now)
        except Cancelled:
            return summary
        new_ids, score_errors = judged.new_ids, judged.errors
        summary["new_jobs"] = len(new_ids)
        summary["scored"] = judged.scored
        summary["errors"] = score_errors

        # ATS-03, with the real feed: only a board that answered can close anything.
        watchlist = next((o.feed for o in outcomes if o.channel == "watchlist" and o.feed is not None), None)
        summary["closed"] = store.close_jobs(ats_feeds.closed_tokens(watchlist, store.watchlist_ats_by_job())) if watchlist else 0

        for outcome in outcomes:
            row = session.get(IntakeRunRow, run_ids.get(outcome.channel, ""))
            if row is None:
                continue
            row.queries = outcome.queries
            row.results = outcome.results
            row.credits_used = outcome.credits_used
            row.new_jobs = len([j for j in found if j.id in new_ids and outcome.channel in {s.channel for s in j.sources}])
            row.errors = [{"code": code, "message": message} for code, message in [*outcome.errors, *score_errors]]
            row.progress = 1
            row.finished_at = iso(now_ms())
            row.settled = True
            # Finding jobs and scoring them fail separately. A channel that fetched nothing has
            # failed; one that fetched a list it couldn't finish scoring is partial, not a
            # success — "succeeded" next to forty unscored jobs reads as a lie.
            row.status = (
                "failed" if outcome.errors and not outcome.jobs else "partial" if outcome.partial or score_errors else "succeeded"
            )
            if outcome.last_uid is not None:
                store.set_inbox_last_uid(outcome.last_uid)
            if not get_settings().wera_demo_mode:
                store.record_channel_stats(
                    outcome.channel,
                    queries=outcome.queries,
                    results=outcome.results,
                    new_jobs=row.new_jobs,
                    emails=outcome.emails,
                )
        if not get_settings().wera_demo_mode:
            store.set_last_run_jobs(sorted(new_ids))
        session.flush()

    logfire.info(
        "Intake finished: {new} new job(s), {scored} scored",
        new=summary["new_jobs"],
        scored=summary["scored"],
    )
    return summary
