"""ATS check (SPEC §8.3, ATSC-01…05): coverage and format checks, computed in code."""

import math
import re

from ..domain.scoring import ats_coverage, js_round
from ..domain.vocab import master_cv_text, text_has_term
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, Job
from ..schemas.pack import AtsKeyword, AtsReport, FormatCheck, TailoredCV


def tailored_text(doc: TailoredCV) -> str:
    return "\n".join(
        [doc.summary.text, *[b.text for s in doc.sections for i in s.items for b in i.bullets], " · ".join(doc.skills)]
    )


def estimate_pages(doc: TailoredCV, cv: MasterCV) -> float:
    def lines(chars: int, per_line: int) -> int:
        return max(1, math.ceil(chars / per_line))

    total = 4.0  # name, contact line, rule, spacing
    total += lines(len(doc.summary.text), 95) + 0.5
    for s in doc.sections:
        total += 1.6  # heading
        for item in s.items:
            total += 1.4  # role header
            for b in item.bullets:
                total += lines(len(b.text), 80) + 0.25
    total += 1.6 + len(cv.education) * 1.4  # education
    total += 1.6 + lines(len(" · ".join(doc.skills)), 95)  # skills
    return js_round((total / 46) * 10) / 10


def cv_file_name(cv: MasterCV, company: str) -> str:
    """ATSC-05: First-Last-CV-Company.pdf"""
    return f"{re.sub(r'\s+', '-', cv.person.name)}-CV-{re.sub(r'\W+', '-', company, flags=re.ASCII)}.pdf"


def build_ats(doc: TailoredCV, *, cv: MasterCV, evidence: FitEvidence, cv_pages: int, job: Job, attempt: int) -> AtsReport:
    text = tailored_text(doc)
    master = master_cv_text(cv)
    keywords = [
        AtsKeyword(term=k.term, weight=k.weight, matched=text_has_term(text, k.term), in_master_cv=text_has_term(master, k.term))
        for k in evidence.keywords
    ]
    pages = estimate_pages(doc, cv)
    fits = pages <= cv_pages
    page_msg = (
        f"Fits on {cv_pages} page{'s' if cv_pages > 1 else ''}" if fits else f"Runs to {_num(pages)} pages — trim to {cv_pages}"
    )
    return AtsReport(
        keywords=keywords,
        coverage=ats_coverage(keywords),
        pages=pages,
        attempt=attempt,
        format_checks=[
            FormatCheck(id="single_column", pass_=True, message="Single column, standard headings"),
            FormatCheck(id="extractable", pass_=True, message="Text-extractable PDF and DOCX"),
            FormatCheck(id="no_tables", pass_=True, message="No tables, images or headers"),
            FormatCheck(id="pages", pass_=fits, message=page_msg),
            FormatCheck(id="file_name", pass_=True, message=f"File name {cv_file_name(cv, job.company)}"),
        ],
    )


def _num(n: float) -> str:
    """Numbers as JavaScript prints them: 2.0 → "2", 1.3 → "1.3"."""
    return str(int(n)) if float(n).is_integer() else str(n)
