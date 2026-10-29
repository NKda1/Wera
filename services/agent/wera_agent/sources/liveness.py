"""SRCH-13: asking whether a posting is still there, for free.

A job listing is not a fact, it is a page that someone will take down. WERA found one, scored it,
and then treated the result as permanent: a `board_search` job could **never** be detected as
withdrawn, because closure (ATS-03) needs an ATS identity and `normalise.from_scrape` doesn't set
one. Nothing else re-examined a stored job at all.

This is the free half of the answer. **No Firecrawl credits are spent here, ever** — only a plain
HTTP GET and the employers' own feeds, which are public JSON. That constraint shapes everything
below, including what it cannot do.

The rule that matters more than any status code: **only positive evidence closes a job.** A board
that blocks a bot, times out, rate-limits, or answers with a redirect WERA won't follow has told
us nothing about the vacancy. That is ATS-03's `answered` discipline (`ats_feeds.py`) restated for
HTTP, and it is the difference between a shortlist that prunes itself and one that quietly deletes
live jobs.

What this honestly cannot do: a great many boards answer `200` with a "no longer accepting
applications" page. `CLOSED_PHRASES` catches the common wordings and will be defeated by any board
that renders the closure client-side — most React boards — or that words it in German or French.
So the phrase list yields `likely_gone`, which **never closes anything**; those postings are
caught by NORM-07's 30-day sweep instead. Detecting them properly needs a rendered scrape at 5
credits a job, which is out of scope by design. Do not mistake this list for a reliable signal.
"""

import re
from typing import Literal

import logfire

from ..http import is_denied, request

# `gone` closes a job. `likely_gone` and `unknown` only ever flag one.
type Verdict = Literal["open", "gone", "likely_gone", "unknown"]

# How much of the page to read. Enough for a closure banner, which is always near the top, and
# little enough that a 2 MB listings page costs nothing to reject.
BODY_HEAD_BYTES = 8192

# A redirect target that has lost the job's identity: the site root, a bare listings index, or a
# page that names the closure. Anything else — another job-shaped path, another host, a login —
# is `unknown`, because a canonical redirect and a "this job moved" redirect look identical.
_LOST_IDENTITY = re.compile(r"^/?$|^/(jobs?|search|careers|vacancies|opportunities)/?$", re.I)
_GONE_IN_PATH = re.compile(r"expired|closed|no-longer|not-found|unavailable|job-removed", re.I)

# Deliberately tight, and deliberately whole phrases. "closed" on its own appears in "recently
# closed roles" sidebars and in company boilerplate; these do not.
CLOSED_PHRASES = (
    "no longer accepting applications",
    "no longer accepting",
    "this job has closed",
    "this job is closed",
    "this vacancy has expired",
    "this vacancy is closed",
    "position has been filled",
    "this position is no longer",
    "this role is no longer available",
    "applications are closed",
    "advert has expired",
)


def classify(status: int, location: str | None, body_head: str) -> Verdict:
    """One HTTP response, as a verdict. Pure, so the whole truth table is testable.

    Ordering is the design: the two codes that unambiguously mean "not here" come first, then
    the redirect question, then the body. Everything not named is `unknown` — the default has to
    be the one that cannot close a job by accident.
    """
    if status in (404, 410):
        return "gone"
    if 300 <= status < 400:
        target = (location or "").strip()
        if not target:
            return "unknown"
        if _GONE_IN_PATH.search(target):
            return "gone"
        # Only a *same-site* collapse to the root or an index is evidence. A redirect to another
        # host may be a CDN, a login, or a rebrand; none of those say the job is gone.
        path = target if target.startswith("/") else _path_of(target)
        return "gone" if path is not None and _LOST_IDENTITY.match(path) else "unknown"
    if status == 200:
        lowered = " ".join(body_head.lower().split())
        return "likely_gone" if any(p in lowered for p in CLOSED_PHRASES) else "open"
    # 401/403/405/429 and every 5xx. A board refusing a bot is not a closed vacancy, and this is
    # the branch that stops a blocked crawler emptying someone's shortlist.
    return "unknown"


def _path_of(url: str) -> str | None:
    from urllib.parse import urlparse

    try:
        return urlparse(url).path or "/"
    except ValueError:
        return None


def check(url: str | None) -> Verdict:
    """Ask one URL whether it is still a job. Never raises, never spends a credit.

    `follow_redirects` stays off (the `http.request` default) — that is what makes the redirect
    rule above possible, and following one would mean re-running `http.check` on a target an
    employer controls.
    """
    if not url or is_denied(url):
        return "unknown"  # SRCH-03: LinkedIn is never fetched, so it is never confirmed either
    try:
        response = request("GET", url)
    except Exception:  # noqa: BLE001 — a transport failure is a fact about the network
        return "unknown"
    try:
        head = response.text[:BODY_HEAD_BYTES] if response.status_code == 200 else ""
    except Exception:  # noqa: BLE001 — an undecodable body is not a closed job
        head = ""
    verdict = classify(response.status_code, response.headers.get("location"), head)
    if verdict != "open":
        logfire.info("Liveness {verdict} for {status}", verdict=verdict, status=response.status_code)
    return verdict
