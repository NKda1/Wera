"""The candidate CV rubric: what the master CV says, sorted for matching and for the letter.

Derived, never edited. The master CV stays the one thing a person writes (CV-03); this is a
reading of it, rebuilt whenever it changes. Most of it is computed in code (`domain.cv_profile`)
and the rest is checked against the CV before it is kept (`llm_outputs.CvProfileOutput`).

No email or phone. Contact details live apart from the CV (CV-06) and enter a document only at
render time (EXP-02), so a profile that carried them would be a second copy with none of those
protections.
"""

from .base import Model


class Identity(Model):
    applicant_name: str
    applicant_linkedin_url: str | None
    applicant_portfolio_url: str | None
    applicant_city: str | None
    applicant_state: str | None


class CoreCompetencies(Model):
    primary_domains: list[str]
    technical_stack: list[str]  # the CV's own skills list, in its order
    methodologies: list[str]


class Achievement(Model):
    """A line that states a number, with the numbers isolated from it."""

    bullet_id: str
    text: str
    metrics: list[str]


class ImpliedAchievement(Model):
    """A line that implies a result without a number — for the applicant to quantify, if true."""

    bullet_id: str
    phrase: str


class RoleHistory(Model):
    role_id: str
    company_name: str
    role_title: str
    duration: str
    quantifiable_achievements: list[Achievement]
    implied_achievements: list[ImpliedAchievement]
    utilized_tools_and_frameworks: list[str]


class Trait(Model):
    trait: str
    bullet_ids: list[str]


class NarrativeBridges(Model):
    """`addressed_gaps` is not here: a gap is a gap against a particular post, so it belongs to
    the pack written for one, not to the CV."""

    strongest_cross_functional_trait: Trait | None


class CvProfile(Model):
    cv_version: int
    identity: Identity
    core_competencies: CoreCompetencies
    professional_history: list[RoleHistory]
    potential_narrative_bridges: NarrativeBridges
    # False when the model half couldn't be had (budget, provider, demo with no CV): the computed
    # half is still true, and the screen can say which parts are missing rather than show blanks.
    judged: bool
