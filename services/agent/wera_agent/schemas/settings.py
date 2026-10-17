"""Settings (SPEC §5.15). Secrets appear only as status, never as values (SET-01)."""

from typing import Annotated, Literal
from urllib.parse import urlparse

from .base import Model, Opt, check, required
from .jobs import FitWeights

type SecretStatus = Literal["set", "missing", "invalid"]


class TierModels(Model):
    fast: Annotated[str, required("Pick a model")]
    scoring: Annotated[str, required("Pick a model")]
    writing: Annotated[str, required("Pick a model")]


class InboxSettings(Model):
    label: str
    folder: str
    last_checked_at: str
    emails_this_week: int
    senders: list[str]


class Settings(Model):
    demo_mode: bool
    redact_contact: bool
    weekly_budget_usd: float
    spent_this_week_usd: float
    cv_pages: Literal[1, 2]
    base_url: str
    models: TierModels
    weights: FitWeights
    inbox: InboxSettings


class SecretsStatus(Model):
    llm: SecretStatus
    firecrawl: SecretStatus
    inbox: SecretStatus


class SettingsResponse(Settings):
    secrets: SecretsStatus
    llm_provider: str
    llm_last_checked_at: Opt[str] = None


def _is_url(v: str) -> bool:
    u = urlparse(v)
    return u.scheme in ("http", "https") and bool(u.netloc)


NonNegative = Annotated[float, check(lambda v: v >= 0, "min", "Can't be negative")]


class PatchWeights(Model):
    core: NonNegative
    adjacent: NonNegative
    experience: NonNegative
    eligibility: NonNegative
    freshness: NonNegative


class InboxPatch(Model):
    senders: list[Annotated[str, required("This can't be empty")]]


class SettingsPatch(Model):
    """Non-secret settings a person can change. Keys are set in the environment only (SET-07)."""

    redact_contact: Opt[bool] = None
    weekly_budget_usd: Opt[
        Annotated[
            float,
            check(lambda v: v >= 0, "min", "Budget can't be negative"),
            check(lambda v: v <= 1000, "max", "Budget is capped at $1,000 a week"),
        ]
    ] = None
    cv_pages: Opt[Literal[1, 2]] = None
    base_url: Opt[Annotated[str, check(_is_url, "url", "Enter a full URL, like https://openrouter.ai/api/v1")]] = None
    models: Opt[TierModels] = None
    weights: Opt[PatchWeights] = None
    inbox: Opt[InboxPatch] = None
