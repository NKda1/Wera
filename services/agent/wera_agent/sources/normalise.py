"""Whatever a source gave us, turned into one `Job` (NORM-03, NORM-04).

An ATS feed already carries clean fields, so those are used as they are. A scraped page gives a
best-effort extraction, and a card from an email gives little more than a title — so this module
fills what it can from code (salary, seniority, language, remote mode) and leaves the rest
honestly empty rather than guessing.

A card nothing could resolve still becomes a job, with `descriptionStatus: "missing"` and no
description (NORM-03). That is deliberate: the person can still see it, open it themselves and
decide. Silently dropping a job they might have wanted is the worse failure.
"""

import hashlib
import html
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..clock import iso, parse_ms
from ..domain import dedupe, places
from ..domain.text import display_case, is_linkedin_url
from ..schemas.jobs import Ats, FoundOn, Job, JobSource, Location, PostedPrecision, Salary
from ..sources.ats_feeds import FeedJob

if TYPE_CHECKING:
    from .feeds import Lead

# Enough to tell a European job ad from an English one without a language model.
NON_ENGLISH = {
    "de": ("und", "wir suchen", "aufgaben", "kenntnisse", "mitarbeiter"),
    "fr": ("et", "nous recherchons", "vous serez", "compétences", "poste"),
    "nl": ("en", "wij zoeken", "ervaring", "jij", "functie"),
    "es": ("y", "buscamos", "experiencia", "puesto", "requisitos"),
}

SENIORITY = (
    ("intern", ("intern", "internship", "placement", "industrial year")),
    ("graduate", ("graduate", "new grad", "grad scheme", "grad programme", "entry-level graduate")),
    ("junior", ("junior", "jr ")),
    ("entry", ("entry level", "entry-level", "no experience")),
    ("mid", ("mid-level", "mid level", "intermediate")),
)

CURRENCIES = {"£": "GBP", "€": "EUR", "$": "USD", "chf": "CHF"}

# One number pattern, used for both ends of a range: digits with any thousands separators and an
# optional "k". `_amount` works out what the separators meant.
_NUMBER = r"\d[\d,.]*\s?k?"
# Every dash a job ad uses between the two ends of a range: hyphen, en dash, em dash and the
# real minus sign. Greenhouse writes "$200,000—$265,000" with an em dash and no spaces, and
# missing it turns a range into a single number — which reads as the whole story.
_RANGE = r"(?:[-–—−]+|to)"
MONEY = re.compile(
    rf"(?P<sym>[£€$]|chf)\s?(?P<low>{_NUMBER})(?:\s*{_RANGE}\s*(?:[£€$]|chf)?\s?(?P<high>{_NUMBER}))?",
    re.I,
)
PERIOD = (
    ("year", ("per annum", "p.a.", "pa", "annum", "year", "yearly")),
    ("day", ("per day", "day rate", "daily")),
    ("hour", ("per hour", "hourly", "/hr", "an hour")),
    ("month", ("per month", "monthly", "pcm")),
)


def _amount(raw: str) -> float | None:
    """ "45k", "30,000" and "55.000" are all salaries; "35.5" is a number.

    A dot or comma is a thousands separator when it groups three digits — which is how both
    "£30,000" and "€55.000" are written — and a decimal point otherwise.
    """
    value = raw.strip().lower().replace(" ", "")
    multiplier = 1000 if value.endswith("k") else 1
    value = value.removesuffix("k").rstrip(".,")
    grouped = bool(re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value))
    value = re.sub(r"[.,]", "", value) if grouped else value.replace(",", "")
    try:
        return float(value) * multiplier
    except ValueError:
        return None


def parse_salary(text: str | None) -> Salary | None:
    """A stated range, or nothing. An invented salary is worse than a missing one."""
    if not text:
        return None
    match = MONEY.search(text)
    if not match:
        return None
    low = _amount(match.group("low") or "")
    high = _amount(match.group("high") or "") if match.group("high") else None
    if low is None:
        return None
    if high is not None and high < low:
        low, high = high, low
    symbol = (match.group("sym") or "").lower()
    period = next((name for name, words in PERIOD if any(w in text.lower() for w in words)), "year")
    # A "salary" of 30 a year is a page number, not a wage.
    if period == "year" and low < 1000:
        return None
    return Salary(min=low, max=high, currency=CURRENCIES.get(symbol, "GBP"), period=period)


def detect_language(text: str | None) -> str:
    """Which language the ad is written in, well enough to skip one WERA can't read."""
    body = (text or "").lower()
    if len(body) < 80:
        return "en"
    scores = {code: sum(1 for w in words if f" {w} " in body) for code, words in NON_ENGLISH.items()}
    best = max(scores, key=lambda c: scores[c]) if scores else "en"
    return best if scores.get(best, 0) >= 3 else "en"


_RELATIVE = re.compile(r"(\d+)\+?\s*(minute|min|hour|hr|day|week|month)s?\s*ago", re.I)
_RELATIVE_UNITS = {
    "minute": 60_000,
    "min": 60_000,
    "hour": 3_600_000,
    "hr": 3_600_000,
    "day": 86_400_000,
    "week": 604_800_000,
    "month": 2_592_000_000,
}
_TODAY = ("just posted", "posted today", "today", "just now", "new")


def parse_posted(raw: str | None, now: int) -> str | None:
    """A posted date WERA can do arithmetic on, or nothing.

    Job boards write "2 Days Ago", not a timestamp. Stored as-is, the first attempt to score the
    job's freshness raises and takes the whole run with it — so anything that isn't a date WERA
    can read becomes no date at all, which the scoring already handles.
    """
    return dated(raw, now)[0]


def dated(raw: str | None, now: int) -> tuple[str | None, PostedPrecision]:
    """The date, and how much it is worth.

    A relative phrase resolved against "now" produces an ISO timestamp indistinguishable from a
    real one, so the distinction has to be carried out of here or it is lost for good. It
    matters most where the number is used to *group* rather than to weight: "2 days ago" read
    on a page cached yesterday can land a job in the wrong bucket, and a header that says
    "Posted today" should mean it.
    """
    if not raw:
        return None, "unknown"
    text = raw.strip()
    try:
        at = parse_ms(text)
    except (ValueError, TypeError):
        pass
    else:
        # Nothing is posted in the future. A date more than a day ahead is some other date read
        # as the posting's: three Gradcracker reads stored the application deadline (12 Nov
        # review). A day's slack is for time zones and clocks, not for a different date.
        if at > now + _RELATIVE_UNITS["day"]:
            return None, "unknown"
        # A real timestamp in the source. Whether it is *authoritative* depends on who said it,
        # which is the caller's business — `from_feed` upgrades this to `exact`.
        return text, "approximate"

    found = _RELATIVE.search(text)
    if found:
        unit = _RELATIVE_UNITS[found.group(2).lower()]
        return iso(now - int(found.group(1)) * unit), "approximate"
    lowered = text.lower()
    if any(word == lowered or lowered.startswith(word) for word in _TODAY):
        return iso(now), "approximate"
    if "yesterday" in lowered:
        return iso(now - _RELATIVE_UNITS["day"]), "approximate"
    return None, "unknown"


def infer_seniority(title: str, description: str | None = None) -> str | None:
    """The level the ad is pitched at, from its own words.

    Whole words only. As a substring, "intern" is inside "internal", "international" and
    "internet", so an engineering role whose description mentioned internal teams was being
    filed as an internship — and the hard filters act on this field.
    """
    haystack = f" {title.lower()} {(description or '')[:400].lower()} "
    for level, words in SENIORITY:
        if any(re.search(rf"\b{re.escape(w.strip())}\b", haystack) for w in words):
            return level
    return None


def infer_remote(*hints: Any) -> str | None:
    blob = " ".join(str(h) for h in hints if h).lower()
    if "fully remote" in blob or "remote-first" in blob or "remote" in blob:
        return "remote"
    if "hybrid" in blob:
        return "hybrid"
    return "onsite" if "on-site" in blob or "onsite" in blob or "in office" in blob else None


def job_id(key: str) -> str:
    """Stable and readable: the same posting always gets the same id."""
    return f"job-{hashlib.sha256(key.encode()).hexdigest()[:10]}"


def _located(location: Location) -> Location:
    """The location as it is saved: an ISO country code, and the city in one spelling.

    The city's case is the board's own (haystackapp.io writes "london, england, GB"), so without
    this the Jobs screen offered "London" and "london" as two places (19 Nov). `dedupe_key` is
    taken from the city as the source wrote it, before this, so no saved job's key changes.
    """
    country = places.country_code(location.raw, location.country)
    return location.model_copy(update={"country": country, "city": city_of(location.city, country)})


# Words a source puts where the city goes that name no place (20 Nov): "Not stated", "Hybrid",
# "Remote". The Jobs screen's Where filter offered each as a city.
_NO_PLACE = re.compile(
    r"\b(not stated|not specified|unspecified|hybrid|remote|remotely|flexible|flexible working|anywhere|"
    r"multiple locations|various locations|various|several locations|location flexible|nationwide|"
    r"in office|office based|on-?site|onsite|work from home|wfh|tbc|tbd|n/a)\b",
    re.I,
)


def city_of(city: str | None, country: str | None = None) -> str | None:
    """The city as it is saved: one spelling (`places.city_name`), or None when the words name no
    one place — a working pattern ("Hybrid", "Remote"), "Not stated", or a choice of two ("London
    or Manchester"), which is no single city to file the job under (20 Nov). The words themselves
    stay in `location.raw`."""
    text = " ".join((city or "").split())
    if not _NO_PLACE.sub(" ", text).strip(" ,;/-–·|()[]") or re.search(r"\s+or\s+", text, re.I):
        return None
    return places.city_name(text, country)


def _job(
    *,
    title: str,
    company: str,
    location: Location,
    remote: str | None,
    description: str | None,
    status: str,
    apply_url: str | None,
    ats: Ats | None,
    source: JobSource,
    posted_at: str | None,
    now: int,
    linkedin_url: str | None = None,
    posted_precision: PostedPrecision | None = None,
    closes_at: str | None = None,
) -> Job:
    # An agency's repost that names its employer is the employer's job, posted by the agency
    # (20 Nov): "Investment Banking Analyst, M&A - Citi" from eFinancialCareers is Citi's
    # "Investment Banking Analyst, M&A", and the card says who reposted it.
    title, company = html.unescape(title).strip(), html.unescape(company).strip()
    posted_by = None
    if (employer := dedupe.employer_named(company, title)) is not None:
        title, posted_by, company = dedupe.bare_title(company, title), company, employer
    else:
        # A title that ends in the poster's own name ("… Investment Analyst - eFinancialCareers") is
        # the title without it (20 Nov); `bare_title` changes nothing else.
        title = dedupe.bare_title(company, title)
    key = dedupe.dedupe_key(ats=ats, apply_url=apply_url, company=company, title=title, city=location.city)
    # Every builder funnels through here, so every posted date is one WERA can read — and every
    # one carries where it came from. A caller that knows better (`from_feed`) says so.
    when, precision = dated(posted_at, now)
    return Job(
        id=job_id(key),
        dedupe_key=key,
        # A board's HTML entities as the characters they stand for: Reed sent "Wells &amp; Co." (20 Nov).
        title=title,
        # "NATIONAL BANK OF CANADA" as "National Bank of Canada"; "AECOM" and "CITI" as they are
        # (20 Nov). `dedupe_key` above is taken from the company as written, and lower-cases it.
        company=display_case(company, brand=True),
        posted_by=posted_by,
        # The country as an ISO code, whatever the source wrote, so jobs can be counted by country.
        location=_located(location),
        remote=remote,
        salary=parse_salary(description),
        description_md=description or None,
        description_status=status,
        apply_url=apply_url or None,
        ats=ats,
        linkedin_url=linkedin_url,
        sources=[source],
        language=detect_language(description),
        posted_at=when,
        posted_precision="unknown" if when is None else (posted_precision or precision),
        first_seen=iso(now),
        last_seen=iso(now),
        status="new",
        seniority=infer_seniority(title, description),
        closes_at=closes_at,
    )


def from_feed(feed: FeedJob, now: int) -> Job:
    """A watchlist job: the employer's own fields, so nothing needs inferring but the extras."""
    job = _job(
        title=feed.title,
        company=feed.company,
        location=feed.location,
        remote=feed.remote or infer_remote(feed.location.raw, feed.description[:400]),
        description=feed.description,
        status="full" if len(feed.description) > 200 else "partial",
        apply_url=feed.apply_url,
        ats=feed.ats,
        source=JobSource(channel="watchlist", detail=feed.ats.board_token, seen_at=iso(now)),
        posted_at=feed.posted_at,
        # ATS-01: the employer's own feed, giving its own timestamp. This is the only channel
        # whose date is worth grouping on without a caveat.
        posted_precision="exact",
        now=now,
        closes_at=feed.closes_at,
    )
    job.international = feed.international
    return job


def closing_date(raw: object, now: int) -> str | None:
    """LIFE-02: an application deadline as an ISO date, or None. Future dates are the point here,
    so unlike `dated` nothing ahead of now is dropped — only what isn't a date at all."""
    text = str(raw or "").strip()
    if not text:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]  # as written: through `parse_ms` a bare date is local midnight, a day early in UTC
    try:
        return iso(parse_ms(text))[:10]
    except (ValueError, TypeError):
        return None


def from_scrape(card: dict[str, Any], *, url: str, site: str, markdown: str | None, now: int) -> Job | None:
    """A board page WERA scraped. Without a title and a company there is no job here."""
    title = str(card.get("title") or "").strip()
    company = str(card.get("company") or "").strip()
    if not title or not company:
        return None
    where = str(card.get("locationRaw") or card.get("location") or "").strip()
    return _job(
        title=title,
        company=company,
        location=Location(city=where.split(",")[0].strip() or None, raw=where or "Not stated"),
        remote=infer_remote(card.get("remote"), where, (markdown or "")[:400]),
        description=markdown,
        status="full" if len(markdown or "") > 200 else "partial",
        apply_url=url,
        ats=None,
        source=JobSource(channel="board_search", detail=site, seen_at=iso(now)),
        posted_at=card.get("postedAt"),
        now=now,
        closes_at=_closes(card, now),
    )


def _closes(card: dict[str, Any], now: int) -> str | None:
    """The card's closing date — or, when it has none, a "posted" date in the future: no job is
    posted ahead of time, so that is the page's deadline read into the wrong field (12 Nov)."""
    if when := closing_date(card.get("closesAt"), now):
        return when
    posted = closing_date(card.get("postedAt"), now)
    return posted if posted and parse_ms(posted) > now + _RELATIVE_UNITS["day"] else None


def from_pasted(text: str, *, title: str, company: str, location_raw: str | None, now: int) -> Job:
    """ATS-04's paste: the description is the one thing WERA certainly has.

    Nothing is invented around it — a paste with no location is "Not stated", because a job the
    person pasted from a site WERA can't read shouldn't acquire a city it never mentioned.
    """
    where = location_raw or ""
    return _job(
        title=title,
        company=company,
        location=Location(city=where.split(",")[0].strip() or None, raw=where or "Not stated"),
        remote=infer_remote(where, text[:400]),
        description=text,
        status="full" if len(text) > 200 else "partial",
        apply_url=None,
        ats=None,
        source=JobSource(channel="manual", detail="pasted-text", seen_at=iso(now)),
        posted_at=None,
        now=now,
    )


def from_card(
    *, title: str, company: str, location_raw: str, url: str | None, detail: str, now: int, channel: str = "email_alert"
) -> Job:
    """A card from an alert email, with no description yet (NORM-03).

    A LinkedIn link is kept so the person can open it, and is never put anywhere WERA fetches
    from (MAIL-07).
    """
    linkedin = url if url and is_linkedin_url(url) else None
    return _job(
        title=title,
        company=company,
        location=Location(city=location_raw.split(",")[0].strip() or None, raw=location_raw or "Not stated"),
        remote=infer_remote(location_raw),
        description=None,
        status="missing",
        apply_url=None if linkedin else url,
        ats=None,
        source=JobSource(channel=channel, detail=detail, seen_at=iso(now)),
        posted_at=None,
        now=now,
        linkedin_url=linkedin,
    )


# The job APIs' tracked links (SRCH-32): Adzuna's `redirect_url` on any of its country sites, and
# Careerjet's `jobviewtrack.com`. A click there is one an advertiser pays for, so WERA stores the
# link for the person to open and never requests it — not to read it, not to check it is still up.
_TRACKED = re.compile(r"(?:^|\.)(?:adzuna\.[a-z.]+|careerjet\.[a-z.]+|jobviewtrack\.com|jooble\.[a-z.]+)$")


def is_tracked_link(url: str | None) -> bool:
    """Whether `url` is a job API's tracked click, which WERA must never fetch (21 Nov)."""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and bool(_TRACKED.search(host))


def from_lead(
    lead: "Lead",
    feed_id: str | None,
    query_country: str | None,
    now: int,
    *,
    match: float | None = None,
    found_on: FoundOn | None = None,
) -> Job:
    """A job API's lead that wasn't read this search, kept as a **preview** (21 Nov, SRCH-26).

    Adzuna gives about 500 characters of the ad and Careerjet about 270, behind a tracked link. A
    search has lookups for its best leads' originals (SRCH-32), and on 20 Nov 228 that passed every
    filter went unread; they are kept here instead, ranked by `match` (`prefilter.lead_ranker`) and
    read in full on demand (`Store.read_preview`).

    What is known is kept and nothing more: the summary is the description, `partial` — so a score
    on it alone is `rough_only`, capped like a card with no description (MATCH-04) — and the
    tracked link is the apply URL, **stored for the person to open and never fetched**
    (`is_tracked_link`). The country is the one the API was asked for, since its place names
    ("Canary Wharf, East London") don't always carry one. Built through `_job` like every other
    source, so the employer's name, the display case and `dedupe_key` are the same as a read job's.

    An aggregator's copy of one job (22 Nov) is kept the same way, with no `feed_id` and
    `found_on` saying which site it is a copy on: its title and company are those its search
    result named (`hunt.copy_lead`), its summary the result's.
    """
    where = " ".join((lead.location or "").split())
    summary = " ".join((lead.snippet or "").split())
    job = _job(
        title=lead.title,
        company=lead.company,
        location=Location(
            city=where.split(",")[0].strip() or None,
            country=query_country.upper() if query_country else None,
            raw=where or "Not stated",
        ),
        remote=infer_remote(where, summary[:400]),
        description=summary or None,
        status="partial" if summary else "missing",
        apply_url=lead.url,
        ats=None,
        source=JobSource(channel="board_search", detail=feed_id or _host(lead.url), seen_at=iso(now)),
        posted_at=lead.date,
        now=now,
    )
    job.status, job.via, job.preview_match, job.found_on = "preview", feed_id, match, found_on
    return job


def from_hunted(
    url: str,
    *,
    title: str,
    employer: str | None,
    site: str,
    listing: str | None,
    now: int,
    match: float | None = None,
) -> Job:
    """A posting link the hunter found that its search didn't read, kept as a **preview** (22 Nov,
    SRCH-22): the link is the posting, so reading it needs no lookup.

    It is keyed by its own address, never by company and title — two "Graduate Analyst" links on
    one board are two jobs — and with the employer's job system's identity when the address carries
    one, so that reading it later lands on this row, not a second one. The title is the link's
    text. The company is the `employer` the link named (`hunt.employer_of`) or, when nothing did,
    the site's name with `found_on.employer_known` false, so the card says "Found on <site>" rather
    than naming an employer it doesn't know. Nothing else is known: no place — not even a country,
    since the search's country is where it looked, not where the job is, and a guess there would
    hide it from a city-only profile — no date, no description. So it isn't hidden for its place
    until it is read, and a score on it alone would be rough-only (MATCH-04).
    """
    from .resolve import ats_from_url

    job = _job(
        title=title,
        company=employer or site,
        # No words for a place at all (not "Not stated", which the filters read as a place they
        # can't find, and hide): the place is judged once the posting is read.
        location=Location(city=None, country=None, raw=""),
        remote=None,
        description=None,
        status="missing",
        apply_url=url,
        ats=ats_from_url(url),
        source=JobSource(channel="board_search", detail=_host(url), seen_at=iso(now)),
        posted_at=None,
        now=now,
    )
    # An agency's site whose link names the employer ("… - Citi" on eFinancialCareers) gives its
    # employer through `_job`, as a read job's title would.
    known = employer is not None or job.posted_by is not None
    job.status, job.preview_match = "preview", match
    job.found_on = FoundOn(site=site, page=listing, kind="posting", employer_known=known)
    return job


def _host(url: str | None) -> str:
    try:
        return (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""
