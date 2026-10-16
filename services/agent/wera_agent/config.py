"""config/*.yaml, loaded into Pydantic models (SPEC §9.2)."""

from datetime import date
from functools import lru_cache

import yaml
from pydantic import BaseModel

from .paths import CONFIG_DIR


class Site(BaseModel):
    domain: str
    name: str
    terms_reviewed_on: date | None = None

    @property
    def reviewed(self) -> bool:
        return self.terms_reviewed_on is not None


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


class WeraConfig(BaseModel):
    models: Models
    weights: Weights
    ats_threshold: float = 0.75
    weekly_budget_usd: float = 5
    cv_pages: int = 1


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache
def sites() -> list[Site]:
    out = [Site.model_validate(s) for s in _load("sites.yaml").get("sites", [])]
    # SRCH-03: linkedin.com can never be a search site, whatever the file says.
    return [s for s in out if not (s.domain == "linkedin.com" or s.domain.endswith(".linkedin.com"))]


@lru_cache
def wera() -> WeraConfig:
    return WeraConfig.model_validate(_load("wera.yaml"))
