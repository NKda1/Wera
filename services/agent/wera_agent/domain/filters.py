"""Hard filters, applied before any model call (MATCH-01).

These are rules, not judgement: an excluded word in the title, a city the person doesn't want,
a seniority above what they are looking for. Running them first is what keeps the model bill
down — a job rejected here costs nothing.

A filtered job is **hidden, not deleted**, and it carries the reason. That matters: a filter is
a guess about what someone wants, and they can look at what it hid and disagree.
"""

import re

from ..clock import now_ms, parse_ms
from ..schemas.jobs import Job
from ..schemas.profile import SearchProfile
from .dedupe import normalise_title

# Seniority words that put a job above an early-career search, in the order they are reported.
ABOVE = ("principal", "staff", "head of", "director", "vp ", "lead ", "senior", "sr.", "sr ")

# Levels a person can be looking for. Anything in ABOVE is above all of them.
EARLY = ("intern", "graduate", "junior", "entry", "mid")

# MATCH-01, title relevance. Words that say what *level* a role is, not what the job is. They
# are dropped from both sides before comparing, which is what lets a target of "Graduate
# Software Engineer" match a posting called "Software Engineer II" — and leaves the seniority
# judgement to `too_senior`, which has a better message for it.
LEVEL_WORDS = frozenset(
    {
        *EARLY,
        "senior",
        "principal",
        "staff",
        "lead",
        "head",
        "director",
        "vp",
        "associate",
        "assistant",
        "trainee",
        "apprentice",
        "placement",
        "i",
        "ii",
        "iii",
        "iv",
        "1",
        "2",
        "3",
        "4",
        "l1",
        "l2",
        "l3",
        "l4",
        "e3",
        "e4",
        "e5",
        "of",
        "the",
        "and",
    }
)

# Job titles that mean the same work. `dedupe.ABBREVIATIONS` deliberately doesn't carry these:
# for de-duplication, "Software Developer" and "Software Engineer" at one company are two
# postings and must not be merged. For *relevance* they are the same job, and someone whose
# target is "Software Engineer" would be annoyed to find the Developer posting hidden.
SAME_WORK = {"developer": "engineer", "programmer": "engineer", "dev": "engineer"}

# Qualifiers a title carries in brackets — "(Python)", "(12-month FTE)". Dropped from both
# sides: requiring a target's bracketed note to appear in the posting hides good matches.
_BRACKETS = re.compile(r"[(\[{][^)\]}]*[)\]}]")


def _words(text: str) -> str:
    return f" {re.sub(r'[^a-z0-9+#./ ]+', ' ', text.lower())} "


def _singular(word: str) -> str:
    """ "Engineers" and "Engineer" are the same job. Both sides get this, so it stays even."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def title_tokens(title: str) -> set[str]:
    """What a title is *about*, with level, brackets, plurals and filler removed."""
    words = normalise_title(_BRACKETS.sub(" ", title)).split()
    return {_singular(SAME_WORK.get(w, w)) for w in words} - LEVEL_WORDS


def matches_a_target(title: str, targets: list[str]) -> bool:
    """Whether a posting is one of the kinds of job this person is looking for (MATCH-01).

    A target matches when every word it is *about* appears in the posting's title: "Software
    Engineer" matches "Senior Software Engineer, Payments" but not "Sales Engineer".

    Subset in that direction, and only that direction, is the whole point — a posting may be
    more specific than the target, never less. `dedupe.title_similarity` is deliberately *not*
    used as a fallback here even though it is the house measure for titles elsewhere: it is
    built on `token_set_ratio`, which scores 100 when either side's words contain the other's.
    That is right for de-duplication, where the question is symmetric, and wrong here, where it
    quietly let a bare "Engineer" satisfy a target of "AI Engineer" — which is every vacancy at
    a watched company, the exact problem this filter exists to solve.

    With no targets this is False, because nothing can match an empty list. The decision to
    hide nothing in that case belongs to `title_mismatch`, which is where the policy lives.
    """
    posting = title_tokens(title)
    if not posting:
        return True  # nothing to judge; let the scorer decide
    return any(wanted and wanted <= posting for wanted in (title_tokens(t) for t in targets))


def title_mismatch(title: str, profile: SearchProfile) -> str | None:
    """MATCH-01: a job that isn't the kind of work this person asked for.

    This is what makes the ATS watchlist usable. A watched company's board carries every
    vacancy it has — account executives, recruiters, warehouse staff — and without this the
    only things standing between those and the scoring tier are an embedding and a paid model
    call. The filters are free; that is the whole reason they run first.

    It fails open on an empty target list, because "no titles yet" is a profile that hasn't
    been set up, not a request to hide everything.
    """
    if not [t for t in profile.titles if t.strip()]:
        return None
    if matches_a_target(title, profile.titles):
        return None
    return "Not one of your target titles"


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
    """A title above every level the profile asks for.

    The profile's own levels are what decide this, which took a fix to become true. The old
    version compared each word in `ABOVE` against `profile.seniority` and skipped any that
    matched — but `ABOVE` holds "senior", "staff", "lead"… and `Seniority` only allows
    "intern"/"graduate"/"junior"/"entry"/"mid", so the two sets could never intersect and the
    skip was unreachable. `profile.seniority` therefore had **no effect on filtering at all**;
    it only ever reached the model's prompt. Someone who added "mid" to widen their search got
    exactly the same hidden list as before, which is the sort of setting that teaches people the
    product doesn't listen.

    Now the levels the profile asks for are mapped to the title words that mean them, and a
    level the person wants is never a reason to hide a job. An empty list means "no level stated",
    which keeps the early-career default rather than hiding nothing — a profile with no levels is
    one that hasn't been set up, not a request to see Director roles.
    """
    haystack = _words(title)
    for word in ABOVE:
        if word.strip() in accepted_level_words(profile):
            continue
        if re.search(rf"(^|\W){re.escape(word.strip())}(\W|$)", haystack):
            return word.strip()
    return None


# Which title words a given level in the profile makes acceptable. Only the levels that overlap
# `ABOVE` need an entry — the early ones are never a reason to hide anything.
LEVEL_ACCEPTS: dict[str, tuple[str, ...]] = {
    "mid": (),  # mid-level is still below "Senior"; it widens the *scoring*, not this filter
    "senior": ("senior", "sr.", "sr"),
    "lead": ("lead", "staff"),
    "principal": ("principal", "staff"),
}


def accepted_level_words(profile: SearchProfile) -> set[str]:
    """Title words the profile has explicitly opted into, so they can't hide a job."""
    out: set[str] = set()
    for level in profile.seniority:
        out.update(LEVEL_ACCEPTS.get(level.lower().strip(), ()))
    return out


def wrong_location(job: Job, profile: SearchProfile) -> str | None:
    """Somewhere the person isn't looking — unless it's remote and they'll take remote.

    All three `remote` settings are answered here, which took a fix. `remote_only` already
    rejected a non-remote job, but `no_remote` rejected nothing: a remote job fell past both
    branches to the location check, and a posting whose location reads "Remote (London)"
    contains "London", so it was *kept* for someone who had said they don't want remote work.
    """
    if profile.remote == "remote_only":
        return None if job.remote == "remote" else "not remote"
    if profile.remote == "no_remote" and job.remote == "remote":
        return "remote"
    if job.remote == "remote":
        return None  # `remote: any`, and this one is remote — where it nominally sits is moot
    place = " ".join(filter(None, [job.location.city, job.location.country, job.location.raw])).lower()
    if not place.strip():
        return None
    cities = [w.lower().strip() for w in profile.locations if w.strip()]
    if any(c in place for c in cities):
        return None
    # SRCH-11: with no cities named, the countries are what "where" means, so a job is judged on
    # those instead. Without this a country-only profile — now a legitimate thing to have — would
    # have nothing to match against and would keep everything.
    #
    # Whole-word, unlike the cities above: a country's short names are two letters, and
    # substring-matching those puts "Austin" in the United States by way of the "us" in its
    # name, and Kyiv in the UK by way of "Ukraine".
    if any(re.search(rf"(^|\W){re.escape(alias)}(\W|$)", place) for alias in country_aliases(profile)):
        return None
    if not cities and not profile.countries:
        return None
    return job.location.city or job.location.raw


def country_aliases(profile: SearchProfile) -> list[str]:
    """Every name a posting might use for one of the profile's target countries."""
    out: list[str] = []
    for code in profile.countries:
        key = code.strip().upper()
        out += list(COUNTRY_ALIASES.get(key, (key.lower(),)))
    return out


# A country's names as a posting would write them, so "London, UK" satisfies a GB target. A code
# with no entry is matched as itself, which is weak but honest.
COUNTRY_ALIASES = {
    "GB": ("united kingdom", "uk", "england", "scotland", "wales", "northern ireland", "britain"),
    "IE": ("ireland", "republic of ireland", "eire"),
    "NL": ("netherlands", "the netherlands", "holland"),
    "DE": ("germany", "deutschland"),
    "FR": ("france",),
    "BE": ("belgium",),
    "ES": ("spain", "españa"),
    "CH": ("switzerland", "schweiz", "suisse"),
    "CZ": ("czechia", "czech republic"),
    "US": ("united states", "usa", "us", "united states of america"),
}


def too_old(job: Job, profile: SearchProfile, now: int) -> str | None:
    """Posted outside the window the profile asked for.

    `recency` used to be a Firecrawl `tbs` argument and nothing else — a hint to a search engine
    about what to rank, never a rule about what to keep. So a 30-day-old posting could come back
    from a "last 24 hours" search, and the watchlist and inbox channels never saw the setting at
    all because they don't go through Firecrawl. This is where it becomes a rule.

    A job whose date WERA doesn't have is **never** hidden on this rule. `postedAt` is often
    absent or approximate (MATCH-08), and hiding a job for having no date would quietly throw
    away every posting from a board that doesn't publish one.
    """
    window = RECENCY_MS.get(profile.recency)
    if window is None or not job.posted_at:
        return None
    try:
        posted = parse_ms(job.posted_at)
    except (ValueError, TypeError):
        return None
    age = now - posted
    if age <= window:
        return None
    # A date the source only guessed at (MATCH-08) gets a day of slack, so "about 7 days ago"
    # isn't hidden from a 7-day search by a rounding error in someone else's wording.
    if job.posted_precision == "approximate" and age <= window + DAY_MS:
        return None
    return f"{round(age / DAY_MS)} days old"


DAY_MS = 24 * 60 * 60 * 1000
RECENCY_MS = {"24h": DAY_MS, "7d": 7 * DAY_MS, "30d": 30 * DAY_MS}


def hidden_reason(job: Job, profile: SearchProfile, now: int | None = None) -> str | None:
    """Why this job shouldn't reach the shortlist, or None if it should.

    Checked in the order a person would read them, so the reason shown is the most obvious one.
    """
    term = excluded_term(job.title, profile)
    if term:
        return f"Title contains an excluded term: {term}"
    senior = too_senior(job.title, profile)
    if senior:
        return f"Title is more senior than your search: {senior}"
    # After seniority, not before: "Head of Data" is both off-target and too senior, and "more
    # senior than your search" is the sharper thing to be told — it means the work is right and
    # the level isn't. What's left here is the genuinely different job, where "not one of your
    # titles" is exactly the point.
    off_target = title_mismatch(job.title, profile)
    if off_target:
        return f"{off_target}: {job.title}"
    where = wrong_location(job, profile)
    if where:
        return f"Location isn't in your search: {where}"
    # Last, because it's the only reason that stops being true on its own — a job hidden for its
    # age was once a job this person wanted, and that reads better after the reasons about fit.
    stale = too_old(job, profile, now if now is not None else now_ms())
    if stale:
        return f"Posted outside your {profile.recency} window: {stale}"
    return None


def apply(jobs: list[Job], profile: SearchProfile, now: int | None = None) -> tuple[list[Job], list[Job]]:
    """Splits jobs into (kept, hidden), setting `status` and `hidden_reason` on the hidden ones."""
    kept: list[Job] = []
    hidden: list[Job] = []
    at = now if now is not None else now_ms()
    for job in jobs:
        reason = hidden_reason(job, profile, at)
        if reason is None:
            kept.append(job)
            continue
        job.status, job.hidden_reason = "hidden", reason
        hidden.append(job)
    return kept, hidden
