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
import re
from typing import Any

from ..clock import iso
from ..domain import dedupe
from ..domain.text import is_linkedin_url
from ..schemas.jobs import Ats, Job, JobSource, Location, Salary
from ..sources.ats_feeds import FeedJob

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
MONEY = re.compile(
    rf"(?P<sym>[£€$]|chf)\s?(?P<low>{_NUMBER})(?:\s*(?:-|–|to)\s*(?:[£€$]|chf)?\s?(?P<high>{_NUMBER}))?",
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


def infer_seniority(title: str, description: str | None = None) -> str | None:
    """The level the ad is pitched at, from its own words."""
    haystack = f" {title.lower()} {(description or '')[:400].lower()} "
    for level, words in SENIORITY:
        if any(w in haystack for w in words):
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
) -> Job:
    key = dedupe.dedupe_key(ats=ats, apply_url=apply_url, company=company, title=title, city=location.city)
    return Job(
        id=job_id(key),
        dedupe_key=key,
        title=title.strip(),
        company=company.strip(),
        location=location,
        remote=remote,
        salary=parse_salary(description),
        description_md=description or None,
        description_status=status,
        apply_url=apply_url or None,
        ats=ats,
        linkedin_url=linkedin_url,
        sources=[source],
        language=detect_language(description),
        posted_at=posted_at,
        first_seen=iso(now),
        last_seen=iso(now),
        status="new",
        seniority=infer_seniority(title, description),
    )


def from_feed(feed: FeedJob, now: int) -> Job:
    """A watchlist job: the employer's own fields, so nothing needs inferring but the extras."""
    return _job(
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
        now=now,
    )


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
