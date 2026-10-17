"""Citation guard (SPEC §8.5): every model-written line must be traceable to the master-CV
entries it cites. Runs before anything reaches the review screen."""

import re

from ..schemas.cv import MasterCV
from ..schemas.pack import Cited, GuardResult, Violation
from .vocab import cv_line_index, skill_text, text_has_term

# Terms the guard watches for: master skills, aliases, job keywords and a common tech list.
COMMON_TECH = ["Docker", "Kubernetes", "AWS", "GCP", "Azure", "Rust", "Java", "React", "Django", "Kafka", "Terraform"]

NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s?%?")


def check_cited(items: list[tuple[str, Cited]], cv: MasterCV, job_keywords: list[str] | None = None) -> GuardResult:
    lines = cv_line_index(cv)
    master_skills = " · ".join(skill_text(s) for s in cv.skills)
    vocabulary = [*[t for s in cv.skills for t in [s.name, *(s.aliases or [])]], *(job_keywords or []), *COMMON_TECH]
    violations: list[Violation] = []

    for location, item in items:
        if item.edited_by_user:
            continue  # G2-02: the user is the authority on their own edits
        if not item.source_ids:
            violations.append(Violation(location=location, rule="sources", detail="No master-CV source cited"))
            continue
        missing = [i for i in item.source_ids if i not in lines]
        if missing:
            violations.append(Violation(location=location, rule="sources", detail=f"Unknown source id: {', '.join(missing)}"))
            continue
        source_text = "\n".join(lines[i]["text"] for i in item.source_ids)

        for n in NUMBER.findall(item.text):
            if n.strip() not in source_text:
                violations.append(Violation(location=location, rule="numbers", detail=f'"{n.strip()}" is not in the cited lines'))
        for term in vocabulary:
            if text_has_term(item.text, term) and not text_has_term(source_text, term) and not text_has_term(master_skills, term):
                violations.append(
                    Violation(location=location, rule="terms", detail=f'"{term}" is not in the cited lines or your skills')
                )
    return GuardResult(pass_=not violations, violations=violations, repaired=False)
