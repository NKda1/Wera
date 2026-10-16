"""Pack documents (SPEC §6.6)."""

from typing import Literal

from pydantic import Field

from .base import Model, Opt


class Cited(Model):
    text: str
    source_ids: list[str]
    edited_by_user: Opt[bool] = None  # G2-02


class Violation(Model):
    location: str
    rule: str
    detail: str


class GuardResult(Model):
    pass_: bool = Field(alias="pass")
    violations: list[Violation]
    repaired: bool


class CvItem(Model):
    role_id: str
    bullets: list[Cited]


class CvSection(Model):
    kind: Literal["experience", "projects"]
    items: list[CvItem]


class TailoredCV(Model):
    job_id: str
    cv_version: int
    summary: Cited
    sections: list[CvSection]
    skills: list[str]
    omitted_bullet_ids: list[str]
    guard: GuardResult


class CompanyFact(Model):
    quote: str


class CoverLetter(Model):
    job_id: str
    greeting: str
    paragraphs: list[Cited]
    signoff: str
    company_fact: CompanyFact
    word_count: int
    guard: GuardResult


class AtsKeyword(Model):
    term: str
    weight: Literal["required", "nice"]
    matched: bool
    in_master_cv: bool


class FormatCheck(Model):
    id: str
    pass_: bool = Field(alias="pass")
    message: str


class AtsReport(Model):
    keywords: list[AtsKeyword]
    coverage: float  # 0–1
    format_checks: list[FormatCheck]
    pages: float
    attempt: int


class AnswerItem(Model):
    id: str
    question: str
    answer: str | None  # None = "Your answer needed" (ANS-02/03)
    source_ids: list[str]
    sensitive: bool


class Pack(Model):
    cv: TailoredCV
    letter: CoverLetter
    ats: AtsReport
    answers: list[AnswerItem]
