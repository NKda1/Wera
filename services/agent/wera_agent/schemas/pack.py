"""Pack documents (SPEC §6.6)."""

from typing import Literal

from pydantic import Field, model_validator

from .base import Model, Opt
from .cv_file import CvFileState
from .jobs import KeywordCategory
from .profile import Seniority


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
    # 18 Nov: a fact from the company's own site, quoted word for word from the page the company
    # brief read (`CompanyBrief.site`), and checked against that stored text. Empty without a brief.
    site_quote: str = ""


type BriefTopic = Literal["what_they_do", "who_they_serve", "size_or_stage", "values", "why_the_role"]


class BriefItem(Model):
    """One thing learned about the company, and the words it was learned from."""

    topic: BriefTopic
    text: str
    source: Literal["post", "site"]
    quote: str  # verbatim in the post, or in `CompanyBrief.site.text`


class CompanySource(Model):
    """The page a brief read, kept so its quotes can be checked again (and by the letter guard)."""

    url: str
    fetched_at: str
    text_hash: str
    text: str


class CompanyBrief(Model):
    """18 Nov: what the post and the company's own About page say about it, each item quoted.

    Items whose quote isn't in its source were dropped before this was saved. `site` is absent
    when WERA didn't read one, and `site_note` says why: an agency's post, a site whose
    robots.txt says no, one that couldn't be read, or no site found."""

    company: str
    items: list[BriefItem]
    site: Opt[CompanySource] = None
    site_note: Opt[str] = None
    model: str
    created_at: str


# The four segments of WERA's ATS letter template, in order. A three-paragraph letter folds the
# alignment into the close, which is why the close may follow either of the two before it.
type LetterSection = Literal[
    "opening_hook", "core_value_proposition", "strategic_alignment_and_culture", "call_to_action_and_close"
]
type HookType = Literal["Introduction", "Core-Metric", "Company-Mission", "Industry-Problem", "Referral"]


class Paragraph(Cited):
    """A letter paragraph, and which part of the template it is doing.

    Both optional, because a letter drafted before the template has no sections to report. A
    paragraph the person rewrites keeps its section: it is still the opening, or still the close.
    """

    section: Opt[LetterSection] = None
    theme: Opt[str] = None  # the competency a proof paragraph argues ("Shipping under constraint")


class CoverLetter(Model):
    job_id: str
    greeting: str
    paragraphs: list[Paragraph]
    signoff: str
    company_fact: CompanyFact
    word_count: int
    guard: GuardResult
    hook_type: Opt[HookType] = None


class AtsKeyword(Model):
    term: str
    weight: Literal["required", "nice"]
    matched: bool
    in_master_cv: bool
    category: Opt[KeywordCategory] = None
    # Whether the cover letter carries the term too. Absent on reports from before the letter was
    # measured. A parser reads the letter as well as the CV, and a letter written to the template
    # is meant to place the post's keywords — so whether it did is a measurement, not a hope.
    in_letter: Opt[bool] = None


class RoleProfile(Model):
    target_role_title: str
    department: str | None
    company_name: str
    industry_name: str | None
    experience_level: Seniority | None


class KeywordMatrix(Model):
    """The post's terms, grouped. The first three are the checkable keywords coverage measures;
    `soft_skills_and_culture` is display and letter context, and never counted."""

    hard_skills: list[str]
    methodologies_and_frameworks: list[str]
    tools_and_software: list[str]
    soft_skills_and_culture: list[str]


class StrategicContext(Model):
    """Why this hire exists, in the post's own words. Every field is a verbatim excerpt of the
    post or null — see `llm_outputs.QuotedContext`, which is what enforces it."""

    primary_job_requirement: str | None
    company_initiative_or_market: str | None
    company_problem_to_solve: str | None
    upcoming_company_goal: str | None
    company_value_or_pillar: str | None


class JobAnalysis(Model):
    """The job-description rubric, filled in for the job a pack is written for.

    Assembled in `nodes.analyse` from three sources so that each part comes from the one that can
    be trusted with it: the role title and company are WERA's own record of the job, the matrix
    is `keywords.extract`'s (already held to the post), and only the department, industry,
    seniority and strategic context are read by `job.analyse`.
    """

    role_profile: RoleProfile
    ats_keyword_matrix: KeywordMatrix
    strategic_context: StrategicContext


class FormatCheck(Model):
    id: str
    pass_: bool = Field(alias="pass")
    message: str


class AtsPart(Model):
    """One part of the ATS score (ATSC-07): its points out of its share of 100, and why."""

    id: Literal["keywords", "evidence", "alignment", "structure", "length"]
    label: str
    points: float
    max: float
    detail: str


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
    # The same sum over the letter's text, for the keywords the CV can back — the only ones the
    # letter may use (`nodes.letter_plan`). Absent when there was no letter to measure.
    letter_coverage: Opt[float] = None
    format_checks: list[FormatCheck]
    pages: float
    # ATSC-07 (14 Nov): a 0–100 score in five parts, computed in code (`nodes.ats.ats_score`),
    # and the score this CV could reach if every keyword it backs were placed — the gap between
    # them is presentation, the gap above `best_score` is skills the CV doesn't have (Upskills).
    # Absent on packs from before.
    score: Opt[float] = None
    best_score: Opt[float] = None
    parts: list[AtsPart] = []
    # Keywords the post asks for that the CV backs in a line but doesn't name as a skill: adding
    # them to the CV's skills is the applicant's call, and the cheapest way to raise the score.
    suggested_skills: list[str] = []

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
    # Absent on packs drafted before the rubric existed, and on any whose `job.analyse` call
    # failed: the analysis informs the letter, so its absence weakens a pack rather than breaking it.
    analysis: Opt[JobAnalysis] = None
    # EXP-01 (18 Nov): the theme of the RenderCV file the CV PDF is rendered from, and whether
    # the person rewrote it. Absent until they change either; the file itself is read separately.
    cv_file: Opt[CvFileState] = None
    # 18 Nov: the company brief, when the person asked for one. The letter is written with it when
    # it exists at drafting time, or rewritten with it on request.
    company: Opt[CompanyBrief] = None
