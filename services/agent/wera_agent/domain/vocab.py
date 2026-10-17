"""Keyword normalisation (ATSC-01): a synonym map so "Postgres" counts as PostgreSQL."""

import re
from functools import lru_cache

from ..schemas.cv import MasterCV, Skill

SYNONYMS: dict[str, list[str]] = {
    "postgresql": ["postgresql", "postgres"],
    "llms": ["llm", "llms", "large language model", "large language models"],
    "agents": ["agent", "agents", "agentic"],
    "pii": ["pii", "personal data"],
    "rest apis": ["rest api", "rest apis", "restful"],
    "guardrails": ["guardrail", "guardrails"],
    "prompting": ["prompting", "prompt engineering", "prompt templates"],
    "next.js": ["next.js", "nextjs"],
    "node.js": ["node.js", "nodejs", "node api"],
    "c# / .net": ["c#", ".net"],
    "model evaluation": ["model evaluation", "llm evaluation", "evals"],
    "docker": ["docker"],
    "rag": ["rag", "retrieval-augmented"],
    "ci/cd": ["ci/cd", "continuous integration"],
    "go": ["golang"],
}

# Short words that are also plain English only count when written as the proper noun.
CASE_SENSITIVE: dict[str, re.Pattern[str]] = {"go": re.compile(r"(^|[^A-Za-z0-9])Go($|[^A-Za-z0-9])")}


def aliases_for(term: str) -> list[str]:
    key = term.lower().strip()
    return SYNONYMS.get(key, [key])


@lru_cache(maxsize=4096)
def _alias_re(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(^|[^a-z0-9]){re.escape(alias)}($|[^a-z0-9])")


def text_has_term(text: str, term: str) -> bool:
    """Whole-word, case-insensitive match of a term or any of its synonyms."""
    cs = CASE_SENSITIVE.get(term.lower())
    if cs and cs.search(text):
        return True
    haystack = text.lower()
    return any(_alias_re(alias).search(haystack) for alias in aliases_for(term))


def skill_text(skill: Skill) -> str:
    return " · ".join([skill.name, *(skill.aliases or [])])


def master_cv_text(cv: MasterCV) -> str:
    """Everything the master CV says, for "is this keyword backed by the CV?" checks."""
    bullets = [f"{b.text} {' '.join(b.tags)}" for r in [*cv.experience, *cv.projects] for b in r.bullets]
    return "\n".join(
        [cv.person.headline or "", cv.summary.text if cv.summary else "", *bullets, *[skill_text(s) for s in cv.skills]]
    )


def cv_line_index(cv: MasterCV) -> dict[str, dict[str, str]]:
    """Every bullet in the CV, keyed by id, plus the summary."""
    out: dict[str, dict[str, str]] = {}
    if cv.summary:
        out[cv.summary.id] = {"text": cv.summary.text, "where": "Summary"}
    for role in [*cv.experience, *cv.projects]:
        for b in role.bullets:
            out[b.id] = {"text": b.text, "where": f"{role.title} — {role.org}"}
    for ed in cv.education:
        for b in ed.notes or []:
            out[b.id] = {"text": b.text, "where": ed.degree}
    return out
