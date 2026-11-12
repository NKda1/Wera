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
from ..sources import (
    ats_discovery,
    ats_feeds,
    browser,
    crawl_cache,
    firecrawl_search,
    inbox,
    normalise,
    resolve,
)
from ..sources.extract import NOT_A_JOB
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
    # SRCH-20: a channel that saved and scored its own jobs (the board search's pipeline). `run`
    # leaves its jobs out of the shared judging, and reads these instead.
    judged: bool = False
    new_ids: set[str] = field(default_factory=set)
    surfaced: int = 0
    queued: int = 0
    # Boards this run added to the watchlist (ATS-05).
    discovered: int = 0
    # SRCH-10: what the crawl cache answered for free, in Firecrawl's credit units.
    credits_saved: int = 0
    # SRCH-05: pages `sources.extract` read for nothing, against pages that still needed a paid
    # Firecrawl scrape.
    local_pages: int = 0
    fallback_pages: int = 0


class Cancelled(Exception):
    """Raised between steps when the person cancelled the run (UX-01)."""


class Progress(Protocol):
    """How a channel says how far through itself it is, as a fraction of its own work.

    The channel doesn't know which slice of the bar it owns — `run` does, because only it knows
    how many channels there are — so a channel reports 0…1 of itself and the caller maps that
    onto the run's bar.

    `phase` and `detail` are the channel's to name: it is the only thing that knows whether it is
    searching or scraping, and which board it is doing it to.
    """

    def __call__(
        self, fraction: float, *, phase: str, detail: str | None = None, done: int, total: int, results: int
    ) -> None: ...


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


def from_watchlist(profile: SearchProfile, now: int, *, discover: bool = False, tick: Progress | None = None) -> ChannelOutcome:
    """ATS-01: the employers' own boards, and ATS-05: finding which boards those are."""
    out = ChannelOutcome(channel="watchlist")
    if discover:
        if tick is not None:
            tick(0.0, phase="discovering", detail="looking for employers hiring your titles", done=0, total=0, results=0)
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
    if tick is not None:
        # One fetch per employer covers every job on that board, so this is the honest unit: a
        # watchlist of 32 companies is 32 requests, not 3,913 jobs.
        tick(
            0.3,
            phase="feeds",
            detail=f"{len(watched)} {'employer' if len(watched) == 1 else 'employers'}",
            done=0,
            total=len(watched),
            results=0,
        )
    result = ats_feeds.fetch_all(watched)
    out.results = len(result.jobs)
    out.jobs = [normalise.from_feed(feed, now) for feed in result.jobs]
    out.errors += [("WATCHLIST_FEED", message) for message in result.errors]
    out.feed = result
    out.jobs += _from_direct_sites(profile, now, tick=tick)
    return out


def _from_direct_sites(profile: SearchProfile, now: int, *, tick: Progress | None = None) -> list[Job]:
    """Boards WERA reads itself (SRCH-04, `Site.direct`) — today, `careers.google.com`.

    Rides the watchlist because it spends no Firecrawl credits, and sits behind the same terms
    gate as every other board: an entry with no `terms_reviewed_on` is loaded, shown in the UI
    and **never fetched**. So this does nothing at all until a person has read the terms and
    recorded the date, which is the intended behaviour rather than a missing step.

    Its failures are returned as jobs-not-found rather than raised: a board WERA can't read must
    not cost the watchlist the employers it could.
    """
    from ..sources import google_careers

    direct = firecrawl_search.direct_sites(profile, sites())
    if not any(s.domain == google_careers.HOST for s in direct):
        return []
    if tick is not None:
        tick(0.7, phase="feeds", detail=google_careers.HOST, done=0, total=0, results=0)
    with browser.session() as fetch_html:
        jobs, errors = google_careers.list_jobs(
            watch_titles(profile), profile.locations, fetch_html=fetch_html, now=now, parse=normalise_with_model
        )
    for message in errors:
        logfire.info("Google Careers: {message}", message=message)
    return jobs


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


def normalise_with_model(text: str) -> dict[str, Any] | None:
    """`extract`'s last free rung: the fast tier reads a page no parser could (SRCH-05).

    About $0.0002 a page against 5 Firecrawl credits (~$0.019). The task was already written —
    its prompt, schema and config row all existed with **no call site** — so this is a wiring
    change, not a new model call.

    Its own short transaction, because the scrape loop runs outside one on purpose: the budget
    check and the `llm_calls` row both need a session, and holding one open across thirty page
    reads would lock SQLite for the whole crawl.
    """
    from ..llm.gateway import structured
    from ..llm.prompts import untrusted
    from ..schemas.llm_outputs import NormalisedJob

    try:
        with transaction() as session:
            out = structured(session, "job.normalise", NormalisedJob, {"job": untrusted("job page", text)}, context={})
        if not out.is_job_posting:
            return {NOT_A_JOB: True}  # read and understood: stop here, pay nothing more for it
        return out.model_dump(by_alias=True)
    except ApiError:
        # A spent budget or a refused key is a reason to fall through to Firecrawl, not to fail
        # the page. `structured` has already recorded what it cost.
        return None


def from_board_search(
    profile: SearchProfile,
    known_urls: set[str],
    caps: SearchCaps,
    now: int,
    cache: "crawl_cache.CrawlCache | None" = None,
    tick: Progress | None = None,
    discover: bool = False,
    run_ids: dict[str, str] | None = None,
) -> ChannelOutcome:
    """Board search: the search pipeline (SRCH-20, `graphs.search_pipeline`), which plans,
    discovers, harvests and scores, stopping at 8–12 jobs (SRCH-26).

    The jobs come back already saved and scored, so `jobs` is empty and `judged` is set: `run`
    mustn't judge them a second time. SRCH-12's weekly look for new boards still rides here first,
    because it is the one part of a board search that still uses Firecrawl search.
    """
    from . import search_pipeline

    out = ChannelOutcome(channel="board_search", judged=True)

    if discover:
        if tick is not None:
            tick(0.0, phase="discovering", detail="looking for new job boards", done=0, total=0, results=0)
        try:
            found, credits, errors = run_site_discovery(profile, now)
            out.discovered = found
            out.credits_used += credits
            out.errors += errors
        except Exception as exc:  # noqa: BLE001 — looking for boards must not cost the search
            logfire.exception("Site discovery failed")
            out.errors.append(("SITE_DISCOVERY", f"Couldn't look for new boards ({type(exc).__name__})."))

    piped = search_pipeline.run(run_ids or {}, now=now, caps=caps, cache=cache)
    out.queries = piped.queries
    out.results = piped.results
    out.credits_used += piped.spend.credits_used
    out.credits_saved = piped.spend.credits_saved
    out.local_pages = piped.spend.local_pages
    out.fallback_pages = piped.spend.fallback_pages
    out.partial = piped.partial
    out.errors += piped.errors
    out.new_ids = piped.new_ids
    out.surfaced = piped.surfaced
    out.queued = piped.queued
    return out


def from_inbox(
    session: Any, profile: SearchProfile, last_uid: int | None, now: int, tick: Progress | None = None
) -> ChannelOutcome:
    """MAIL-01…10: read the alerts folder, then resolve what it found."""
    out = ChannelOutcome(channel="email_alert")
    env = get_settings()
    password = env.alerts_imap_app_password.get_secret_value() if env.alerts_imap_app_password else ""
    if not (env.alerts_imap_host and env.alerts_imap_user and password):
        out.errors.append(("INBOX_NOT_CONFIGURED", "Set the inbox details to use the alert channel."))
        return out
    if tick is not None:
        # The mailbox, not the account: `alerts_folder` is what a person configured, and naming it
        # is how they can tell "read the wrong folder" from "no new alerts".
        tick(0.2, phase="reading", detail=env.alerts_folder, done=0, total=0, results=0)

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


# How long a `detail` may be, matching `intake_runs.detail`. A job title or a board domain fits;
# anything longer is truncated rather than refused, because a heartbeat must never be the thing
# that fails a run.
DETAIL_LIMIT = 200


def report(
    run_ids: dict[str, str],
    *,
    phase: str,
    progress: float,
    detail: str | None = None,
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
                    # `detail` is cleared when not given, unlike the counters below. A phase has
                    # moved on by the time the next heartbeat lands, and a sentence left behind
                    # from the previous one is worse than no sentence: it says WERA is reading a
                    # board it finished with minutes ago.
                    row.detail = detail[:DETAIL_LIMIT] if detail else None
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
    # Jobs left in the pool after this pass took its batches — what "Find more" starts from.
    pool: int = 0
    # Jobs this pass left with a fit report — what SRCH-26 counts towards its 8–12. Hidden jobs
    # never get one, so they never count.
    surfaced: int = 0
    # The new jobs that made the shortlist rather than the hidden list: what a run calls "new".
    visible_new: set[str] = field(default_factory=set)


def judge(
    jobs: list[Job],
    *,
    now: int,
    run_ids: dict[str, str] | None = None,
    progress_from: float = SEARCH_SHARE,
    progress_to: float = 1.0,
    limit: int | None = None,
    only_these: bool = False,
) -> Judged:
    """Filter, save, pre-filter, score, rank — in that order, and the order matters.

    The filters run first because they are free (MATCH-01). The jobs are **saved next**, before
    anything is scored: a fit report belongs to a job, so the job has to exist first, and it
    means a run whose scoring fails still leaves the jobs on the list rather than losing them.
    Only then is the expensive step reached, on a pre-filtered shortlist (MATCH-05).

    **Only a batch or two of it, though.** `limit` is how many jobs this pass scores, defaulting
    to `config.scored_per_run` — eight at a time, two batches. A job takes about eleven seconds,
    so the forty this used to work through was seven minutes during which the shortlist showed
    nothing new: the broad search was done in the first quarter of that and then sat unreadable.
    What the rest of the pool does now is wait, listed, in `Store.jobs_needing_score` — and
    "Find more" calls up the next batch. Ordering is best-first, so a batch is always worse than
    the one before it and stopping early costs the least.

    **`only_these`** is the search pipeline's mode (SRCH-20, stage 4): score the jobs it just read,
    in the order discovery ranked them, and leave the rest of the pool alone. Without it a round
    of four postings would score the best four of a backlog of hundreds instead.

    **One transaction per scored job, not one for the lot.** Holding a write transaction open for
    the whole pass meant three things: no progress could be read until it was over, no other
    request could write to SQLite in the meantime, and a run that died lost every score it had
    already paid for. Committing per job fixes all three, and costs nothing semantically —
    re-running skips jobs that already have a valid fit report (SCH-02, COST-04), so a partially
    scored run is exactly what the next one expects. That is also what makes batching safe: a
    batch is just a pass that stopped early, which the pool already knew how to represent.
    """
    from ..db import state
    from ..services.store import Store

    runs = run_ids or {}
    out = Judged()

    # The free rules and the embedding pre-filter. Fast on a small list and *not* fast on a real
    # one — against 15 boards carrying 3,913 vacancies the filters alone are seconds, and the
    # pre-filter embeds every survivor locally — so it gets its own named stage rather than
    # looking like the first job taking an unexplained minute to score.
    report(runs, phase="filtering", progress=progress_from, detail=f"{len(jobs)} found" if jobs else "checking the shortlist")

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
        saved_jobs, out.new_ids = store.upsert_jobs([*kept, *hidden], run_ids=runs)
        out.visible_new = out.new_ids & {j.id for j in saved_jobs[: len(kept)]}
        # Everything still waiting for a score, not just what this run turned up. A board search
        # skips URLs it already knows (SRCH-05), so a job left unscored by yesterday's cap was
        # never found again and so never scored — and a CV change invalidates every stored score
        # at once with nothing to recompute them. Both are the same omission, fixed in one place.
        #
        # Read from the database rather than unioned with `saved_jobs`, which is both simpler and
        # more correct: `upsert_jobs` has already written this run's finds, so these rows *are*
        # the merged jobs, and asking with `cached_evidence`'s own predicate means a re-sighting
        # whose score is still valid can't take a slot under the day's cap from one that has none.
        scoreable = store.jobs_needing_score(cv_version=cv.version, model=model)
        if only_these:
            mine = [j.id for j in saved_jobs[: len(kept)]]
            waiting = {j.id: j for j in scoreable}
            scoreable = [waiting[i] for i in dict.fromkeys(mine) if i in waiting]
        cfg = wera()
        # MATCH-05's day ceiling, minus what today has already spent. Counted here rather than
        # left to the batch arithmetic, because batching hands the cap out in instalments and
        # three "score more" clicks must not add up to more than one old run was allowed.
        remaining_today = max(cfg.max_scored_per_day - store.scored_today(), 0)
        take = min(limit if limit is not None else cfg.scored_per_run, remaining_today)
        # Ranked over the *whole* pool, then sliced. Ranking only the slice would mean each batch
        # was the best of an arbitrary subset rather than the best of what's left.
        ranked = (
            scoreable if only_these else prefilter.shortlist(scoreable, cv, demo_mode=demo_mode, top_n=max(len(scoreable), 1))
        )
        chosen = ranked[:take]
        out.pool = max(len(scoreable) - len(chosen), 0)
        if scoreable and not chosen:
            out.errors.append(
                ("DAILY_CAP", f"Today's scoring limit of {cfg.max_scored_per_day} jobs is used up. The rest are waiting.")
            )

    scored: list[tuple[Job, Any]] = []
    for position, job in enumerate(chosen):
        # Reported before the call, not after: the first score takes about eleven seconds, and
        # the screen should say what it is waiting for rather than show nothing until it lands.
        report(
            runs,
            phase="scoring",
            # `progress_from` is where searching left off: a quarter of the bar for an intake run,
            # and zero for a re-score, which never searched for anything.
            progress=progress_from + (progress_to - progress_from) * (position / max(len(chosen), 1)),
            # The job being judged. Scoring is ~98% of a real run's wall clock at roughly eleven
            # seconds a job, so "Scoring 3 / 40" alone leaves the longest phase in the product
            # with nothing changing between one job and the next.
            detail=f"{job.title} · {job.company}",
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
    out.surfaced = len(scored)
    if out.scored:
        from ..services import analytics

        analytics.refresh_quietly()  # OBS-03: the new totals, for anyone reading `analytics`
    return out


async def run_rescore(run_id: str, channel: str = "rescore") -> dict[str, Any]:
    """A scoring-only pass on a worker thread, so the request that asked for it can return."""
    import asyncio

    try:
        return await asyncio.to_thread(rescore, run_id, channel)
    finally:
        from ..llm.gateway import flush_unrecorded

        flush_unrecorded()


def rescore(run_id: str, channel: str = "rescore") -> dict[str, Any]:
    """Judge jobs WERA already has, with no fetching at all (CV-08, SCH-02, MATCH-05).

    `rescore` recomputes what a CV change invalidated. (`score_more` — "Find more" — used to be the
    same pass; it now goes on to search when the backlog is short, and is `find_more`.) It is `judge` with nothing new to add, and
    all the work comes from `jobs_needing_score` — which is why this is so small. The real change
    was teaching `judge` to ask what still needs scoring, rather than scoring only what a run had
    just found; this exists so that can happen when the CV changes or when someone asks, instead
    of only when the next search is.
    """
    from ..db.models import IntakeRunRow

    now = now_ms()
    run_ids = {channel: run_id}
    summary: dict[str, Any] = {"new_jobs": 0, "scored": 0, "credits_saved": 0, "errors": []}

    if channel == "score_more":
        return find_more(run_id)

    try:
        judged = judge([], now=now, run_ids=run_ids, progress_from=0.0)
    except Cancelled:
        logfire.info("{channel} cancelled", channel=channel)
        return summary

    summary["scored"] = judged.scored
    summary["errors"] = judged.errors
    with transaction() as session:
        row = session.get(IntakeRunRow, run_id)
        if row is not None and row.status == "running":
            row.errors = [{"code": code, "message": message} for code, message in judged.errors]
            row.progress, row.phase = 1, "done"
            row.detail = None
            row.finished_at = iso(now_ms())
            row.settled = True
            row.status = "partial" if judged.errors else "succeeded"
    logfire.info(
        "{channel} finished: {scored} job(s) judged, {pool} still waiting",
        channel=channel,
        scored=judged.scored,
        pool=judged.pool,
    )
    return summary


def find_more(run_id: str) -> dict[str, Any]:
    """ "Find more" (SRCH-26): the next 8–12 — what's saved and unscored first, then the queue, then
    the plan's unsent searches (`search_pipeline.run`, mode "more")."""
    from ..db.models import IntakeRunRow
    from ..services.store import Store
    from . import search_pipeline

    now = now_ms()
    cfg = wera()
    caps = SearchCaps(
        max_queries=cfg.max_queries,
        max_pages=cfg.max_pages,
        max_credits=cfg.max_credits_per_run,
        max_serper_queries=cfg.max_serper_queries,
    )
    with transaction() as session:
        week_left = max(0, cfg.max_credits_per_week - Store(session, now).credits_this_week())
    caps.max_credits = min(caps.max_credits, week_left)
    cache = crawl_cache.CrawlCache.from_config(now)
    try:
        piped = search_pipeline.run({"score_more": run_id}, now=now, caps=caps, cache=cache, mode="more")
    except Cancelled:
        logfire.info("Find more cancelled")
        return {"new_jobs": 0, "scored": 0, "credits_saved": 0, "errors": []}

    with transaction() as session:
        row = session.get(IntakeRunRow, run_id)
        if row is not None and row.status == "running":
            row.queries, row.results = piped.queries, piped.results
            row.credits_used, row.credits_saved = piped.spend.credits_used, piped.spend.credits_saved
            row.local_pages, row.fallback_pages = piped.spend.local_pages, piped.spend.fallback_pages
            row.new_jobs, row.queued, row.total = len(piped.new_ids), piped.queued, piped.surfaced
            row.errors = [{"code": code, "message": message} for code, message in piped.errors]
            row.progress, row.phase, row.detail = 1, "done", None
            row.finished_at, row.settled = iso(now_ms()), True
            row.status = (
                "failed" if piped.errors and not piped.surfaced else "partial" if piped.errors or piped.partial else "succeeded"
            )
    return {
        "new_jobs": len(piped.new_ids),
        "scored": piped.surfaced,
        "credits_saved": piped.spend.credits_saved,
        "errors": piped.errors,
    }


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
        max_serper_queries=cfg.max_serper_queries,
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
        # The week's Firecrawl ceiling, alongside the run's own. Per-run caps bound one click; a
        # schedule (SCH-01's cron) clicks for the person, and without this nothing bounded a week.
        week_left = max(0, cfg.max_credits_per_week - store.credits_this_week())
        if week_left < caps.max_credits:
            caps.max_credits = week_left
            logfire.info("Weekly Firecrawl ceiling: {left} credit(s) left this week", left=week_left)

    # SRCH-10. Built outside any transaction, because the cache opens its own short ones — and
    # pruned here rather than on a timer, since a run is the only thing that adds to it.
    cache = crawl_cache.CrawlCache.from_config(now)
    if cache is not None:
        cache.prune()

    outcomes: list[ChannelOutcome] = []
    report(run_ids, phase="queued", progress=0.02)

    def ticker(channel: str, index: int) -> Progress:
        """The slice of the bar this channel owns, as something it can report into.

        Targeted at this channel's own row, not every row in the run: "7 / 16 queries" is a fact
        about the board search, and writing it onto the watchlist's row too would be a lie. The
        between-channel heartbeat below still moves every row, because that paces the run.
        """
        lo = SEARCH_SHARE * index / max(len(channels), 1)
        hi = SEARCH_SHARE * (index + 1) / max(len(channels), 1)
        mine = {channel: run_ids[channel]} if channel in run_ids else {}

        def tick(fraction: float, *, phase: str, detail: str | None = None, done: int, total: int, results: int) -> None:
            progress = lo + (hi - lo) * min(max(fraction, 0.0), 1.0)
            report(
                mine,
                phase=phase,
                progress=max(progress, 0.02),
                detail=detail,
                done=done,
                total=total,
                results=results,
            )

        return tick

    for index, channel in enumerate(channels):
        try:
            with transaction() as session:
                _check_cancelled(session, list(run_ids.values()))
            if channel == "watchlist":
                outcomes.append(from_watchlist(profile, now, discover=discover, tick=ticker(channel, index)))
            elif channel == "board_search":
                mine = {channel: run_ids[channel]} if channel in run_ids else {}
                outcomes.append(
                    from_board_search(
                        profile, known_urls, caps, now, cache, ticker(channel, index), discover=find_sites, run_ids=mine
                    )
                )
            else:
                with transaction() as session:
                    outcomes.append(from_inbox(session, profile, last_uid, now, tick=ticker(channel, index)))
        except Cancelled:
            logfire.info("Intake cancelled before {channel}", channel=channel)
            break
        except ApiError as exc:
            outcomes.append(ChannelOutcome(channel=channel, errors=[(exc.code, exc.message)]))
        except Exception as exc:  # noqa: BLE001 — one channel never takes the run with it
            logfire.exception("Channel {channel} failed", channel=channel)
            outcomes.append(ChannelOutcome(channel=channel, errors=[("CHANNEL_FAILED", type(exc).__name__)]))

    if cache is not None:
        cache.summary()

    # Everything together, so a job found twice is scored once. Named, because on a run that
    # found a few hundred sightings this is seconds of silence between two long phases, and
    # silence between stages is what a frozen bar is made of.
    total_found = sum(len(o.jobs) for o in outcomes)
    report(
        run_ids,
        phase="collapsing",
        progress=SEARCH_SHARE,
        detail=f"{total_found} {'sighting' if total_found == 1 else 'sightings'}",
    )
    found = dedupe.collapse([job for outcome in outcomes for job in outcome.jobs])

    # Outside a transaction: `judge` opens one per scored job so progress is readable while it
    # works and the API is not locked out for the seven minutes that takes.
    #
    # Only the channels that didn't judge their own jobs. The board search's pipeline has already
    # saved and scored its 8–12 (SRCH-26); a shared pass after it, on a search-only run, would go
    # on to score up to twelve more from the backlog that nobody asked for.
    shared = {o.channel: run_ids[o.channel] for o in outcomes if not o.judged and o.channel in run_ids}
    try:
        with transaction() as session:
            _check_cancelled(session, list(run_ids.values()))
        judged = judge(found, now=now, run_ids=shared) if shared else Judged()
    except Cancelled:
        return summary

    # ATS-03's sweep walks every job the watchlist can speak for, so it is the last thing between
    # the final score and a finished run. Named for the same reason as `collapsing`: an unnamed
    # pause at 99% is where someone reloads the page.
    report(run_ids, phase="closing", progress=0.99, detail="checking which jobs are still open")

    with transaction() as session:
        store = Store(session, now)
        new_ids, score_errors = judged.new_ids, judged.errors
        piped = [o for o in outcomes if o.judged]
        summary["new_jobs"] = len(new_ids) + sum(len(o.new_ids) for o in piped)
        summary["scored"] = judged.scored + sum(o.surfaced for o in piped)
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
            row.local_pages = outcome.local_pages
            row.fallback_pages = outcome.fallback_pages
            if outcome.judged:
                row.new_jobs, row.queued, scoring_errors = len(outcome.new_ids), outcome.queued, []
            else:
                row.new_jobs = len([j for j in found if j.id in new_ids and outcome.channel in {s.channel for s in j.sources}])
                scoring_errors = score_errors
            row.errors = [{"code": code, "message": message} for code, message in [*outcome.errors, *scoring_errors]]
            row.progress, row.phase = 1, "done"
            # A finished run has nothing it is doing, so nothing to say about it. The summary
            # line takes over from here (`runStatus`).
            row.detail = None
            row.finished_at = iso(now_ms())
            row.settled = True
            # Finding jobs and scoring them fail separately. A channel that fetched nothing has
            # failed; one that fetched a list it couldn't finish scoring is partial, not a
            # success — "succeeded" next to forty unscored jobs reads as a lie.
            produced = outcome.jobs or outcome.surfaced or outcome.new_ids
            row.status = (
                "failed" if outcome.errors and not produced else "partial" if outcome.partial or scoring_errors else "succeeded"
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
        session.flush()

    logfire.info(
        "Intake finished: {new} new job(s), {scored} scored",
        new=summary["new_jobs"],
        scored=summary["scored"],
    )
    return summary
