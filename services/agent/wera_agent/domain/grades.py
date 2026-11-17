"""International organisations' grades: what level a post is, and who may apply (SRCH-34).

The UN common system writes both into one code. "P-2" is an early-career professional post,
recruited internationally — open to any nationality, and it moves people. "NO-B" is the same
level, recruited nationally — open to nationals of the duty station's country. "G-5" is general
service, recruited locally. A title rarely says any of this ("Associate Information Systems
Officer"), so the grade decides the level instead of the title's words (MATCH-01).

Written the way the organisations write them: WHO "P3", WIPO "NOD", the UN "P-3", ITU "Grade: P4".
"""

import re
from typing import Literal, NamedTuple

type Level = Literal["intern", "entry", "mid", "senior", "director"]
type Recruitment = Literal["international", "local"]


class Grade(NamedTuple):
    code: str  # written one way: "P-2", "NO-B", "G-5", "I-1"
    level: Level
    recruitment: Recruitment


# The level each grade is, by its family and step. P-1/P-2 are where graduates enter (with the
# Young Professionals Programme and JPOs); P-3 asks for about five years, P-4 seven, P-5 ten.
_GRADE = re.compile(r"\b(P|D|NO|G|GS|FS|I)[\s-]?([1-7]|[A-E])\b", re.I)
_LEVELS: dict[str, dict[str, Level]] = {
    "P": {"1": "entry", "2": "entry", "3": "mid", "4": "senior", "5": "senior"},
    "D": {"1": "director", "2": "director"},
    "NO": {"A": "entry", "B": "entry", "C": "mid", "D": "senior", "E": "senior"},
    "G": {"1": "entry", "2": "entry", "3": "entry", "4": "entry", "5": "entry", "6": "mid", "7": "mid"},
    "FS": {"1": "entry", "2": "entry", "3": "entry", "4": "mid", "5": "mid", "6": "senior", "7": "senior"},
    "I": {"1": "intern"},
}
_LOCAL = {"NO", "G", "GS"}
# Programmes that are a grade in all but name.
_PROGRAMMES = (
    (re.compile(r"\b(young professionals? programme|YPP)\b", re.I), Grade("YPP", "entry", "international")),
    (re.compile(r"\b(junior professional officer|JPO)\b", re.I), Grade("JPO", "entry", "international")),
)


def parse(text: str | None) -> Grade | None:
    """The grade a post's grade field or title gives, or None ("No grade", a consultancy)."""
    if not text:
        return None
    for match in _GRADE.finditer(text):
        family, step = match.group(1).upper(), match.group(2).upper()
        family = "G" if family == "GS" else family
        level = _LEVELS.get(family, {}).get(step)
        if level is not None:
            code = f"{family}-{step}"
            return Grade(code, level, "local" if family in _LOCAL else "international")
    for pattern, grade in _PROGRAMMES:
        if pattern.search(text):
            return grade
    return None


# A post with no grade can still say it is for nationals: WHO's "National Consultant", the UN's
# "National Professional Officer".
_NATIONAL = re.compile(r"\bnational\s+(consultant|professional|officer|expert|programme officer)", re.I)


def recruitment(title: str, grade: Grade | None, stated: str | None = None) -> Recruitment | None:
    """Who a post is open to: what the organisation states, else the grade, else the title."""
    said = (stated or "").strip().lower()
    if said in ("international", "local"):
        return said  # type: ignore[return-value]
    if grade is not None:
        return grade.recruitment
    if _NATIONAL.search(title):
        return "local"
    return None
