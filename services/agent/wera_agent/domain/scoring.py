"""Scoring in code (SPEC §8). Models produce sub-scores with evidence; every total here is
computed deterministically, so changing a weight re-ranks without new LLM calls.

Rounding follows JavaScript's Math.round (half up) so totals match the web app exactly."""

import math

from ..clock import parse_ms
from ..schemas.jobs import FitEvidence, FitReport, FitWeights, Job
from ..schemas.pack import AtsKeyword, AtsReport

DEFAULT_WEIGHTS = FitWeights(core=0.35, adjacent=0.2, experience=0.2, eligibility=0.15, freshness=0.1)

# §8.2 rough-only jobs (no description) are capped, since the score is a title match.
ROUGH_CAP = 6.5

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
    job_id: str, evidence: FitEvidence, posted_at: str | None, rough_only: bool, weights: FitWeights, now_ms: int
) -> FitReport:
    freshness = freshness_score(posted_at, now_ms)
    return FitReport(
        **evidence.model_dump(),
        job_id=job_id,
        freshness=freshness,
        rough_only=rough_only,
        total=fit_total(evidence, freshness, weights, rough_only),
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


def should_retry_tailoring(report: AtsReport, threshold: float = 0.75) -> bool:
    """§8.3 retry rule: below threshold AND something missing that the master CV can back up."""
    if report.attempt >= 3:
        return False
    return report.coverage < threshold and any(not k.matched and k.in_master_cv for k in report.keywords)
