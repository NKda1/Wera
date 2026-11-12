"""Fit scoring, and the cache that stops WERA paying for it twice (MATCH-04, COST-04).

The scoring tier returns four sub-scores with evidence; the total is computed in code from the
person's own weights, so changing a weight re-ranks instantly and costs nothing (§8.2). A model
never sees a total and never produces one.

A score is cached against three things, because any of them changing makes the old score wrong:
the job's own content, the CV version it was judged against, and the model that judged it. Get
that key wrong in either direction and WERA either re-scores the same job every morning or
shows a stale score for a CV the person has since rewritten.
"""

import hashlib

import logfire
from sqlalchemy.orm import Session

from ..clock import iso
from ..db.models import FitReportRow
from ..domain.scoring import build_fit_report
from ..domain.vocab import in_post, text_has_term
from ..errors import ApiError
from ..llm.gateway import structured
from ..llm.prompts import cv_text, untrusted
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, FitReport, FitWeights, Job, JobKeyword
from ..schemas.llm_outputs import CULTURE_LIMIT, KEYWORD_LIMIT, FitScoreOutput, KeywordList


def job_hash(job: Job) -> str:
    """The parts of a job that could change its score. A new description is a new job to score;
    a new `lastSeen` is not."""
    seed = "\x1f".join(
        [
            job.title,
            job.company,
            job.location.raw,
            job.remote or "",
            job.description_md or "",
            job.description_status,
        ]
    )
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def cached_evidence(session: Session, job: Job, *, cv_version: int, model: str) -> FitEvidence | None:
    """A score already paid for, if it is still the right score (COST-04, SCH-02)."""
    row = session.get(FitReportRow, job.id)
    if row is None or row.cv_version != cv_version or row.model != model:
        return None
    if (row.job_hash or "") != job_hash(job):
        return None
    return with_posted_keywords(FitEvidence.model_validate(row.evidence), job)


def with_posted_keywords(evidence: FitEvidence, job: Job) -> FitEvidence:
    """Stored evidence with only the keywords its post contains (`vocab.in_post`).

    On read rather than by invalidating the cache: the sub-scores are still right, and a new cache
    key would have re-scored every job on the shortlist to fix a list that code can fix for free.
    """
    evidence.keywords = in_post(evidence.keywords, job.description_md)
    evidence.culture = [c for c in evidence.culture if job.description_md and text_has_term(job.description_md, c)]
    return evidence


def save_evidence(session: Session, job: Job, evidence: FitEvidence, *, cv_version: int, now: int) -> None:
    row = session.get(FitReportRow, job.id)
    data = evidence.model_dump(mode="json")
    if row is None:
        session.add(
            FitReportRow(
                job_id=job.id,
                evidence=data,
                model=evidence.model,
                cv_version=cv_version,
                job_hash=job_hash(job),
                created_at=iso(now),
            )
        )
    else:
        row.evidence, row.model, row.cv_version, row.job_hash = data, evidence.model, cv_version, job_hash(job)
    session.flush()


def _job_block(job: Job) -> str:
    """The job as the model sees it: WERA's own facts, then the ad itself as data (§9.3)."""
    header = f"Job: {job.title} at {job.company} ({job.location.raw}); remote mode: {job.remote or 'not stated'}"
    return f"{header}\n{untrusted('job post', job.description_md or '(no description available)')}"


def keywords_for(session: Session, job: Job, cv: MasterCV) -> tuple[list[JobKeyword], list[str]]:
    """ATSC-01: what the ad asks for, on the fast tier — the checkable keywords, and its culture.

    Split here and nowhere else, so that every reader of `FitEvidence.keywords` gets only terms a
    CV can name: the coverage denominator, the skills line and the citation guard all read that
    list, and none of them should ever see "ownership".
    """
    out = structured(
        session,
        "keywords.extract",
        KeywordList,
        {"job": _job_block(job)},
        # The post itself, not `_job_block`: WERA's own header line would let a keyword through
        # on the strength of the job title it wrote, which is not the post asking for it.
        context={"cv": cv, "post": job.description_md or ""},
    )
    checkable = [k for k in out.keywords if k.category != "soft_skill"]
    culture = [k.term for k in out.keywords if k.category == "soft_skill"]
    keywords = [JobKeyword(term=k.term, weight=k.weight, category=k.category) for k in checkable[:KEYWORD_LIMIT]]
    return keywords, culture[:CULTURE_LIMIT]


def score(session: Session, job: Job, cv: MasterCV, *, model: str) -> FitEvidence:
    """One job, judged by the scoring tier. The total is not asked for and not accepted."""
    verdict = structured(
        session,
        "fit.score",
        FitScoreOutput,
        {"cv": cv_text(cv), "job": _job_block(job)},
        context={"cv": cv},
    )
    keywords, culture = keywords_for(session, job, cv) if job.description_md else ([], [])
    return FitEvidence(
        core=verdict.core,
        adjacent=verdict.adjacent,
        experience=verdict.experience,
        eligibility=verdict.eligibility,
        reason=verdict.reason,
        job_quote=verdict.job_quote,
        cv_bullet_ids=verdict.cv_bullet_ids,
        keywords=keywords,
        culture=culture,
        model=model,
    )


def evidence_for(
    session: Session, job: Job, cv: MasterCV, *, model: str, now: int
) -> tuple[FitEvidence | None, bool, str | None]:
    """The job's evidence, from the cache if it is still valid (COST-04).

    Returns `(evidence, scored_now, error_code)`. A job that couldn't be scored comes back with
    no evidence rather than a guess — it stays on the list unranked instead of claiming a fit.
    """
    cached = cached_evidence(session, job, cv_version=cv.version, model=model)
    if cached is not None:
        return cached, False, None
    try:
        evidence = score(session, job, cv, model=model)
    except ApiError as exc:
        logfire.warn("Couldn't score {job_id}: {code}", job_id=job.id, code=exc.code)
        return None, False, exc.code
    save_evidence(session, job, evidence, cv_version=cv.version, now=now)
    return evidence, True, None


def report_for(job: Job, evidence: FitEvidence, weights: FitWeights, now: int) -> FitReport:
    """§8.2: the total, computed here and never by a model. A job with no description is
    `rough_only`, which caps its total — a title match is not a fit."""
    return build_fit_report(
        job.id,
        evidence,
        job.posted_at,
        job.description_status == "missing",
        weights,
        now,
    )


def rank(scored: list[tuple[Job, FitReport]]) -> list[tuple[Job, FitReport]]:
    """Best first, then most recently posted. Plain Python — ranking is not a model's job."""
    return sorted(scored, key=lambda pair: (-pair[1].total, -(pair[1].freshness or 0), pair[0].title))
