"""Degree requirements against the CV's education (UPSK-05, 20 Nov).

A post's "Bachelor's degree in Business Administration" is an education requirement, not a skill:
on 20 Nov Upskills listed "Bachelor’s Degree" and "Business Administration" as gaps to a CV whose
education reads "BSc (Hons) Economics and Finance". Nothing in WERA modelled a degree at all — the
terms were matched as skills, a BSc never says "bachelor's", and an Economics degree never says
"Business Administration", so both were "missing".

This module reads a degree's *level* from how a CV writes it (BSc, MEng, PhD…), and judges a
post's ask (`EducationAsk`, from `keywords.extract`) as met, a level gap or a subject gap. Subjects
are compared by family, the way a recruiter reads "a degree in Business, Economics or a related
field": an Economics graduate has a business degree for that purpose, and a Physics graduate has a
quantitative one.
"""

import re
from typing import Literal

from ..schemas.cv import MasterCV
from ..schemas.jobs import EducationAsk
from .vocab import normalise_term

type Level = Literal["none", "bachelor", "master", "phd", "professional"]
type Verdict = Literal["met", "level_gap", "subject_gap"]

RANK: dict[str, int] = {"none": 0, "bachelor": 1, "master": 2, "phd": 3}

# How CVs write each level, most senior first so "MPhil" is a master's and "Integrated Masters
# (MEng)" isn't read as a bachelor's for its "Hons". Matched on the degree line only, never on
# free text, where "MA" and "BA" are ordinary letters.
_LEVELS: list[tuple[str, re.Pattern[str]]] = [
    ("phd", re.compile(r"\b(ph\.?\s?d|d\.?\s?phil|doctorate|doctor of|eng\.?\s?d)\b")),
    (
        "master",
        re.compile(
            r"\b(m\.?\s?sc|msc|m\.?\s?a|m\.?\s?eng|meng|mba|m\.?\s?phil|llm|ll\.m|m\.?\s?res|m\.?\s?st|"
            r"m\.?\s?phys|m\.?\s?math|m\.?\s?chem|m\.?\s?sci|m\.?\s?arch|m\.?\s?ed|m\.?\s?s|master'?s?|masters)\b"
        ),
    ),
    (
        "bachelor",
        re.compile(
            r"\b(b\.?\s?sc|bsc|b\.?\s?a|b\.?\s?eng|beng|b\.?\s?com|bba|llb|ll\.b|b\.?\s?s|b\.?\s?ed|"
            r"b\.?\s?arch|a\.?\s?b|bachelor'?s?|hons|honours|undergraduate degree)\b"
        ),
    ),
]

# Words that make a post's keyword a degree requirement rather than a skill. Older fit reports
# (before `EducationAsk`) carry these in their keyword lists; Upskills drops them through
# `looks_like_degree` so the gap list is right without re-reading every post.
_DEGREE_WORDS = re.compile(
    r"\b(degree|bachelor'?s?|master'?s?|masters|ph\.?\s?d|doctorate|bsc|msc|mba|b\.?a\.|m\.?a\.|beng|meng|"
    r"undergraduate|postgraduate|graduate degree|a[- ]levels?|gcses?|diploma)\b"
)

# Fields of study, grouped as recruiters group them. A subject belongs to every family it names;
# two subjects match when they share a family. "related field" and its kin match the CV's own
# subject families, which is what a post means by them.
FAMILIES: dict[str, tuple[str, ...]] = {
    "business": (
        "business", "business administration", "business management", "business studies", "management",
        "commerce", "economics", "finance", "accounting", "accountancy", "marketing", "international business",
        "financial economics", "banking", "actuarial science",
    ),
    "quantitative": (
        "mathematics", "maths", "math", "statistics", "physics", "economics", "econometrics", "finance",
        "financial mathematics", "financial engineering", "actuarial science", "engineering", "computer science",
        "data science", "operational research", "operations research", "quantitative", "stem",
    ),
    "computing": (
        "computer science", "computing", "software engineering", "information technology", "information systems",
        "data science", "artificial intelligence", "machine learning", "computer engineering", "cybersecurity",
        "mathematics", "electrical engineering", "electronic engineering",
    ),
    "engineering": (
        "engineering", "mechanical engineering", "electrical engineering", "electronic engineering",
        "civil engineering", "chemical engineering", "aerospace engineering", "physics", "stem",
    ),
    "law": ("law", "legal studies", "jurisprudence"),
    "life sciences": (
        "biology", "biochemistry", "chemistry", "biomedical science", "pharmacology", "pharmacy", "medicine",
        "nursing", "life sciences", "neuroscience", "psychology",
    ),
    "humanities": (
        "english", "history", "philosophy", "politics", "international relations", "languages", "linguistics",
        "geography", "sociology", "psychology", "communications", "journalism", "media studies",
    ),
}  # fmt: skip

# A subject that means "whatever family the applicant's degree is in".
_ANY_RELATED = re.compile(r"\b(related|relevant|similar|equivalent|appropriate|any)\b")


def level_of(degree: str) -> str:
    """The level of one degree line as a CV writes it: "BSc (Hons) Economics" → "bachelor"."""
    text = normalise_term(degree)
    return next((level for level, pattern in _LEVELS if pattern.search(text)), "none")


def cv_level(cv: MasterCV) -> str:
    """The highest level the CV's education shows; "none" when it shows no degree."""
    return max((level_of(e.degree) for e in cv.education), key=lambda lvl: RANK.get(lvl, 0), default="none")


def families(subject: str) -> set[str]:
    """Every family a subject belongs to, by any family member it names ("BSc Economics and
    Finance" is business and quantitative)."""
    text = normalise_term(subject)
    return {
        name
        for name, members in FAMILIES.items()
        if any(re.search(rf"(^|[^a-z]){re.escape(m)}($|[^a-z])", text) for m in members)
    }


def _subject_met(subjects: list[str], cv: MasterCV) -> bool:
    degrees = [e.degree for e in cv.education if level_of(e.degree) != "none"] or [e.degree for e in cv.education]
    own = set().union(*(families(d) for d in degrees)) if degrees else set()
    for subject in subjects:
        wanted = normalise_term(subject)
        if any(wanted and wanted in normalise_term(d) for d in degrees):
            return True
        if families(subject) & own:
            return True
        if _ANY_RELATED.search(wanted) and own:
            return True
    return False


def meets(ask: EducationAsk | None, cv: MasterCV) -> Verdict | None:
    """Whether the CV's education answers the post's ask. None when the post asks for nothing a
    degree can answer (no level, no subject, or a professional qualification — those are keywords)."""
    if ask is None or (ask.level in ("none", "professional") and not ask.subjects):
        return None
    if ask.level in RANK and RANK[cv_level(cv)] < RANK[ask.level]:
        return "level_gap"
    if ask.subjects and not _subject_met(ask.subjects, cv):
        return "subject_gap"
    return "met"


def looks_like_degree(term: str) -> bool:
    """A keyword that is a degree or a bare field of study, not a skill: "Bachelor’s Degree",
    "MSc", "Business Administration". Skills that share words with a subject ("Financial
    modelling", "Machine learning", "Data science") are not fields of study and stay."""
    text = normalise_term(term)
    return bool(_DEGREE_WORDS.search(text)) or text in _FIELD_NAMES


# Subjects that, standing alone as a post's keyword, name a field of study rather than a skill.
# "Finance", "Accounting", "Data science" and "Machine learning" are not here: a post asking for
# them is asking for the work, and a CV that does it says so.
_FIELD_NAMES = frozenset(
    {
        "business administration", "business management", "business studies", "international business",
        "computer science", "information technology", "information systems", "financial economics",
        "economics", "mathematics", "maths", "physics", "statistics", "engineering", "law", "stem",
        "mechanical engineering", "electrical engineering", "electronic engineering", "civil engineering",
        "chemical engineering", "aerospace engineering", "computer engineering", "international relations",
        "life sciences", "biomedical science", "legal studies", "media studies", "operational research",
        "operations research", "a quantitative discipline", "quantitative discipline", "related field",
    }
)  # fmt: skip


def looks_like_vague_subject(subject: str) -> bool:
    """A subject that names no programme: "a related field", "a quantitative discipline"."""
    text = normalise_term(subject)
    return bool(_ANY_RELATED.search(text)) or text in {
        "stem",
        "quantitative",
        "a quantitative discipline",
        "quantitative discipline",
        "numerate",
        "a numerate degree",
    }


def programme_name(level: str, subject: str) -> str:
    """How a programme reads on the page: "Master's in Finance"."""
    label = {"bachelor": "Bachelor's", "master": "Master's", "phd": "PhD"}.get(level, "Degree")
    return f"{label} in {subject.strip()}"
