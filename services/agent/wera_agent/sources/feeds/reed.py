"""Reed's Jobseeker API (UK; SRCH-31): a search, then each queued posting's full text.

Basic auth with the key as the username and no password. The search gives a snippet and a date;
`/jobs/{id}` gives the whole description as HTML, the salary and the closing date. The search has
no date filter, so results older than 21 days are dropped here (SRCH-24) before any is queued, and
the details call is only made for what was queued.
"""

from datetime import datetime
from typing import Any

from ...settings import get_settings
from ..ats_feeds import html_to_text
from ..provider_guard import Guard
from . import FeedQuery, Lead, call

SEARCH = "https://www.reed.co.uk/api/1.0/search"
DETAILS = "https://www.reed.co.uk/api/1.0/jobs/{id}"
PER_SEARCH = 100  # Reed's maximum, and one call
MILES = 15
PERIODS = {"per annum": "year", "per month": "month", "per day": "day", "per hour": "hour"}


def _auth() -> tuple[str, str]:
    key = get_settings().reed_api_key
    return (key.get_secret_value().strip() if key else "", "")


def search(query: FeedQuery, guard: Guard, *, graduate: bool) -> tuple[list[Lead], str]:
    params: dict[str, Any] = {"keywords": query.role if graduate and query.level else query.title, "resultsToTake": PER_SEARCH}
    if query.city:
        params |= {"locationName": query.city, "distanceFromLocation": MILES}
    if graduate:
        params["graduate"] = "true"
    data, how = call(guard, "reed", SEARCH, params=params, auth=_auth())
    rows = data.get("results") if isinstance(data, dict) else None
    leads = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("jobId") or not row.get("jobUrl"):
            continue
        leads.append(
            Lead(
                url=str(row["jobUrl"]),
                title=str(row.get("jobTitle") or "").strip(),
                company=str(row.get("employerName") or "").strip(),
                location=str(row.get("locationName") or "").strip(),
                date=uk_date(row.get("date")),
                snippet=str(row.get("jobDescription") or "").strip()[:300],
                payload={"jobId": int(row["jobId"])},
            )
        )
    return leads, how


def read(payload: dict[str, Any], guard: Guard) -> dict[str, Any] | None:
    """The posting's card, from the details call: what the Harvester checks and saves."""
    data, _how = call(guard, "reed", DETAILS.format(id=int(payload["jobId"])), params={}, auth=_auth())
    if not isinstance(data, dict) or not data.get("jobId"):
        return None  # Reed answers an unknown id with a blank job
    salary = None
    low, high = (
        data.get("yearlyMinimumSalary") or data.get("minimumSalary"),
        data.get("yearlyMaximumSalary") or data.get("maximumSalary"),
    )
    if low or high:
        yearly = bool(data.get("yearlyMinimumSalary") or data.get("yearlyMaximumSalary"))
        period = "year" if yearly else PERIODS.get(str(data.get("salaryType") or "").lower(), "year")
        salary = {"min": low, "max": high, "currency": str(data.get("currency") or "GBP"), "period": period}
    return {
        "title": str(data.get("jobTitle") or "").strip(),
        "company": str(data.get("employerName") or "").strip(),
        "locationRaw": str(data.get("locationName") or "").strip(),
        "description": html_to_text(str(data.get("jobDescription") or "")),
        "postedAt": uk_date(data.get("datePosted")),
        "closesAt": uk_date(data.get("expirationDate")),
        "salary": salary,
        "url": str(data.get("jobUrl") or ""),
    }


def uk_date(value: object) -> str | None:
    """Reed writes dates as 14/10/2026."""
    try:
        return datetime.strptime(str(value).strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None
