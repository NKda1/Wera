"""Settings (SPEC §5.15). Secrets appear only as status, never as values (SET-01)."""

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import AfterValidator, Field

from .base import Model, Opt, check, fail, required
from .jobs import FitWeights

type SecretStatus = Literal["set", "missing", "invalid"]


class TierModels(Model):
    fast: Annotated[str, required("Pick a model")]
    # Structured selection — short replies of ids, not prose. See `config.Tier`.
    select: Annotated[str, required("Pick a model")]
    scoring: Annotated[str, required("Pick a model")]
    writing: Annotated[str, required("Pick a model")]


class InboxSettings(Model):
    label: str
    folder: str
    # Null until the inbox has actually been checked, which on a real install is until the
    # first scheduled run. It was typed as a plain string because only demo mode — which seeds
    # a timestamp — had ever been loaded; outside demo mode the seed writes null and the whole
    # Settings page answered 500. Required-and-nullable rather than `Opt`: the Settings screen
    # always reports the inbox's state, and "never checked" is that state, not a missing field.
    last_checked_at: str | None
    emails_this_week: int
    senders: list[str]


# SCH-01's scheduled search: off until the person turns it on, and then weekday mornings unless
# they choose otherwise. Local time, as the rest of the schedule is.
DEFAULT_SEARCH_CRON = "0 8 * * 1-5"
# The closest two scheduled searches may be. A board's results barely move in a few hours, so a
# tighter schedule mostly finds what the last run found and still spends credits asking.
MIN_SEARCH_HOURS = 6


def check_search_cron(v: str) -> str:
    """A 5-field crontab that APScheduler reads, firing no more often than every six hours."""
    from datetime import datetime, timedelta

    from apscheduler.triggers.cron import CronTrigger

    v = " ".join(v.split())
    try:
        trigger = CronTrigger.from_crontab(v)
    except ValueError as exc:
        raise fail(
            "cron", f"That isn't a schedule WERA can read ({exc}). Use five fields: minute hour day month weekday."
        ) from None
    start = datetime.now().astimezone()
    fires, at = [], start
    for _ in range(40):
        at = trigger.get_next_fire_time(at, at + timedelta(seconds=1))
        if at is None:
            break
        fires.append(at)
    if not fires:
        raise fail("cron", "That schedule never runs.")
    gaps = [b - a for a, b in zip(fires, fires[1:], strict=False)]
    if gaps and min(gaps) < timedelta(hours=MIN_SEARCH_HOURS):
        raise fail(
            "cron",
            f"Searches need at least {MIN_SEARCH_HOURS} hours between them — closer than that mostly finds the same jobs "
            "and still spends credits.",
        )
    return v


class SearchSchedule(Model):
    """The scheduled job search: when the Strategist and the Harvester run without being asked."""

    enabled: bool
    cron: str
    # In the response only, computed rather than stored: when it next runs (null when it's off),
    # and the Firecrawl credits spent this week against the ceiling that bounds a schedule.
    next_run_at: Opt[str] = None
    credits_this_week: Opt[int] = None
    weekly_credit_cap: Opt[int] = None


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
    # Defaulted so a settings blob written before scheduling existed still loads, as "off". A
    # factory rather than an instance: an instance is written into the JSON Schema as a default,
    # and the contract generator then emitted three copies of the type, one per use.
    search_schedule: SearchSchedule = Field(default_factory=lambda: SearchSchedule(enabled=False, cron=DEFAULT_SEARCH_CRON))


class SecretsStatus(Model):
    llm: SecretStatus
    firecrawl: SecretStatus
    # Defaulted so a test or a caller building the status by hand needn't know about it.
    serper: SecretStatus = "missing"
    inbox: SecretStatus


class SettingsResponse(Settings):
    secrets: SecretsStatus
    llm_provider: str
    llm_last_checked_at: Opt[str] = None
    # SRCH-27: whether Settings offers "Clear jobs". From `config/wera.yaml`, never settable here:
    # a control that deletes applications must not be one a request can switch on.
    allow_clear_jobs: bool = False


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


class SearchSchedulePatch(Model):
    enabled: bool
    cron: Annotated[str, AfterValidator(check_search_cron)]


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
    search_schedule: Opt[SearchSchedulePatch] = None
