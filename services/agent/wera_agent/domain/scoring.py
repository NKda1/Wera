"""Scoring in code (SPEC §8). Models produce sub-scores with evidence; every total here is
computed deterministically, so changing a weight re-ranks without new LLM calls.

Rounding follows JavaScript's Math.round (half up) so totals match the web app exactly."""

import math

from ..clock import parse_ms
from ..schemas.jobs import FitEvidence, FitReport, FitWeights
from ..schemas.pack import AtsKeyword, AtsReport

DEFAULT_WEIGHTS = FitWeights(core=0.35, adjacent=0.2, experience=0.2, eligibility=0.15, freshness=0.1)

# §8.2 rough-only jobs (no description) are capped, since the score is a title match.
ROUGH_CAP = 6.5

DAY_MS = 24 * 60 * 60 * 1000


def js_round(n: float) -> int:
    return math.floor(n + 0.5)


def round1(n: float) -> float:
    return js_round(n * 10) / 10


def freshness_score(posted_at: str | None, now_ms: int) -> float:
    """§8.2: 10 if posted ≤ 2 days ago, 7 if ≤ 7, 4 if ≤ 14, else 1. Unknown dates score as stale."""
    if not posted_at:
        return 1
    try:
        days = (now_ms - parse_ms(posted_at)) / DAY_MS
    except (ValueError, TypeError):
        # Sources normalise their dates (`normalise.parse_posted`), so reaching here means a row
        # written before that or by something new. One unreadable date scores as stale; it does
        # not raise and take the rest of the run's scoring with it.
        return 1
    if days <= 2:
        return 10
    if days <= 7:
        return 7
    if days <= 14:
        return 4
    return 1


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
