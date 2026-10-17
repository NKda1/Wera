"""Hard filters, applied before any model call (MATCH-01).

These are rules, not judgement: an excluded word in the title, a city the person doesn't want,
a seniority above what they are looking for. Running them first is what keeps the model bill
down — a job rejected here costs nothing.

A filtered job is **hidden, not deleted**, and it carries the reason. That matters: a filter is
a guess about what someone wants, and they can look at what it hid and disagree.
"""

import re

from ..schemas.jobs import Job
from ..schemas.profile import SearchProfile

# Seniority words that put a job above an early-career search, in the order they are reported.
ABOVE = ("principal", "staff", "head of", "director", "vp ", "lead ", "senior", "sr.", "sr ")

# Levels a person can be looking for. Anything in ABOVE is above all of them.
EARLY = ("intern", "graduate", "junior", "entry", "mid")


def _words(text: str) -> str:
    return f" {re.sub(r'[^a-z0-9+#./ ]+', ' ', text.lower())} "


def excluded_term(title: str, profile: SearchProfile) -> str | None:
    """The person's own exclusion list, matched whole-word so "Lead" doesn't hit "Leadership"."""
    haystack = _words(title)
    for term in profile.exclude:
        if not term.strip():
            continue
        if re.search(rf"(^|\W){re.escape(term.lower().strip())}(\W|$)", haystack):
            return term
    return None


def too_senior(title: str, profile: SearchProfile) -> str | None:
    """A title above every level the profile asks for. `mid` accepts "Senior" nowhere, but a
    profile that explicitly includes a level never filters on it."""
    haystack = _words(title)
    wanted = {s.lower() for s in profile.seniority}
    for word in ABOVE:
        if word.strip() in wanted:
            continue
        if re.search(rf"(^|\W){re.escape(word.strip())}(\W|$)", haystack):
            return word.strip()
    return None


def wrong_location(job: Job, profile: SearchProfile) -> str | None:
    """Somewhere the person isn't looking — unless it's remote and they'll take remote."""
    if profile.remote != "no_remote" and job.remote == "remote":
        return None
    if profile.remote == "remote_only" and job.remote != "remote":
        return "not remote"
    place = " ".join(filter(None, [job.location.city, job.location.country, job.location.raw])).lower()
    if not place.strip():
        return None
    if any(wanted.lower().strip() in place for wanted in profile.locations if wanted.strip()):
        return None
    return job.location.city or job.location.raw


def hidden_reason(job: Job, profile: SearchProfile) -> str | None:
    """Why this job shouldn't reach the shortlist, or None if it should.

    Checked in the order a person would read them, so the reason shown is the most obvious one.
    """
    term = excluded_term(job.title, profile)
    if term:
        return f"Title contains an excluded term: {term}"
    senior = too_senior(job.title, profile)
    if senior:
        return f"Title is more senior than your search: {senior}"
    where = wrong_location(job, profile)
    if where:
        return f"Location isn't in your search: {where}"
    return None


def apply(jobs: list[Job], profile: SearchProfile) -> tuple[list[Job], list[Job]]:
    """Splits jobs into (kept, hidden), setting `status` and `hidden_reason` on the hidden ones."""
    kept: list[Job] = []
    hidden: list[Job] = []
    for job in jobs:
        reason = hidden_reason(job, profile)
        if reason is None:
            kept.append(job)
            continue
        job.status, job.hidden_reason = "hidden", reason
        hidden.append(job)
    return kept, hidden
