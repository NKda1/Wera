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

**Short of 8, the previews (22 Nov).** A search — or "Find more" — that has read everything it
queued and has nothing left to send, still under `surface.min`, reads its best previews in full,
best match first, until it has 8 or its budgets end (`_read_short`): a hunted link is read where it
is, a job API's lead or an aggregator's copy with a lookup from the search's one allowance
(`lookups_per_search`), and every read is scored within the day's limit (MATCH-05). At its end,
the hunted links still queued — the ones this search didn't read — become previews themselves.
Since the evening of 22 Nov: each ticked country's floor first, a good match with no lookup left
scored on its summary, and a preview read counted as found by this search (`Store.upsert_jobs`).
"""

import contextvars
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
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
from ..sources import feeds, harvest, hunt, rotation, targets
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
# 22 Nov: a short search's previews, read this many at once, and at most this many a search — the
# read budget on top of the lookups' and the day's scoring limit. A round is about what one more
# round of the queue would have read; three of them is about a search's worth of misses.
PREVIEW_ROUND = 8
PREVIEW_READS = 24
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
    # SRCH-22 (21 Nov): what the hunter did across the search's tiers.
    hunted: hunt.Hunted = field(default_factory=hunt.Hunted)
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
    # 22 Nov: previews saved this run — aggregators' copies the lookups didn't reach, and hunted
    # links it didn't read — and previews it read in full because it was short.
    previews_saved: int = 0
    previews_read: int = 0
    # 22 Nov, for the log: scored under the fit bar, and new on the list with no score yet.
    under_bar: int = 0
    unscored: int = 0

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
                    # SRCH-22 (21 Nov): the hunter's pages, renders, refusals and what they gave.
                    "hunt_pages": out.hunted.pages,
                    "hunt_listings": out.hunted.listings,
                    "hunt_followed": out.hunted.followed,
                    "hunt_rendered": out.hunted.rendered,
                    "hunt_credits": out.hunted.credits,
                    "hunt_refused": out.hunted.refused,
                    "hunt_skipped_refused": out.hunted.skipped,
                    "hunt_disallowed": out.hunted.disallowed,
                    "hunt_render_capped": out.hunted.render_capped,
                    "hunt_found": out.hunted.found,
                    "hunt_new": out.hunted.new,
                    "hunt_copies": out.hunted.copies,
                    "hunt_originals": out.hunted.originals,
                    "hunt_postings_by_page": out.hunted.by_page,
                    "read": out.read,
                    "accepted_by": dict(out.by_path),
                    "rejected": dict(out.rejected),
                    "firecrawl_credits": out.spend.credits_used,
                    "surfaced": out.surfaced,
                    "queued": out.queued,
                    "short": out.short or "",
                    "countries_dry": out.dry,
                    "previews_saved": out.previews_saved,
                    "previews_read": out.previews_read,
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
        credits_week = store.credits_this_week()
    if not planned:
        out.errors.append(("NO_QUERIES", "Add a target title to your search profile to search."))
        return

    # Redis and Serper are checked before anything is spent: a run that can't queue or can't ask
    # stops here, not after paying for half of it.
    client = targets.connect()
    queue = targets.TargetQueue(client, key, pool=pool, rank=rank, reader=reader)
    # SRCH-20 (21 Nov): what this search sends — each planned search's first phrasing and page this
    # pool hasn't asked in the last few days, in the plan's order. `planned` stays whole for the job
    # APIs, which ask by title and place, not by these words.
    memory = rotation.Asked(client, pool, now=now)
    turned = rotation.rotate(planned, memory.recall(), now=now)
    bar.done("planning", _plan_note(planned, matrix, now, turned), started)
    meter = Guard(client, "serper")
    _allowance(run_ids, bar, meter, credits_week)
    serper = Serper(max_queries=caps.max_serper_queries, guard=meter)
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
    # SRCH-32 (22 Nov): the search's one lookup allowance — the job APIs' snippet leads, the
    # aggregators' copies the tiers turn up, and the previews a short search reads — on one Serper
    # client of its own, made the first time anything needs a lookup.
    lookups = _Lookups(
        lambda: Serper(max_queries=wera().lookups_per_search, window=strategist.RESOURCE_WINDOW, guard=Guard(client, "serper")),
        wera().lookups_per_search,
    )
    # UPSK-06: the leads the title rule dropped, for Upskills once reading is done.
    near: list[strategist.NearMiss] = []

    # SRCH-26 (18 Nov): Firecrawl's search, asked instead when Serper's day is spent or it is
    # failing — never for the run's own cap, which is the person's limit, not a provider's.
    stand_in = FirecrawlSearch(max_queries=wera().source_share.firecrawl_searches, spend=out.spend, caps=caps)
    # SRCH-22 (21 Nov): one hunter for the search, so its caps, refusals and hunt cache span every
    # tier. Renders are charged to the run's credits; aggregators' originals take the search's lookups.
    hunter = hunt.Hunter(
        redis=client,
        run=run_ids.get("board_search") or next(iter(run_ids.values()), None),
        spend=out.spend,
        caps=caps,
        render=bool(get_settings().firecrawl_api_key),
        lookup=lookups.get,
        countries=sorted(countries),
    )

    def searcher() -> "Serper | FirecrawlSearch | None":
        if not serper.stopped:
            return serper
        if serper.stopped in ("daily_budget", "breaker") and not stand_in.stopped and get_settings().firecrawl_api_key:
            return stand_in
        return None

    def sent() -> int:
        return serper.sent + stand_in.sent + lookups.sent

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
            asked = strategist.discover_feeds(
                planned,
                profile,
                queue=queue,
                known_urls=known,
                sites=boards,
                now=now,
                redis=client,
                person=person,
                resource=lookups.get(),
                on_step=bar.step("apis"),
                rank=rank,
                # Two-thirds of the allowance at most, and only for good matches: the Google tiers'
                # copies and a short search's previews are still to come (22 Nov).
                lookups=strategist.api_lookups(lookups.total),
                bar=strategist.LOOKUP_BAR,
            )
            near.extend(asked.near_misses)
            queue.mark_tier_sent(0)
            # 21 Nov: the leads past the lookups, kept as previews — after the hard filters, with
            # no model call — for the person to read on demand, and for "Find more" to read.
            previewed = _save_previews(asked.previews, run_ids, now=now)
            out.previews_saved += previewed
            bar.done("apis", _feeds_note(asked, previewed), started)
            out.feeds = asked.by_feed
            out.leads = {
                "unique": asked.unique,
                "passed": asked.passed,
                "queued": asked.queued,
                "not_looked_for": asked.not_looked_for,
                "previews": previewed,
            }
            out.leads |= {f"dropped_{why}": n for why, n in asked.dropped.items()}
            out.queries = sent()
            out.errors += [("SEARCH", message) for message in asked.errors if ("SEARCH", message) not in out.errors]
            return True
        # The rotated plan: what this search asks (`rotation.rotate`). A search whose every phrasing
        # was asked lately is resting, so a tier can be shorter than planned, or empty.
        plan = turned.plan if tier != 4 else strategist.page_two_worth_asking(turned.plan, queue.postings_by_query())
        if only is not None:
            plan = [q for q in plan if q.gl in only]
        sending = sum(1 for q in plan if q.tier == tier)
        if not sending:
            if only is not None:
                return False
            # No page 1 was rich enough for its page 2, or every search in the tier was asked
            # lately (rotation): nothing to pay for.
            queue.mark_tier_sent(tier)
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
            hunter=hunter,
            on_asked=memory.note,
        )
        if only is None:
            queue.mark_tier_sent(tier)
        # 22 Nov: aggregators' copies the lookups didn't reach, kept to read when asked.
        previewed = _save_previews(found.previews, run_ids, now=now)
        out.previews_saved += previewed
        note = _search_note(tier, found, "Firecrawl" if engine is stand_in else "Google", only, previewed)
        bar.done("searching", note + _google_left(meter), started)
        out.tiers.append(tier)
        out.queries = sent()
        out.results += found.results
        out.kinds.update(found.kinds)
        out.listings_read += found.listings_read
        out.hunted.add(found.hunted)
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
    read_previews = False
    tried: set[str] = set()
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
                # 22 Nov: nothing left to read or send, and short of `surface.min` — a search or
                # "Find more" alike reads its best previews in full, best match first, until it has
                # 8 or its budgets end, and scores them like any posting it read (21 Nov this was
                # "Find more" only, once every tier was sent).
                if not read_previews:
                    read_previews = True
                    errors = _read_short(
                        out,
                        run_ids,
                        lookups=lookups,
                        tried=tried,
                        surface_min=surface_min,
                        surface_max=surface_max,
                        boards=boards,
                        profile=profile,
                        caps=caps,
                        now=now,
                        cache=cache,
                        fetch_html=fetch_html,
                        redis=client,
                        countries=sorted(countries),
                        at=bar.edges["reading"][0]
                        + (bar.edges["reading"][1] - bar.edges["reading"][0]) * out.surfaced / surface_max,
                    )
                    if errors is not None:
                        out.queries = sent()
                        if _stops(errors):
                            code = next(c for c, _ in errors if c in intake.STOPS_THE_RUN or c == "DAILY_CAP")
                            out.short = None if out.surfaced >= surface_min else _code_reason(code)
                            break
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
            out.under_bar += judged.under_bar
            out.unscored += judged.unscored
            out.errors += judged.errors
            if _stops(judged.errors):
                code = next(c for c, _ in judged.errors if c in intake.STOPS_THE_RUN or c == "DAILY_CAP")
                out.short = None if out.surfaced >= surface_min else _code_reason(code)
                break

    # 22 Nov: the hunted links still queued are the ones this search didn't read: each becomes a
    # preview now, and leaves this person's queue, so it is one or the other and never both.
    out.previews_saved += _hunted_to_previews(
        queue, here, rank=rank, boards=boards, run_ids=run_ids, now=now, at=bar.edges["reading"][1]
    )
    out.queries = sent()
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


class _Lookups:
    """A search's one lookup allowance (SRCH-32, 22 Nov): a Serper client of its own with
    `lookups_per_search` searches, made the first time something needs a lookup — most searches'
    copies and previews need none, and a client that is never asked shouldn't exist."""

    def __init__(self, make: "Callable[[], Serper]", total: int) -> None:
        self._make, self.total = make, total
        self.client: Serper | None = None

    def get(self) -> Serper:
        if self.client is None:
            self.client = self._make()
        return self.client

    @property
    def sent(self) -> int:
        return self.client.sent if self.client is not None else 0

    def left(self) -> int:
        """Lookups this search can still send: none once the client stopped (its cap, the day's
        share, the breaker). A lookup answered from Redis costs nothing, so this is the least left."""
        if self.total <= 0:
            return 0
        client = self.get()
        return 0 if client.stopped else max(client.max_queries - client.sent, 0)


def _save_previews(found: list["strategist.Preview"], run_ids: dict[str, str], *, now: int) -> int:
    """The job APIs' leads past the lookups, saved as previews (21 Nov): built as jobs
    (`normalise.from_lead`), through the hard filters, ranked by their `lead_ranker` score, and
    never shown to a model. Returns how many are new. A failure here costs the previews, never the
    search. An aggregator's copy (22 Nov) is saved the same way, with the site it is a copy on."""
    from ..services.store import Store
    from ..sources import normalise

    if not found:
        return 0
    try:
        jobs = [normalise.from_lead(p.lead, p.feed, p.country, now, match=p.score, found_on=p.found_on) for p in found]
        with transaction() as session:
            return Store(session, now).save_previews(jobs, run_ids=run_ids)
    except intake.Cancelled:
        raise
    except Exception:  # noqa: BLE001 — previews are extra, never a reason a search fails
        logfire.exception("Couldn't save the search's previews")
        return 0


def _hunted_to_previews(
    queue: targets.TargetQueue,
    here: "Callable[[targets.Target], bool]",
    *,
    rank: "targets.Ranker | None",
    boards: list,
    run_ids: dict[str, str],
    now: int,
    at: float = 1.0,
) -> int:
    """22 Nov: the hunted links this search queued and didn't read, taken off this person's queue
    and saved as previews (`normalise.from_hunted`): keyed by their own address, ranked by the
    queue's ranker on their link text, through the hard filters, with no model call — a short
    search, or the person, reads one where it is with no lookup. A link for a country no longer
    ticked stays pooled for it. Returns how many previews are new."""
    from ..services.store import Store
    from ..sources import normalise

    left = queue.take(1_000_000, accept=lambda t: t.listing is not None and here(t))
    if not left:
        return 0
    try:
        scores = rank([(t.title, t.snippet) for t in left]) if rank is not None else [None] * len(left)
        jobs = []
        for target, score in zip(left, scores, strict=True):
            title, employer = hunt.employer_of(target.url, target.title)
            jobs.append(
                normalise.from_hunted(
                    target.url,
                    title=title or target.title,
                    employer=employer,
                    site=hunt.site_name(target.listing or target.url, boards),
                    listing=target.listing,
                    now=now,
                    match=score,
                )
            )
        with transaction() as session:
            saved = Store(session, now).save_previews(jobs, run_ids=run_ids)
    except intake.Cancelled:
        raise
    except Exception:  # noqa: BLE001 — previews are extra; the links are put back rather than lost
        logfire.exception("Couldn't save the hunted links as previews")
        queue.put_back(left)
        return 0
    if saved:
        message = f"{_count(saved, 'link')} the hunter found and this search didn't read saved as previews"
        intake.report(run_ids, phase="harvesting", progress=at, detail=message, note=message)
    logfire.info("Hunted links saved as previews: {saved} new of {left}", saved=saved, left=len(left))
    return saved


def _read_short(
    out: Piped,
    run_ids: dict[str, str],
    *,
    lookups: _Lookups,
    tried: set[str],
    surface_min: int,
    surface_max: int,
    boards: list,
    profile: object,
    caps: SearchCaps,
    now: int,
    cache: "CrawlCache | None",
    fetch_html: object,
    redis: object,
    at: float,
    countries: "list[str] | None" = None,
) -> list[tuple[str, str]] | None:
    """A search short of `surface_min` with nothing left to read or send (22 Nov): its best
    previews read in full, `PREVIEW_ROUND` at a time, until it has `surface_min` or a budget ends —
    `PREVIEW_READS` a search, the lookups left (a hunted link needs none; a job API's lead or an
    aggregator's copy needs one), the day's scoring limit (MATCH-05, `intake.judge`), the Firecrawl
    caps. Scored like the postings a search reads, fit bar included. Returns the scoring's errors,
    or None when there was nothing to read.

    **Each ticked country's floor first** (SRCH-32, 22 Nov): the previews are read in
    `targets.floors_first`'s order — each country's floor of `PREVIEW_READS`, from its previews at
    the lookup bar or above, then the rest best match first — so the lookups they take are shared
    out by country too. Best match first alone read British previews only, with 28 Swiss ones
    waiting.

    **Out of lookups, a good match is scored on its summary** (SRCH-26, MATCH-04, 22 Nov): a job
    API's lead or a copy at `LOOKUP_BAR` (Good) or better that can't get a lookup is scored rough
    on its summary, capped, rather than waiting for the person; a weaker one waits. The tracked
    link is never requested either way."""
    from ..services.store import Store
    from ..sources import previews

    with transaction() as session:
        waiting = [p for p in Store(session, now).list_previews() if p.id not in tried]

    def good(p: object) -> bool:
        match = getattr(p, "preview_match", None)
        return match is None or match >= strategist.LOOKUP_BAR

    ticked = list(countries or [])
    if ticked:
        # A preview for a place no longer ticked waits for it: the filters would only hide it.
        waiting = [p for p in waiting if not p.location.country or p.location.country.lower() in ticked]
    order = targets.floors_first(waiting, PREVIEW_READS, ticked, lambda p: p.location.country, floor_ok=good, limit=len(waiting))
    waiting = [waiting[i] for i in order]
    read = originals = summary = unread = 0
    added = under = unscored = 0
    errors: list[tuple[str, str]] = []
    while waiting and out.surfaced < surface_min and read < PREVIEW_READS:
        with transaction() as session:
            intake._check_cancelled(session, list(run_ids.values()))
            allowance = Store(session, now).score_allowance()
            cap_message = Store(session, now).score_cap_message() if allowance == 0 else ""
        if allowance == 0:
            # Nothing read that can't be scored: the lookups and the reads keep for the person.
            errors.append(("DAILY_CAP", cap_message))
            out.errors.append(("DAILY_CAP", cap_message))
            break
        room = min(PREVIEW_ROUND, PREVIEW_READS - read, allowance if allowance is not None else PREVIEW_ROUND)
        left = None  # asked only when a preview needs a lookup
        pick: list = []
        on_summary: set[str] = set()
        for preview in waiting:
            if len(pick) >= room:
                break
            if previews.is_direct(preview):
                pick.append(preview)
                continue
            if preview.title and preview.company:
                left = lookups.left() if left is None else left
                if left > 0:
                    pick.append(preview)
                    left -= 1
                    continue
            if good(preview) and preview.preview_match is not None:
                pick.append(preview)  # Good or better, and no lookup for it: scored on its summary
                on_summary.add(preview.id)
        if not pick:
            break  # what is left needs lookups this search hasn't got, and is under Good: it waits
        chosen = {p.id for p in pick}
        tried |= chosen
        waiting = [p for p in waiting if p.id not in chosen]
        looking = any(not previews.is_direct(p) and p.id not in on_summary for p in pick)
        got = previews.read_many(
            pick,
            serper=lookups.get() if looking else None,
            sites=boards,
            profile=profile,  # type: ignore[arg-type]
            caps=caps,
            spend=out.spend,
            now=now,
            cache=cache,
            fetch_html=fetch_html,  # type: ignore[arg-type]
            parse=intake.normalise_with_model,
            redis=redis,
            lookups_at_once=strategist.LOOKUPS_AT_ONCE,
            no_lookup=on_summary,
        )
        read += len(pick)
        summary += len(on_summary)
        originals += got.originals
        unread += len(got.unread)
        if not got.jobs:
            continue
        judged = intake.judge(
            got.jobs, now=now, run_ids=run_ids, progress_from=at, progress_to=at, limit=len(got.jobs), only_these=True
        )
        out.surfaced += judged.surfaced
        out.new_ids |= judged.visible_new
        out.under_bar += judged.under_bar
        out.unscored += judged.unscored
        added += len(judged.visible_new)
        under += judged.under_bar
        unscored += judged.unscored
        out.errors += judged.errors
        errors += judged.errors
        if _stops(judged.errors):
            break
    if not read:
        return errors or None
    out.read += read
    out.by_path["preview"] += read - unread
    out.previews_read += read
    full = read - summary
    did = [f"read {_count(full, 'preview')} in full ({originals} had their original)" if full else ""]
    did.append(f"{summary} scored on their summary (no lookups left)" if summary else "")
    did.append(f"{unread} couldn't be read" if unread else "")
    message = f"{_added_note(added, under, unscored)} · short of {surface_min}: {', '.join(d for d in did if d)}"
    intake.report(run_ids, phase="harvesting", progress=at, detail=message, note=message)
    return errors


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


def _plan_note(planned: list, matrix: object | None, now: int, turned: "rotation.Rotated | None" = None) -> str:
    """What planning came to, for the run's log — and, since 21 Nov, how much of it is new to ask."""
    from ..clock import iso

    countries = _count(len({q.gl for q in planned if q.gl}), "country", "countries")
    searches = _count(len(planned), "search", "searches")
    if matrix is None:
        text = f"The Strategist didn't answer, so the searches use your titles as written: {searches} in {countries}"
    elif getattr(matrix, "generated_at", None) == iso(now):
        text = f"The Strategist planned {searches} in {countries}"
    else:
        text = f"Today's plan still fits your CV and profile: {searches} in {countries}"
    return text + (_rotation_note(turned) if turned is not None else "")


def _rotation_note(turned: "rotation.Rotated") -> str:
    """SRCH-20 (21 Nov): how this search's queries differ from the plan's own words, which a
    search a day earlier may already have sent. Nothing to say when it asks the plan as written."""
    parts = [
        f"{turned.rephrased} in other words" if turned.rephrased else "",
        f"{turned.deeper} at a later page" if turned.deeper else "",
        f"{turned.rested} asked lately, resting" if turned.rested else "",
    ]
    said = [p for p in parts if p]
    return f" · {', '.join(said)}" if said else ""


def _allowance(run_ids: dict[str, str], bar: Bar, meter: Guard, credits_week: int) -> None:
    """H4 (21 Nov): what is left of the providers' allowances as the search starts, as a line in
    its log — the Serper day every run and person shares (`guard.daily`, the share a search may
    use), and the week's Firecrawl credits (`max_credits_per_week`). Said before anything is spent,
    so a short search can be read against what it had."""
    week = wera().max_credits_per_week
    parts = [f"Firecrawl credits left this week: {max(week - credits_week, 0)} of {week}"]
    if (left_of := meter.left_today()) is not None:
        parts.insert(0, f"Google searches left today: {left_of[0]} of {left_of[1]}")
    message = " · ".join(parts)
    intake.report(run_ids, phase="planning", progress=bar.edges["planning"][1], stage=1.0, detail=message, note=message)


def _google_left(meter: Guard) -> str:
    """The Serper day left after a tier, for its note: " · 88 of 120 Google searches left today"."""
    left_of = meter.left_today()
    return f" · {left_of[0]} of {left_of[1]} Google searches left today" if left_of else ""


def _feeds_note(asked: "strategist.FeedDiscovery", previewed: int = 0) -> str:
    used = {f: st for f, st in asked.by_feed.items() if "skipped" not in st}
    searches = sum(int(st.get("searches", 0)) for st in used.values())  # type: ignore[call-overload]
    leads = sum(int(st.get("leads", 0)) for st in used.values())  # type: ignore[call-overload]
    each = ", ".join(f"{feeds.FEEDS[f].name} {st['searches']}" for f, st in used.items() if st.get("searches"))
    left = [feeds.FEEDS[f].name for f, st in asked.by_feed.items() if st.get("skipped")]
    # 22 Nov: links and reads, not jobs — "683 postings, 35 queued" was read as jobs found.
    text = f"Job APIs: {_count(searches, 'search', 'searches')}, {_count(leads, 'link')} to postings, {asked.queued} to read"
    if each:
        text += f" ({each})"
    if asked.unique:
        text += f" · {asked.passed} of {asked.unique} passed your filters"
    if previewed:
        # 21 Nov: the leads past the lookups, kept to show and read on demand.
        text += f" · {previewed} saved as previews"
    if left:
        text += f" · not asked: {', '.join(left)}"
    return text


def _search_note(
    tier: int, found: "strategist.Discovery", engine: str = "Google", only: set[str] | None = None, previewed: int = 0
) -> str:
    postings = found.kinds["posting"]
    where = f" for {', '.join(sorted(c.upper() for c in only))}" if only else ""
    text = (
        f"{engine}, tier {tier}{where}: {_count(found.asked, 'search', 'searches')}, "
        f"{_count(found.results, 'result')}, {_count(postings, 'link')} to postings, {found.queued} to read"
    )
    skipped = [
        part
        for part in (
            f"{found.excluded} left out by title" if found.excluded else "",
            f"{found.known} already saved" if found.known else "",
        )
        if part
    ]
    text += (f" ({', '.join(skipped)})" if skipped else "") + _hunt_note(found.hunted)
    if previewed:
        text += f" · {_count(previewed, 'copy', 'copies')} saved as previews"
    return text


def _hunt_note(hunted: hunt.Hunted) -> str:
    """SRCH-22 (21 Nov): what hunting the pass's pages of jobs came to, after the search's own
    counts — "Hunted 12 pages (3 rendered): 85 links to postings, 41 not seen before", and the
    pages it left alone, which say why a page gave nothing. Empty when the pass had no page to hunt."""
    if not (hunted.listings or hunted.refused or hunted.skipped or hunted.disallowed or hunted.originals):
        return ""
    text = (
        f" · Hunted {_count(hunted.pages, 'page')} ({hunted.rendered} rendered): "
        f"{_count(hunted.found, 'link')} to postings, {hunted.new} not seen before"
    )
    left = hunted.refused + hunted.skipped
    alone = [
        part
        for part in (
            f"{left} refused" if left else "",
            f"{hunted.disallowed} barred by robots.txt" if hunted.disallowed else "",
            f"{_count(hunted.originals, 'original')} behind aggregators" if hunted.originals else "",
        )
        if part
    ]
    return text + (f" ({', '.join(alone)})" if alone else "")


def _added_note(added: int, under: int, unscored: int) -> str:
    """What reached the person, first in a reading step's log line (22 Nov): "9 added to your list
    (2 not scored yet), 1 under your bar". The step's other numbers are links and reads, and were
    read as jobs — "35 queued to read", then 1 on the list."""
    text = f"{added} added to your list"
    if unscored:
        text += f" ({unscored} not scored yet)"
    return text + (f", {under} under your bar" if under else "")


def _reading_note(out: Piped, split: str = "") -> str:
    """What reading came to, short enough for the log's 200 characters: what reached the list first
    (22 Nov), then the reads, split by place and source (SRCH-26), and what they came to. How each
    was read is in Logfire."""
    refused = sum(out.rejected.values())
    where = f" ({split})" if split else ""
    text = f"{_added_note(len(out.new_ids), out.under_bar, out.unscored)} · read {_count(out.read, 'posting')}{where}"
    text += f": {sum(out.by_path.values())} readable"
    if refused:
        top = out.rejected.most_common(1)[0][0].replace("-", " ")
        text += f", {refused} refused (most: {top})"
    return text
