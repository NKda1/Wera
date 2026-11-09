"""Pack documents (SPEC §6.6)."""

from typing import Literal

from pydantic import Field, model_validator

from .base import Model, Opt


class Cited(Model):
    text: str
    source_ids: list[str]
    edited_by_user: Opt[bool] = None  # G2-02
    # TAIL-07: this line was *written* from the lines it cites, not copied from one of them.
    # True for exactly one thing — the composed professional profile of a CV whose master has no
    # summary (`nodes.tailor._profile`) — and it exists because `Store._assert_verbatim` requires
    # every cited line to equal the master line it names, which a composed line can never do.
    #
    # Without it the two rules collide and the pack is lost: the eval measured `CV_DRIFTED` on 2
    # of 3 packs, with the third "passing" only because `summary.write` had been truncated into
    # the empty-profile fallback — so the feature either failed the save or didn't happen.
    #
    # Narrow on purpose. It exempts a line from the *verbatim* check only; `domain.guard.check_cited`
    # still holds it to its sources, and that is the right check for prose. A model cannot set it:
    # `SummaryDraft` has no such field, so the only writer is WERA's own code.
    composed: Opt[bool] = None


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
    # LTR-06: what the post says about how the company works, quoted verbatim. Empty when it says
    # nothing of the kind. Kept beside `quote` rather than merged into it because the review
    # screen checks both against the post, and because they answer different questions: one is
    # "they wrote this letter for us", the other is "they know why they want to work here".
    policy_quote: str = ""


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
    coverage: float  # 0–1, over every keyword the post asks for
    # The same sum over the keywords the master CV can actually back — in other words, how much of
    # what WERA *could* have covered it did cover. Defaults to `coverage` so a pack stored before
    # this field existed still reads.
    #
    # Two numbers because one was being read as the answer to two different questions. `coverage`
    # is the honest ATS answer and it is low whenever an applicant is a partial match, which is
    # most of the time — a post listing 14 technologies against a CV holding 3 scores 19% however
    # well WERA did its job, and next to a fit score of 8 that reads as a contradiction rather
    # than as "you can back 3 of these 14". `backed_coverage` is the half WERA is accountable for;
    # the gap between them is the applicant's to close, and `AtsKeyword.in_master_cv` says which
    # terms are in it.
    backed_coverage: float = -1.0
    format_checks: list[FormatCheck]
    pages: float
    attempt: int

    @model_validator(mode="after")
    def _default_backed(self) -> "AtsReport":
        if self.backed_coverage < 0:
            self.backed_coverage = self.coverage
        return self


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
