"""The search pipeline (SRCH-20): Strategist → Redis → Harvester → fit scoring, in that order.

Every board search runs through `run`, whether a person clicked "Search now", the schedule fired,
or "Find more" asked for the next few. The order is the point, and it is enforced here rather
than left to each caller:

1. **Plan.** The Strategist's queries for the current CV and profile (`Store.search_queries`),
   refreshed first if the CV or profile has changed since the plan was made.
2. **Discover.** The queries go to Serper a tier at a time; results are classified, pages of
   results are read for their links, and postings are queued in Redis (`strategist.discover`).
3. **Harvest.** Queued postings are read a few at a time, free rungs first, and checked
   (`sources.harvest`). Nothing reaches stage 4 that wasn't classified a posting.
4. **Score.** The jobs that passed are filtered and scored (`intake.judge`, `only_these`).

**8–12 (SRCH-26).** Stages 3–4 repeat until `surface.max` jobs are scored, then the run stops and
what it didn't read stays queued. If the queue runs dry first and fewer than `surface.min` are
scored, stage 2 sends the plan's next tier and stages 3–4 go on — until `min` is reached or
something stops it, which the run then names: the Serper limit, the week's Firecrawl credits,
today's scoring limit, or no more searches in the plan. It never pads with weak results.

**Find more** starts from what is already waiting: jobs saved but never scored, then the queue,
then the tiers this plan hasn't sent yet.
"""

import contextvars
import threading
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import logfire

from ..config import sites, wera
from ..db.session import engine as db_engine
from ..db.session import transaction
from ..domain.dedupe import canonical_url
from ..errors import ApiError
from ..nodes import prefilter, strategist
from ..settings import get_settings
from ..sources import feeds, harvest, targets
from ..sources.firecrawl_search import FirecrawlSearch, SearchCaps, SearchOutcome
from ..sources.provider_guard import Guard
from ..sources.serper import Serper
from . import intake

if TYPE_CHECKING:
    from ..sources.crawl_cache import CrawlCache

type Mode = Literal["search", "more"]

# UX-01: a board search's steps, in order, each owning a slice of its bar (`search_progress` in
# config/wera.yaml), and the phase each reports as.
STAGES: tuple[str, ...] = ("planning", "apis", "searching", "reading")
PHASE_OF = {"planning": "planning", "apis": "apis", "searching": "searching", "reading": "harvesting"}
# How often a step that waits on one long call (the Strategist's plan) moves its bar.
PACE_SECONDS = 2.0
# How a posting was read, as the run's log says it.
READ_HOW = {
    "json-ld": "from the page's job data",
    "model": "by the fast model",
    "firecrawl": "by a paid scrape",
    "cache": "from the crawl cache",
    "feed": "from the job API",
    "ats-feed": "from the employer's own feed",
    "json-ld-rendered": "from the page's job data after rendering it",
    "browser": "after rendering the page",
}


def _read_how(path: str) -> str:
    if path in READ_HOW:
        return READ_HOW[path]
    if path in feeds.FEEDS:
        return f"from {feeds.FEEDS[path].name}'s API"
    return path.replace("-", " ")


class Bar:
    """A board search's own bar (UX-01, 17 Nov).

    The bar used to have no number and one line of detail — "the job APIs", "tier 1 · 15
    searches" — that stood for a minute at a time, so nobody watching could tell what was
    happening or whether it had stopped. Each step now reports how far through itself it is, in a
    sentence naming what it is doing, and adds a line to the run's log when it finishes.
    """

    def __init__(self, run_ids: dict[str, str]) -> None:
        self.run_ids = run_ids
        shares = wera().search_progress
        self.edges: dict[str, tuple[float, float]] = {}
        at = 0.0
        for stage in STAGES:
            share = getattr(shares, stage)
            self.edges[stage] = (at, at + share)
            at += share

    def at(self, stage: str, fraction: float, detail: str, *, done: int | None = None, total: int | None = None) -> None:
        lo, hi = self.edges[stage]
        part = min(max(fraction, 0.0), 1.0)
        intake.report(
            self.run_ids,
            phase=PHASE_OF[stage],
            progress=lo + (hi - lo) * part,
            stage=part,
            detail=detail,
            done=done,
            total=total,
        )

    def step(self, stage: str) -> "strategist.Step":
        """What a stage's own code calls with (fraction, sentence, done, total)."""
        return lambda fraction, detail, done, total: self.at(stage, fraction, detail, done=done, total=total)

    def done(self, stage: str, message: str, started: float) -> None:
        """The step finished: its end of the bar, and a line in the run's log."""
        lo, hi = self.edges[stage]
        intake.report(
            self.run_ids,
            phase=PHASE_OF[stage],
            progress=hi,
            stage=1.0,
            detail=message,
            note=message,
            took_ms=int((time.monotonic() - started) * 1000),
        )

    @contextmanager
    def pacing(self, stage: str, detail: str, *, expected_s: float) -> Iterator[None]:
        """Moves the stage's bar while one long call runs, from how long that call usually takes.

        Held at 95% of the stage until the call answers, and saying how long it has been — a bar
        frozen at 3% for 115 seconds, as the Strategist's plan was on 16 Nov, reads as a hang.
        """
        started = time.monotonic()
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(PACE_SECONDS):
                waited = time.monotonic() - started
                usually = f", usually {round(expected_s)} s" if waited > expected_s else ""
                self.at(stage, min(0.95, waited / max(expected_s, 1.0)), f"{detail} · {round(waited)} s{usually}")

        self.at(stage, 0.0, detail)
        pacer = threading.Thread(target=beat, name="search-pace", daemon=True)
        pacer.start()
        try:
            yield
        finally:
            stop.set()
            pacer.join(timeout=PACE_SECONDS)


@dataclass
class Piped:
    """What one pipeline run did. `intake.run` writes it onto the board search's row."""

    queries: int = 0
    results: int = 0
    kinds: Counter = field(default_factory=Counter)
    listings_read: int = 0
    tiers: list[int] = field(default_factory=list)
    read: int = 0
    by_path: Counter = field(default_factory=Counter)
    rejected: Counter = field(default_factory=Counter)
    surfaced: int = 0
    new_ids: set[str] = field(default_factory=set)
    queued: int = 0
    spend: SearchOutcome = field(default_factory=SearchOutcome)
    errors: list[tuple[str, str]] = field(default_factory=list)
    # Why the run stopped short of `surface.min`, in words; None when it didn't.
    short: str | None = None
    # Countries that ran dry under their floor, as "us 2 of 3" (SRCH-26, 19 Nov).
    dry: list[str] = field(default_factory=list)
    # SRCH-31/32: what each job API did — searches, leads, queued, or why it was skipped.
    feeds: dict[str, dict[str, object]] = field(default_factory=dict)
    # 20 Nov: the job APIs' leads by what became of them (`strategist.FeedDiscovery`).
    leads: dict[str, int] = field(default_factory=dict)
    # UPSK-06: what the near-miss pass read (`services.near_misses.Captured`).
    near_misses: object | None = None

    @property
    def partial(self) -> bool:
        return self.short is not None or self.spend.partial


def run(
    run_ids: dict[str, str],
    *,
    now: int,
    caps: SearchCaps,
    cache: "CrawlCache | None" = None,
    mode: Mode = "search",
) -> Piped:
    """One pass of SRCH-20 for the rows in `run_ids`. Never raises for a stage that fails: the
    failure is recorded and the pass ends, keeping whatever the earlier stages found."""
    out = Piped()
    surface = wera().surface
    with logfire.span("search pipeline", mode=mode) as span:
        try:
            _run(out, run_ids, now=now, caps=caps, cache=cache, mode=mode, surface_min=surface.min, surface_max=surface.max)
        except intake.Cancelled:
            raise
        except ApiError as exc:
            out.errors.append((exc.code, exc.message))
        finally:
            span.set_attributes(
                {
                    "tiers": out.tiers,
                    "serper_queries": out.queries,
                    "results": out.results,
                    "postings": out.kinds["posting"],
                    "listings_read": out.listings_read,
                    "read": out.read,
                    "accepted_by": dict(out.by_path),
                    "rejected": dict(out.rejected),
                    "firecrawl_credits": out.spend.credits_used,
                    "surfaced": out.surfaced,
                    "queued": out.queued,
                    "short": out.short or "",
                    "countries_dry": out.dry,
                }
            )
    return out


def _run(
    out: Piped,
    run_ids: dict[str, str],
    *,
    now: int,
    caps: SearchCaps,
    cache: "CrawlCache | None",
    mode: Mode,
    surface_min: int,
    surface_max: int,
) -> None:
    from ..services.store import Store

    bar = Bar(run_ids)
    # "Find more" first scores what is already saved and waiting — no network at all — and only
    # goes looking if that leaves it short.
    if mode == "more":
        judged = intake.judge([], now=now, run_ids=run_ids, progress_from=0.0, progress_to=0.5, limit=surface_max)
        out.surfaced += judged.surfaced
        out.errors += judged.errors
        if out.surfaced >= surface_min or _stops(judged.errors):
            return

    # Stage 1: the plan, and its queries.
    started = time.monotonic()
    with transaction() as session:
        store = Store(session, now)
        profile = store.get_profile()
        asking = len(strategist.target_countries(profile))
        with bar.pacing(
            "planning",
            f"Planning searches for {_count(asking, 'country', 'countries')} with the Strategist",
            expected_s=store.plan_seconds(),
        ):
            planned, matrix = store.search_queries(refresh=True)
        known = store.known_apply_urls()
        key = store.plan_key()
        pool, reader = store.pool_key(), store.reader_key()
        # SRCH-26 (17 Nov): the pool is read best match first, scored against the CV for free.
        cv = store.cv()
        rank = prefilter.lead_ranker(cv, profile.titles if profile else [], demo_mode=get_settings().wera_demo_mode)
    if not planned:
        out.errors.append(("NO_QUERIES", "Add a target title to your search profile to search."))
        return
    bar.done("planning", _plan_note(planned, matrix, now), started)

    # Redis and Serper are checked before anything is spent: a run that can't queue or can't ask
    # stops here, not after paying for half of it.
    client = targets.connect()
    queue = targets.TargetQueue(client, key, pool=pool, rank=rank, reader=reader)
    serper = Serper(max_queries=caps.max_serper_queries, guard=Guard(client, "serper"))
    queue.retire_others()
    boards = sites()
    tiers = sorted({q.tier for q in planned})
    # SRCH-31: the job APIs are "tier 0", asked before Serper — free, full text or a lead to find —
    # so Serper's first tier is only sent when they leave the search short of `surface.min`.
    person = feeds.recall_person(client, list(run_ids.values()))
    countries = {q.gl for q in planned if q.gl} or {c.lower() for c in (profile.countries if profile else [])}

    saved = {canonical_url(u) or u for u in known}

    def here(target: targets.Target) -> bool:
        """A pooled posting this search may read: found for a country now ticked (or for none), and
        not one the person already has — a shared pool outlives a CV, and a job already saved is
        not worth paying to read again."""
        if (canonical_url(target.url) or target.url) in saved:
            return False
        return not target.country or not countries or target.country in countries

    # SRCH-26: best match first, each ticked country's share — and, since 20 Nov, no share by site.
    balance = targets.Balance(sorted(countries), per_run=surface_max)

    if any(feeds.skipped(f, countries=countries, person=person) is None for f in feeds.FEEDS):
        tiers = [0, *tiers]
    pending = [t for t in tiers if mode == "search" or t not in queue.tiers_sent()]
    # Serper for finding snippet leads' originals (SRCH-32), on its own cap; made when tier 0 runs.
    resource: list[Serper] = []
    # UPSK-06: the leads the title rule dropped, for Upskills once reading is done.
    near: list[strategist.NearMiss] = []

    # SRCH-26 (18 Nov): Firecrawl's search, asked instead when Serper's day is spent or it is
    # failing — never for the run's own cap, which is the person's limit, not a provider's.
    stand_in = FirecrawlSearch(max_queries=wera().source_share.firecrawl_searches, spend=out.spend, caps=caps)

    def searcher() -> "Serper | FirecrawlSearch | None":
        if not serper.stopped:
            return serper
        if serper.stopped in ("daily_budget", "breaker") and not stand_in.stopped and get_settings().firecrawl_api_key:
            return stand_in
        return None

    def sent() -> int:
        return serper.sent + stand_in.sent + sum(r.sent for r in resource)

    def discover_next(only: set[str] | None = None) -> bool:
        """Stage 2 for the next tier. False when there is no tier left or Serper stopped.

        `only` (20 Nov): just the next tier's searches for these countries, which leaves the tier
        to send in full later — for a ticked country that ran dry under its floor."""
        if not pending or searcher() is None:
            return False
        if only is not None and pending[0] == 0:
            return False  # the job APIs are asked whole, at the start
        tier = pending[0] if only is not None else pending.pop(0)
        started = time.monotonic()
        if tier == 0:
            bar.at("apis", 0.0, "Asking the job APIs for your titles in each place")
            resource.append(
                Serper(max_queries=strategist.RESOURCE_QUERIES, window=strategist.RESOURCE_WINDOW, guard=Guard(client, "serper"))
            )
            asked = strategist.discover_feeds(
                planned,
                profile,
                queue=queue,
                known_urls=known,
                sites=boards,
                now=now,
                redis=client,
                person=person,
                resource=resource[0],
                on_step=bar.step("apis"),
                rank=rank,
            )
            near.extend(asked.near_misses)
            queue.mark_tier_sent(0)
            bar.done("apis", _feeds_note(asked), started)
            out.feeds = asked.by_feed
            out.leads = {
                "unique": asked.unique,
                "passed": asked.passed,
                "queued": asked.queued,
                "not_looked_for": asked.not_looked_for,
            }
            out.leads |= {f"dropped_{why}": n for why, n in asked.dropped.items()}
            out.queries = sent()
            out.errors += [("SEARCH", message) for message in asked.errors if ("SEARCH", message) not in out.errors]
            return True
        plan = planned if tier != 4 else strategist.page_two_worth_asking(planned, queue.postings_by_query())
        if only is not None:
            plan = [q for q in plan if q.gl in only]
        sending = sum(1 for q in plan if q.tier == tier)
        if not sending:
            if only is not None:
                return False
            queue.mark_tier_sent(tier)  # no page 1 was rich enough for its page 2: nothing to pay for
            return discover_next()
        engine = searcher()
        if engine is stand_in:
            room = max(stand_in.max_queries - stand_in.sent, 0)
            said = f"Searching with Firecrawl (Serper is resting): tier {tier}, up to {_count(min(sending, room), 'search', 'searches')}"
        else:
            room = max(serper.max_queries - serper.sent, 0)
            said = (
                f"Searching Google: tier {tier}, {_count(sending, 'search', 'searches')}"
                if sending <= room
                else f"Searching Google: tier {tier}, {room} of its {sending} searches (this run's limit is {serper.max_queries})"
            )
        bar.at("searching", 0.0, said, done=0, total=min(sending, room))
        found = strategist.discover(
            plan,
            [tier],
            serper=engine,  # type: ignore[arg-type]
            queue=queue,
            known_urls=known,
            sites=boards,
            profile=profile,
            on_step=bar.step("searching"),
            engine="firecrawl" if engine is stand_in else "google",
        )
        if only is None:
            queue.mark_tier_sent(tier)
        bar.done("searching", _search_note(tier, found, "Firecrawl" if engine is stand_in else "Google", only), started)
        out.tiers.append(tier)
        out.queries = sent()
        out.results += found.results
        out.kinds.update(found.kinds)
        out.listings_read += found.listings_read
        out.errors += [("SEARCH", message) for message in found.errors if ("SEARCH", message) not in out.errors]
        return True

    if mode == "search":
        discover_next()  # a search always searches; "Find more" reads the queue first

    reading_started = time.monotonic()

    def reading(target: targets.Target) -> None:
        bar.at(
            "reading",
            out.surfaced / surface_max,
            f"Reading {target.title or 'a posting'} on {target.host} · {out.surfaced} of {surface_max} found so far",
            done=out.surfaced,
            total=surface_max,
        )

    # 20 Nov: postings are read a few at once (`harvest.READERS`), so the browser lives on a thread
    # of its own that every reader hands its pages to.
    topped_up = False
    resumed_waiting = False
    with harvest.browser_lane() as fetch_html:
        while out.surfaced < surface_max:
            with transaction() as session:
                intake._check_cancelled(session, list(run_ids.values()))
            batch = queue.take(min(harvest.BATCH, surface_max - out.surfaced), accept=here, balance=balance)
            if not batch:
                if out.surfaced >= surface_min:
                    # 20 Nov: with no share by site, one full-text API can fill a search from one
                    # country, and the run stopped with the US and Canada at 0 and 1 of their 6.
                    # A ticked country that ran dry under its floor gets the next tier's searches
                    # for it, once, before the search ends.
                    dry = {c for c, _got, _floor in balance.short()}
                    if mode == "search" and dry and not topped_up and discover_next(only=dry):
                        topped_up = True
                        continue
                    break
                if discover_next():
                    continue
                # 20 Nov: leads an earlier search kept because the day's lookups were spent — "Find
                # more" looks for their originals once, before saying there is nothing left.
                if not resumed_waiting and strategist.waiting(queue):
                    resumed_waiting = True
                    lookup = Serper(
                        max_queries=strategist.RESOURCE_QUERIES, window=strategist.RESOURCE_WINDOW, guard=Guard(client, "serper")
                    )
                    resource.append(lookup)
                    looked, queued = strategist.look_for_waiting(
                        queue, profile=profile, sites=boards, known_urls=known, resource=lookup
                    )
                    if looked:
                        message = f"Looked for {_count(looked, 'lead')} kept from an earlier search: {queued} queued to read"
                        intake.report(run_ids, phase="searching", progress=bar.edges["reading"][0], detail=message, note=message)
                    if queued:
                        continue
                out.short = _why_short(serper, pending, out.spend)
                break

            # Stage 3: read and check.
            lo, hi = bar.edges["reading"]
            progress = lo + (hi - lo) * out.surfaced / surface_max
            read = harvest.harvest(
                batch,
                caps=caps,
                spend=out.spend,
                now=now,
                sites=boards,
                queue=queue,
                cache=cache,
                fetch_html=fetch_html,
                parse=intake.normalise_with_model,
                redis=client,
                on_read=reading,
            )
            out.read += len(batch) - read.put_back
            out.by_path.update(read.by_path)
            out.rejected.update(read.rejected)
            if read.put_back and not read.jobs:
                # A Firecrawl cap stopped paying, and nothing else in the round could be read for
                # free. The rest of the queue would go the same way.
                out.short = None if out.surfaced >= surface_min else _paid_cap(out.spend, caps)
                break
            if not read.jobs:
                continue

            # Stage 4: filter and score what passed, and nothing else.
            judged = intake.judge(
                read.jobs,
                now=now,
                run_ids=run_ids,
                progress_from=progress,
                progress_to=min(hi, progress + (hi - lo) * len(read.jobs) / surface_max),
                limit=surface_max - out.surfaced,
                only_these=True,
            )
            out.surfaced += judged.surfaced
            # New and on the shortlist: a job saved hidden is new to the database, not to the person.
            out.new_ids |= judged.visible_new
            out.errors += judged.errors
            if _stops(judged.errors):
                code = next(c for c, _ in judged.errors if c in intake.STOPS_THE_RUN or c == "DAILY_CAP")
                out.short = None if out.surfaced >= surface_min else _code_reason(code)
                break

    out.queued = queue.size(accept=here)
    out.dry = [f"{c} {got} of {floor}" for c, got, floor in balance.short()]
    if out.read or out.surfaced:
        bar.done("reading", _reading_note(out, balance.split()), reading_started)
    if not _stops(out.errors):
        _near_misses(out, run_ids, near, cv=cv, rank=rank, now=now, at=bar.edges["reading"][1], redis=client)
    if out.short:
        out.errors.append(("FEWER_THAN_8", f"Found {out.surfaced} of the {surface_min} a search aims for: {out.short}."))
    logfire.info(
        "Search surfaced {surfaced} job(s), {queued} more queued",
        surfaced=out.surfaced,
        queued=out.queued,
        short=out.short or "",
        feeds=out.feeds,
    )


def _near_misses(
    out: Piped,
    run_ids: dict[str, str],
    near: list["strategist.NearMiss"],
    *,
    cv: object,
    rank: object,
    now: int,
    at: float,
    redis: object | None = None,
) -> None:
    """UPSK-06 (20 Nov): the jobs this CV nearly matched, read for what they ask, once the search's
    own jobs are scored. A failure here costs Upskills some near misses and never the search.

    **In the background on a real install** (20 Nov): read inside the run, it held a search open
    31–40 s after its last job was scored, and held "Find more" 32 s to read three — time the person
    spent waiting on Upskills, which nothing on the Jobs screen needs. `capture` opens its own units
    of work, so it runs on its own thread once the search's jobs are in, and says what it read in
    the logs rather than the run's step log (the run has finished by then). SQLite and the demo
    keep it in line, in order, where the tests and the one writer need it.
    """
    from ..services import near_misses

    if wera().upskills.near_miss_per_search <= 0:
        return
    if db_engine().dialect.name != "sqlite" and not get_settings().wera_demo_mode:

        def later() -> None:
            try:
                got = near_misses.capture(near, cv=cv, rank=rank, now=now, redis=redis)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 — Upskills' extra evidence, never anything a search waits on
                logfire.exception("Near misses for Upskills failed")
                return
            logfire.info(
                "Upskills: {extracted} near misses read for what they ask ({above} close to the CV of {candidates})",
                extracted=got.extracted,
                above=got.above_floor,
                candidates=got.candidates,
                seconds=got.seconds,
            )

        threading.Thread(target=contextvars.copy_context().run, args=(later,), name="near-misses", daemon=True).start()
        return
    started = time.monotonic()
    intake.report(run_ids, phase="harvesting", progress=at, detail="Reading the jobs you nearly matched, for Upskills")
    try:
        got = near_misses.capture(near, cv=cv, rank=rank, now=now, redis=redis)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — Upskills' extra evidence, never a reason a search fails
        logfire.exception("Near misses for Upskills failed")
        return
    out.near_misses = got
    if got.candidates:
        message = (
            f"Upskills: {got.extracted} of the jobs you nearly matched read for what they ask "
            f"({got.above_floor} close to your CV of {got.candidates}, {got.already_read} read before)"
        )
        intake.report(
            run_ids,
            phase="harvesting",
            progress=at,
            detail=message,
            note=message,
            took_ms=int((time.monotonic() - started) * 1000),
        )


def _stops(errors: list[tuple[str, str]]) -> bool:
    return any(code in intake.STOPS_THE_RUN or code == "DAILY_CAP" for code, _ in errors)


def _code_reason(code: str) -> str:
    if code == "DAILY_CAP":
        return "the scoring limit of the last 24 hours is used up"
    if code == "BUDGET_REACHED":
        return "this week's model budget is spent"
    return "the model provider stopped answering"


def _why_short(serper: Serper, pending: list[int], spend: SearchOutcome) -> str:
    """Which limit ended a search below `surface.min`, in the words the run row shows."""
    if serper.stopped == "cap":
        return f"the limit of {serper.max_queries} searches a run was reached"
    if serper.stopped == "breaker":
        return "Serper was rate-limiting or failing, so WERA is leaving it alone for a minute — try again shortly"
    if serper.stopped == "daily_budget":
        return "today's Serper budget for searches is spent — the scheduled runs keep the rest"
    if serper.stopped:
        return "Serper refused the key"
    if spend.capped:
        return _paid_cap(spend, None)
    if not pending:
        return "every search in the plan has been sent — there were no more postings to read"
    return "the searches found no more postings"


def _paid_cap(spend: SearchOutcome, caps: SearchCaps | None) -> str:
    """Which Firecrawl limit stopped paying: the paid reads a run may make, or its credits."""
    if spend.capped == "pages":
        limit = f" of {caps.max_pages}" if caps is not None else ""
        return f"the limit{limit} paid page reads a run may make was reached"
    return "this run's Firecrawl credits are spent"


def _count(n: int, noun: str, plural: str | None = None) -> str:
    return f"{n} {noun if n == 1 else plural or noun + 's'}"


def _plan_note(planned: list, matrix: object | None, now: int) -> str:
    """What planning came to, for the run's log."""
    from ..clock import iso

    countries = _count(len({q.gl for q in planned if q.gl}), "country", "countries")
    searches = _count(len(planned), "search", "searches")
    if matrix is None:
        return f"The Strategist didn't answer, so the searches use your titles as written: {searches} in {countries}"
    if getattr(matrix, "generated_at", None) == iso(now):
        return f"The Strategist planned {searches} in {countries}"
    return f"Today's plan still fits your CV and profile: {searches} in {countries}"


def _feeds_note(asked: "strategist.FeedDiscovery") -> str:
    used = {f: st for f, st in asked.by_feed.items() if "skipped" not in st}
    searches = sum(int(st.get("searches", 0)) for st in used.values())  # type: ignore[call-overload]
    leads = sum(int(st.get("leads", 0)) for st in used.values())  # type: ignore[call-overload]
    each = ", ".join(f"{feeds.FEEDS[f].name} {st['searches']}" for f, st in used.items() if st.get("searches"))
    left = [feeds.FEEDS[f].name for f, st in asked.by_feed.items() if st.get("skipped")]
    text = f"Job APIs: {_count(searches, 'search', 'searches')}, {_count(leads, 'posting')}, {asked.queued} queued to read"
    if each:
        text += f" ({each})"
    if asked.unique:
        text += f" · {asked.passed} of {asked.unique} passed your filters"
    if asked.resumed:
        text += f" · {asked.resumed} kept from an earlier search looked for"
    if asked.waiting:
        text += f" · {_count(asked.waiting, 'lead')} waiting for the next search with Google to spare"
    if left:
        text += f" · not asked: {', '.join(left)}"
    return text


def _search_note(tier: int, found: "strategist.Discovery", engine: str = "Google", only: set[str] | None = None) -> str:
    postings = found.kinds["posting"]
    where = f" for {', '.join(sorted(c.upper() for c in only))}" if only else ""
    text = (
        f"{engine}, tier {tier}{where}: {_count(found.asked, 'search', 'searches')}, "
        f"{_count(found.results, 'result')}, {_count(postings, 'posting')}, {found.queued} queued to read"
    )
    skipped = [
        part
        for part in (
            f"{found.excluded} left out by title" if found.excluded else "",
            f"{found.known} already saved" if found.known else "",
        )
        if part
    ]
    return text + (f" ({', '.join(skipped)})" if skipped else "")


def _reading_note(out: Piped, split: str = "") -> str:
    """What reading came to, short enough for the log's 200 characters: the split by place and
    source first (SRCH-26), then what was kept and scored. How each was read is in Logfire."""
    refused = sum(out.rejected.values())
    where = f" ({split})" if split else ""
    text = f"Read {_count(out.read, 'posting')}{where}: {sum(out.by_path.values())} kept"
    if refused:
        top = out.rejected.most_common(1)[0][0].replace("-", " ")
        text += f", {refused} refused (most: {top})"
    return text + f" · {_count(out.surfaced, 'job')} scored, {len(out.new_ids)} new on your shortlist"
