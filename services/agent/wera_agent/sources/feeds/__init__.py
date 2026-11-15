"""The job APIs (SRCH-31, SRCH-32): asked before Serper, each through the provider guard.

Four official APIs, in two kinds:

* **Full text** — Reed (UK) and USAJOBS (US federal). A posting is read from the API itself
  (Reed's details call, or USAJOBS' own payload), never by fetching its page, and checked like
  any other (SRCH-23). Queued as `Target(source="feed")`.
* **Snippet only** — Adzuna and Careerjet. They give a title, a company and a few lines, behind a
  tracked link on a pay-per-click network. **That link is never fetched**: it would register as a
  click someone paid for. Instead each lead's original posting is looked for with one Serper
  search (`resource.py`), and what is queued is that page, with `via` saying who found it.

Each API only runs where it has jobs (`countries`), with its key set, and with
`terms_reviewed_on` set in `config/wera.yaml` — the same gate as a board in sites.yaml. Careerjet
also needs the person searching: its terms require their IP address and browser on every call
(`Person`), so a scheduled run, which has no person, skips it and says so.

The queries come from the plan, never the CV: the titles the Strategist would search for, at the
person's level and in their city. No CV text is ever sent to any of them.
"""

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
import logfire

from ...config import FeedConfig, wera
from ...http import request
from ...settings import get_settings
from ..provider_guard import Guard, answer_key

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


# Adzuna's countries, from its API; Careerjet's are the locales WERA maps a country to.
ADZUNA_COUNTRIES = frozenset(
    ["gb", "us", "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "in", "it", "mx", "nl", "nz", "pl", "sg", "za"]
)
CAREERJET_LOCALES = {
    "gb": "en_GB", "us": "en_US", "ie": "en_IE", "ca": "en_CA", "au": "en_AU", "nz": "en_NZ", "in": "en_IN",
    "za": "en_ZA", "sg": "en_SG", "de": "de_DE", "at": "de_AT", "ch": "de_CH", "fr": "fr_FR", "be": "fr_BE",
    "es": "es_ES", "it": "it_IT", "nl": "nl_NL", "pl": "pl_PL", "se": "sv_SE", "pt": "pt_PT",
}  # fmt: skip

FEEDS: dict[str, Feed] = {
    "reed": Feed("reed", "Reed", frozenset({"gb"}), full_text=True),
    "usajobs": Feed("usajobs", "USAJOBS", frozenset({"us"}), full_text=True),
    "adzuna": Feed("adzuna", "Adzuna", ADZUNA_COUNTRIES, full_text=False),
    "careerjet": Feed("careerjet", "Careerjet", frozenset(CAREERJET_LOCALES), full_text=False, needs_person=True),
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
    }[feed_id]
    names = [name for name, value in needs.items() if unset(value)]
    return ", ".join(names) or None


def skipped(feed_id: str, *, countries: set[str], person: Person | None) -> str | None:
    """Why this API won't be asked in this search, or None when it will."""
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
    return None


def queries_for(titles: list[str], cities: list[str], countries: list[str]) -> list[FeedQuery]:
    """One query per title, per city when there is exactly one country (a city belongs to it),
    else per country. Titles keep the plan's order, best first."""
    out: list[FeedQuery] = []
    places: list[tuple[str | None, str]] = [(c, countries[0]) for c in cities] if len(countries) == 1 and cities else []
    places = places or [(None, c) for c in countries]
    for title in titles:
        role, level = split_level(title)
        for city, country in places:
            out.append(FeedQuery(title=title, role=role, level=level, city=city, country=country))
    return out


def split_level(title: str) -> tuple[str, str | None]:
    lowered = title.lower()
    for word in LEVEL_WORDS:
        if re.search(rf"(^|\W){re.escape(word)}(\W|$)", lowered):
            role = re.sub(rf"(?i)(^|\W){re.escape(word)}(\W|$)", " ", title)
            return " ".join(role.split()) or title, word.replace("-", " ").removesuffix("ship")
    return title, None


def call(
    guard: Guard, feed_id: str, url: str, *, params: dict[str, Any], key_params: dict[str, Any] | None = None, **kwargs: Any
) -> tuple[Any | None, str]:
    """One GET to an API, through its guard: a shared answer when there is one, else one call for
    everyone asking, counted on the day's meter; a 429 or repeated failure opens the breaker.
    Returns the JSON (or None) and how it was answered. `key_params`, when given, is what the
    answer is shared under instead of `params` — the question without who asked it."""
    if seconds := guard.open_for():
        return None, f"breaker open {seconds}s"
    key = answer_key(feed_id, url, sorted((key_params if key_params is not None else params).items()))

    def fetch() -> Any | None:
        try:
            response = request("GET", url, params=params, timeout=TIMEOUT, **kwargs)
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
