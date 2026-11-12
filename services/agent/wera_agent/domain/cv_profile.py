"""The CV rubric's computed half — everything that can be read off the master CV without a model.

Metric isolation is the main reason this is code. The rubric asks for "quantifiable
achievements (percentages, revenue, time saved, team size)", and a number either is in a line
or it isn't: asking a model to find them is asking it to be right about something a regex
cannot get wrong, with the risk that it rounds, converts or adds one along the way.
"""

import re

from ..schemas.cv import MasterCV, Role
from ..schemas.cv_profile import (
    Achievement,
    CoreCompetencies,
    CvProfile,
    Identity,
    ImpliedAchievement,
    NarrativeBridges,
    RoleHistory,
    Trait,
)
from ..schemas.llm_outputs import CvProfileOutput
from .vocab import text_has_term

_UNITS = (
    r"%|percent\b|x\b|×|k\b|m\b|bn\b|ms\b|"
    r"(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?|"
    r"users?|customers?|clients?|people|engineers?|developers?|teams?|"
    r"requests?|transactions?|pages?|countries|languages|stores?|sites?|"
    r"services?|products?|projects?|clients?|accounts?|cases?|reports?)\b"
)
# A number not glued to a word ("GPT-4", "S3", "C++17" are names, not results), with an optional
# currency in front and a unit after.
METRIC = re.compile(rf"(?<![\w-])(?:[£$€]\s?)?\d[\d,]*(?:\.\d+)?(?:\s?(?:{_UNITS}))?", re.IGNORECASE)
_YEAR = re.compile(r"(?:19|20)\d{2}")
_BARE = re.compile(r"[\d,.]+")
_PREVIOUS_WORD = re.compile(r"(\S+)\s+$")


def metrics_in(text: str) -> list[str]:
    """The numbers a line states as results, in order.

    Two kinds of number are not results. A bare year is a date. A bare number straight after a
    name in the middle of a line is a version — "Python 3.11", "Java 17" — and reading it as an
    achievement would offer "3.11" to the letter as something the applicant accomplished.
    """
    out: list[str] = []
    for m in METRIC.finditer(text):
        span = m.group(0).strip()
        if _YEAR.fullmatch(span):
            continue
        before = _PREVIOUS_WORD.search(text[: m.start()])
        if _BARE.fullmatch(span) and before and before.start() > 0 and before.group(1)[:1].isupper():
            continue
        out.append(span)
    return out


def _tools(role: Role, cv: MasterCV) -> list[str]:
    """The CV's own skills that this role's lines mention — tools named in the work, not claimed."""
    text = "\n".join(b.text for b in role.bullets)
    return [s.name for s in cv.skills if any(text_has_term(text, t) for t in [s.name, *(s.aliases or [])])]


def _duration(role: Role) -> str:
    return f"{role.start} – {role.end}" if role.start else role.end


def _link(cv: MasterCV, *words: str) -> str | None:
    return next((link.url for link in cv.person.links if any(w in f"{link.label} {link.url}".lower() for w in words)), None)


def _identity(cv: MasterCV) -> Identity:
    city, _, rest = cv.person.location.partition(",")
    return Identity(
        applicant_name=cv.person.name,
        applicant_linkedin_url=_link(cv, "linkedin"),
        applicant_portfolio_url=_link(cv, "portfolio", "website", "personal site"),
        applicant_city=city.strip() or None,
        applicant_state=rest.strip() or None,
    )


def build_profile(cv: MasterCV, judged: CvProfileOutput | None) -> CvProfile:
    """The whole rubric: computed parts always, the model's parts when it has them.

    `judged` must already have been validated against this CV — `CvProfileOutput` drops anything
    the CV doesn't back, which is the only reason its parts can be merged in unexamined here.
    """
    implied = judged.implied_metrics if judged else []
    history = [
        RoleHistory(
            role_id=role.id,
            company_name=role.org,
            role_title=role.title,
            duration=_duration(role),
            quantifiable_achievements=[
                Achievement(bullet_id=b.id, text=b.text, metrics=found) for b in role.bullets if (found := metrics_in(b.text))
            ],
            # A line that already states a number has its result; "implied" is for the ones that don't.
            implied_achievements=[
                ImpliedAchievement(bullet_id=m.bullet_id, phrase=m.phrase)
                for m in implied
                if any(b.id == m.bullet_id and not metrics_in(b.text) for b in role.bullets)
            ],
            utilized_tools_and_frameworks=_tools(role, cv),
        )
        for role in [*cv.experience, *cv.projects]
    ]
    trait = judged.strongest_cross_functional_trait if judged else None
    return CvProfile(
        cv_version=cv.version,
        identity=_identity(cv),
        core_competencies=CoreCompetencies(
            primary_domains=judged.primary_domains if judged else [],
            technical_stack=[s.name for s in cv.skills],
            methodologies=judged.methodologies if judged else [],
        ),
        professional_history=history,
        potential_narrative_bridges=NarrativeBridges(
            strongest_cross_functional_trait=Trait(trait=trait.trait, bullet_ids=trait.bullet_ids) if trait else None
        ),
        judged=judged is not None,
    )
