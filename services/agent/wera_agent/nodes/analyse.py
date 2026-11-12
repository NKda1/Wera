"""The job-description rubric, filled in for the job a pack is being written for.

Run once per draft, not once per scored job: the strategic context is only ever read by the
letter, and a shortlist scores dozens of jobs for every one that gets a pack. The keyword matrix
costs nothing extra here — it is the scoring step's keyword list, regrouped.
"""

from sqlalchemy.orm import Session

from ..llm.gateway import structured
from ..schemas.jobs import FitEvidence, Job
from ..schemas.llm_outputs import JobAnalysisOutput
from ..schemas.pack import JobAnalysis, KeywordMatrix, RoleProfile, StrategicContext
from .fit import _job_block


def keyword_matrix(evidence: FitEvidence) -> KeywordMatrix:
    """`FitEvidence.keywords` by category, plus the culture terms beside them.

    A keyword stored before categories existed has none, and goes under hard skills: it was
    extracted under the old rule, which admitted only concrete, checkable terms, so that is the
    closest of the three to what it is.
    """

    def of(*categories: str | None) -> list[str]:
        return [k.term for k in evidence.keywords if k.category in categories]

    return KeywordMatrix(
        hard_skills=of("hard_skill", None),
        methodologies_and_frameworks=of("methodology"),
        tools_and_software=of("tool"),
        soft_skills_and_culture=list(evidence.culture),
    )


def analyse(session: Session, job: Job, evidence: FitEvidence) -> JobAnalysis:
    """One `job.analyse` call, held to the post, assembled with what WERA already knows.

    Raises the gateway's `ApiError` on failure; the caller decides whether a pack can go on
    without it (it can — see `drafting.step_cv`).
    """
    out = structured(
        session,
        "job.analyse",
        JobAnalysisOutput,
        {"job": _job_block(job)},
        context={"post": job.description_md or ""},
    )
    return JobAnalysis(
        role_profile=RoleProfile(
            target_role_title=job.title,
            department=out.role_profile.department,
            company_name=job.company,
            industry_name=out.role_profile.industry_name,
            experience_level=out.role_profile.experience_level,
        ),
        ats_keyword_matrix=keyword_matrix(evidence),
        strategic_context=StrategicContext.model_validate(out.strategic_context.model_dump()),
    )
