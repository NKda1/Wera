"""Intake runs (SPEC §6.7)."""

from typing import Literal

from .base import Model, Opt

# The channels a search actually fetches from: the three a person can turn on in their profile.
type SearchChannel = Literal["board_search", "email_alert", "watchlist"]

# What can occupy a row in `intake_runs`. `rescore` and `score_more` are *not* channels — they
# fetch nothing and spend no Firecrawl credits, and neither is in the search profile, so neither
# can be toggled or estimated. They share the table because they are the same long, cancellable,
# progress-reporting scoring pass a search ends with, which means the Jobs screen's run panel
# displays them for free.
#
# The two are the same work and different intents, which is why they are two names and not one
# with a flag: `rescore` recomputes scores a CV change invalidated, `score_more` calls up the next
# batch from the broad search's pool. A person who asked for one would not recognise the other's
# label, and the label is the only part of a run they read.
#
# `SearchChannel` is the narrow type: anything offering channels as *choices* takes that, and only
# something displaying a run takes this one.
type IntakeChannel = Literal["board_search", "email_alert", "watchlist", "rescore", "score_more"]


# UX-01: the stages a run moves through, in order. Every one of these already existed as a step
# in `graphs.intake`; none of them reached the person, because `phase` only had three values and
# so "searching" stood for everything from a board-discovery search to the last page scraped.
#
# The order is the contract: the Jobs screen renders a checklist by comparing a run's phase
# against this list, so a stage added in the middle must be added in the middle here too.
#
# Which stages a given run passes through depends on what it is — a board search scrapes, a
# watchlist reads feeds, a re-score does neither — so the screen takes its step list from the
# channel (`STEPS` in SearchRun.tsx) and uses this only for "have we got there yet".
type RunPhase = Literal[
    "queued",
    # Finding *boards* rather than jobs: ATS-05 weekly, and SRCH-12's site proposals.
    "discovering",
    "planning",  # SRCH-20 stage 1: the Strategist's plan and its queries
    "harvesting",  # SRCH-20 stage 3: the Harvester reading queued postings, free rungs first
    "searching",  # SRCH-20 stage 2: Serper queries, and reading pages of results for their links
    "scraping",  # reading pages a search found — no longer a board-search stage, kept for old rows
    "feeds",  # employers' own ATS feeds, free and daily
    "reading",  # the alert mailbox
    "collapsing",  # NORM-05/06 dedupe, so a job found three ways is scored once
    "filtering",  # MATCH-01's free rules, then the local embedding pre-filter
    "scoring",  # the expensive one: a model per job
    "closing",  # ATS-03's closed-job sweep
    "done",
]

# Everything before `scoring`, in `RunPhase` order. The bar gives all of it one quarter
# (`intake.SEARCH_SHARE`), because scoring is where a real run spends its time.
PHASE_ORDER: tuple[str, ...] = (
    "queued",
    "discovering",
    "planning",
    "searching",
    "harvesting",
    "scraping",
    "feeds",
    "reading",
    "collapsing",
    "filtering",
    "scoring",
    "closing",
    "done",
)


class RunError(Model):
    code: str
    message: str  # sanitised, §9.4


class IntakeRun(Model):
    id: str
    channel: IntakeChannel
    status: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
    started_at: str
    finished_at: Opt[str] = None
    progress: float  # 0–1, for the UI
    # UX-01: what the run is doing, and where it is inside that. `scoring` is the long phase —
    # a real run spends almost all of its wall clock there — so `done`/`total` are what make the
    # bar move at all outside demo mode.
    phase: RunPhase
    # The noun: which board is being searched, which job is being scored. Null when the run has
    # nothing to add — a heartbeat is best-effort, so an absent detail means "nothing said".
    detail: str | None = None
    done: int
    total: int
    queries: int
    results: int
    new_jobs: int
    credits_used: int
    # SRCH-10: what the crawl cache answered for free, in the same units as `credits_used`. Shown
    # beside it rather than subtracted from it — "0 credits" and "0 credits, 40 saved" are
    # different stories, and only the second one says the run did any work.
    credits_saved: int = 0
    # SRCH-05: how the pages were read. Shown rather than inferred from `credits_used`, because
    # "0 credits" could mean the ladder worked or could mean nothing was found, and those are
    # opposite stories.
    local_pages: int = 0
    fallback_pages: int = 0
    # SRCH-26: postings found and left waiting for "Find more", so "12 new jobs · 18 more queued"
    # can say there is more without anything having been paid for it yet.
    queued: int = 0
    # How many jobs a batch is, so the screen knows when one has landed and can show it without
    # waiting for the rest. A fact about how the run is handing out scoring, which is why it
    # travels with the run rather than being a constant the UI repeats.
    batch_size: int = 0
    errors: list[RunError]


class ChannelSummary(Model):
    # The profile screen's channel strip, so: never a re-score.
    channel: SearchChannel
    detail: str
    stats: str
    enabled: bool


class Estimate(Model):
    queries: int
    pages: int
    credits: int
    usd: float
    companies: int
    demo: bool


class RunIds(Model):
    run_ids: list[str]
