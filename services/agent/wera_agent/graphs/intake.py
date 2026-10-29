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
from typing import Any, Protocol

import logfire

from ..clock import iso, now_ms, parse_ms
from ..config import companies, companies_file, senders, sites, wera, write_companies
from ..db.session import transaction
from ..domain import dedupe, filters
from ..errors import ApiError
from ..llm.gateway import AUTH, BUDGET, MESSAGES, QUOTA, RATE
from ..nodes import fit, prefilter
from ..schemas.jobs import Job
from ..schemas.llm_outputs import JobCardList
from ..schemas.profile import SearchProfile
from ..settings import get_settings
from ..sources import ats_discovery, ats_feeds, crawl_cache, firecrawl_search, inbox, normalise, resolve
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
    # Boards this run added to the watchlist (ATS-05).
    discovered: int = 0
    # SRCH-10: what the crawl cache answered for free, in Firecrawl's credit units.
    credits_saved: int = 0


class Cancelled(Exception):
    """Raised between steps when the person cancelled the run (UX-01)."""


class Progress(Protocol):
    """How a channel says how far through itself it is, as a fraction of its own work.

    The channel doesn't know which slice of the bar it owns — `run` does, because only it knows
    how many channels there are — so a channel reports 0…1 of itself and the caller maps that
    onto the run's bar.
    """

    def __call__(self, fraction: float, *, done: int, total: int, results: int) -> None: ...


# ---------------------------------------------------------------------------
# Channels


def watch_titles(profile: SearchProfile) -> list[str]:
    """What the watchlist looks for: the file's own list, or the search profile's titles.

    Defaulting to the profile is what ties the watchlist to the CV — upload a new CV, approve
    the regenerated profile, and the boards WERA watches follow (CV-03, PRF-04).
    """
    configured = [t for t in companies_file().watch_titles if t.strip()]
    return configured or [t for t in profile.titles if t.strip()]


def discovery_due(last_run: str | None, now: int) -> bool:
    """ATS-05: weekly by default, and immediately on an install that has never run it."""
    every = companies_file().discover_every_days
    if every <= 0 or get_settings().wera_demo_mode:
        return False  # DEMO-02: the demo never searches
    if not last_run:
        return True
    try:
        return now - parse_ms(last_run) >= every * 24 * 60 * 60 * 1000
    except (ValueError, TypeError):
        return True


def run_discovery(profile: SearchProfile, now: int) -> tuple[ats_discovery.DiscoveryOutcome, int]:
    """Searches for boards hiring for the watch titles and rewrites companies.yaml (ATS-05)."""
    cfg, file = wera(), companies_file()
    titles = watch_titles(profile)
    if not titles:
        return ats_discovery.DiscoveryOutcome(), len(file.companies)

    merged, outcome = ats_discovery.discover(
        titles,
        profile.locations,
        file.companies,
        max_queries=cfg.discovery_max_queries,
        max_companies=file.max_companies,
        credits_per_search=firecrawl_search.SEARCH_CREDITS,
        today=datetime.fromtimestamp(now / 1000, tz=UTC).date(),
    )
    # Only rewrite when something actually changed: an unchanged file keeps its mtime, which
    # keeps `git status` quiet on the mornings discovery found nothing new.
    if [c.model_dump() for c in merged] != [c.model_dump() for c in file.companies]:
        write_companies(file.model_copy(update={"companies": merged}))
    return outcome, len(merged)


def from_watchlist(profile: SearchProfile, now: int, *, discover: bool = False) -> ChannelOutcome:
    """ATS-01: the employers' own boards, and ATS-05: finding which boards those are."""
    out = ChannelOutcome(channel="watchlist")
    if discover:
        try:
            found, _total = run_discovery(profile, now)
            out.queries = found.queries
            out.credits_used = found.credits_used
            out.discovered = len([c for c in found.found if c.discovered_on])
            out.errors += [("BOARD_DISCOVERY", message) for message in found.errors]
        except Exception as exc:  # noqa: BLE001 — discovery failing must not cost the watchlist
            logfire.exception("Board discovery failed")
            out.errors.append(("BOARD_DISCOVERY", f"Couldn't search for new boards ({type(exc).__name__})."))

    watched = companies()
    if not watched:
        return out
    result = ats_feeds.fetch_all(watched)
    out.results = len(result.jobs)
    out.jobs = [normalise.from_feed(feed, now) for feed in result.jobs]
    out.errors += [("WATCHLIST_FEED", message) for message in result.errors]
    out.feed = result
    return out


def sites_due(last_run: str | None, now: int) -> bool:
    """SRCH-12: weekly by default, and on the first board search an install ever runs.

    Mirrors `discovery_due` exactly, including the demo guard — and for the same reason it is
    weekly rather than per-run: the constraint is the person who has to review a proposal, not
    the four credits. A queue nobody clears is indistinguishable from a broken feature.
    """
    from ..config import sites_file

    every = sites_file().discover_every_days
    if every <= 0 or get_settings().wera_demo_mode:
        return False  # DEMO-02: the demo never searches
    if not last_run:
        return True
    try:
        return now - parse_ms(last_run) >= every * 24 * 60 * 60 * 1000
    except (ValueError, TypeError):
        return True


def run_site_discovery(profile: SearchProfile, now: int) -> tuple[int, int, list[tuple[str, str]]]:
    """Propose new boards and write them unreviewed. Returns (proposed, credits, errors).

    Rides the board-search channel rather than the watchlist, because it spends Firecrawl
    credits and the whole point of the scheduler's shape is that credit spend happens when a
    person clicks (see `api/scheduler.py`). It also means they are present to see the proposal.
    """
    from ..config import sites_file, write_sites
    from ..sources import site_discovery

    file = sites_file()
    merged, outcome = site_discovery.discover(profile, file, today=datetime.fromtimestamp(now / 1000, tz=UTC).date())
    if outcome.found:
        # Append-only, so this can never cost someone a board they chose.
        write_sites(merged)
    return len(outcome.found), outcome.credits_used, [("SITE_DISCOVERY", m) for m in outcome.errors]


def from_board_search(
    profile: SearchProfile,
    known_urls: set[str],
    caps: SearchCaps,
    now: int,
    cache: "crawl_cache.CrawlCache | None" = None,
    tick: Progress | None = None,
    discover: bool = False,
) -> ChannelOutcome:
    """SRCH-01…07: search the enabled boards, then scrape only what's new.

    `tick` is how the bar moves. This channel is the slow one — twenty queries and up to thirty
    scrapes — so it reports inside itself rather than only on the way out.
    """
    out = ChannelOutcome(channel="board_search")

    if discover:
        try:
            found, credits, errors = run_site_discovery(profile, now)
            out.discovered = found
            out.credits_used += credits
            out.errors += errors
        except Exception as exc:  # noqa: BLE001 — looking for boards must not cost the search
            logfire.exception("Site discovery failed")
            out.errors.append(("SITE_DISCOVERY", f"Couldn't look for new boards ({type(exc).__name__})."))

    def on_query(outcome: firecrawl_search.SearchOutcome, answered: int, planned: int) -> None:
        if tick is not None:
            tick(QUERY_SHARE * answered / max(planned, 1), done=answered, total=planned, results=len(outcome.hits))

    outcome = firecrawl_search.search(profile, sites(), caps, known_urls, cache=cache, on_query=on_query)
    out.results = len(outcome.hits)
    out.errors = [("BOARD_SEARCH", message) for message in outcome.errors]

    planned = outcome.queries_answered
    for index, hit in enumerate(outcome.hits):
        card = firecrawl_search.scrape_card(hit.url, firecrawl_search.CARD_SCHEMA, outcome, caps, cache=cache)
        if tick is not None:
            tick(
                QUERY_SHARE + (1 - QUERY_SHARE) * (index + 1) / len(outcome.hits),
                done=planned,
                total=planned,
                results=len(outcome.hits),
            )
        if card is None:
            continue
        job = normalise.from_scrape(card, url=hit.url, site=hit.site, markdown=card.get("description"), now=now)
        if job is not None:
            out.jobs.append(job)
    # Queries *answered*, not queries sent. `intake_runs.queries` is what the Jobs screen shows,
    # and a warm run reporting "0 / 20 queries" reads exactly like a run that did nothing. The
    # narrower `queries_run` stays inside `SearchOutcome`, where the caps use it.
    out.queries = outcome.queries_answered
    # `+=`, not `=`: site discovery may already have spent some of this channel's credits, and
    # a plain assignment would hide them from the run's total and from the cap's arithmetic.
    out.credits_used += outcome.credits_used
    out.credits_saved = outcome.credits_saved
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


# How a run's 0–1 bar is divided. Scoring is the long phase — 40 jobs at roughly eleven seconds
# each — so fetching is deliberately given a small slice. A bar that sat at 0 for seven minutes
# and then jumped to 1 was the honest reading of the original code, which only ever animated
# `progress` in demo mode.
SEARCH_SHARE = 0.25

# How the board search's own slice divides between asking and reading. Fetching is *not* seconds
# — twenty queries and up to thirty page scrapes is minutes of wall clock — and reporting only
# between channels left the bar frozen at 2% for all of it, which is what "the loading bar
# doesn't work" meant. A scrape is slower than a search but there are fewer of them, so the two
# halves come out roughly even.
QUERY_SHARE = 0.6


def report(
    run_ids: dict[str, str],
    *,
    phase: str,
    progress: float,
    done: int | None = None,
    total: int | None = None,
    results: int | None = None,
) -> None:
    """Publish a progress heartbeat, in its own committed transaction.

    It has to commit. Scoring used to run inside one long write transaction, which meant the
    number was unreadable until the work it described had finished, and — on SQLite — that no
    other request could write for the duration either. Each heartbeat ends the transaction,
    which both publishes the figure and lets the API back in.

    A heartbeat is never worth failing a run over, so this swallows its own errors.
    """
    from ..db.models import IntakeRunRow

    try:
        with transaction() as session:
            for run_id in run_ids.values():
                row = session.get(IntakeRunRow, run_id)
                if row is not None and row.status == "running":
                    row.phase, row.progress = phase, round(progress, 4)
                    # Omitted means "leave it alone", not "zero". The between-channel heartbeat
                    # carries only a progress figure, and zeroing the counts there blanked the
                    # label — "20 / 20 queries" flicked back to "Searching boards…" on the step
                    # before scoring started.
                    if done is not None:
                        row.done = done
                    if total is not None:
                        row.total = total
                    if results is not None:
                        # So "found 12 so far" is readable while the crawl is still going. The
                        # final tally is written once at the end of the run either way.
                        row.results = results
    except Exception:  # noqa: BLE001 — telling someone how far along we are is not load-bearing
        logfire.warn("Could not record run progress")


@dataclass
class Judged:
    new_ids: set[str] = field(default_factory=set)
    scored: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def judge(jobs: list[Job], *, now: int, run_ids: dict[str, str] | None = None) -> Judged:
    """Filter, save, pre-filter, score, rank — in that order, and the order matters.

    The filters run first because they are free (MATCH-01). The jobs are **saved next**, before
    anything is scored: a fit report belongs to a job, so the job has to exist first, and it
    means a run whose scoring fails still leaves the jobs on the list rather than losing them.
    Only then is the expensive step reached, on a pre-filtered shortlist (MATCH-05).

    **One transaction per scored job, not one for the lot.** Scoring forty jobs takes about
    seven minutes, and holding a write transaction open for it meant three things: no progress
    could be read until it was over, no other request could write to SQLite in the meantime, and
    a run that died lost every score it had already paid for. Committing per job fixes all
    three, and costs nothing semantically — re-running skips jobs that already have a valid fit
    report (SCH-02, COST-04), so a partially scored run is exactly what the next run expects.
    """
    from ..db import state
    from ..services.store import Store

    runs = run_ids or {}
    out = Judged()

    # Everything that is free, in one transaction: filter, store, pre-filter.
    with transaction() as session:
        store = Store(session, now)
        cv = store.cv()
        profile = store.get_profile()
        saved = state.settings_data(session)
        weights = store.weights()
        model = saved["models"]["scoring"]
        demo_mode = get_settings().wera_demo_mode

        kept, hidden = filters.apply(jobs, profile, now)  # MATCH-01, before any model call
        saved_jobs, out.new_ids = store.upsert_jobs([*kept, *hidden])
        # Score what was stored, not what was found: a merged sighting kept the id it already had.
        scoreable = [j for j in saved_jobs if j.status == "new"]
        chosen = prefilter.shortlist(scoreable, cv, demo_mode=demo_mode, top_n=wera().max_scored_per_day)  # MATCH-05

    scored: list[tuple[Job, Any]] = []
    for position, job in enumerate(chosen):
        # Reported before the call, not after: the first score takes about eleven seconds, and
        # the screen should say what it is waiting for rather than show nothing until it lands.
        report(
            runs,
            phase="scoring",
            progress=SEARCH_SHARE + (1 - SEARCH_SHARE) * (position / max(len(chosen), 1)),
            done=position,
            total=len(chosen),
        )
        with transaction() as session:
            _check_cancelled(session, list(runs.values()))
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
    summary: dict[str, Any] = {"new_jobs": 0, "scored": 0, "credits_saved": 0, "errors": []}

    with transaction() as session:
        store = Store(session, now)
        profile = store.get_profile()
        known_urls = store.known_apply_urls()
        last_uid = store.inbox_last_uid()
        discover = "watchlist" in channels and discovery_due(store.ats_discovered_at(), now)
        # SRCH-12's own gate. Two kinds of discovery, two clocks: boards (ATS-05) ride the free
        # watchlist, sites ride the paid board search.
        find_sites = "board_search" in channels and sites_due(store.sites_discovered_at(), now)

    # SRCH-10. Built outside any transaction, because the cache opens its own short ones — and
    # pruned here rather than on a timer, since a run is the only thing that adds to it.
    cache = crawl_cache.CrawlCache.from_config(now)
    if cache is not None:
        cache.prune()

    outcomes: list[ChannelOutcome] = []
    report(run_ids, phase="searching", progress=0.02)

    def ticker(channel: str, index: int) -> Progress:
        """The slice of the bar this channel owns, as something it can report into.

        Targeted at this channel's own row, not every row in the run: "7 / 16 queries" is a fact
        about the board search, and writing it onto the watchlist's row too would be a lie. The
        between-channel heartbeat below still moves every row, because that paces the run.
        """
        lo = SEARCH_SHARE * index / max(len(channels), 1)
        hi = SEARCH_SHARE * (index + 1) / max(len(channels), 1)
        mine = {channel: run_ids[channel]} if channel in run_ids else {}

        def tick(fraction: float, *, done: int, total: int, results: int) -> None:
            progress = lo + (hi - lo) * min(max(fraction, 0.0), 1.0)
            report(mine, phase="searching", progress=max(progress, 0.02), done=done, total=total, results=results)

        return tick

    for index, channel in enumerate(channels):
        try:
            with transaction() as session:
                _check_cancelled(session, list(run_ids.values()))
            if channel == "watchlist":
                outcomes.append(from_watchlist(profile, now, discover=discover))
            elif channel == "board_search":
                outcomes.append(
                    from_board_search(profile, known_urls, caps, now, cache, ticker(channel, index), discover=find_sites)
                )
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
        report(run_ids, phase="searching", progress=SEARCH_SHARE * (index + 1) / max(len(channels), 1))

    if cache is not None:
        cache.summary()

    # Everything together, so a job found twice is scored once.
    found = dedupe.collapse([job for outcome in outcomes for job in outcome.jobs])

    # Outside a transaction: `judge` opens one per scored job so progress is readable while it
    # works and the API is not locked out for the seven minutes that takes.
    try:
        with transaction() as session:
            _check_cancelled(session, list(run_ids.values()))
        judged = judge(found, now=now, run_ids=run_ids)
    except Cancelled:
        return summary

    with transaction() as session:
        store = Store(session, now)
        new_ids, score_errors = judged.new_ids, judged.errors
        summary["new_jobs"] = len(new_ids)
        summary["scored"] = judged.scored
        summary["credits_saved"] = sum(o.credits_saved for o in outcomes)
        summary["errors"] = score_errors

        # ATS-03, with the real feed: only a board that answered can close anything.
        watchlist = next((o.feed for o in outcomes if o.channel == "watchlist" and o.feed is not None), None)
        summary["closed"] = store.close_jobs(ats_feeds.closed_tokens(watchlist, store.ats_by_job())) if watchlist else 0

        for outcome in outcomes:
            row = session.get(IntakeRunRow, run_ids.get(outcome.channel, ""))
            if row is None:
                continue
            row.queries = outcome.queries
            row.results = outcome.results
            row.credits_used = outcome.credits_used
            row.credits_saved = outcome.credits_saved
            row.new_jobs = len([j for j in found if j.id in new_ids and outcome.channel in {s.channel for s in j.sources}])
            row.errors = [{"code": code, "message": message} for code, message in [*outcome.errors, *score_errors]]
            row.progress, row.phase = 1, "done"
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
            if outcome.channel == "watchlist" and discover:
                store.set_ats_discovered_at(iso(now))
            if outcome.channel == "board_search" and find_sites:
                # Written whether or not anything was proposed, so a provider outage doesn't make
                # every later run retry and spend the credits the gate exists to save.
                store.set_sites_discovered_at(iso(now))
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
