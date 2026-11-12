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

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import logfire

from ..config import sites, wera
from ..db.session import transaction
from ..errors import ApiError
from ..nodes import strategist
from ..sources import browser, harvest, targets
from ..sources.firecrawl_search import SearchCaps, SearchOutcome
from ..sources.serper import Serper
from . import intake

if TYPE_CHECKING:
    from ..sources.crawl_cache import CrawlCache

type Mode = Literal["search", "more"]

# Where the bar is at each point of a search, for its own row. Scoring is where the time goes —
# eleven seconds a job — so the jobs scored decide most of it.
PLANNED_AT = 0.03
SEARCHED_AT = 0.12


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

    # "Find more" first scores what is already saved and waiting — no network at all — and only
    # goes looking if that leaves it short.
    if mode == "more":
        judged = intake.judge([], now=now, run_ids=run_ids, progress_from=0.0, progress_to=0.5, limit=surface_max)
        out.surfaced += judged.surfaced
        out.errors += judged.errors
        if out.surfaced >= surface_min or _stops(judged.errors):
            return

    # Stage 1: the plan, and its queries.
    intake.report(run_ids, phase="planning", progress=PLANNED_AT, detail="the Strategist's plan")
    with transaction() as session:
        store = Store(session, now)
        planned, _matrix = store.search_queries(refresh=True)
        profile = store.get_profile()
        known = store.known_apply_urls()
        key = store.plan_key()
    if not planned:
        out.errors.append(("NO_QUERIES", "Add a target title to your search profile to search."))
        return

    # Redis and Serper are checked before anything is spent: a run that can't queue or can't ask
    # stops here, not after paying for half of it.
    queue = targets.TargetQueue(targets.connect(), key)
    serper = Serper(max_queries=caps.max_serper_queries)
    queue.retire_others()
    boards = sites()
    tiers = sorted({q.tier for q in planned})
    pending = [t for t in tiers if mode == "search" or t not in queue.tiers_sent()]

    def discover_next() -> bool:
        """Stage 2 for the next tier. False when there is no tier left or Serper stopped."""
        if not pending or serper.stopped:
            return False
        tier = pending.pop(0)
        sending = sum(1 for q in planned if q.tier == tier)
        intake.report(run_ids, phase="searching", progress=PLANNED_AT, detail=f"tier {tier} · {sending} searches")
        found = strategist.discover(planned, [tier], serper=serper, queue=queue, known_urls=known, sites=boards, profile=profile)
        queue.mark_tier_sent(tier)
        out.tiers.append(tier)
        out.queries = serper.sent
        out.results += found.results
        out.kinds.update(found.kinds)
        out.listings_read += found.listings_read
        out.errors += [("SEARCH", message) for message in found.errors if ("SEARCH", message) not in out.errors]
        return True

    if mode == "search":
        discover_next()  # a search always searches; "Find more" reads the queue first

    with browser.session() as fetch_html:
        while out.surfaced < surface_max:
            with transaction() as session:
                intake._check_cancelled(session, list(run_ids.values()))
            batch = queue.take(min(harvest.BATCH, surface_max - out.surfaced))
            if not batch:
                if out.surfaced >= surface_min:
                    break
                if discover_next():
                    continue
                out.short = _why_short(serper, pending, out.spend)
                break

            # Stage 3: read and check.
            progress = SEARCHED_AT + (1 - SEARCHED_AT) * out.surfaced / surface_max
            intake.report(
                run_ids, phase="harvesting", progress=progress, detail=batch[0].host, done=out.surfaced, total=surface_max
            )
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
                progress_to=min(1.0, progress + (1 - SEARCHED_AT) * len(read.jobs) / surface_max),
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

    out.queued = queue.size()
    if out.short:
        out.errors.append(("FEWER_THAN_8", f"Found {out.surfaced} of the {surface_min} a search aims for: {out.short}."))
    logfire.info(
        "Search surfaced {surfaced} job(s), {queued} more queued",
        surfaced=out.surfaced,
        queued=out.queued,
        short=out.short or "",
    )


def _stops(errors: list[tuple[str, str]]) -> bool:
    return any(code in intake.STOPS_THE_RUN or code == "DAILY_CAP" for code, _ in errors)


def _code_reason(code: str) -> str:
    if code == "DAILY_CAP":
        return "today's scoring limit is used up"
    if code == "BUDGET_REACHED":
        return "this week's model budget is spent"
    return "the model provider stopped answering"


def _why_short(serper: Serper, pending: list[int], spend: SearchOutcome) -> str:
    """Which limit ended a search below `surface.min`, in the words the run row shows."""
    if serper.stopped == "cap":
        return f"the limit of {serper.max_queries} searches a run was reached"
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
