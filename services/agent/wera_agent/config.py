"""config/*.yaml, loaded into Pydantic models (SPEC §9.2)."""

from datetime import date
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .paths import CONFIG_DIR

type Tier = Literal["fast", "scoring", "writing"]
type Effort = Literal["off", "low", "on"]


class Site(BaseModel):
    domain: str
    name: str
    terms_reviewed_on: date | None = None

    @property
    def reviewed(self) -> bool:
        return self.terms_reviewed_on is not None


class Sender(BaseModel):
    """An email sender WERA will read alerts from (MAIL-03)."""

    domain: str
    parser: Literal["linkedin", "indeed", "generic"]
    enabled: bool = True


class Company(BaseModel):
    """An employer whose public ATS feed WERA watches (ATS-01)."""

    name: str
    vendor: Literal["greenhouse", "lever", "ashby"]
    token: str
    enabled: bool = True


class Models(BaseModel):
    fast: str
    scoring: str
    writing: str


class Weights(BaseModel):
    core: float
    adjacent: float
    experience: float
    eligibility: float
    freshness: float


class Price(BaseModel):
    """USD per million tokens."""

    input: float = Field(alias="in")
    output: float = Field(alias="out")


class Task(BaseModel):
    """One row of the SPEC §9.3 task table."""

    tier: Tier
    max_tokens: int
    reasoning: Effort


class WeraConfig(BaseModel):
    models: Models
    fallbacks: dict[str, list[str]] = {}
    prices: dict[str, Price]
    tasks: dict[str, Task]
    weights: Weights
    ats_threshold: float = 0.75
    weekly_budget_usd: float = 5
    cv_pages: int = 1
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2

    def task(self, name: str) -> Task:
        try:
            return self.tasks[name]
        except KeyError:
            raise KeyError(f"{name} isn't in config/wera.yaml tasks (SPEC §9.3)") from None

    def price(self, model: str) -> Price:
        """An unlisted model is charged at `default`, never at zero."""
        return self.prices.get(model) or self.prices["default"]


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def sites() -> list[Site]:
    out = [Site.model_validate(s) for s in _load("sites.yaml").get("sites", [])]
    # SRCH-03: linkedin.com can never be a search site, whatever the file says.
    return [s for s in out if not (s.domain == "linkedin.com" or s.domain.endswith(".linkedin.com"))]


@lru_cache
def senders() -> list[Sender]:
    return [Sender.model_validate(s) for s in _load("senders.yaml").get("senders", []) if s.get("enabled", True)]


@lru_cache
def companies() -> list[Company]:
    return [Company.model_validate(c) for c in _load("companies.yaml").get("companies", []) if c.get("enabled", True)]


@lru_cache
def wera() -> WeraConfig:
    return WeraConfig.model_validate(_load("wera.yaml"))
