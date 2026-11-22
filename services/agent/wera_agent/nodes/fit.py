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
from typing import NamedTuple

import logfire
from sqlalchemy.orm import Session

from ..clock import iso
from ..db.models import FitReportRow
from ..domain.degrees import looks_like_degree
from ..domain.scoring import build_fit_report, rough_only
from ..domain.vocab import in_post, text_has_term
from ..errors import ApiError
from ..llm.gateway import structured
from ..llm.prompts import cv_text, untrusted
from ..schemas.cv import MasterCV
from ..schemas.jobs import EducationAsk, FitEvidence, FitReport, FitWeights, Job, JobKeyword
from ..schemas.llm_outputs import CULTURE_LIMIT, KEYWORD_LIMIT, FitScoreOutput, KeywordList

# The scoring rubric's version (MATCH-04). Bump it when `FIT_SCORE` changes what a score means, and
# every stored score is re-scored by the next rescore run — keywords are still reused, since what a
# post asks for doesn't change with the rubric. 2026-11-16: location left `eligibility`.
RUBRIC = "2026-11-18"  # 18 Nov: skill levels reach the scorer (MATCH-04)


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


def scoring_fingerprint(cv: MasterCV) -> str:
    """What the scorer reads of the CV, with the rubric it reads it by (18 Nov).

    A score is stale when this changes, not when the CV version does: a save that changes no
    skill, bullet or level (a renamed alias, a note) keeps every score, and a level edit changes
    the one thing the scorer now reads. Built from `cv_text(home=False)`, which is what the prompt
    is given, so the two can't disagree.
    """
    return hashlib.sha256(f"{RUBRIC}\n{cv_text(cv, home=False)}".encode()).hexdigest()[:32]


def cached_evidence(session: Session, job: Job, *, cv_version: int, model: str, cv_hash: str | None = None) -> FitEvidence | None:
    """A score already paid for, if it is still the right score (COST-04, SCH-02).

    Judged by the CV's scoring fingerprint when both the score and the caller have one, else by
    the CV version — the rule for scores written before fingerprints existed (18 Nov).
    """
    row = session.get(FitReportRow, job.id)
    if row is None or row.model != model:
        return None
    if cv_hash is not None and row.cv_hash is not None:
        if row.cv_hash != cv_hash:
            return None
    elif row.cv_version != cv_version:
        return None
    if (row.job_hash or "") != job_hash(job) or not current_rubric(row.evidence):
        return None
    return with_posted_keywords(FitEvidence.model_validate(row.evidence), job)


def current_rubric(evidence: dict) -> bool:
    """Whether stored evidence was judged with today's rubric (`RUBRIC`)."""
    return evidence.get("rubric") == RUBRIC


def with_posted_keywords(evidence: FitEvidence, job: Job) -> FitEvidence:
    """Stored evidence with only the keywords its post contains (`vocab.in_post`).

    On read rather than by invalidating the cache: the sub-scores are still right, and a new cache
    key would have re-scored every job on the shortlist to fix a list that code can fix for free.
    """
    evidence.keywords = in_post(evidence.keywords, job.description_md)
    evidence.culture = [c for c in evidence.culture if job.description_md and text_has_term(job.description_md, c)]
    return evidence


def save_evidence(
    session: Session, job: Job, evidence: FitEvidence, *, cv_version: int, now: int, cv_hash: str | None = None
) -> None:
    row = session.get(FitReportRow, job.id)
    data = evidence.model_dump(mode="json")
    if row is None:
        session.add(
            FitReportRow(
                job_id=job.id,
                evidence=data,
                model=evidence.model,
                cv_version=cv_version,
                cv_hash=cv_hash,
                job_hash=job_hash(job),
                created_at=iso(now),
            )
        )
    else:
        row.evidence, row.model, row.cv_version, row.job_hash = data, evidence.model, cv_version, job_hash(job)
        row.cv_hash = cv_hash
    session.flush()


def _job_block(job: Job) -> str:
    """The job as the model sees it: WERA's own facts, then the ad itself as data (§9.3)."""
    header = f"Job: {job.title} at {job.company} ({job.location.raw}); remote mode: {job.remote or 'not stated'}"
    return f"{header}\n{untrusted('job post', job.description_md or '(no description available)')}"


class PostAsks(NamedTuple):
    """What a post asks for, as `keywords.extract` reads it: the checkable keywords, its culture
    words, and the degree it asks for (UPSK-05)."""

    keywords: list[JobKeyword]
    culture: list[str]
    education: EducationAsk | None = None


def keywords_for(session: Session, job: Job, cv: MasterCV) -> PostAsks:
    """ATSC-01: what the ad asks for, on the fast tier — the checkable keywords, its culture, and
    the degree it asks for.

    Split here and nowhere else, so that every reader of `FitEvidence.keywords` gets only terms a
    CV can name: the coverage denominator, the skills line and the citation guard all read that
    list, and none of them should ever see "ownership" — or "Bachelor's degree", which is an
    education requirement (`domain.degrees`), not a skill.
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
    checkable = [k for k in out.keywords if k.category != "soft_skill" and not looks_like_degree(k.term)]
    culture = [k.term for k in out.keywords if k.category == "soft_skill"]
    keywords = [JobKeyword(term=k.term, weight=k.weight, category=k.category) for k in checkable[:KEYWORD_LIMIT]]
    return PostAsks(keywords, culture[:CULTURE_LIMIT], out.education)


def known_keywords(session: Session, job: Job) -> PostAsks | None:
    """The keywords a fit report already holds for this very post, when it has any.

    What a post asks for is a fact about the post: `keywords.extract` reads the ad alone (the CV
    rides along only as validation context, and the validator checks against the post). So a
    re-score after a CV edit, or with another model, reuses them instead of paying to read the ad
    again — the old report's `job_hash` says whether the ad is the one it read.
    """
    row = session.get(FitReportRow, job.id)
    if row is None or (row.job_hash or "") != job_hash(job):
        return None
    stored = FitEvidence.model_validate(row.evidence)
    if not stored.keywords:
        return None
    logfire.info("Keywords for {job_id} reused from its last report", job_id=job.id)
    return PostAsks(in_post(stored.keywords, job.description_md), stored.culture, stored.education)


def score(session: Session, job: Job, cv: MasterCV, *, model: str) -> FitEvidence:
    """One job, judged by the scoring tier. The total is not asked for and not accepted."""
    verdict = structured(
        session,
        "fit.score",
        FitScoreOutput,
        {"cv": cv_text(cv, home=False), "job": _job_block(job)},
        context={"cv": cv},
    )
    asks = known_keywords(session, job) or (keywords_for(session, job, cv) if job.description_md else PostAsks([], []))
    return FitEvidence(
        core=verdict.core,
        adjacent=verdict.adjacent,
        experience=verdict.experience,
        eligibility=verdict.eligibility,
        reason=verdict.reason,
        job_quote=verdict.job_quote,
        cv_bullet_ids=verdict.cv_bullet_ids,
        keywords=asks.keywords,
        culture=asks.culture,
        education=asks.education,
        model=model,
        rubric=RUBRIC,
    )


def evidence_for(
    session: Session, job: Job, cv: MasterCV, *, model: str, now: int
) -> tuple[FitEvidence | None, bool, str | None]:
    """The job's evidence, from the cache if it is still valid (COST-04).

    Returns `(evidence, scored_now, error_code)`. A job that couldn't be scored comes back with
    no evidence rather than a guess — it stays on the list unranked instead of claiming a fit.
    """
    fingerprint = scoring_fingerprint(cv)
    cached = cached_evidence(session, job, cv_version=cv.version, model=model, cv_hash=fingerprint)
    if cached is not None:
        return cached, False, None
    try:
        evidence = score(session, job, cv, model=model)
    except ApiError as exc:
        logfire.warn("Couldn't score {job_id}: {code}", job_id=job.id, code=exc.code)
        return None, False, exc.code
    save_evidence(session, job, evidence, cv_version=cv.version, now=now, cv_hash=fingerprint)
    return evidence, True, None


def report_for(job: Job, evidence: FitEvidence, weights: FitWeights, now: int) -> FitReport:
    """§8.2: the total, computed here and never by a model. A job with no description, or only
    a job API's summary of one (21 Nov), is `rough_only`, which caps its total — a title match is
    not a fit (`scoring.rough_only`)."""
    return build_fit_report(
        job.id,
        evidence,
        job.posted_at,
        rough_only(job),
        weights,
        now,
    )


def rank(scored: list[tuple[Job, FitReport]]) -> list[tuple[Job, FitReport]]:
    """Best first, then most recently posted. Plain Python — ranking is not a model's job."""
    return sorted(scored, key=lambda pair: (-pair[1].total, -(pair[1].freshness or 0), pair[0].title))
