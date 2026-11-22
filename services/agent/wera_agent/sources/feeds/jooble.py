"""Jooble's REST API (SRCH-32, 22 Nov): leads only, never read.

`POST https://jooble.org/api/<key>` with a JSON body — `keywords` the title, `location` the city or
the country's name, `page` "1" — answers `jobs`: a title, a company, a location, a `snippet` of the
ad (with Jooble's highlighting in HTML), `updated` and a `link`. **That link is a tracked
jooble.org click and is never requested** (`normalise.is_tracked_link`, `harvest._gate`): like
Adzuna's and Careerjet's, each lead's original posting is looked for with Serper (`resource.py`),
inside the search's one lookup allowance with each ticked country's floor first, and the rest are
previews.

The key is in the URL's path, so the answer is shared under the URL without it: two people asking
the same thing share one answer, and the key is never written into Redis.

**Terms:** the API page (jooble.org/api/about, read 22 Nov) states none for the API: it invites
sites to "take search queries from Jooble and post the results on your website in your own design".
The site's own terms (jooble.org/info/terms) forbid copying its content for republication and any
automatic access to the site; they say nothing of the API, attribution or caching. Read both
before setting `terms_reviewed_on`.
"""

from ...domain import places
from ...settings import get_settings
from ..ats_feeds import html_to_text
from ..provider_guard import Guard
from . import FeedQuery, Lead, call

ENDPOINT = "https://jooble.org/api/"


def search(query: FeedQuery, guard: Guard) -> tuple[list[Lead], str]:
    key = get_settings().jooble_api_key
    secret = key.get_secret_value().strip() if key else ""
    # 22 Nov, live: a bare city is read as anywhere of that name ("London" came back as London, KY),
    # so the place is always written with its country.
    country = places.country_name(query.country)
    body = {"keywords": query.title, "location": f"{query.city}, {country}" if query.city else country, "page": "1"}
    data, how = call(guard, "jooble", f"{ENDPOINT}{secret}", params={}, key_url=ENDPOINT, method="POST", json=body)
    rows = data.get("jobs") if isinstance(data, dict) else None
    leads = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("link"):
            continue
        # 22 Nov, live: the key answers on jooble.org only, a US index that fills a thin country
        # with near names ("Switzerland County, IN" for Switzerland). A lead somewhere else than the
        # country asked is dropped; one whose place can't be told is kept for the filters to judge.
        if _elsewhere(str(row.get("location") or ""), query.country):
            continue
        leads.append(
            Lead(
                url=str(row["link"]),
                title=html_to_text(str(row.get("title") or "")).strip(),
                company=str(row.get("company") or "").strip(),
                location=str(row.get("location") or "").strip(),
                date=str(row.get("updated") or "")[:10] or None,
                snippet=html_to_text(str(row.get("snippet") or "")).strip(),
            )
        )
    return leads, how


def _elsewhere(location: str, country: str) -> bool:
    """Whether a Jooble lead is in another country than the one asked. "London, KY" ends in a US
    state, so it is the US whatever the city; otherwise the place is coded as any posting's is."""
    last = location.rsplit(",", 1)[-1].strip().upper() if "," in location else ""
    found = "US" if last in places.US_STATES else places.country_code(location)
    return bool(found) and found.lower() != country.lower()
