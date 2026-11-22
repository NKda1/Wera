"""The job APIs (SRCH-31, SRCH-32): asked before Serper, each through the provider guard.

Official APIs, in two kinds:

* **Full text** — Reed (UK), USAJOBS (US federal), ReliefWeb (international organisations),
  Himalayas (remote jobs), Arbeitnow (Europe: a feed of the latest jobs with no search, read once
  a day and matched here) and The Muse (US, entry level), the last three from 22 Nov. A posting is
  read from the API itself (Reed's details call, or the others' own payload), never by fetching
  its page, and checked like any other (SRCH-23).
  Queued as `Target(source="feed")`. They come first in `FEEDS`, so the snippet APIs' lookups are
  decided once every full-text posting is in.
* **Snippet only** — Adzuna, Careerjet and Jooble (22 Nov). They give a title, a company and a few lines, behind a
  tracked link on a pay-per-click network. **That link is never fetched**: it would register as a
  click someone paid for. Instead each lead's original posting is looked for with one Serper
  search (`resource.py`), and what is queued is that page, with `via` saying who found it.

Each API only runs where it has jobs (`countries`), with its key set, and with
`terms_reviewed_on` set in `config/wera.yaml` — the same gate as a board in sites.yaml. Careerjet
also needs the person searching: its terms require their IP address and browser on every call
(`Person`), so a scheduled run, which has no person, skips it and says so. An API that only has
remote jobs (Himalayas) is skipped for a profile that said no to remote work.

The queries come from the plan, never the CV: the titles the Strategist would search for, at the
person's level and in their city. No CV text is ever sent to any of them.
"""

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
import logfire

from ...config import FeedConfig, wera
from ...domain.places import COUNTRIES
from ...http import request
from ...settings import get_settings
from ..provider_guard import Guard, answer_key

if TYPE_CHECKING:
    from ...schemas.profile import SearchProfile

# Real-time callers wait this long for an API at most (the blueprint's "~1.2 s" is for a search a
# person is watching; WERA's searches stream, so a slower API delays its own postings only).
TIMEOUT = httpx.Timeout(8.0, connect=3.0)
# Level words a job title carries, kept apart from the role so an API can be asked for both the
# way it searches best ("graduate" in the title, "software engineer" anywhere).
LEVEL_WORDS = ("graduate", "junior", "entry level", "entry-level", "trainee", "intern", "internship", "apprentice", "placement")
PERSON_TTL_SECONDS = 60 * 60
PERSON_KEY = "wera:person:"


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    countries: frozenset[str]
    full_text: bool
    needs_person: bool = False
    # Only remote jobs: no use to someone who said they don't want remote work.
    remote_only: bool = False


# Adzuna's countries, from its API; Careerjet's are the locales WERA maps a country to.
ADZUNA_COUNTRIES = frozenset(
    ["gb", "us", "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "in", "it", "mx", "nl", "nz", "pl", "sg", "za"]
)
CAREERJET_LOCALES = {
    "gb": "en_GB", "us": "en_US", "ie": "en_IE", "ca": "en_CA", "au": "en_AU", "nz": "en_NZ", "in": "en_IN",
    "za": "en_ZA", "sg": "en_SG", "de": "de_DE", "at": "de_AT", "ch": "de_CH", "fr": "fr_FR", "be": "fr_BE",
    "es": "es_ES", "it": "it_IT", "nl": "nl_NL", "pl": "pl_PL", "se": "sv_SE", "pt": "pt_PT",
}  # fmt: skip

# Jooble's country sites (22 Nov): its API page names none, so these are the ones WERA's places
# know that Jooble runs a site for (`<cc>.jooble.org`, `jooble.org` for the US).
JOOBLE_COUNTRIES = frozenset(
    ["gb", "us", "ca", "au", "nz", "ie", "in", "za", "sg", "de", "at", "ch", "fr", "be", "nl", "es", "it", "pl",
     "se", "pt", "dk", "no", "fi", "cz", "hu", "ro", "gr", "mx", "br", "ae", "tr"]
)  # fmt: skip
# Arbeitnow's jobs come from employers in Germany and its neighbours, and the UK through its .co.uk
# feed (22 Nov); a remote one may be taken from anywhere (`arbeitnow.matches`).
ARBEITNOW_COUNTRIES = frozenset({"de", "nl", "at", "ch", "gb"})

EVERYWHERE = frozenset(c.lower() for c in COUNTRIES)

# Full text first (22 Nov), then the snippet APIs.
FEEDS: dict[str, Feed] = {
    "reed": Feed("reed", "Reed", frozenset({"gb"}), full_text=True),
    "usajobs": Feed("usajobs", "USAJOBS", frozenset({"us"}), full_text=True),
    # SRCH-35: UN agencies, international organisations and NGOs, everywhere.
    "reliefweb": Feed("reliefweb", "ReliefWeb", EVERYWHERE, full_text=True),
    # SRCH-31 (22 Nov): remote jobs open to people in the country asked, or anywhere.
    "himalayas": Feed("himalayas", "Himalayas", EVERYWHERE, full_text=True, remote_only=True),
    # SRCH-31 (22 Nov): no search — the day's latest jobs, matched to the plan here.
    "arbeitnow": Feed("arbeitnow", "Arbeitnow", ARBEITNOW_COUNTRIES, full_text=True),
    # SRCH-31 (22 Nov): US entry-level jobs, asked by category and city.
    "themuse": Feed("themuse", "The Muse", frozenset({"us"}), full_text=True),
    "adzuna": Feed("adzuna", "Adzuna", ADZUNA_COUNTRIES, full_text=False),
    "careerjet": Feed("careerjet", "Careerjet", frozenset(CAREERJET_LOCALES), full_text=False, needs_person=True),
    # SRCH-32 (22 Nov): snippets behind a tracked jooble.org link, never fetched.
    "jooble": Feed("jooble", "Jooble", JOOBLE_COUNTRIES, full_text=False),
}


@dataclass(frozen=True)
class Person:
    """Who is searching, as Careerjet's terms ask: their IP address and browser. Held for an hour."""

    ip: str
    user_agent: str


@dataclass(frozen=True)
class FeedQuery:
    title: str  # as the plan has it: "Graduate Software Engineer"
    role: str  # without its level words: "Software Engineer"
    level: str | None  # "graduate", when the title had one
    city: str | None
    country: str  # lower case, as in the plan's `gl`


@dataclass(frozen=True)
class Lead:
    """One result from an API, before anything is read."""

    url: str
    title: str
    company: str
    location: str
    date: str | None  # ISO, when the API gave one
    snippet: str
    payload: dict[str, Any] | None = None


def config(feed_id: str) -> FeedConfig:
    return wera().feeds.get(feed_id) or FeedConfig(enabled=False)


def missing_key(feed_id: str) -> str | None:
    s = get_settings()

    def unset(value: Any) -> bool:
        raw = value.get_secret_value() if hasattr(value, "get_secret_value") else value
        return not (raw or "").strip()

    needs = {
        "reed": {"REED_API_KEY": s.reed_api_key},
        "usajobs": {"USAJOBS_API_KEY": s.usajobs_api_key, "USAJOBS_EMAIL": s.usajobs_email},
        "adzuna": {"ADZUNA_APP_ID": s.adzuna_app_id, "ADZUNA_APP_KEY": s.adzuna_app_key},
        "careerjet": {"CAREERJET_API_KEY": s.careerjet_api_key},
        "reliefweb": {"RELIEFWEB_APPNAME": s.reliefweb_appname},
        "himalayas": {"HIMALAYAS_API_KEY": s.himalayas_api_key},
        "jooble": {"JOOBLE_API_KEY": s.jooble_api_key},
        # No key at all (Arbeitnow), or an optional one (The Muse's raises its hourly limit).
        "arbeitnow": {},
        "themuse": {},
    }[feed_id]
    names = [name for name, value in needs.items() if unset(value)]
    return ", ".join(names) or None


def skipped(feed_id: str, *, countries: set[str], person: Person | None, profile: "SearchProfile | None" = None) -> str | None:
    """Why this API won't be asked in this search, or None when it will. Without a profile, an
    API is judged on everything but the person's own wishes (the place picker asks that way)."""
    feed, cfg = FEEDS[feed_id], config(feed_id)
    if not cfg.enabled:
        return "turned off in config/wera.yaml"
    if cfg.terms_reviewed_on is None:
        return "its terms aren't reviewed yet (terms_reviewed_on in config/wera.yaml)"
    if not countries & feed.countries:
        return f"it has no jobs in {', '.join(sorted(c.upper() for c in countries)) or 'any country chosen'}"
    if keys := missing_key(feed_id):
        return f"{keys} not set in .env.local"
    if feed.needs_person and person is None:
        return "it needs the person searching, and a scheduled run has none"
    if feed.remote_only and profile is not None and profile.remote == "no_remote":
        return "you said you don't want remote work"
    return None


def queries_for(titles: list[str], places: list[tuple[str | None, str]]) -> list[FeedQuery]:
    """One query per title, per place: a `(city, country)` pair, city None for the whole country.
    Titles keep the plan's order, best first; places the order `places_for` gives them.

    The places used to be the profile's cities only when it had exactly one country, and else
    every country without a city — so a profile of London and Switzerland asked Reed for the
    whole of the UK, and the filter then hid most of what came back (16 Nov)."""
    out: list[FeedQuery] = []
    for title in titles:
        role, level = split_level(title)
        for city, country in places:
            out.append(FeedQuery(title=title, role=role, level=level, city=city, country=country))
    return out


def places_for(where: Any, *, city_scope: str, countries: list[str]) -> list[tuple[str | None, str]]:
    """Each country's named cities (two at most), else the country; with `city_scope: first`, the
    country follows its cities. `where` is `places.where(...)`; `countries` are the plan's `gl`s."""
    out: list[tuple[str | None, str]] = []
    for country in countries:
        cities = [*where.cities.get(country.upper(), []), *where.loose][:2]
        out += [(city, country) for city in cities]
        if not cities or city_scope == "first":
            out.append((None, country))
    return out


def split_level(title: str) -> tuple[str, str | None]:
    lowered = title.lower()
    for word in LEVEL_WORDS:
        if re.search(rf"(^|\W){re.escape(word)}(\W|$)", lowered):
            role = re.sub(rf"(?i)(^|\W){re.escape(word)}(\W|$)", " ", title)
            return " ".join(role.split()) or title, word.replace("-", " ").removesuffix("ship")
    return title, None


def call(
    guard: Guard,
    feed_id: str,
    url: str,
    *,
    params: dict[str, Any],
    key_params: dict[str, Any] | None = None,
    key_url: str | None = None,
    method: str = "GET",
    **kwargs: Any,
) -> tuple[Any | None, str]:
    """One call to an API, through its guard: a shared answer when there is one, else one call for
    everyone asking, counted on the day's meter; a 429 or repeated failure opens the breaker.
    Returns the JSON (or None) and how it was answered. `key_params`, when given, is what the
    answer is shared under instead of `params` — the question without who asked it; `key_url` the
    same for a URL that carries a key in its path (Jooble's). A POST's `json` body is part of the
    question, so it is part of the key."""
    if seconds := guard.open_for():
        return None, f"breaker open {seconds}s"
    key = answer_key(
        feed_id, key_url or url, sorted((key_params if key_params is not None else params).items()), kwargs.get("json")
    )

    def fetch() -> Any | None:
        try:
            response = request(method, url, params=params, timeout=TIMEOUT, **kwargs)
        except httpx.HTTPError as exc:
            guard.failed(type(exc).__name__)
            return None
        guard.count()
        if response.status_code == 429:
            guard.trip("HTTP 429")
            return None
        if response.status_code >= 500:
            guard.failed(f"HTTP {response.status_code}")
            return None
        if response.status_code != 200:
            logfire.warn("{feed} answered HTTP {status}", feed=feed_id, status=response.status_code, body=response.text[:200])
            return None
        try:
            return response.json()
        except json.JSONDecodeError:
            return None

    return guard.shared(key, fetch)


def remember_person(redis: Any, run_id: str, person: Person) -> None:
    """The route that starts a search hands its person to the run, which runs after the request
    has returned. Kept an hour, and only for Careerjet."""
    try:
        redis.set(f"{PERSON_KEY}{run_id}", json.dumps({"ip": person.ip, "user_agent": person.user_agent}), ex=PERSON_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — without it Careerjet is skipped, nothing else
        logfire.warn("Couldn't hand the searcher to the run; Careerjet will be skipped")


def recall_person(redis: Any, run_ids: list[str]) -> Person | None:
    for run_id in run_ids:
        try:
            raw = redis.get(f"{PERSON_KEY}{run_id}")
        except Exception:  # noqa: BLE001
            return None
        if raw:
            data = json.loads(raw)
            return Person(ip=data["ip"], user_agent=data["user_agent"])
    return None
