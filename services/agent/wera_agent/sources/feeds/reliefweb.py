"""ReliefWeb's Jobs API (UN OCHA; SRCH-35): one call, the full posting in the answer.

ReliefWeb carries vacancies from UN agencies (UNICEF, UNHCR, WFP, IOM, UNDP…), other international
organisations and NGOs, with each posting's whole text (`body`). Every call names an approved
`appname` — ReliefWeb refuses any other with a 403 — requested once from apidoc.reliefweb.int and
set as `RELIEFWEB_APPNAME`.

Asked once a title (not once a title and city, like Reed: its search is the whole world), for
postings created in the last 21 days (SRCH-24), at 0–2 years of experience when every level the
person wants is early-career. Where: the profile's countries; and, with "International
organisations, any duty station" on, also any posting from an international organisation, which
then carries `international` so the location rule can tell it apart (SRCH-34).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from ...domain import grades, places
from ...settings import get_settings
from ..provider_guard import Guard
from . import FeedQuery, Lead, call

SEARCH = "https://api.reliefweb.int/v2/jobs"
PER_SEARCH = 30
# ReliefWeb's own name for a source that is an international organisation, not an NGO or government.
INTERNATIONAL = "International Organization"
FIELDS = [
    "title", "body", "url", "url_alias", "date.created", "date.closing", "source.name", "source.shortname",
    "source.type.name", "country.name", "country.iso3", "city.name", "experience.name", "career_categories.name",
]  # fmt: skip


def search(query: FeedQuery, guard: Guard, *, countries: list[str], international: bool, early: bool) -> tuple[list[Lead], str]:
    """One title, everywhere the person looks. `countries` are ISO alpha-2, upper or lower case."""
    since = (datetime.now(UTC) - timedelta(days=21)).strftime("%Y-%m-%dT00:00:00+00:00")
    conditions: list[dict[str, Any]] = [{"field": "date.created", "value": {"from": since}}]
    if early:
        conditions.append({"field": "experience.name", "value": "0-2 years"})
    iso3 = sorted({places.ISO3[c.upper()] for c in countries if c.upper() in places.ISO3})
    where: list[dict[str, Any]] = [{"field": "country.iso3", "value": iso3, "operator": "OR"}] if iso3 else []
    if international:
        where.append({"field": "source.type.name", "value": INTERNATIONAL})
    if where:
        conditions.append(where[0] if len(where) == 1 else {"operator": "OR", "conditions": where})
    body = {
        "preset": "latest",
        "profile": "full",
        "query": {"value": query.role},
        "filter": {"operator": "AND", "conditions": conditions},
        "fields": {"include": FIELDS},
        "limit": PER_SEARCH,
    }
    data, how = call(guard, "reliefweb", SEARCH, method="POST", params={"appname": _appname()}, json=body)
    items = (data or {}).get("data") if isinstance(data, dict) else None
    leads = []
    for item in items if isinstance(items, list) else []:
        fields = item.get("fields") if isinstance(item, dict) else None
        if not isinstance(fields, dict):
            continue
        card = card_of(fields)
        if not card["url"] or not card["title"]:
            continue
        leads.append(
            Lead(
                url=card["url"],
                title=card["title"],
                company=card["company"],
                location=card["locationRaw"],
                date=card["postedAt"],
                snippet=card["description"][:300],
                payload={"card": card},
            )
        )
    return leads, how


def card_of(fields: dict[str, Any]) -> dict[str, Any]:
    """A posting as a card (`harvest._checked` reads it like USAJOBS'), with `international` when
    its source is an international organisation."""
    sources = fields.get("source") or []
    source = sources[0] if sources and isinstance(sources[0], dict) else {}
    cities = [c.get("name") for c in fields.get("city") or [] if isinstance(c, dict) and c.get("name")]
    countries = [c.get("name") for c in fields.get("country") or [] if isinstance(c, dict) and c.get("name")]
    where = ", ".join([*cities[:1], *countries[:1]])
    title = str(fields.get("title") or "").strip()
    body = str(fields.get("body") or "").strip()
    dates = fields.get("date") or {}
    card: dict[str, Any] = {
        "title": title,
        "company": str(source.get("name") or source.get("shortname") or "").strip(),
        "locationRaw": where or "Not stated",
        "description": body,
        "postedAt": str(dates.get("created") or "")[:10] or None,
        "closesAt": str(dates.get("closing") or "")[:10] or None,
        "url": str(fields.get("url_alias") or fields.get("url") or ""),
    }
    if ((source.get("type") or {}).get("name") or "") == INTERNATIONAL:
        grade = grades.parse(title) or grades.parse(body[:600])
        family = next((c.get("name") for c in fields.get("career_categories") or [] if isinstance(c, dict)), None)
        card["international"] = {
            "org": f"reliefweb:{(source.get('shortname') or source.get('name') or '').strip().lower()}",
            "grade": grade.code if grade else None,
            "recruitment": grades.recruitment(title, grade),
            "family": family,
        }
    return card


def _appname() -> str:
    return (get_settings().reliefweb_appname or "").strip()
