"""The Strategist's target matrix: where to look for this person's next job, and in what words.

One market per target country. Each says which of the known boards serve that market best, in
order, and what to search for there — the profile's own titles, plus the titles as a local
board would write them. Boards the Strategist believes exist but WERA doesn't know are not here:
they go through the same proposal queue as discovery (`sites.yaml`, SRCH-12) and are searched
only once a person has reviewed them (SRCH-04), so `proposed` records what was proposed, not
what may be searched.
"""

from typing import Literal

from .base import Model, Opt


class LocalTerm(Model):
    term: str  # as a local board would write it ("Softwareentwickler")
    language: str  # ISO 639-1
    title: str  # the profile title it stands for, so a result can be traced to what was asked


class Market(Model):
    country: str  # ISO 3166-1 alpha-2, one of the profile's countries
    languages: list[str]  # ISO 639-1, the languages postings in this market are written in
    search_terms: list[LocalTerm]
    boards: list[str]  # known domains serving this market, best first
    proposed: list[str]  # new domains sent to the review queue on this pass


class PlannedQuery(Model):
    """One search the Strategist will send (SRCH-20, stage 1). Built in code from the parsed CV and
    the profile, never by a model, and stored on the plan so the queries a search used can be read
    back — and so a new CV, which makes a new plan, can never search with the last CV's words."""

    q: str
    gl: str  # Google's country, lower case ("gb"); empty for no country
    hl: str  # the language results are asked for in ("en", "de")
    source: Literal["open", "board"]  # an open search, or a `site:` search of one reviewed board
    domain: Opt[str] = None  # the board, for a board query
    # 1 is always sent. 2 and 3 only when a search is short of 8 jobs (SRCH-26): the remaining
    # titles and title + CV skill, then the titles as local boards write them.
    tier: int
    title: str  # the profile title it serves, so a result can be traced to what was asked
    # Which page of Google's results: tier 4 asks for page 2 of tier 1's searches, for when page 1
    # is all postings WERA already has. Still one Serper credit.
    page: int = 1


class TargetMatrix(Model):
    field: str  # the professional field, in plain words ("corporate law", "backend engineering")
    markets: list[Market]
    # What it was built from. A matrix is only used while all three still hold: a new CV, a new
    # profile or a new model is a different question. The profile is fingerprinted by the fields
    # the plan depends on (`strategist.profile_key`), not by when it was saved: a save that
    # changes nothing the plan reads shouldn't throw it away, and one that does must, however
    # close together the two saves were.
    cv_version: int
    profile_key: str
    model: str
    generated_at: str
    # Defaulted so a plan saved before queries existed still loads; `Store.strategy` fills it in.
    queries: list[PlannedQuery] = []
