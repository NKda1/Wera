"""USAJOBS' Search API (US federal jobs; SRCH-31): one call, the full posting in the answer.

USAJOBS requires three headers: `Host`, the key as `Authorization-Key`, and the email the key
was registered with as `User-Agent`. `Fields=Full` puts each posting's summary, duties and
requirements in the search answer, so no posting page is ever fetched; `DatePosted=21` is
SRCH-24's cut. `HiringPath=graduates;student` (the Pathways programmes) when the person is
looking at intern or graduate level.
"""

from typing import Any

from ...settings import get_settings
from ..provider_guard import Guard
from . import FeedQuery, Lead, call

SEARCH = "https://data.usajobs.gov/api/Search"
PER_SEARCH = 50
PERIODS = {"PA": "year", "PM": "month", "PD": "day", "PH": "hour"}


def _headers() -> dict[str, str]:
    s = get_settings()
    key = s.usajobs_api_key.get_secret_value().strip() if s.usajobs_api_key else ""
    return {"Host": "data.usajobs.gov", "User-Agent": (s.usajobs_email or "").strip(), "Authorization-Key": key}


def search(query: FeedQuery, guard: Guard, *, pathways: bool) -> tuple[list[Lead], str]:
    params: dict[str, Any] = {"Keyword": query.role, "DatePosted": 21, "ResultsPerPage": PER_SEARCH, "Fields": "Full"}
    if query.city:
        params["LocationName"] = query.city
    if pathways:
        params["HiringPath"] = "graduates;student"
    data, how = call(guard, "usajobs", SEARCH, params=params, headers=_headers())
    items = ((data or {}).get("SearchResult") or {}).get("SearchResultItems") if isinstance(data, dict) else None
    leads = []
    for item in items if isinstance(items, list) else []:
        job = item.get("MatchedObjectDescriptor") if isinstance(item, dict) else None
        if not isinstance(job, dict) or not job.get("PositionURI"):
            continue
        card = card_of(job)
        leads.append(
            Lead(
                url=str(job["PositionURI"]),
                title=card["title"],
                company=card["company"],
                location=card["locationRaw"],
                date=card["postedAt"],
                snippet=card["description"][:300],
                payload={"card": card},
            )
        )
    return leads, how


def card_of(job: dict[str, Any]) -> dict[str, Any]:
    details = ((job.get("UserArea") or {}).get("Details") or {}) if isinstance(job.get("UserArea"), dict) else {}
    parts = [
        ("Summary", details.get("JobSummary")),
        ("Duties", "\n".join(f"- {d}" for d in details.get("MajorDuties") or [] if d)),
        ("Qualifications", job.get("QualificationSummary")),
        ("Requirements", details.get("Requirements")),
        ("Education", details.get("Education")),
        ("How you will be evaluated", details.get("Evaluations")),
    ]
    description = "\n\n".join(f"## {head}\n{str(body).strip()}" for head, body in parts if body and str(body).strip())
    pay = (job.get("PositionRemuneration") or [{}])[0] or {}
    salary = None
    if pay.get("MinimumRange") or pay.get("MaximumRange"):
        salary = {
            "min": _number(pay.get("MinimumRange")),
            "max": _number(pay.get("MaximumRange")),
            "currency": "USD",
            "period": PERIODS.get(str(pay.get("RateIntervalCode") or "PA"), "year"),
        }
    return {
        "title": str(job.get("PositionTitle") or "").strip(),
        "company": str(job.get("OrganizationName") or job.get("DepartmentName") or "").strip(),
        "locationRaw": str(job.get("PositionLocationDisplay") or "").strip(),
        "description": description,
        "postedAt": str(job.get("PublicationStartDate") or "")[:10] or None,
        "closesAt": str(job.get("ApplicationCloseDate") or "")[:10] or None,
        "salary": salary,
        "url": str(job.get("PositionURI") or ""),
    }


def _number(value: object) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None
