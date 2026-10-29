"""Intake runs (SPEC §6.7)."""

from typing import Literal

from .base import Model, Opt

type IntakeChannel = Literal["board_search", "email_alert", "watchlist"]


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
    phase: Literal["searching", "scoring", "done"]
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
    errors: list[RunError]


class ChannelSummary(Model):
    channel: IntakeChannel
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
