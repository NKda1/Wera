"""config/*.yaml, loaded into Pydantic models (SPEC §9.2)."""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .paths import CONFIG_DIR

type Tier = Literal["fast", "scoring", "writing"]
type Effort = Literal["off", "low", "on"]


type Region = Literal["europe", "north_america", "apac", "global"]


class Site(BaseModel):
    """A board WERA may search, and where its jobs actually are (SRCH-11).

    `countries` is the point of this model. Most boards are national — Gradcracker is UK
    graduate schemes, Built In London is one city — so searching all of them for a job in Berlin
    spends credits on boards that structurally cannot answer. The profile picks countries and
    the enabled set follows, instead of a person hand-picking domains and having to know which
    serves where.
    """

    domain: str
    name: str
    terms_reviewed_on: date | None = None
    # ISO 3166-1 alpha-2, upper case. Empty means "no country claim", which is treated as
    # serving everywhere: a site that hasn't been tagged yet should not silently disappear from
    # every search, because that failure looks exactly like a board that stopped working.
    countries: list[str] = []
    region: Region = "global"
    # SRCH-12, mirroring `Company`'s vocabulary so the two kinds of discovery read alike: set
    # when WERA found this board itself. An entry without it was added by hand.
    discovered_on: date | None = None
    matched: str | None = None  # the target title whose search turned this board up
    # The person said no. Never proposed again, and never searched even if a review date somehow
    # appears — a decline is a decision, not a postponement.
    declined_on: date | None = None
    # SPEC §9.2, from the free robots.txt check at discovery. `None` means "not checked", which
    # the approval card says out loud rather than implying a clean bill of health.
    robots_ok: bool | None = None
    # Up to two URLs the host matched on, so reviewing a proposal means looking at real postings
    # rather than taking WERA's word that this is a job board.
    examples: list[str] = []

    @property
    def reviewed(self) -> bool:
        """SRCH-04: searchable only once a human recorded that they read the terms."""
        return self.terms_reviewed_on is not None and self.declined_on is None

    @property
    def manual(self) -> bool:
        """Added by a person rather than found by WERA. Mirrors `Company.manual`."""
        return self.discovered_on is None

    @property
    def proposed(self) -> bool:
        """Found by WERA and still waiting on a decision."""
        return self.discovered_on is not None and self.terms_reviewed_on is None and self.declined_on is None

    def serves(self, countries: list[str]) -> bool:
        """Whether this board is worth asking about jobs in any of these countries."""
        if not self.countries or not countries:
            return True
        mine = {c.strip().upper() for c in self.countries}
        return any(c.strip().upper() in mine for c in countries)


class SitesFile(BaseModel):
    """config/sites.yaml. Partly written by WERA (SRCH-12), so it has its own model.

    Site discovery is **append-only**: it never removes or reorders an entry, and simply stops
    proposing once the file reaches `max_sites`. `Company` needs a `pinned` flag because ATS-05
    *drops* entries to respect its cap; not having to do that here deletes the whole class of
    "discovery ate my board" bugs.
    """

    # Counts entries nobody has declined. A ceiling on the review queue as much as on the file:
    # two proposals a week is already about as many as anyone reviews.
    max_sites: int = 12
    discover_every_days: int = 7
    sites: list[Site] = []


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
    # ATS-05: set when WERA found this board itself, by searching for the watch titles. An
    # entry without it was added by hand and is never removed automatically.
    discovered_on: date | None = None
    matched: str | None = None  # the watch title whose search turned this board up
    pinned: bool = False  # keep this one whatever discovery finds

    @property
    def manual(self) -> bool:
        """Added by a person — pinned, or simply never discovered. Never auto-removed."""
        return self.pinned or self.discovered_on is None

    @property
    def board(self) -> str:
        return f"{self.vendor}:{self.token}"


class CompaniesFile(BaseModel):
    """config/companies.yaml. Partly written by WERA (ATS-05), so it has its own model."""

    # The kinds of job to look for boards hiring for. Empty means "use the search profile's
    # target titles", which is what most people want and what keeps the two from drifting.
    watch_titles: list[str] = []
    # A cap on the watchlist, because every company here is a feed fetched on every daily run.
    max_companies: int = 60
    # How often discovery searches for new boards. Boards don't appear daily and each search
    # costs Firecrawl credits, so weekly is the default.
    discover_every_days: int = 7
    companies: list[Company] = []


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


class CrawlCacheConfig(BaseModel):
    """SRCH-10: how long WERA may answer a crawl from what the last run already paid for.

    Minutes rather than hours because the two windows are measured against different things —
    one against a person's attention span, the other against a job ad's shelf life — and
    rounding both to hours hid how differently they behave.
    """

    enabled: bool = True
    # A search filtered to "posted this week" shifts through the day, so this is the short one:
    # six hours means clicking Search twice in an afternoon is free, while tomorrow starts fresh.
    # Deliberately shorter than the card window — a stale *search* hides jobs that exist, which
    # is the one failure a job hunt can't tolerate.
    search_minutes: int = 360
    # A job ad's text doesn't change after it's posted, and a page is 5 credits against a
    # search's 2. Two days rather than one because runs are manual and therefore irregular: at
    # 24h a Monday-then-Tuesday pair would hit or miss on the hour it happened to be clicked.
    card_minutes: int = 2880
    # A page WERA read and found no job on. The same two days, and for the same reason — this is
    # the entry that stops a permanently-unparseable page being re-read at 5 credits every run.
    # A board that starts working again produces *new* URLs, which have no entry here, so a long
    # window costs nothing in missed jobs.
    unreadable_minutes: int = 2880
    # A card payload is a whole job description. 500 of those is already tens of megabytes for a
    # table that is explicitly disposable, so the ceiling is a count, enforced by `prune`.
    max_entries: int = 500
    # Nothing bigger than this is cached at all. One pathological page shouldn't be able to put
    # a megabyte of boilerplate in front of the 499 entries that would have earned their place.
    max_payload_bytes: int = 200_000


class LifecycleConfig(BaseModel):
    """SRCH-13 / NORM-07: how the free lifecycle sweep is bounded.

    All three are about politeness rather than money — the sweep spends no Firecrawl credits —
    so the caps exist so a database with thousands of jobs doesn't fire thousands of requests at
    one board at 07:00.
    """

    # One fetch per employer, so this bounds employers, not jobs: thirty jobs at one board is one
    # request. Worth more than it looks — a feed answer is positive evidence *and* an exact date.
    max_boards_per_sweep: int = 40
    # Plain HTTP, one per posting no feed can speak for. The expensive-in-wall-clock half.
    max_http_checks: int = 150
    # NORM-07's window. Read against `max(last_seen, last_confirmed_at)`, never `last_seen` alone.
    archive_after_days: int = 30


class WeraConfig(BaseModel):
    models: Models
    fallbacks: dict[str, list[str]] = {}
    prices: dict[str, Price]
    tasks: dict[str, Task]
    weights: Weights
    crawl_cache: CrawlCacheConfig = CrawlCacheConfig()
    lifecycle: LifecycleConfig = LifecycleConfig()
    max_queries: int = 20
    max_pages: int = 30
    max_credits_per_run: int = 100
    discovery_max_queries: int = 8
    max_scored_per_day: int = 40
    # LTR-04. Empty disables the check, which is why it's a config list rather than a constant:
    # the phrases that read as filler are a matter of taste and of language.
    letter_banned_phrases: list[str] = []
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
    out = sites_file().sites
    # SRCH-03: linkedin.com can never be a search site, whatever the file says. Enforced at the
    # load boundary, which is why nothing downstream has to remember it.
    return [s for s in out if not (s.domain == "linkedin.com" or s.domain.endswith(".linkedin.com"))]


@lru_cache
def sites_file() -> SitesFile:
    """config/sites.yaml, including the file-level settings discovery needs.

    Backward compatible with the bare `{sites: [...]}` the file was: both new keys default.
    """
    return SitesFile.model_validate(_load("sites.yaml"))


@lru_cache
def senders() -> list[Sender]:
    return [Sender.model_validate(s) for s in _load("senders.yaml").get("senders", []) if s.get("enabled", True)]


@lru_cache
def companies_file() -> CompaniesFile:
    return CompaniesFile.model_validate(_load("companies.yaml"))


@lru_cache
def companies() -> list[Company]:
    return [c for c in companies_file().companies if c.enabled]


# The header kept on companies.yaml when WERA rewrites it. Discovery owns the list but not the
# explanation, and a file that silently rewrote itself with no note would be alarming to find.
_COMPANIES_HEADER = """\
# Employers whose public job feed WERA watches (ATS-01).
#
# These are the boards Greenhouse, Lever and Ashby publish for anyone — no key, no scraping, no
# terms to accept. A company here is checked on the daily watchlist run, and a job that stops
# appearing in its feed is marked closed (ATS-03).
#
# `token` is the board identifier in the employer's careers URL, not a secret:
#   https://job-boards.greenhouse.io/<token>      https://jobs.lever.co/<token>
#   https://jobs.ashbyhq.com/<token>
#
# WERA MAINTAINS THIS LIST (ATS-05). Every `discover_every_days`, it searches the ATS vendors
# for boards hiring for `watch_titles` and rewrites the entries below, keeping the newest and
# dropping boards that stopped answering. Entries it found carry `discovered_on`.
#
# Your own entries are safe. An entry with no `discovered_on`, or one marked `pinned: true`, is
# never removed or reordered by discovery — add companies by hand whenever you like.
#
# `watch_titles` empty means "use the target titles from my search profile", which is usually
# what you want: then changing your CV changes what WERA watches.
#
# To add a company by hand, append an entry with no `discovered_on`:
#
#   - name: Monzo
#     vendor: greenhouse      # greenhouse · lever · ashby
#     token: monzo
#
# Set `enabled: false` to keep an entry on file without fetching it, and `pinned: true` on a
# discovered entry to stop it being dropped when discovery moves on.
"""


def write_companies(data: CompaniesFile) -> Path:
    """Rewrites config/companies.yaml and forgets the cached copy (ATS-05).

    A generated config file is unusual in WERA, and it is here because the alternative is
    worse: hand-maintaining thirty board tokens is the kind of chore that silently stops
    happening, and a stale watchlist looks exactly like a working one. Keeping it in the config
    file rather than the database means it is reviewable, diffable and editable by hand — which
    is the whole reason the manual entries are protected.
    """
    body = {
        "watch_titles": data.watch_titles,
        "max_companies": data.max_companies,
        "discover_every_days": data.discover_every_days,
        "companies": [
            {
                k: (v.isoformat() if isinstance(v, date) else v)
                for k, v in c.model_dump(exclude_defaults=True, exclude_none=True).items()
            }
            | {"name": c.name, "vendor": c.vendor, "token": c.token}
            for c in data.companies
        ],
    }
    path = CONFIG_DIR / "companies.yaml"
    text = _COMPANIES_HEADER + "\n" + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(text, encoding="utf-8")
    companies_file.cache_clear()
    companies.cache_clear()
    return path


_SITES_HEADER = """\
# Boards searched by Firecrawl (SPEC §9.2, SRCH-04). linkedin.com is always excluded and can't
# be listed here.
#
# `terms_reviewed_on` IS THE GATE. A site with `null` there is loaded, shown in the UI and never
# searched. Setting a date is a record that a person read that site's terms of service and its
# robots.txt and decided WERA may search it (SPEC §11.3) — it is not a convenience flag, and
# nothing in the code will set it for you.
#
# `countries` (ISO 3166-1 alpha-2) is what the search profile selects on (SRCH-11). Most boards
# are national, so a search for work in Berlin should never spend credits asking a UK graduate
# board. A site with no `countries` is treated as serving everywhere, because an untagged board
# quietly vanishing from every search looks identical to a board that broke.
#
# WERA PROPOSES ENTRIES HERE (SRCH-12). Every `discover_every_days`, a board search also looks
# for boards carrying your target titles and appends what it finds with `terms_reviewed_on: null`
# and a `discovered_on` date, plus `examples` — the postings it matched on, so you have something
# real to look at. Discovery is append-only: it never removes or reorders an entry.
#
# `declined_on` means you said no. That entry is never proposed again and never searched.
#
# To add a board by hand, append an entry with no `discovered_on` and fill in the review date
# yourself once you have actually read the terms:
#
#   - domain: example-jobs.com
#     name: Example Jobs
#     terms_reviewed_on: 2026-01-31
#     countries: [GB]
#     region: europe
"""


def write_sites(data: SitesFile) -> Path:
    """Rewrites config/sites.yaml and forgets the cached copies (SRCH-12).

    Entries are built field by field rather than through `model_dump(exclude_none=True)`, which
    is what `write_companies` does. That difference is deliberate and load-bearing: dropping a
    `None` here would drop `terms_reviewed_on: null`, and that explicit null is the line that
    makes a discovered site unsearchable. Omitting it would read as "no opinion" to a human and
    would be the one field this file exists to be unambiguous about.

    SRCH-03 is re-applied on the way out, so a denied host cannot be written even by a caller
    that built the list carelessly.
    """
    kept = [s for s in data.sites if not (s.domain == "linkedin.com" or s.domain.endswith(".linkedin.com"))]
    entries = []
    for s in kept:
        entry: dict[str, Any] = {
            "domain": s.domain,
            "name": s.name,
            # Always written, never omitted. See the docstring.
            "terms_reviewed_on": s.terms_reviewed_on.isoformat() if s.terms_reviewed_on else None,
        }
        if s.countries:
            entry["countries"] = [c.strip().upper() for c in s.countries]
        if s.region != "global":
            entry["region"] = s.region
        for key, value in (("discovered_on", s.discovered_on), ("declined_on", s.declined_on)):
            if value is not None:
                entry[key] = value.isoformat()
        if s.matched:
            entry["matched"] = s.matched
        if s.robots_ok is not None:
            entry["robots_ok"] = s.robots_ok
        if s.examples:
            entry["examples"] = s.examples
        entries.append(entry)

    body = {"max_sites": data.max_sites, "discover_every_days": data.discover_every_days, "sites": entries}
    path = CONFIG_DIR / "sites.yaml"
    path.write_text(_SITES_HEADER + "\n" + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    # Both, or the write is invisible for the life of the process — `sites()` reads through
    # `sites_file()` and each is cached separately.
    sites_file.cache_clear()
    sites.cache_clear()
    return path


@lru_cache
def wera() -> WeraConfig:
    return WeraConfig.model_validate(_load("wera.yaml"))
