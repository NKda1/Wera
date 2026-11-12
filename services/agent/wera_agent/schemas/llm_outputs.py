"""What a model is allowed to return (SPEC §9.3).

These are deliberately narrower than the stored contracts: they carry only what the model
writes, so anything computed in code can't be faked by a model. The fit total is the clearest
case — `FitScoreOutput` has the four sub-scores and no total at all (MATCH-04).

Every constraint that matters lives on the model, so `with_structured_output(strict=True)`
sends it to the provider as JSON Schema *and* Pydantic re-checks it on the way back. Checks
that need the master CV take it through validation context:

    TailorSelection.model_validate(data, context={"cv": cv})

Strict JSON schema enforces *shape*, and some of what matters here is *content* — whether a line
is the applicant's own wording, whether a quote is really in the post. Where a constraint can be
made structural it is, because that is the only kind a model cannot get wrong: see
`TailorSelection`, which has no free-text field at all.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import ValidationInfo, field_validator, model_validator

from ..domain.verbatim import appears_in
from ..domain.vocab import in_post
from .base import Model, Opt, check, count, fail, required
from .jobs import Location, RemoteMode, Salary, SubScore
from .pack import HookType, LetterSection, StrategicContext
from .profile import Recency, Seniority

Score = Annotated[float, check(lambda v: 0 <= v <= 10, "range", "Scores run from 0 to 10")]
# A provider's strict mode ignores `maxLength`, so this limit has to be in the prompt as well —
# `llm.prompts` imports it from here so the two cannot drift.
REASON_LIMIT = 200
Reason = Annotated[str, check(lambda v: len(v) <= REASON_LIMIT, "max", f"Keep the reason under {REASON_LIMIT} characters")]
Term = Annotated[str, required("This can't be empty")]

# A bound on the letter's length, as a validator rather than a sentence in the prompt. Same
# reason as `REASON_LIMIT`: strict mode ignores `maxLength`, so a bound that is only described is
# a bound that is only suggested — every `fit.score` reply overran `reason` until the limit was
# enforced for real.
#
# **Wider than LTR-01's 250–350, deliberately.** That range is the target and it stays in
# `LETTER_WRITE`; this is the range outside which something has gone wrong. The two are not the
# same job, and conflating them is expensive: `llm.demo`'s reference letters — written before any
# of this and perfectly good letters — come in at 212–238 words, so a 250-word floor would have
# rejected every one of them and spent a retry doing it. A validator that fires on good output is
# worse than no validator, because it burns the attempt that a genuinely bad reply needs.
#
# So: a letter under 150 words is a stub, and over 500 is an essay. Either is drift worth one
# retry carrying the numbers back (§8.5 step 1), which is actionable in a way "write it well"
# is not.
# Moved with LTR-01's target (now 180–260): a stub is under 120, an essay over 400.
LETTER_MIN_WORDS = 120
LETTER_MAX_WORDS = 400


def _cv(info: ValidationInfo) -> Any | None:
    """The master CV, when the caller passed one as validation context."""
    return (info.context or {}).get("cv") if info.context else None


def _known_bullet_ids(cv: Any) -> set[str]:
    return (
        {b.id for r in [*cv.experience, *cv.projects] for b in r.bullets}
        | {b.id for e in cv.education for b in (e.notes or [])}
        | ({cv.summary.id} if cv.summary else set())
    )


def _known_skills(cv: Any) -> set[str]:
    out: set[str] = set()
    for skill in cv.skills:
        out.add(skill.name.casefold())
        out.update(a.casefold() for a in (skill.aliases or []))
    return out


class Cites(Model):
    """A written line and the master-CV entries it came from (TAIL-03)."""

    text: Annotated[str, required("The line can't be empty")]
    source_ids: list[str]

    @field_validator("source_ids")
    @classmethod
    def _drop_unknown_ids(cls, v: list[str], info: ValidationInfo) -> list[str]:
        """§8.5 step 2 — but as a filter, not a veto.

        This used to raise, which meant **one mistyped id threw away an entire letter**. A model
        writing four cited paragraphs has four chances to mangle an opaque id like `exp-2-b3`,
        and `letter.write` was failing its first attempt essentially every time: two calls and
        twice the wall clock for every cover letter, at 17–22 seconds each.

        Dropping instead loses nothing, because the guard behind this is *stricter* and works per
        paragraph rather than per letter. `domain.guard.check_cited` asks whether the text is
        actually carried by the lines it cites; a paragraph left with no sources fails that and
        is repaired or dropped, with the drop shown to the person (LTR-01). So an invented
        citation ends up in exactly the same place it did before — it just doesn't take three
        good paragraphs and twenty seconds with it on the way.

        Note what is deliberately *not* done here: `source_ids` no longer carries a minimum. An
        empty list is a paragraph the guard will reject, which is a better outcome than a retry.
        """
        cv = _cv(info)
        if cv is None:
            return v
        known = _known_bullet_ids(cv)
        return [i for i in v if i in known]


# ---- profile.generate (PRF-01) ----


class ProfileProposal(Model):
    """A proposed search profile. Channels, sites and timestamps are set in code, not here."""

    titles: Annotated[list[Term], count(1, 8, "Propose at least 1 title", "Propose at most 8 titles")]
    skills: Annotated[list[Term], count(1, 15, "Propose at least 1 skill", "Propose at most 15 skills")]
    locations: Annotated[list[Term], count(1, 8, "Propose at least 1 location", "Propose at most 8 locations")]
    remote: Literal["any", "remote_only", "no_remote"]
    seniority: list[Seniority]
    exclude: list[Term]
    recency: Recency

    @field_validator("skills")
    @classmethod
    def _skills_from_cv(cls, v: list[Term], info: ValidationInfo) -> list[Term]:
        """A profile skill has to be a skill the CV claims; the model can't add to it."""
        cv = _cv(info)
        if cv is not None:
            known = _known_skills(cv)
            unknown = [s for s in v if s.casefold() not in known]
            if unknown:
                raise fail("skills", f"Not in the master CV's skills: {', '.join(unknown)}")
        return v


# ---- cv.parse (CV-03) ----
#
# Turning an uploaded PDF or DOCX into a master CV. Two things are deliberately absent, and
# both are absent for the same reason — a model must not be able to break the citation guard:
#
# * **No ids.** Every bullet id is assigned in code (`domain.cv_build`), because ids are the
#   whole basis of §8.5: a tailored line is trusted because it cites one. A model that invented
#   its own could repeat one, and two different lines sharing an id makes "this claim came from
#   your CV" unverifiable. `put_cv` rejects duplicates, so a model inventing them would simply
#   fail the save — assigning them here means it cannot arise.
# * **No version.** That is the store's, incremented on save (CV-03).
#
# The text being parsed is a file somebody uploaded, so the prompt wraps it as untrusted data
# (SEC-08). The worst a CV carrying "ignore your instructions" can do is produce something that
# fails validation here, and the person reviews every field before anything is saved.


class ParsedRole(Model):
    """One job or project as the CV states it."""

    title: Annotated[str, required("Every role needs a title")]
    org: Annotated[str, required("Every role needs an employer or project name")]
    location: Opt[str] = None
    # Not `YearMonth`: a CV writes dates every way there is ("Sept 2024", "09/2024", "2024").
    # The model is asked for YYYY-MM and `cv_build.year_month` repairs what comes back, because
    # rejecting the whole CV over one badly written date would be a poor trade.
    start: str
    end: str  # YYYY-MM, or "present"
    bullets: list[str]


class ParsedEducation(Model):
    degree: Annotated[str, required("Every entry needs a degree")]
    institution: Annotated[str, required("Every entry needs an institution")]
    start: Opt[str] = None
    end: Opt[str] = None


class ParsedLink(Model):
    label: str
    url: str


class ParsedLanguage(Model):
    name: str
    level: str


class ParsedCv(Model):
    """A CV as read out of an uploaded file. Reviewed by the person before anything is saved."""

    name: Annotated[str, required("The CV must have a name on it")]
    headline: Opt[str] = None
    location: str
    links: list[ParsedLink]
    summary: Opt[str] = None
    experience: list[ParsedRole]
    projects: list[ParsedRole]
    education: list[ParsedEducation]
    skills: Annotated[list[Term], count(0, 40, "", "At most 40 skills")]
    languages: list[ParsedLanguage]


# ---- fit.score ----


class FitScoreOutput(Model):
    """MATCH-04: four sub-scores with evidence. The total is computed in code (§8.2)."""

    core: SubScore
    adjacent: SubScore
    experience: SubScore
    eligibility: SubScore
    reason: Reason
    job_quote: str
    cv_bullet_ids: list[str]

    @field_validator("core", "adjacent", "experience", "eligibility")
    @classmethod
    def _in_range(cls, v: SubScore) -> SubScore:
        if not 0 <= v.score <= 10:
            raise fail("range", "Sub-scores run from 0 to 10")
        return v


# ---- keywords.extract (ATSC-01) ----


KEYWORD_LIMIT = 25  # checkable keywords — what coverage divides by
CULTURE_LIMIT = 8  # soft skills and culture, display only


class ExtractedKeyword(Model):
    """One term from the post, sorted into the ATS keyword matrix.

    `soft_skill` exists so the model has somewhere to put "cross-functional collaboration" other
    than the checkable list. Asking it to leave soft skills out entirely meant they leaked in as
    `required` keywords; giving them their own bucket, split off in code, is what keeps them out
    of coverage (`fit.keywords_for`).
    """

    term: Term
    weight: Literal["required", "nice"]
    category: Literal["hard_skill", "methodology", "tool", "soft_skill"]


class KeywordList(Model):
    keywords: Annotated[
        list[ExtractedKeyword], count(0, KEYWORD_LIMIT + CULTURE_LIMIT, "", f"At most {KEYWORD_LIMIT + CULTURE_LIMIT} terms")
    ]

    @field_validator("keywords")
    @classmethod
    def _only_what_the_post_says(cls, v: list[ExtractedKeyword], info: ValidationInfo) -> list[ExtractedKeyword]:
        """A keyword the post doesn't contain is dropped, not argued with.

        A filter for the same reason `Cites` filters ids: one invented term ("Kubernetes", for a
        post that only says Docker) shouldn't cost the call its retry, when the other twenty are
        right and dropping it loses nothing. Without the post in context there is nothing to
        check against, so the list passes through — the read-back side checks it again.
        """
        post = (info.context or {}).get("post") if info.context else None
        return v if post is None else in_post(v, post)


# ---- job.analyse ----
#
# The parts of the job-description rubric a model has to read for. Title, company and the
# keyword matrix are not here: WERA already knows the first two, and the matrix is
# `keywords.extract`'s, so `nodes.analyse` assembles the stored `JobAnalysis` from all three.


class RoleProfileOutput(Model):
    department: str | None
    industry_name: str | None
    experience_level: Seniority | None


class QuotedContext(StrategicContext):
    """What the post says about why this hire exists — each one a quote from the post, or null.

    Quotes rather than summaries because the letter is written from these (LTR-06), and the
    company half of a letter is where a model most wants to invent: there is no CV to hold it to
    there, only the post. A summary can't be checked; a quote can.
    """

    @model_validator(mode="after")
    def _quotes_from_the_post(self, info: ValidationInfo) -> "QuotedContext":
        """An excerpt the post doesn't contain becomes null — the honest answer, and the one the
        rubric asks for when a field can't be read off the text."""
        post = (info.context or {}).get("post") if info.context else None
        if post is None:
            return self
        for name in type(self).model_fields:
            if not appears_in(getattr(self, name), post):
                setattr(self, name, None)
        return self


class JobAnalysisOutput(Model):
    role_profile: RoleProfileOutput
    strategic_context: QuotedContext


# ---- strategist.plan ----
#
# The Strategist's reply, and the checks that keep it to what it was asked. It may rank the
# boards it was shown and propose a few it wasn't; it may not invent a market the person isn't
# looking in, put a board in a market it doesn't serve, or turn a search term into a query
# language (`"`, `OR`) that would change what a board search asks.

_HOST = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_OPERATORS = re.compile(r'["()]|\b(?:OR|AND|NOT)\b')
_LANG = re.compile(r"[a-z]{2}")
TERMS_PER_MARKET = 8
PROPOSALS_PER_PLAN = 2


class LocalTermOutput(Model):
    term: Term
    language: Term
    title: Term


class ProposedBoardOutput(Model):
    domain: Term
    name: Term
    sectors: list[str]
    # Unbounded on purpose. It is never stored or shown — it is there so the model has to justify
    # a suggestion before making it — and as a `Reason` its 200-character cap failed the first
    # live plan, spending a retry on a sentence nobody reads.
    why: str


class MarketOutput(Model):
    country: Term
    languages: list[str]
    search_terms: Annotated[list[LocalTermOutput], count(0, 20, "", "At most 20 search terms per market")]
    boards: list[str]
    proposed_boards: Annotated[list[ProposedBoardOutput], count(0, 5, "", "At most 5 proposed boards per market")]


class StrategistOutput(Model):
    field: Term
    markets: Annotated[list[MarketOutput], count(0, 8, "", "At most 8 markets")]

    @model_validator(mode="after")
    def _within_what_was_asked(self, info: ValidationInfo) -> "StrategistOutput":
        """Filters, like the rest of this module: a stray market or an unknown board is dropped,
        and the call keeps everything it got right."""
        ctx = info.context or {}
        if "boards" not in ctx:
            return self
        from ..http import is_denied

        countries: set[str] = ctx.get("countries") or set()
        boards: dict[str, list[str]] = ctx["boards"]  # known domain → countries it serves ([] = all)
        titles = {t.casefold() for t in ctx.get("titles") or []}
        kept: dict[str, MarketOutput] = {}
        for m in self.markets:
            m.country = m.country.strip().upper()
            if m.country in kept or (countries and m.country not in countries):
                continue
            m.languages = list(
                dict.fromkeys(lang.strip().lower() for lang in m.languages if _LANG.fullmatch(lang.strip().lower()))
            )
            terms: list[LocalTermOutput] = []
            for t in m.search_terms:
                t.term = " ".join(_OPERATORS.sub(" ", t.term).split())
                t.language = t.language.strip().lower()
                usable = t.term and len(t.term) <= 60 and t.language in m.languages and t.title.casefold() in titles
                if usable and t.term.casefold() not in {x.term.casefold() for x in terms}:
                    terms.append(t)
            m.search_terms = terms[:TERMS_PER_MARKET]
            m.boards = list(
                dict.fromkeys(
                    d.strip().lower()
                    for d in m.boards
                    if d.strip().lower() in boards and (not boards[d.strip().lower()] or m.country in boards[d.strip().lower()])
                )
            )
            proposed: list[ProposedBoardOutput] = []
            for p in m.proposed_boards:
                p.domain = p.domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
                if _HOST.fullmatch(p.domain) and p.domain not in boards and not is_denied(f"https://{p.domain}/"):
                    proposed.append(p)
            m.proposed_boards = proposed
            kept[m.country] = m
        self.markets = list(kept.values())
        return self


# ---- cv.profile ----
#
# The judgement half of the CV rubric. Everything a regex or a lookup can answer — identity,
# per-role tools, numeric achievements — is computed in `domain.cv_profile`, so the model is only
# asked what needs reading, and everything it returns is checked against the CV.


class ImpliedMetric(Model):
    """An achievement that implies a number without stating one, for the applicant to quantify."""

    bullet_id: Term
    phrase: Term


class CrossFunctionalTrait(Model):
    trait: Term
    bullet_ids: list[str]


class CvProfileOutput(Model):
    primary_domains: Annotated[list[Term], count(0, 5, "", "At most 5 domains")]
    methodologies: Annotated[list[Term], count(0, 10, "", "At most 10 methodologies")]
    implied_metrics: Annotated[list[ImpliedMetric], count(0, 10, "", "At most 10 implied metrics")]
    strongest_cross_functional_trait: CrossFunctionalTrait | None

    @model_validator(mode="after")
    def _backed_by_the_cv(self, info: ValidationInfo) -> "CvProfileOutput":
        """Filters, not vetoes, like `Cites`: a methodology the CV never names, a phrase that
        isn't in the bullet it names, and a trait with no line behind it are each dropped."""
        cv = _cv(info)
        if cv is None:
            return self
        from ..domain.vocab import master_cv_text, text_has_term

        text = master_cv_text(cv)
        self.methodologies = [m for m in self.methodologies if text_has_term(text, m)]
        lines = {b.id: b.text for r in [*cv.experience, *cv.projects] for b in r.bullets}
        # The phrase, not the line: measured live, the model returned whole bullets here, which
        # points at a line without saying which part of it is the result to quantify.
        self.implied_metrics = [
            m
            for m in self.implied_metrics
            if m.bullet_id in lines
            and appears_in(m.phrase, lines[m.bullet_id])
            and len(m.phrase.strip(" .")) < 0.8 * len(lines[m.bullet_id].strip(" ."))
        ]
        trait = self.strongest_cross_functional_trait
        if trait is not None:
            trait.bullet_ids = [i for i in trait.bullet_ids if i in lines]
            if not trait.bullet_ids:
                self.strongest_cross_functional_trait = None
        return self


# ---- cv.tailor, letter.write, answers.draft ----


class RolePick(Model):
    """One master-CV role, and which of its lines to show (TAIL-01).

    Ids only. There is deliberately no `text` field — see `TailorSelection`.
    """

    role_id: Annotated[str, required("Name the role this belongs to")]
    bullet_ids: Annotated[list[str], count(1, None, "A role needs at least one line")]

    @model_validator(mode="after")
    def _bullets_belong_to_role(self, info: ValidationInfo) -> "RolePick":
        """The role must exist, and each line must be one of *that* role's lines.

        Checking the pair rather than each id separately is what stops a bullet being filed under
        a role it did not come from: every id would still be "in the master CV", and the rendered
        CV would attribute real work to the wrong employer.
        """
        cv = _cv(info)
        if cv is None:
            return self
        role = next((r for r in [*cv.experience, *cv.projects] if r.id == self.role_id), None)
        if role is None:
            raise fail("role_id", f"Not a role in the master CV: {self.role_id}")
        mine = {b.id for b in role.bullets}
        stray = [i for i in self.bullet_ids if i not in mine]
        if stray:
            raise fail("bullet_ids", f"Not lines of {self.role_id}: {', '.join(stray)}")
        return self


class TailorSelection(Model):
    """cv.tailor's reply: which master-CV lines to show, and in what order (TAIL-01).

    **Selection, not writing — and the schema is what makes that true, not the prompt.** This
    replaced a `TailorOutput` that carried each bullet's `text`, so the model re-typed every line
    on every call. The wording drifted, the citation guard (§8.5) rejected the drift, a
    `guard.repair` call fired per failing line, and whatever still failed was dropped — a
    different set each run, which is why the same job produced a different CV every time.

    Strict JSON schema could not have caught that: it enforces shape, and a drifted bullet is
    perfectly well-shaped. So the fix is the absence of a field. Nothing here is free text —
    every value is an id or a skill drawn from the CV's own list — so drifted wording is not
    merely invalid, it is unrepresentable. `nodes.tailor` copies the text from the master CV.
    """

    # None when the CV has no summary to show, or when the model chose to omit it. Either way
    # `nodes.tailor.master_summary` decides the fallback, not the model (TAIL-07).
    summary_id: str | None
    experience: list[RolePick]
    projects: list[RolePick]
    skills: list[Term]
    omitted_bullet_ids: list[str]

    @field_validator("summary_id")
    @classmethod
    def _summary_is_the_summary(cls, v: str | None, info: ValidationInfo) -> str | None:
        """The professional profile is the CV's own summary line or nothing.

        Narrower than "any known id" on purpose: a bullet promoted to the top of the page reads
        as a profile the applicant wrote, and they didn't.
        """
        cv = _cv(info)
        if v and cv is not None:
            expected = cv.summary.id if cv.summary else None
            if v != expected:
                raise fail("summary_id", "summaryId must be the master CV's own summary id, or null")
        return v

    @field_validator("skills")
    @classmethod
    def _skills_from_cv(cls, v: list[Term], info: ValidationInfo) -> list[Term]:
        """TAIL-01: the tailored CV may reorder and drop, never invent."""
        cv = _cv(info)
        if cv is not None:
            known = _known_skills(cv)
            unknown = [s for s in v if s.casefold() not in known]
            if unknown:
                raise fail("skills", f"Not in the master CV's skills: {', '.join(unknown)}")
        return v

    @field_validator("omitted_bullet_ids")
    @classmethod
    def _omitted_exist(cls, v: list[str], info: ValidationInfo) -> list[str]:
        """A line can only be "left out" if it was there to leave out — otherwise the review
        screen offers the person a bullet they can't add back."""
        cv = _cv(info)
        if cv is not None:
            unknown = [i for i in v if i not in _known_bullet_ids(cv)]
            if unknown:
                raise fail("omitted_bullet_ids", f"Not in the master CV: {', '.join(unknown)}")
        return v


class SummaryDraft(Cites):
    """TAIL-07: a professional profile composed from cited master-CV lines.

    The one piece of CV prose a model is allowed to write, and only when the master CV has no
    summary of its own to select — see `SUMMARY_WRITE` for why the exception exists and how it is
    bounded. `Cites` gives it the id filter; `nodes.tailor` then puts it through the same citation
    guard as a letter paragraph, so an unsupported profile is repaired or dropped rather than
    printed at the top of the page.

    The word bound is a validator for the same reason `LetterOutput._length` is: strict mode
    ignores `maxLength`, and a profile that runs to a paragraph stops being a profile.
    """

    @model_validator(mode="after")
    def _length(self) -> "SummaryDraft":
        n = len(self.text.split())
        if not 20 <= n <= 90:
            raise fail("length", f"The profile is {n} words; write between 20 and 90")
        return self


# The sign-off is a closed set rather than free text: there are three forms a cover letter ends
# with, and everything else a model reaches for ("Warmest regards", "Yours faithfully, with
# thanks") is either wrong for the register or wrong for the century.
type Valediction = Literal["Sincerely", "Best regards", "Respectfully"]


class LetterParagraph(Cites):
    """One cited paragraph, and the competency it is making the case for.

    `theme` is deliberately write-only — nothing renders it. Naming the competency is what stops
    a paragraph being a second helping of the one before it: a model that has to label what this
    paragraph is proving writes a paragraph that proves one thing.
    """

    theme: str = ""
    # Which segment of the letter template this paragraph is. Defaulted rather than required so
    # a reply that leaves it out costs a label, not a retry — the letter is still the letter.
    section: LetterSection = "core_value_proposition"


class LetterOutput(Model):
    greeting: Annotated[str, required("Add a greeting")]
    paragraphs: Annotated[list[LetterParagraph], count(3, 4, "Write 3 or 4 paragraphs", "Write 3 or 4 paragraphs")]
    signoff: Annotated[str, required("Add a sign-off")]
    # Which opening the letter commits to. Also write-only, and also not decoration: the opening
    # used to be whatever the model drifted into, which was usually a restatement of the job title.
    # Choosing from a closed set forces a decision about what the first sentence is *for*.
    hook_type: HookType = "Core-Metric"
    company_quote: str  # LTR-02: one fact quoted word for word from the post
    # LTR-06: a second verbatim fact, about how the company works — its remote or hybrid policy,
    # learning budget, sponsorship, how it's structured. Empty when the post states none, which
    # is common and is not a failure: an invented policy is far worse than a missing paragraph.
    # Verified by exact substring against the post, exactly like `company_quote`.
    policy_quote: str = ""

    @field_validator("greeting")
    @classmethod
    def _no_dead_salutation(cls, v: str) -> str:
        """ "To Whom It May Concern" dates a letter by about forty years, and "Dear Sir or Madam"
        guesses at something it has no business guessing at.

        Corrected rather than rejected: the salutation is one line of furniture and the rest of
        the letter may be perfectly good, so spending a retry — of two — on the greeting would be
        a poor trade. `nodes.tailor` has no say in this; it arrives already fixed.
        """
        dead = ("to whom it may concern", "dear sir or madam", "dear sir/madam", "dear sirs", "dear sir", "dear madam")
        return "Dear Hiring Team," if v.strip().lower().rstrip(",:").strip() in {d.rstrip(",:") for d in dead} else v

    @field_validator("signoff")
    @classmethod
    def _valediction(cls, v: str) -> str:
        """The sign-off, normalised to one of the three forms.

        The name on the second line is replaced at render time from the master CV (TAIL-02), so
        only the valediction itself matters here — and an unrecognised one becomes "Sincerely"
        rather than failing the letter, for the same reason as the greeting above.
        """
        first, _, rest = v.partition("\n")
        allowed = {"sincerely": "Sincerely", "best regards": "Best regards", "respectfully": "Respectfully"}
        chosen = allowed.get(first.strip().rstrip(",").strip().lower(), "Sincerely")
        return f"{chosen},\n{rest.strip()}" if rest.strip() else f"{chosen},"

    @model_validator(mode="after")
    def _length(self) -> "LetterOutput":
        """LTR-01's word count, enforced on the model's reply.

        Counted over the paragraphs only — the greeting and sign-off are fixed furniture, and
        including them would make the bound depend on how long a name is.
        """
        count_ = sum(len(p.text.split()) for p in self.paragraphs)
        if not LETTER_MIN_WORDS <= count_ <= LETTER_MAX_WORDS:
            raise fail(
                "length",
                f"The letter is {count_} words; write between {LETTER_MIN_WORDS} and {LETTER_MAX_WORDS}",
            )
        return self


class AnswerDraft(Model):
    id: Annotated[str, required("Name the question this answers")]
    answer: str | None  # null = "your answer needed" (ANS-02/03)
    source_ids: list[str]

    @field_validator("source_ids")
    @classmethod
    def _cited_when_answered(cls, v: list[str], info: ValidationInfo) -> list[str]:
        if info.data.get("answer") and not v:
            raise fail("source_ids", "An answer must cite the master CV, or be left for the person")
        return v


class AnswersOutput(Model):
    answers: list[AnswerDraft]


# ---- guard.repair (§8.5 step 6) ----


class RepairOutput(Model):
    """One repaired item, or null when the model can't fix it from the cited sources."""

    item: Cites | None


# ---- guard.claims ----
#
# The citation guard checks numbers and named skills, which a regex can do. It cannot check a
# claim: "the team adopted it for all outbound LLM traffic" passed it, against a CV line that
# says only "wrote prompt templates and evaluation notes … with the security team". This is the
# check that can, held to the same rule as everything else here — a finding is a quote.


class ClaimFinding(Model):
    location: Term  # which paragraph, as given ("p0")
    claim: Term  # the unsupported words, copied from that paragraph


class ClaimCheck(Model):
    findings: Annotated[list[ClaimFinding], count(0, 20, "", "At most 20 findings")]

    @field_validator("findings")
    @classmethod
    def _quoted_from_the_paragraph(cls, v: list[ClaimFinding], info: ValidationInfo) -> list[ClaimFinding]:
        """A finding that isn't a quote from the paragraph it names is dropped: the checker may
        point at words, never put them in a letter's mouth."""
        texts: dict[str, str] | None = (info.context or {}).get("paragraphs") if info.context else None
        if texts is None:
            return v
        return [f for f in v if f.location in texts and appears_in(f.claim, texts[f.location])]


# ---- job.normalise and email.extract ----


class NormalisedJob(Model):
    # A way to say "this page isn't a job" that is an answer, not a failure. Without it a model
    # shown an advert slot or a listing correctly returned no title — which failed `required`,
    # was retried, failed again, and then fell through to a paid Firecrawl scrape of the same
    # non-job (seen in Logfire on a live harvest, 2026-11-10).
    is_job_posting: bool
    title: str | None
    company: str | None
    location: Location

    @model_validator(mode="after")
    def _a_posting_has_its_facts(self) -> "NormalisedJob":
        if self.is_job_posting and not ((self.title or "").strip() and (self.company or "").strip()):
            raise fail("required", "A job posting needs its title and company; if this isn't one, set isJobPosting to false")
        return self

    remote: RemoteMode | None
    salary: Opt[Salary] = None
    language: str
    seniority: Opt[Seniority] = None
    posted_at: Opt[str] = None


class JobCard(Model):
    """A job spotted in an alert email, before the full post is fetched."""

    title: Annotated[str, required("Add a title")]
    company: Annotated[str, required("Add a company")]
    location_raw: str
    url_token: Opt[str] = None  # MAIL-05: links are replaced by tokens before the call


class JobCardList(Model):
    cards: list[JobCard]
