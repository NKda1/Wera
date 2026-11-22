"""Scoring in code (SPEC §8). Models produce sub-scores with evidence; every total here is
computed deterministically, so changing a weight re-ranks without new LLM calls.

Rounding follows JavaScript's Math.round (half up) so totals match the web app exactly."""

import math

from ..clock import parse_ms
from ..schemas.jobs import FitEvidence, FitReport, FitWeights, Job
from ..schemas.pack import AtsKeyword

DEFAULT_WEIGHTS = FitWeights(core=0.35, adjacent=0.2, experience=0.2, eligibility=0.15, freshness=0.1)

# §8.2 rough-only jobs (no description) are capped, since the score is a title match.
ROUGH_CAP = 6.5


def rough_only(job: Job) -> bool:
    """MATCH-04: whether a score could only be rough — judged on less than the ad itself.

    A card with no description (`missing`) always was. *(21 Nov)* So is one with only part of it
    (`partial`): a preview read without its original is scored on a job API's summary, 270–500
    characters that name the role and a skill or two and none of what it asks, and a summary that
    happens to read well is not a fit any more than a title is. Capped at `ROUGH_CAP` the same way.
    """
    return job.description_status != "full"


# 21 Nov: a preview's label from its `lead_ranker` match (cosine of title and summary against the
# CV, plus 0.1 when the title names a target title), never a number on screen — the match is a
# similarity, not a fit, and a "0.71" beside a fit of 7.1 would read as the same scale. Calibrated
# on the finance CV (v9) against 1,035 real Adzuna and Careerjet leads in Redis, 21 Nov: see
# `PREVIEW_LABELS` in SPEC SRCH-26. Strong is about the best fifth, Good the next two-fifths.
PREVIEW_STRONG = 0.80
PREVIEW_GOOD = 0.72


def preview_label(match: float | None) -> str:
    """ "strong", "good" or "possible" (21 Nov). Unranked (no embedding model) is "possible"."""
    if match is None:
        return "possible"
    if match >= PREVIEW_STRONG:
        return "strong"
    return "good" if match >= PREVIEW_GOOD else "possible"


DAY_MS = 24 * 60 * 60 * 1000

# §8.2's freshness ladder, and the groups the Jobs screen puts jobs into, from one table —
# because they are the same judgement asked twice. A weighting that said a job was fresh while
# a header filed it under "Older" would be the kind of contradiction nobody reports and
# everybody stops trusting.
#
#   (within this many days, the score, the group it belongs to)
URGENCY: tuple[tuple[float, float, str], ...] = (
    (2, 10, "today"),
    (7, 7, "this_week"),
    (14, 4, "two_weeks"),
)
STALE_SCORE = 1.0


def js_round(n: float) -> int:
    return math.floor(n + 0.5)


def round1(n: float) -> float:
    return js_round(n * 10) / 10


def days_since(posted_at: str | None, now_ms: int) -> float | None:
    """How old a posting is, or None when WERA has no readable date for it.

    Sources normalise their dates (`normalise.parse_posted`), so an unreadable one here means a
    row written before that or by something new. It comes back as None rather than raising and
    taking the rest of the run's scoring with it.
    """
    if not posted_at:
        return None
    try:
        return (now_ms - parse_ms(posted_at)) / DAY_MS
    except (ValueError, TypeError):
        return None


def freshness_score(posted_at: str | None, now_ms: int) -> float:
    """§8.2: 10 if posted ≤ 2 days ago, 7 if ≤ 7, 4 if ≤ 14, else 1. Unknown dates score as stale."""
    days = days_since(posted_at, now_ms)
    if days is None:
        return STALE_SCORE
    return next((score for limit, score, _ in URGENCY if days <= limit), STALE_SCORE)


def urgency(posted_at: str | None, now_ms: int) -> str:
    """Which group a posting belongs to on the Jobs screen.

    `unknown` is its own group and is deliberately *not* folded into `older`, even though both
    score 1 for freshness. For a weighting, treating "no date" as stale is a safe default — it
    costs the job a tenth of its total and the person still sees it. For a *group header* it is
    a claim: filing a job under "Over two weeks old" says WERA knows when it was posted, and it
    doesn't. A reader can act on "no date given"; they can't act on a wrong one.
    """
    days = days_since(posted_at, now_ms)
    if days is None:
        return "unknown"
    return next((name for limit, _, name in URGENCY if days <= limit), "older")


# How recently an "it's still open" answer still counts for, in days. One would re-flag
# everything between the morning sweep and lunchtime; a week would call a job open on week-old
# evidence. Two means a confirmation survives a missed sweep.
CONFIRMED_WITHIN_DAYS = 2.0


def liveness(job: Job, now_ms: int) -> str:
    """MATCH-10: whether WERA believes this posting is still open, and on what evidence.

    Read from the lifecycle fields rather than stored, for the same reason `urgency` is: it is a
    claim about now, and a stored one is wrong by morning.

    The order matters. A closed job is `gone` whatever the timestamps say, because `status` is
    the decision and these fields are only the evidence behind it. Then "asked and confirmed",
    then "never asked" — which is distinct from "asked and couldn't tell", and conflating the two
    would tell someone WERA had checked a job it had never looked at.
    """
    if job.status == "closed":
        return "gone"
    if job.last_confirmed_at:
        age = days_since(job.last_confirmed_at, now_ms)
        if age is not None and age <= CONFIRMED_WITHIN_DAYS:
            return "open"
    return "unreachable" if job.last_checked_at else "unverified"


# LIFE-03 (14 Nov): how long a job stays on the main list without anything showing it open. A
# week: twice the median life of a closed job is 24 days (12 days, measured on 1,252 closures),
# and a job nobody could confirm for a week is more likely gone than not.
PROOF_OF_LIFE_DAYS = 7.0
# LIFE-04: open this long, and the advert is often standing rather than a vacancy.
EVERGREEN_DAYS = 60.0


def possibly_closed(job: Job, now_ms: int) -> bool:
    """Nothing has shown this job open for a week (LIFE-03). Its last proof of life is the later of
    a confirmation (`last_confirmed_at`) and being found — a source listing it the day it was
    first seen is evidence it was there. `last_seen` is not proof (LIFE-01): a cached card can
    mention a job that closed yesterday."""
    if job.status not in ("new", "shortlisted", "snoozed"):
        return False
    proofs = [d for d in (days_since(job.last_confirmed_at, now_ms), days_since(job.first_seen, now_ms)) if d is not None]
    return bool(proofs) and min(proofs) > PROOF_OF_LIFE_DAYS


def evergreen(job: Job, now_ms: int) -> bool:
    """Open two months or more (LIFE-04): posted that long ago, or first seen that long ago."""
    if job.status == "closed":
        return False
    age = days_since(job.posted_at, now_ms) if job.posted_at else days_since(job.first_seen, now_ms)
    return age is not None and age >= EVERGREEN_DAYS


def normalise_weights(w: FitWeights) -> FitWeights:
    total = w.core + w.adjacent + w.experience + w.eligibility + w.freshness
    if total <= 0:
        return DEFAULT_WEIGHTS
    return FitWeights(
        core=w.core / total,
        adjacent=w.adjacent / total,
        experience=w.experience / total,
        eligibility=w.eligibility / total,
        freshness=w.freshness / total,
    )


def fit_total(e: FitEvidence, freshness: float, weights: FitWeights, rough_only: bool) -> float:
    w = normalise_weights(weights)
    total = (
        w.core * e.core.score
        + w.adjacent * e.adjacent.score
        + w.experience * e.experience.score
        + w.eligibility * e.eligibility.score
        + w.freshness * freshness
    )
    return round1(min(total, ROUGH_CAP) if rough_only else total)


def build_fit_report(
    job_id: str,
    evidence: FitEvidence,
    posted_at: str | None,
    rough_only: bool,
    weights: FitWeights,
    now_ms: int,
    *,
    stale: bool = False,
) -> FitReport:
    freshness = freshness_score(posted_at, now_ms)
    return FitReport(
        **evidence.model_dump(),
        job_id=job_id,
        freshness=freshness,
        rough_only=rough_only,
        total=fit_total(evidence, freshness, weights, rough_only),
        stale=stale,
    )


def ats_coverage(keywords: list[AtsKeyword]) -> float:
    """§8.3: required keywords weigh 2, nice-to-have 1."""
    total = hit = 0
    for k in keywords:
        w = 2 if k.weight == "required" else 1
        total += w
        if k.matched:
            hit += w
    return 0 if total == 0 else hit / total


# `should_retry_tailoring` was here. It implemented §8.3's retry rule — re-tailor when coverage is
# below the threshold and the master CV can back something that's missing — and it has been
# removed rather than merely disabled, because the condition it tested is now handled before it
# could ever be true: `nodes.ats.reconcile_skills` covers those keywords in code, from the CV's
# own skills, at no inference cost. See that function for the measurements that made the loop
# untenable. It also never read `ats_threshold` from the config; the new path does.
