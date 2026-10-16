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
    queries: int
    results: int
    new_jobs: int
    credits_used: int
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
