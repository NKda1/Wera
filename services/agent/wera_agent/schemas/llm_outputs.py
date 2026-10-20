"""What a model is allowed to return (SPEC §9.3).

These are deliberately narrower than the stored contracts: they carry only what the model
writes, so anything computed in code can't be faked by a model. The fit total is the clearest
case — `FitScoreOutput` has the four sub-scores and no total at all (MATCH-04).

Every constraint that matters lives on the model, so `with_structured_output(strict=True)`
sends it to the provider as JSON Schema *and* Pydantic re-checks it on the way back. Checks
that need the master CV take it through validation context:

    TailorOutput.model_validate(data, context={"cv": cv})
"""

from typing import Annotated, Any, Literal

from pydantic import ValidationInfo, field_validator

from .base import Model, Opt, check, count, fail, required
from .jobs import JobKeyword, Location, RemoteMode, Salary, SubScore
from .profile import Recency, Seniority

Score = Annotated[float, check(lambda v: 0 <= v <= 10, "range", "Scores run from 0 to 10")]
# A provider's strict mode ignores `maxLength`, so this limit has to be in the prompt as well —
# `llm.prompts` imports it from here so the two cannot drift.
REASON_LIMIT = 200
Reason = Annotated[str, check(lambda v: len(v) <= REASON_LIMIT, "max", f"Keep the reason under {REASON_LIMIT} characters")]
Term = Annotated[str, required("This can't be empty")]


def _cv(info: ValidationInfo) -> Any | None:
    """The master CV, when the caller passed one as validation context."""
    return (info.context or {}).get("cv") if info.context else None


def _known_bullet_ids(cv: Any) -> set[str]:
    return (
        {b.id for r in [*cv.experience, *cv.projects] for b in r.bullets}
        | {b.id for e in cv.education for b in (e.notes or [])}
        | ({cv.summary.id} if cv.summary else set())
    )


def _known_skills(cv: Any) -> set[str]:
    out: set[str] = set()
    for skill in cv.skills:
        out.add(skill.name.casefold())
        out.update(a.casefold() for a in (skill.aliases or []))
    return out


class Cites(Model):
    """A written line and the master-CV entries it came from (TAIL-03)."""

    text: Annotated[str, required("The line can't be empty")]
    source_ids: Annotated[list[str], count(1, None, "Every line must cite at least one master-CV entry")]

    @field_validator("source_ids")
    @classmethod
    def _ids_exist(cls, v: list[str], info: ValidationInfo) -> list[str]:
        """§8.5 step 2, as a schema check so a bad citation never reaches the guard."""
        cv = _cv(info)
        if cv is not None:
            unknown = [i for i in v if i not in _known_bullet_ids(cv)]
            if unknown:
                raise fail("source_ids", f"Not in the master CV: {', '.join(unknown)}")
        return v


# ---- profile.generate (PRF-01) ----


class ProfileProposal(Model):
    """A proposed search profile. Channels, sites and timestamps are set in code, not here."""

    titles: Annotated[list[Term], count(1, 8, "Propose at least 1 title", "Propose at most 8 titles")]
    skills: Annotated[list[Term], count(1, 15, "Propose at least 1 skill", "Propose at most 15 skills")]
    locations: Annotated[list[Term], count(1, 8, "Propose at least 1 location", "Propose at most 8 locations")]
    remote: Literal["any", "remote_only", "no_remote"]
    seniority: list[Seniority]
    exclude: list[Term]
    recency: Recency

    @field_validator("skills")
    @classmethod
    def _skills_from_cv(cls, v: list[Term], info: ValidationInfo) -> list[Term]:
        """A profile skill has to be a skill the CV claims; the model can't add to it."""
        cv = _cv(info)
        if cv is not None:
            known = _known_skills(cv)
            unknown = [s for s in v if s.casefold() not in known]
            if unknown:
                raise fail("skills", f"Not in the master CV's skills: {', '.join(unknown)}")
        return v


# ---- match.verdict and fit.score ----


class MatchVerdict(Model):
    verdict: Literal["strong", "possible", "weak"]
    reason: Reason
    job_quote: str


class FitScoreOutput(Model):
    """MATCH-04: four sub-scores with evidence. The total is computed in code (§8.2)."""

    core: SubScore
    adjacent: SubScore
    experience: SubScore
    eligibility: SubScore
    reason: Reason
    job_quote: str
    cv_bullet_ids: list[str]

    @field_validator("core", "adjacent", "experience", "eligibility")
    @classmethod
    def _in_range(cls, v: SubScore) -> SubScore:
        if not 0 <= v.score <= 10:
            raise fail("range", "Sub-scores run from 0 to 10")
        return v


# ---- keywords.extract (ATSC-01) ----


class KeywordList(Model):
    keywords: Annotated[list[JobKeyword], count(0, 25, "", "At most 25 keywords")]


# ---- cv.tailor, letter.write, answers.draft ----


class TailorItem(Model):
    role_id: Annotated[str, required("Name the role this belongs to")]
    bullets: Annotated[list[Cites], count(1, None, "A role needs at least one line")]


class TailorOutput(Model):
    summary: Cites
    experience: list[TailorItem]
    projects: list[TailorItem]
    skills: list[Term]
    omitted_bullet_ids: list[str]

    @field_validator("skills")
    @classmethod
    def _skills_from_cv(cls, v: list[Term], info: ValidationInfo) -> list[Term]:
        """TAIL-01: the tailored CV may reorder and drop, never invent."""
        cv = _cv(info)
        if cv is not None:
            known = _known_skills(cv)
            unknown = [s for s in v if s.casefold() not in known]
            if unknown:
                raise fail("skills", f"Not in the master CV's skills: {', '.join(unknown)}")
        return v

    @field_validator("experience", "projects")
    @classmethod
    def _roles_exist(cls, v: list[TailorItem], info: ValidationInfo) -> list[TailorItem]:
        cv = _cv(info)
        if cv is not None:
            known = {r.id for r in [*cv.experience, *cv.projects]}
            unknown = [i.role_id for i in v if i.role_id not in known]
            if unknown:
                raise fail("role_id", f"Not a role in the master CV: {', '.join(unknown)}")
        return v


class LetterOutput(Model):
    greeting: Annotated[str, required("Add a greeting")]
    paragraphs: Annotated[list[Cites], count(3, 4, "Write 3 or 4 paragraphs", "Write 3 or 4 paragraphs")]
    signoff: Annotated[str, required("Add a sign-off")]
    company_quote: str  # LTR-03: one fact quoted word for word from the post


class AnswerDraft(Model):
    id: Annotated[str, required("Name the question this answers")]
    answer: str | None  # null = "your answer needed" (ANS-02/03)
    source_ids: list[str]

    @field_validator("source_ids")
    @classmethod
    def _cited_when_answered(cls, v: list[str], info: ValidationInfo) -> list[str]:
        if info.data.get("answer") and not v:
            raise fail("source_ids", "An answer must cite the master CV, or be left for the person")
        return v


class AnswersOutput(Model):
    answers: list[AnswerDraft]


# ---- guard.repair (§8.5 step 6) ----


class RepairOutput(Model):
    """One repaired item, or null when the model can't fix it from the cited sources."""

    item: Cites | None


# ---- job.normalise and email.extract ----


class NormalisedJob(Model):
    title: Annotated[str, required("Add a title")]
    company: Annotated[str, required("Add a company")]
    location: Location
    remote: RemoteMode | None
    salary: Opt[Salary] = None
    language: str
    seniority: Opt[Seniority] = None
    posted_at: Opt[str] = None


class JobCard(Model):
    """A job spotted in an alert email, before the full post is fetched."""

    title: Annotated[str, required("Add a title")]
    company: Annotated[str, required("Add a company")]
    location_raw: str
    url_token: Opt[str] = None  # MAIL-05: links are replaced by tokens before the call


class JobCardList(Model):
    cards: list[JobCard]


# ---- outreach.draft ----


class OutreachDraft(Model):
    note: str
    follow_up: str
