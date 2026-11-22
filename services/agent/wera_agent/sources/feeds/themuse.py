"""The Muse's public jobs API (US, entry level; SRCH-31, 22 Nov): the full posting in the answer.

The Muse has no keyword search: `GET /api/public/jobs` is filtered by `category`, `level` and
`location`, and answers 20 jobs a page with each one's whole text (`contents`, HTML). So it is
asked once a role family (`domain/role_family`) and place: the family's Muse categories,
`level=Entry Level` (and `Internship` when the person wants internships), the ticked US city as
The Muse writes it ("New York, NY"), or "Flexible / Remote" for the whole country, newest first,
pages 1 and 2. A title in no family ("other") isn't asked: there would be nothing to ask it by.
The titles of what comes back are judged by MATCH-01 like any other API's.

The key is optional (`THEMUSE_API_KEY` raises the hourly limit). When set it is sent as `api_key`
and is never part of a shared answer's key: everyone's question is the same question.

**Terms:** their developer page (themuse.com/developers/api/v2) refused WERA's reader on 22 Nov
(HTTP 403), so neither its terms nor any attribution rule was read. Read them before setting
`terms_reviewed_on`.
"""

from typing import Any

from ...domain import places, role_family
from ...settings import get_settings
from ..ats_feeds import html_to_text
from ..provider_guard import Guard
from . import FeedQuery, Lead, call

SEARCH = "https://www.themuse.com/api/public/jobs"
REMOTE = "Flexible / Remote"
ENTRY_LEVEL = "Entry Level"
INTERNSHIP = "Internship"
PAGES = 2
# Role family → The Muse's categories for it (their category names, as their API writes them).
CATEGORIES: dict[str, tuple[str, ...]] = {
    "quantitative": ("Accounting and Finance", "Data and Analytics"),
    "data": ("Data and Analytics", "Data Science"),
    "software": ("Software Engineering", "Computer and IT"),
    "finance": ("Accounting and Finance",),
    "consulting": ("Business Operations", "Product Management", "Project Management"),
    "legal": ("Legal Services",),
    "marketing": ("Advertising and Marketing", "Media, PR, and Communications"),
    "sales": ("Sales", "Account Management"),
    "operations": ("Business Operations", "Human Resources and Recruitment", "Administration and Office"),
    "engineering": ("Science and Engineering",),
    "health": ("Healthcare", "Science and Engineering"),
}
# US cities as The Muse names them. A city not here is asked as the whole country.
CITIES: dict[str, str] = {
    "new york": "New York, NY", "new york city": "New York, NY", "nyc": "New York, NY", "brooklyn": "New York, NY",
    "san francisco": "San Francisco, CA", "los angeles": "Los Angeles, CA", "san diego": "San Diego, CA",
    "san jose": "San Jose, CA", "oakland": "Oakland, CA", "chicago": "Chicago, IL", "boston": "Boston, MA",
    "cambridge, ma": "Cambridge, MA", "seattle": "Seattle, WA", "austin": "Austin, TX", "dallas": "Dallas, TX",
    "houston": "Houston, TX", "washington": "Washington, DC", "washington, dc": "Washington, DC",
    "washington dc": "Washington, DC", "denver": "Denver, CO", "atlanta": "Atlanta, GA",
    "philadelphia": "Philadelphia, PA", "pittsburgh": "Pittsburgh, PA", "miami": "Miami, FL",
    "portland": "Portland, OR", "minneapolis": "Minneapolis, MN", "charlotte": "Charlotte, NC",
    "phoenix": "Phoenix, AZ", "detroit": "Detroit, MI", "nashville": "Nashville, TN", "salt lake city": "Salt Lake City, UT",
}  # fmt: skip


def categories_for(role: str) -> tuple[str, ...]:
    """The Muse's categories for a role's family; none for a role in no family."""
    return CATEGORIES.get(role_family.family_of(role), ())


def location_for(city: str | None, *, remote: str) -> str | None:
    """Where to ask: the city as The Muse writes it, else "Flexible / Remote" — or, for someone who
    doesn't want remote work, nowhere in particular (the whole of the US)."""
    if city:
        text = " ".join(city.lower().split())
        if named := CITIES.get(text) or CITIES.get(text.split(",")[0].strip()):
            return named
    return None if remote == "no_remote" else REMOTE


def search(query: FeedQuery, guard: Guard, *, interns: bool, remote: str) -> tuple[list[Lead], str]:
    """One role's family in one place, two pages. `remote` is the profile's remote wish."""
    cats = categories_for(query.role)
    if not cats:
        return [], "failed"
    params: dict[str, Any] = {
        "category": list(cats),
        "level": [ENTRY_LEVEL, *([INTERNSHIP] if interns else [])],
        "descending": "true",
    }
    if where := location_for(query.city, remote=remote):
        params["location"] = where
    key = get_settings().themuse_api_key
    secret = key.get_secret_value().strip() if key else ""
    leads: list[Lead] = []
    hows: list[str] = []
    for page in range(1, PAGES + 1):
        shared = params | {"page": page}
        data, how = call(guard, "themuse", SEARCH, params=shared | ({"api_key": secret} if secret else {}), key_params=shared)
        hows.append(how)
        items = data.get("results") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            card = card_of(item)
            if card["url"] and card["title"]:
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
        if not isinstance(data, dict) or int(data.get("page_count") or 0) <= page:
            break
    return leads, "fetched" if "fetched" in hows else hows[0]


def card_of(item: dict[str, Any]) -> dict[str, Any]:
    """A job as a card. A "Flexible / Remote" job is remote; its country is the US when a place it
    names is there, else the one other country it names, else none."""
    names = [str(p.get("name") or "").strip() for p in item.get("locations") or [] if isinstance(p, dict)]
    names = [n for n in names if n]
    remote = any(n.casefold() == REMOTE.casefold() or n.casefold() == "remote" for n in names)
    codes = {code for n in names if n.casefold() not in (REMOTE.casefold(), "remote") if (code := places.country_code(n))}
    card: dict[str, Any] = {
        "title": str(item.get("name") or "").strip(),
        "company": str((item.get("company") or {}).get("name") or "").strip(),
        "locationRaw": "; ".join(names) or "Not stated",
        "description": html_to_text(str(item.get("contents") or "")),
        "postedAt": str(item.get("publication_date") or "").strip() or None,
        "url": str((item.get("refs") or {}).get("landing_page") or "").strip(),
        "country": "US" if "US" in codes else (next(iter(codes)) if len(codes) == 1 else None),
    }
    if remote:
        card["remote"] = "remote"
    return card
