"""Adzuna's search API (SRCH-32): leads only, never read.

Adzuna gives a title, a company, a location, a date and about 500 characters of the ad, behind a
tracked `redirect_url`. WERA never requests that link (it is a click Adzuna's advertisers pay
for); `resource.py` looks for the employer's own posting instead. Every job found this way shows
Adzuna's linked label on its card, as Adzuna's terms ask.

The query that works (measured 13 Nov, London, 21 days): the level word in `title_only` and the
role in `what`. The whole title in `what` ("graduate software engineer") found nothing in
London; the role with `title_only=graduate` found six graduate programmes.
"""

from typing import Any

from ...settings import get_settings
from ..provider_guard import Guard
from . import FeedQuery, Lead, call

SEARCH = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
PER_SEARCH = 50


def search(query: FeedQuery, guard: Guard) -> tuple[list[Lead], str]:
    s = get_settings()
    params: dict[str, Any] = {
        "app_id": (s.adzuna_app_id or "").strip(),
        "app_key": s.adzuna_app_key.get_secret_value().strip() if s.adzuna_app_key else "",
        "results_per_page": PER_SEARCH,
        "max_days_old": 21,
        "sort_by": "date",
        "what": query.role,
        "content-type": "application/json",
    }
    if query.level:
        params["title_only"] = query.level
    if query.city:
        params["where"] = query.city
    data, how = call(guard, "adzuna", SEARCH.format(country=query.country), params=params)
    rows = data.get("results") if isinstance(data, dict) else None
    leads = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("redirect_url"):
            continue
        leads.append(
            Lead(
                url=str(row["redirect_url"]),
                title=str(row.get("title") or "").strip(),
                company=str((row.get("company") or {}).get("display_name") or "").strip(),
                location=str((row.get("location") or {}).get("display_name") or "").strip(),
                date=str(row.get("created") or "")[:10] or None,
                snippet=str(row.get("description") or "").strip(),
            )
        )
    return leads, how
