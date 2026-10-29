"""Search profile (SPEC §6.2) with the PRF-03 limits; messages name the field."""

from typing import Annotated, Literal

from pydantic import field_validator

from .base import Model, count, fail, required

# The levels a search can ask for. The three above `mid` exist so `filters.too_senior` has
# something to honour: before they did, every level in this list was below every word the filter
# hides, so the setting could not widen a search even in principle.
type Seniority = Literal["intern", "graduate", "junior", "entry", "mid", "senior", "lead", "principal"]
type Recency = Literal["24h", "7d", "30d"]
Term = Annotated[str, required("This can't be empty")]

# ISO 3166-1 alpha-2. Upper-cased on the way in so "gb" and "GB" are the same country.
Country = Annotated[str, required("Pick a country")]


class ProfileChannels(Model):
    board_search: bool
    inbox: bool
    watchlist: bool


class SearchProfile(Model):
    titles: Annotated[list[Term], count(1, 8, "Add at least 1 target title", "Use at most 8 target titles")]
    skills: Annotated[list[Term], count(1, 15, "Add at least 1 skill", "Use at most 15 skills")]
    # No lower bound any more: `countries` carries "where", and a profile that wants every city
    # in a country shouldn't have to invent one to list.
    locations: Annotated[list[Term], count(0, 8, "Add a location or leave it empty", "Use at most 8 locations")]
    # SRCH-11: where the person is looking for work, as countries. This is what decides which
    # boards are searched — most are national, so asking a UK graduate board about Berlin spends
    # credits it cannot answer. `locations` narrows *within* these to particular cities.
    #
    # Empty means "no country restriction", which is what every profile written before this
    # field existed means. That keeps old rows readable with no migration, and it fails open: a
    # profile with no countries searches every reviewed board, rather than none.
    countries: Annotated[list[Country], count(0, 8, "Pick a country or leave it empty", "Use at most 8 countries")] = []
    remote: Literal["any", "remote_only", "no_remote"]
    seniority: list[Seniority]
    exclude: list[Term]  # title terms
    sites: list[Term]  # domains enabled in config/sites.yaml
    recency: Recency
    channels: ProfileChannels
    generated_from_cv_version: int
    updated_at: str

    @field_validator("countries")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        """Stored upper-case so a country can be compared without everyone remembering to."""
        seen: list[str] = []
        for code in v:
            code = code.strip().upper()
            if code and code not in seen:
                seen.append(code)
        return seen

    @field_validator("channels")
    @classmethod
    def _one_channel(cls, v: ProfileChannels) -> ProfileChannels:
        if not (v.board_search or v.inbox or v.watchlist):
            raise fail("channels", "Turn on at least one channel")
        return v

    def targets_anywhere(self) -> bool:
        """No country named, so no board is ruled out on geography."""
        return not self.countries
