"""Himalayas' jobs API (remote jobs; SRCH-31, 22 Nov): one search, the full posting in the answer.

Every job on Himalayas is remote, open to people in the countries it lists (`locationRestrictions`)
or anywhere. Asked once a role and country: `q` the role, `country` its ISO code, `Entry-level`
when every level the person wants is early-career, newest first, page 1 (20 jobs). Each answer
holds the posting's whole text (`description`, HTML), so no page is ever fetched. Results older
than 21 days are dropped here (SRCH-24), and so is a job already past its `expiryDate`.

**Their terms (clause 113, read 22 Nov), which this file and its callers keep:**

* every Apply uses the job's `applicationLink` unchanged — it is the lead's URL, and so the job's
  `apply_url`, and a same-posting merge never replaces it (`dedupe.merge_sources`);
* a visible link to the job on Himalayas, or to himalayas.app: the job's Himalayas page (`guid`)
  is kept as `viaUrl`, and the card shows "Jobs by Himalayas" linked to it;
* jobs are removed once they expire: `expiryDate` is the job's `closes_at` (LIFE-02), and the free
  re-check (SRCH-13) asks `status` below, never the job's page;
* the key is used from WERA's server only, and nothing is passed on to another board — WERA
  publishes nothing.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

import logfire

from ...clock import iso
from ...domain import places
from ...http import request
from ...settings import get_settings
from ..ats_feeds import html_to_text
from ..provider_guard import Guard
from . import TIMEOUT, FeedQuery, Lead, call

SEARCH = "https://himalayas.app/jobs/api/search"
HOME = "https://himalayas.app"
# The single-job endpoint is the guid's path with `/jobs/api` after the domain: 200 open, 410 closed.
STATUS = "https://himalayas.app/jobs/api{path}"
ENTRY_LEVEL = "Entry-level"
MAX_AGE = timedelta(days=21)
# Himalayas' pay periods, as WERA's. A week's or a fortnight's pay is put as a year's.
PERIODS = {
    "hourly": ("hour", 1),
    "monthly": ("month", 1),
    "annual": ("year", 1),
    "weekly": ("year", 52),
    "fortnightly": ("year", 26),
}
_JOB_PATH = re.compile(r"^/companies/[^/]+/jobs/[^/]+/?$")

type Status = Literal["open", "gone", "unknown"]


def _headers() -> dict[str, str]:
    key = get_settings().himalayas_api_key
    return {"Authorization": f"Bearer {key.get_secret_value().strip() if key else ''}"}


def search(query: FeedQuery, guard: Guard, *, early: bool, now: datetime | None = None) -> tuple[list[Lead], str]:
    """One role in one country. `early`: every level the person wants is early-career."""
    params: dict[str, Any] = {"q": query.role, "country": query.country.upper(), "sort": "recent", "page": 1}
    if early:
        params["seniority"] = ENTRY_LEVEL
    data, how = call(guard, "himalayas", SEARCH, params=params, headers=_headers())
    items = data.get("jobs") if isinstance(data, dict) else None
    at = now or datetime.now(UTC)
    leads = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not _fresh(item, at):
            continue
        card = card_of(item, country=query.country)
        if not card["url"] or not card["title"]:
            continue  # no applicationLink: nothing WERA may send the person to
        leads.append(
            Lead(
                url=card["url"],
                title=card["title"],
                company=card["company"],
                location=card["locationRaw"],
                date=card["postedAt"],
                snippet=(str(item.get("excerpt") or "").strip() or card["description"])[:300],
                payload={"card": card},
            )
        )
    return leads, how


def _fresh(item: dict[str, Any], now: datetime) -> bool:
    posted, expires = _when(item.get("pubDate")), _when(item.get("expiryDate"))
    if posted is not None and posted < now - MAX_AGE:
        return False
    return expires is None or expires >= now


def card_of(item: dict[str, Any], *, country: str | None) -> dict[str, Any]:
    """A job as a card (`harvest._checked` reads it like ReliefWeb's). `country` is the one asked;
    the card's `country` is that one when the job lists it, and None for a job open anywhere."""
    # The OpenAPI spec gives `{alpha2, name, slug}` objects; the live search answered with plain
    # country names ("United Kingdom", 22 Nov). Both are read; a name is coded as any place is.
    names: list[str] = []
    codes: set[str] = set()
    for r in item.get("locationRestrictions") or []:
        name = str((r.get("name") or r.get("alpha2")) if isinstance(r, dict) else r or "").strip()
        code = str(r.get("alpha2") or "").upper() if isinstance(r, dict) else ""
        if name:
            names.append(name)
            codes.add(code or (places.country_code(name) or ""))
    codes.discard("")
    asked = (country or "").upper()
    posted, closes = _when(item.get("pubDate")), _when(item.get("expiryDate"))
    return {
        "title": str(item.get("title") or "").strip(),
        "company": str(item.get("companyName") or "").strip(),
        "locationRaw": f"Remote ({', '.join(names)})" if names else "Remote (worldwide)",
        "remote": "remote",
        "country": asked if asked and asked in codes else None,
        "description": html_to_text(str(item.get("description") or "")),
        "postedAt": iso(posted.timestamp() * 1000) if posted else None,
        "closesAt": closes.date().isoformat() if closes else None,
        "salary": _salary(item),
        "url": str(item.get("applicationLink") or "").strip(),  # unchanged: their terms
        "viaUrl": page_of(item.get("guid")),
    }


def _salary(item: dict[str, Any]) -> dict[str, Any] | None:
    low, high = _number(item.get("minSalary")), _number(item.get("maxSalary"))
    if low is None and high is None:
        return None
    period, times = PERIODS.get(str(item.get("salaryPeriod") or "annual").lower(), ("year", 1))
    return {
        "min": low * times if low is not None else None,
        "max": high * times if high is not None else None,
        "currency": str(item.get("currency") or "USD").upper(),
        "period": period,
    }


def page_of(guid: object) -> str | None:
    """The job's own page on Himalayas, from its `guid` (`https://himalayas.app/companies/<c>/jobs/<slug>`).
    None for a guid that isn't one; the card's label then links to himalayas.app."""
    try:
        parsed = urlparse(str(guid or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ("himalayas.app", "www.himalayas.app") or not _JOB_PATH.match(parsed.path):
        return None
    return f"{HOME}{parsed.path.rstrip('/')}"


def status_url(page: str | None) -> str | None:
    if not page or not page.startswith(f"{HOME}/companies/"):
        return None
    return STATUS.format(path=page.removeprefix(HOME))


def status(page: str | None, guard: Guard) -> Status:
    """SRCH-13 for a Himalayas job: their status endpoint, through the guard — 410 is closed, 200
    is open, anything else is unknown. The job's page is never fetched. Not shared: a re-check
    asks because yesterday's answer may have changed."""
    url = status_url(page)
    if url is None or guard.open_for():
        return "unknown"
    try:
        response = request("GET", url, headers=_headers(), timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — a transport failure says nothing about the job
        guard.failed(type(exc).__name__)
        return "unknown"
    guard.count()
    if response.status_code == 429:
        guard.trip("HTTP 429")
        return "unknown"
    if response.status_code >= 500:
        guard.failed(f"HTTP {response.status_code}")
        return "unknown"
    if response.status_code == 410:
        return "gone"
    if response.status_code == 200:
        return "open"
    logfire.info("Himalayas answered HTTP {status} for a job's status", status=response.status_code)
    return "unknown"


def _when(value: object) -> datetime | None:
    """Himalayas' dates are Unix seconds."""
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=UTC) if seconds > 0 else None


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
