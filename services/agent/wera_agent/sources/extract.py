"""Reading a job off a page without paying for it (SRCH-05).

One Firecrawl JSON scrape costs 5 credits against a search's 2, and page scrapes were 5 of every
7 credits a run spent — so with Firecrawl at ~85% of the bill, this one call was most of the
invoice. What the 5 credits buy is an LLM reading a rendered page into fields, and the whole of
that is reproducible locally.

**The ladder, cheapest first.** Each rung answers for the pages the one above it couldn't:

1. **The employer's own JSON**, when the URL is a Greenhouse, Lever or Ashby posting. The most
   authoritative source there is, free, and no guessing.
2. **JSON-LD `JobPosting`**, over plain HTTP. This is the rung that does the work: Google for Jobs
   is populated by Google *reading this markup off employers' pages*, so any board that wants to
   appear there — which is all of them — publishes it. Free, and no inference at all.
3. **The same two, after a browser render.** A React board serves an empty shell to `httpx` and
   the identical markup once JavaScript has run.
4. **`job.normalise` on the fast tier**, from the page's text. About $0.0002 against 5 Firecrawl
   credits (~$0.019) — two orders of magnitude cheaper, and the task was already written: its
   prompt, schema and config row all existed with no call site.

Firecrawl is the fifth rung and lives in `firecrawl_search.scrape_card`, because that is what
owns the credit caps. This module never spends a credit and never raises.

What comes back is a `CARD_SCHEMA`-shaped dict, so every rung is interchangeable with the
Firecrawl scrape it replaces and `normalise.from_scrape` cannot tell them apart.
"""

import json
from collections.abc import Callable
from typing import Any

import logfire

# The lexbor backend, matching `sources.inbox` — selectolax 1.0 dropped Modest and importing it
# now raises outright.
from selectolax.lexbor import LexborHTMLParser

from ..http import request
from .ats_feeds import html_to_text, remote_mode
from .browser import Fetcher

# How a card was read, for the run's own telemetry. The point of recording it is that "the local
# path works" stops being an assumption and becomes a number someone can look at.
type How = str

# A card dict plus how it was read; `None` when no rung could answer.
type Found = tuple[dict[str, Any] | None, How]

# Parsing a whole listings page is pointless and a 10 MB one is hostile. A job posting's markup
# is near the top; this is generous for any real page.
MAX_HTML_BYTES = 3_000_000

# What the LLM rung is given. The first part of a page is the job and the rest is navigation,
# related roles and footers — and `job.normalise` is capped at 1,200 reply tokens anyway.
TEXT_FOR_MODEL = 12_000

# Enough text to be a description rather than a cookie banner. Below this the page didn't render.
MIN_DESCRIPTION = 200


def card(
    url: str,
    *,
    fetch_html: Fetcher | None = None,
    parse: Callable[[str], dict[str, Any] | None] | None = None,
) -> Found:
    """Read a job off `url`, or `(None, reason)`.

    `fetch_html` renders with a browser (`browser.session`); `parse` runs the `job.normalise`
    task. Both are injected rather than imported so this module needs neither a browser nor a
    database to test, and so the caller owns the budget for the one that costs money.
    """
    ats = _from_ats(url)
    if ats is not None:
        return ats, "ats-feed"

    html = _get(url)
    if html:
        found = _from_json_ld(html)
        if found is not None:
            return found, "json-ld"

    rendered = fetch_html(url) if fetch_html is not None else None
    if rendered:
        found = _from_json_ld(rendered)
        if found is not None:
            return found, "json-ld-rendered"

    # The model sees whichever version of the page read better. A rendered page is usually the
    # fuller one, but a render that failed leaves `None`, not an empty string to prefer.
    best = rendered or html
    if parse is not None and best:
        found = _from_model(best, parse)
        if found is not None:
            return found, "model"

    return None, "unreadable" if best else "unreachable"


# ---------------------------------------------------------------------------
# Rung 1: the employer's own feed


def _from_ats(url: str) -> dict[str, Any] | None:
    """A Greenhouse, Lever or Ashby posting, from the vendor's own JSON.

    The board endpoint carries every job, so this reads the board and picks one. That is more
    bytes than a per-job endpoint would be, and it is deliberate: `ats_feeds` already knows how
    to parse all three vendors correctly, and a second set of per-vendor parsers would be a
    second thing to keep right.
    """
    from ..config import Company
    from ..domain.text import title_case
    from . import ats_feeds, resolve

    ats = resolve.ats_from_url(url)
    if ats is None:
        return None
    company = Company(name=title_case(ats.board_token), vendor=ats.vendor, token=ats.board_token)
    try:
        jobs, error = ats_feeds.fetch_company(company)
    except Exception:  # noqa: BLE001 — a board that won't answer costs this rung, not the page
        return None
    if error is not None:
        return None
    found = next((j for j in jobs if j.ats.job_id == ats.job_id), None)
    if found is None or not found.title:
        return None
    return {
        "title": found.title,
        "company": found.company,
        "location": found.location.raw,
        "remote": found.remote or "",
        "postedAt": found.posted_at or "",
        "description": found.description or "",
    }


# ---------------------------------------------------------------------------
# Rung 2 and 3: JSON-LD


def _get(url: str) -> str | None:
    """The page over plain HTTP. Redirects followed — a board moving a posting is routine, and
    unlike `liveness.check` nothing here reads a redirect as evidence of anything."""
    try:
        response = request("GET", url, follow_redirects=True)
    except Exception:  # noqa: BLE001 — unreachable is an answer
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.content[:MAX_HTML_BYTES]
        return body.decode(response.encoding or "utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — an undecodable body is not a job
        return None


def _json_ld_blocks(html: str) -> list[Any]:
    """Every `application/ld+json` payload on the page, parsed. A broken one is skipped."""
    out: list[Any] = []
    try:
        tree = LexborHTMLParser(html)
    except Exception:  # noqa: BLE001
        return out
    for node in tree.css('script[type="application/ld+json"]'):
        raw = (node.text() or "").strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except (ValueError, TypeError):
            continue  # a board shipping invalid JSON-LD is common and not worth a line of log
    return out


def _job_postings(payload: Any) -> list[dict[str, Any]]:
    """Every `JobPosting` inside one payload, however it is wrapped.

    Three shapes are all common and all valid: the posting on its own, a list of things, and an
    `@graph` with the posting among them. Missing the nested ones would mean falling through to a
    paid scrape on pages that had the answer in plain sight.
    """
    if isinstance(payload, list):
        return [p for item in payload for p in _job_postings(item)]
    if not isinstance(payload, dict):
        return []
    types = payload.get("@type")
    names = types if isinstance(types, list) else [types]
    if any(isinstance(n, str) and n.lower() == "jobposting" for n in names):
        return [payload]
    return [p for item in payload.get("@graph") or [] for p in _job_postings(item)]


def _from_json_ld(html: str) -> dict[str, Any] | None:
    for payload in _json_ld_blocks(html):
        for posting in _job_postings(payload):
            built = _card_from_posting(posting)
            if built is not None:
                return built
    return None


def _card_from_posting(posting: dict[str, Any]) -> dict[str, Any] | None:
    """One `JobPosting` as a card. `None` unless it has the two fields a job needs."""
    title = _string(posting.get("title")).strip()
    company = _string(_dig(posting.get("hiringOrganization"), "name")).strip()
    if not title or not company:
        # `normalise.from_scrape` would reject it anyway; refusing here keeps the ladder honest
        # about which rung actually answered.
        return None
    return {
        "title": title[:200],
        "company": company[:200],
        "location": _place(posting) or "Not stated",
        "remote": _remote_of(posting) or "",
        "postedAt": _string(posting.get("datePosted")).strip(),
        "description": html_to_text(_string(posting.get("description"))),
    }


def _place(posting: dict[str, Any]) -> str:
    """The posting's location as a human would write it: "London, United Kingdom"."""
    locations = posting.get("jobLocation")
    first = locations[0] if isinstance(locations, list) and locations else locations
    address = _dig(first, "address") if isinstance(first, dict) else None
    if not isinstance(address, dict):
        return _string(_dig(first, "name")).strip()
    parts = [
        _string(address.get("addressLocality")).strip(),
        _string(address.get("addressRegion")).strip(),
        _string(address.get("addressCountry") if isinstance(address.get("addressCountry"), str) else None).strip()
        or _string(_dig(address.get("addressCountry"), "name")).strip(),
    ]
    return ", ".join(p for p in parts if p)


def _remote_of(posting: dict[str, Any]) -> str | None:
    """schema.org says remote with `jobLocationType: TELECOMMUTE`; everything else is wording."""
    if "telecommute" in _string(posting.get("jobLocationType")).lower():
        return "remote"
    return remote_mode(posting.get("employmentType"), _string(posting.get("title")), _place(posting))


def _dig(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _place_of(value: Any) -> str:
    """A location as card text, from either a `Location`-shaped dict or a plain string."""
    if isinstance(value, dict):
        raw = _string(value.get("raw")).strip()
        if raw:
            return raw
        return ", ".join(p for p in (_string(value.get("city")).strip(), _string(value.get("country")).strip()) if p)
    return _string(value).strip()


def _string(value: Any) -> str:
    """JSON-LD is loosely typed in the wild: a field documented as a string arrives as a number,
    a list of strings, or a nested object. Anything unusable becomes empty rather than raising."""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return " ".join(_string(v) for v in value if v)
    return ""


# ---------------------------------------------------------------------------
# Rung 4: the fast tier


def _from_model(html: str, parse: Callable[[str], dict[str, Any] | None]) -> dict[str, Any] | None:
    """`job.normalise` over the page's text. The last rung before anything is paid for."""
    text = html_to_text(html)[:TEXT_FOR_MODEL]
    if len(text) < MIN_DESCRIPTION:
        return None  # nothing rendered; a model would be guessing from navigation
    try:
        found = parse(text)
    except Exception:  # noqa: BLE001 — the caller records its own failures; this rung just ends
        return None
    if not found:
        return None
    title = _string(found.get("title")).strip()
    company = _string(found.get("company")).strip()
    if not title or not company:
        return None
    logfire.info("Read a job with the fast tier rather than a paid scrape")
    return {
        "title": title[:200],
        "company": company[:200],
        # `NormalisedJob.location` is a `Location`, not a string — so this reads `raw` off the
        # object and still accepts a bare string, because a card's `location` is text and a
        # caller supplying one directly is a reasonable thing to allow.
        "location": _place_of(found.get("location")) or "Not stated",
        "remote": _string(found.get("remote")).strip(),
        "postedAt": _string(found.get("postedAt")).strip(),
        # The page's own text, not the model's summary of it: the description is what later
        # prompts quote from (LTR-02), so a paraphrase would make a "verbatim" quote a lie.
        "description": text,
    }
