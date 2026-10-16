"""Search profile (SPEC §6.2) with the PRF-03 limits; messages name the field."""

from typing import Annotated, Literal

from pydantic import field_validator

from .base import Model, count, fail, required

type Seniority = Literal["intern", "graduate", "junior", "entry", "mid"]
type Recency = Literal["24h", "7d", "30d"]
Term = Annotated[str, required("This can't be empty")]


class ProfileChannels(Model):
    board_search: bool
    inbox: bool
    watchlist: bool


class SearchProfile(Model):
    titles: Annotated[list[Term], count(1, 8, "Add at least 1 target title", "Use at most 8 target titles")]
    skills: Annotated[list[Term], count(1, 15, "Add at least 1 skill", "Use at most 15 skills")]
    locations: Annotated[list[Term], count(1, 8, "Add at least 1 location", "Use at most 8 locations")]
    remote: Literal["any", "remote_only", "no_remote"]
    seniority: list[Seniority]
    exclude: list[Term]  # title terms
    sites: list[Term]  # domains enabled in config/sites.yaml
    recency: Recency
    channels: ProfileChannels
    generated_from_cv_version: int
    updated_at: str

    @field_validator("channels")
    @classmethod
    def _one_channel(cls, v: ProfileChannels) -> ProfileChannels:
        if not (v.board_search or v.inbox or v.watchlist):
            raise fail("channels", "Turn on at least one channel")
        return v
