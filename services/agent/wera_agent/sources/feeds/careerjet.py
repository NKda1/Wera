"""Careerjet's v4 search API (SRCH-32): leads only, never read, and only for a person searching.

Basic auth with the key as the username. Its terms require `user_ip` and `user_agent` — the
person whose action triggered the call — on every request (403 "Missing param user_ip or
user_agent" without them), so a scheduled run skips Careerjet. The key itself only works from
the IP addresses registered on the publisher account: from anywhere else Careerjet answers
"Unauthorized access from IP …" (seen 13 Nov). And it wants a `Referer` ("Undeclared referrer"
without one): the site the search is made from, `CAREERJET_REFERER`, WERA's own web app here.

Like Adzuna it gives an excerpt and a tracked link (`jobviewtrack.com`), which is never fetched;
`resource.py` looks for the original posting.
"""

from email.utils import parsedate_to_datetime
from typing import Any

from ...settings import get_settings
from ..provider_guard import Guard
from . import CAREERJET_LOCALES, FeedQuery, Lead, Person, call

SEARCH = "https://search.api.careerjet.net/v4/query"
PER_SEARCH = 50


def search(query: FeedQuery, guard: Guard, *, person: Person) -> tuple[list[Lead], str]:
    key = get_settings().careerjet_api_key
    params: dict[str, Any] = {
        "locale_code": CAREERJET_LOCALES[query.country],
        "keywords": query.title,
        "page_size": PER_SEARCH,
        "sort": "date",
        "fragment_size": 300,
        "user_ip": person.ip,
        "user_agent": person.user_agent,
    }
    if query.city:
        params["location"] = query.city
    # The person's IP is part of the request, not of the question: two people asking the same
    # thing share one answer (`call` keys on the params, so it is left out of the key).
    shared = {k: v for k, v in params.items() if k not in ("user_ip", "user_agent")}
    data, how = call(
        guard,
        "careerjet",
        SEARCH,
        params=params,
        key_params=shared,
        auth=(key.get_secret_value().strip() if key else "", ""),
        headers={"Referer": get_settings().careerjet_referer},
    )
    rows = data.get("jobs") if isinstance(data, dict) and data.get("type") == "JOBS" else None
    leads = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("url"):
            continue
        leads.append(
            Lead(
                url=str(row["url"]),
                title=str(row.get("title") or "").strip(),
                company=str(row.get("company") or "").strip(),
                location=str(row.get("locations") or "").strip(),
                date=_date(row.get("date")),
                snippet=str(row.get("description") or "").strip(),
            )
        )
    return leads, how


def _date(value: object) -> str | None:
    """'Wed, 15 Nov 2025 19:13:43 GMT', as Careerjet writes it."""
    try:
        return parsedate_to_datetime(str(value)).date().isoformat()
    except (TypeError, ValueError):
        return None
