"""Scoring in code (SPEC §8)."""

from wera_agent.clock import iso, parse_ms
from wera_agent.domain.scoring import DEFAULT_WEIGHTS, ROUGH_CAP, ats_coverage, fit_total, freshness_score, should_retry_tailoring
from wera_agent.schemas.jobs import FitEvidence, FitWeights
from wera_agent.schemas.pack import AtsKeyword, AtsReport

NOW = parse_ms("2026-10-12T09:00:00.000Z")


def days_ago(d: float) -> str:
    return iso(NOW - d * 864e5)


def test_freshness_uses_the_documented_bands():
    assert [freshness_score(days_ago(d), NOW) for d in (1, 2, 5, 10, 30)] == [10, 10, 7, 4, 1]
    assert freshness_score(None, NOW) == 1


def test_reproduces_the_mockups_top_five(demo_jobs):
    totals = []
    for s in demo_jobs["shortlist"][:5]:
        fresh = freshness_score(days_ago(s["postedDaysAgo"]), NOW)
        totals.append(fit_total(FitEvidence.model_validate(s["evidence"]), fresh, DEFAULT_WEIGHTS, s["description"] is None))
    assert totals == [8.6, 8.1, 7.4, 7.0, 6.2]


def test_caps_rough_title_only_matches(demo_jobs):
    e = FitEvidence.model_validate(demo_jobs["shortlist"][0]["evidence"])
    for sub in (e.core, e.adjacent, e.experience, e.eligibility):
        sub.score = 10
    assert fit_total(e, 10, DEFAULT_WEIGHTS, True) == ROUGH_CAP


def test_re_ranks_from_weights_alone(demo_jobs):
    fernhill, northwind = (FitEvidence.model_validate(s["evidence"]) for s in demo_jobs["shortlist"][:2])
    adjacent_heavy = FitWeights(core=0.1, adjacent=0.6, experience=0.1, eligibility=0.1, freshness=0.1)
    assert fit_total(northwind, 10, adjacent_heavy, False) > fit_total(fernhill, 10, adjacent_heavy, False)


def kw(weight: str, matched: bool, in_cv: bool = True, term: str = "x") -> AtsKeyword:
    return AtsKeyword(term=term, weight=weight, matched=matched, in_master_cv=in_cv)


def test_ats_coverage_weighs_required_keywords_double():
    kws = [*[kw("required", True)] * 6, kw("required", False), *[kw("nice", True)] * 6, kw("nice", False)]
    assert round(ats_coverage(kws) * 100) == 86


def test_retries_only_when_a_missing_keyword_is_backed_by_the_master_cv():
    def report(k: AtsKeyword, attempt: int = 1) -> AtsReport:
        return AtsReport(keywords=[k], coverage=0.5, format_checks=[], pages=1, attempt=attempt)

    assert not should_retry_tailoring(report(kw("required", False, False, "Docker")))
    assert should_retry_tailoring(report(kw("required", False, True, "Python")))
    assert not should_retry_tailoring(report(kw("required", False, True, "Python"), attempt=3))
