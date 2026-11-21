"""Tailored CV (TAIL-01…06) and cover letter (LTR-01…05).

Each node turns one model reply into the stored contract, then puts every written line through
the citation guard and its repair pass. What the guard can't vouch for is dropped and listed,
so the review screen can show it.

The job description reaches the model inside an untrusted-data block (§9.3): it is the one
input here that an employer — or someone posting as one — controls.
"""

import contextvars
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import logfire
from sqlalchemy.orm import Session

from ..config import wera
from ..db.session import transaction
from ..domain import dedupe
from ..domain.guard import check_cited
from ..domain.text import display_case
from ..domain.vocab import cv_line_index, master_cv_text, text_has_term
from ..errors import ApiError
from ..llm.demo import PackInput, named_contact, no_stop, words
from ..llm.gateway import structured
from ..llm.prompts import PARAGRAPH_BRIEFS, cv_text, untrusted
from ..schemas.cv import MasterCV, Summary
from ..schemas.llm_outputs import LetterOutput, LetterParagraphOutput, SummaryDraft, TailorSelection
from ..schemas.pack import (
    Cited,
    CompanyFact,
    CoverLetter,
    CvItem,
    CvSection,
    GuardResult,
    Paragraph,
    TailoredCV,
    Violation,
)
from .guard import claim_violations, guard_and_repair
from .letter_plan import (
    SECTIONS,
    SectionPlan,
    context_text,
    keywords_text,
    plan,
    section_keywords_text,
    section_plans,
    template_paragraph,
)

# Roughly how many bullets fit, by CV length. The model is told the budget; `estimate_pages`
# checks what actually came back.
LINES_PER_PAGE = 6

# LTR-01: a letter is 3 or 4 paragraphs. One redraft when the guard leaves fewer — two calls is
# the most a letter is worth, and the person can always ask for changes at Gate 2.
MIN_PARAGRAPHS = 3
MAX_LETTER_ATTEMPTS = 2
# LTR-01's range. Below its floor a letter is asked for once more, with its own word count quoted
# back; unlike `LETTER_MIN_WORDS` it never fails a pack — the longer of the two attempts is kept.
# 150, not the 180 first tried: an honest letter from the CV's own lines came out at 155–202 words
# on the real CV (`wera eval --packs`, 2026-11-12), and a target it misses half the time only buys
# a second call that comes back no longer.
TARGET_MIN_WORDS = 150
TARGET_MAX_WORDS = 230


def job_block(inp: PackInput) -> str:
    """The job as the model sees it: facts WERA parsed, then the post itself as data.

    Truncated to `job_description_limit`. The first part of an ad is the job and the rest is
    benefits, boilerplate and legal text, and these are the prompts that also carry the whole CV
    — `prefilter.job_text` has always clipped at 2,000 characters while the three writing prompts
    sent everything, so the uncapped calls were the expensive ones.

    Safe for `_guard_letter`'s quote check, which tests `company_quote` against the *full*
    description: the model only ever sees a prefix, so a quote it takes from what it saw is still
    a substring of the whole.
    """
    description = (inp.job.description_md or "")[: wera().job_description_limit]
    return f"{job_header(inp)}\n{untrusted('job post', description)}"


def job_header(inp: PackInput) -> str:
    """The job's one-line header, without the post: all a `letter.paragraph` call is given of
    the job, beside the post's words its plan already chose (20 Nov)."""
    job = inp.job
    if dedupe.is_intermediary(job.company):
        # Said outright, so the letter names the employer when the post does and otherwise
        # writes to the hiring team — not to the board as if it were hiring.
        return (
            f"Job: {job.title} ({job.location.raw}), posted by {job.company} — a job board or agency, not the "
            "employer. Name the employer only if the post does; never write to the board as the employer."
        )
    return f"Job: {job.title} at {job.company} ({job.location.raw})"


def _keywords(inp: PackInput, *, backed_only: bool = False) -> str:
    """The post's keywords as a prompt reads them. `backed_only` keeps only what the CV can back:
    the profile is prose a model writes, and on 13 Nov two of three dropped profiles had named a
    keyword the post asked for and the CV doesn't have (`summary.write` was given them all)."""
    keywords = inp.evidence.keywords
    if backed_only:
        master = master_cv_text(inp.cv)
        keywords = [k for k in keywords if text_has_term(master, k.term)]
    required = [k.term for k in keywords if k.weight == "required"]
    nice = [k.term for k in keywords if k.weight == "nice"]
    return f"required — {', '.join(required) or 'none'}; nice to have — {', '.join(nice) or 'none'}"


def _own_summary(cv: MasterCV) -> bool:
    """The applicant wrote this summary (or kept WERA's after editing it): use it as written."""
    return bool(cv.summary and cv.summary.text.strip() and not cv.summary.generated)


def tailor(session: Session, inp: PackInput) -> TailoredCV:
    """TAIL-01: selects and orders master-CV lines for this job.

    The model returns ids; the text is copied here. That is what makes the result the same twice
    for the same inputs — see `TailorSelection` for why it used to vary.
    """
    cv = inp.cv
    out = structured(
        session,
        "cv.tailor",
        TailorSelection,
        {
            "cv": cv_text(cv),
            "job": job_block(inp),
            "keywords": _keywords(inp),
            "note": inp.note or "nothing in particular",
            "pages": str(inp.cv_pages),
            "budget": str(inp.cv_pages * LINES_PER_PAGE),
        },
        context={"cv": cv},
        demo_input=inp,
    )

    lines = cv_line_index(cv)
    sections = [
        section
        for kind, picks in (("experience", out.experience), ("projects", out.projects))
        if (
            section := CvSection(
                kind=kind,
                items=[item for p in picks if (item := _item(lines, p)) is not None],
            )
        ).items
    ]
    doc = TailoredCV(
        job_id=inp.job.id,
        cv_version=cv.version,
        # TAIL-07: the profile is not optional. Preference order, strongest claim first: the
        # applicant's own summary (their words, cites itself, cannot fail the guard), then one
        # composed from their cited CV lines, then nothing. `cv.tailor` may only ever *select*,
        # which is why composing is a separate call rather than a field it could have filled.
        summary=(_from_master(lines, out.summary_id) if out.summary_id and _own_summary(cv) else None) or _profile(session, inp),
        sections=sections,
        skills=out.skills,
        omitted_bullet_ids=out.omitted_bullet_ids,
        guard={"pass": True, "violations": [], "repaired": False},
    )
    return _guard_cv(session, doc, inp)


def _profile(session: Session, inp: PackInput) -> Cited:
    """The profile for a CV whose master has none (TAIL-07).

    Composed rather than selected, and the only CV prose a model writes here — see
    `prompts.SUMMARY_WRITE` for why the exception is drawn this narrowly. It is guarded with the
    rest of the document by `_guard_cv`, so nothing skips a check by arriving through this path.

    A composition that fails outright leaves the profile empty, which is where the old behaviour
    started: a CV with no summary. That is the honest floor, not the target — `cv.parse` now asks
    for the profile explicitly, so the usual answer is that there is a master summary to select.
    """
    own = master_summary(inp.cv)
    if _own_summary(inp.cv):
        return own
    # The master summary is WERA's own (or there is none): this job gets one written for it, and
    # WERA's general one is the fallback — never nothing, when there is one (14 Nov).
    # Built from the lines, without WERA's general summary in view: shown it, the model copied it
    # nearly word for word into every pack (`wera eval --packs` on a real CV, 14 Nov), so the
    # per-job profile was the general one paid for again. Only WERA's own is hidden this way.
    lines_only = inp.cv.model_copy(update={"summary": None})
    try:
        out = structured(
            session,
            "summary.write",
            SummaryDraft,
            {"cv": cv_text(lines_only), "job": job_block(inp), "keywords": _keywords(inp, backed_only=True)},
            context={"cv": inp.cv},
            demo_input=inp,
        )
    except ApiError:
        logfire.info("No per-job profile for {job_id}; using the master summary if there is one", job_id=inp.job.id)
        return own
    # `composed=True` is what lets this line through `Store._assert_verbatim`, which requires a
    # cited line to equal the master line it names — true of every *selected* line and never of
    # this one. See `Cited.composed`; it changes no other check.
    return Cited(text=out.text, source_ids=out.source_ids, composed=True)


def _from_master(lines: dict[str, dict[str, str]], bullet_id: str) -> Cited | None:
    """One master-CV line, verbatim, citing itself.

    This is where "selection, not writing" stops being a request in a prompt and becomes a
    property of the data: the text *is* the cited line, so it cannot have drifted from it, and
    `check_cited` passes without a single `guard.repair` call.

    `None` only when the id isn't in the index. `RolePick` already rejects that, so this is the
    belt to its braces — and it matters because validation context is optional, so a caller that
    forgot `context={"cv": cv}` would otherwise reach here with an unchecked id.
    """
    line = lines.get(bullet_id)
    return Cited(text=line["text"], source_ids=[bullet_id]) if line else None


def _item(lines: dict[str, dict[str, str]], pick) -> CvItem | None:  # noqa: ANN001 — a RolePick
    """One role's selected lines. `None` when none of them resolved, so an empty role is dropped
    rather than rendered as a heading with nothing under it."""
    bullets = [b for b in (_from_master(lines, i) for i in pick.bullet_ids) if b is not None]
    return CvItem(role_id=pick.role_id, bullets=bullets) if bullets else None


def cv_locations(doc: TailoredCV) -> list[tuple[str, Cited]]:
    """The same location names the review screen uses for its edit targets."""
    items: list[tuple[str, Cited]] = []
    if doc.summary.text:
        items.append(("summary", doc.summary))
    for si, section in enumerate(doc.sections):
        for ii, item in enumerate(section.items):
            for bi, bullet in enumerate(item.bullets):
                items.append((f"s{si}.i{ii}.b{bi}", bullet))
    return items


def _guard_cv(session: Session, doc: TailoredCV, inp: PackInput) -> TailoredCV:
    """§8.5 over the whole document, applying repairs and dropping what still fails."""
    result, decisions = guard_and_repair(session, cv_locations(doc), inp.cv, [k.term for k in inp.evidence.keywords])
    if decisions:
        if "summary" in decisions:
            # TAIL-07: the professional profile is not an optional line. Dropping it is the right
            # answer for a bullet — a shorter CV beats an unsupported claim — but a CV with no
            # profile at the top reads as unfinished, and a summary is the line most likely to
            # fail a strict citation check in the first place, because summarising *is*
            # paraphrasing across the whole document.
            #
            # So a rejected summary falls back to the applicant's own master summary rather than
            # to nothing. That text is theirs, already in the CV, and cites itself — it cannot
            # fail the guard, and it cannot claim anything they haven't already written down.
            #
            # When there is no master summary either, this is the composed profile from `_profile`
            # being rejected, and an empty profile is the right floor: it had its repair pass and
            # still couldn't be vouched for.
            doc.summary = decisions["summary"] or master_summary(inp.cv)
        for si, section in enumerate(doc.sections):
            for ii, item in enumerate(section.items):
                kept: list[Cited] = []
                for bi, bullet in enumerate(item.bullets):
                    location = f"s{si}.i{ii}.b{bi}"
                    replacement = decisions.get(location, bullet)
                    if replacement is None:
                        # TAIL-06: a dropped line goes back on the "left out" list, so the
                        # person can put it back themselves if they disagree.
                        doc.omitted_bullet_ids = [*doc.omitted_bullet_ids, *bullet.source_ids]
                        continue
                    kept.append(replacement)
                item.bullets = kept
        doc.sections = [s for s in doc.sections if any(i.bullets for i in s.items)]
        for section in doc.sections:
            section.items = [i for i in section.items if i.bullets]
    doc.guard = result
    # Also covers the model simply not writing one. A tailored CV always carries a profile when
    # the master has one to carry.
    if not doc.summary.text.strip():
        doc.summary = master_summary(inp.cv)
    return doc


# The id WERA gives a summary it wrote for the master CV. Fixed, so it is the same line across
# versions and a pack citing it still resolves.
GENERATED_SUMMARY_ID = "sum-wera"
NO_JOB = (
    "No particular job: this is the general professional profile at the top of the applicant's "
    "master CV. Say what they are, the evidence that matters most across their CV, and what they "
    "are looking for next."
)


def compose_master_summary(session: Session, cv: MasterCV) -> Summary | None:
    """TAIL-07: a general profile for a CV that has none, from its own lines, guarded.

    The same prompt and guard as a pack's profile, without a job. None when it can't be vouched
    for — an empty profile is still the honest floor — and in demo mode, whose CV has its own.
    """
    from ..settings import get_settings

    if get_settings().wera_demo_mode:
        return None
    try:
        out = structured(
            session, "summary.write", SummaryDraft, {"cv": cv_text(cv), "job": NO_JOB, "keywords": "none"}, context={"cv": cv}
        )
    except ApiError:
        logfire.info("Couldn't write a master summary for CV version {version}", version=cv.version)
        return None
    cited = Cited(text=out.text, source_ids=out.source_ids, composed=True)
    _result, decisions = guard_and_repair(session, [("summary", cited)], cv, [])
    if "summary" in decisions:
        cited = decisions["summary"]
        if cited is None:
            logfire.info("The master summary WERA wrote couldn't be vouched for; left empty")
            return None
    return Summary(id=GENERATED_SUMMARY_ID, text=cited.text, generated=True)


def master_summary(cv: MasterCV) -> Cited:
    """The applicant's own professional profile, citing itself.

    Empty only when the master CV genuinely has no summary — then there is nothing honest to
    fall back to, and an absent profile is the truthful outcome rather than an invented one.
    """
    if cv.summary and cv.summary.text.strip():
        return Cited(text=cv.summary.text, source_ids=[cv.summary.id])
    return Cited(text="", source_ids=[])


@dataclass
class LetterStats:
    """What writing one letter took, for `evaluate.run_letters` and the letter's span. Not stored."""

    mode: str
    calls: int = 0  # `letter.write` or `letter.paragraph` calls made
    failed_calls: int = 0  # of those, how many raised (the gateway's own retries are inside one)
    written_words: int = 0  # what the first round wrote, before the guard
    dropped: int = 0  # paragraphs the guard cut and nothing replaced (single mode)
    regenerated: int = 0  # paragraphs written again after failing the guard (paragraph mode)
    templated: int = 0  # paragraphs built from the CV's own lines instead (paragraph mode)
    lengthened: int = 0  # paragraphs written again because the letter came out short
    shortened: int = 0  # and because it came out long (20 Nov)
    # Why first-round paragraphs failed, by guard rule ("claims", "numbers", "filler", …).
    failed_on: dict[str, int] = field(default_factory=dict)


def letter(session: Session, inp: PackInput, doc: TailoredCV) -> CoverLetter:
    """LTR-01…05: 3–4 paragraphs, one company fact quoted from the post, every claim cited.

    Written the way `letters.mode` says (20 Nov): a paragraph per call by default, or the one
    call that wrote every letter before (`single`), kept for rollback and comparison.
    """
    return write_letter(session, inp, doc)[0]


def write_letter(
    session: Session, inp: PackInput, doc: TailoredCV, *, mode: str | None = None
) -> tuple[CoverLetter, LetterStats]:
    """`letter`, with what it took. `mode` overrides the configured one, for the eval."""
    mode = mode or wera().letters.mode
    with logfire.span("letter {mode}", mode=mode, job_id=inp.job.id) as span:
        made, stats = _by_paragraphs(session, inp, doc) if mode == "paragraphs" else _single(session, inp, doc)
        span.set_attributes({f"letter.{k}": v for k, v in stats.__dict__.items() if isinstance(v, int)})
        span.set_attribute("letter.words", made.word_count)
    return made, stats


def _single(session: Session, inp: PackInput, doc: TailoredCV) -> tuple[CoverLetter, LetterStats]:
    """`letters.mode: single` — the whole letter in one `letter.write` call, guarded and repaired.

    The guard can drop a paragraph the CV doesn't back. A letter it leaves with fewer than three
    paragraphs, or under LTR-01's 150 words, is asked for once more, told what was cut and why.
    If the second one is no better, the fuller of the two is kept with its violations showing,
    because a short letter the person can see the problem with beats a silent one.

    Two fixes of 20 Nov, kept here although this is no longer the default: a second call that
    fails outright keeps the first letter rather than losing it (the call had no `try`, so a
    good first letter went with the error), and "too short" reads what the guard *left*.
    """
    cv = inp.cv
    stats = LetterStats(mode="single")
    tailored = "\n".join(f"- {b.text}" for _, b in cv_locations(doc))
    keywords = keywords_text(plan(inp.evidence, cv))
    context = context_text(inp.analysis, inp.brief)
    best: CoverLetter | None = None
    feedback: str | None = None

    for _attempt in range(MAX_LETTER_ATTEMPTS):
        stats.calls += 1
        try:
            out = structured(
                session,
                "letter.write",
                LetterOutput,
                {
                    "cv": cv_text(cv),
                    "job": job_block(inp),
                    "tailored": tailored or "(no lines selected)",
                    "wants": _what_they_want(inp),
                    "note": _letter_note(inp.note, feedback),
                    "keywords": keywords,
                    "context": context,
                },
                context={"cv": cv},
                demo_input=inp,
            )
        except ApiError as exc:
            stats.failed_calls += 1
            if best is None:
                raise
            logfire.warn(
                "Letter for {job_id}: the second attempt failed ({code}); keeping the first", job_id=inp.job.id, code=exc.code
            )
            break
        if best is None:
            stats.written_words = sum(len(p.text.split()) for p in out.paragraphs)
        candidate = _guard_letter(session, out, inp)
        filler = banned_phrases(candidate)
        # Too few paragraphs left, or a paragraph cut and the letter left under LTR-01's floor.
        # The second used to pass: three paragraphs was "enough", and the word count read what the
        # model *wrote* rather than what the guard *left*. Measured on the 15 letters to 17 Nov: the
        # 6 that lost a paragraph — usually the proof — came out at 124–148 words and none was
        # asked for again, while every four-paragraph letter landed at 153–214.
        cut = any(v.rule == "dropped" for v in candidate.guard.violations)
        dropped = len(candidate.paragraphs) < MIN_PARAGRAPHS or (cut and candidate.word_count < TARGET_MIN_WORDS)
        # What the guard left, not what the model wrote (20 Nov): a repair that shrank a paragraph
        # left a 130-word letter that the model's own 160 words said was long enough.
        short = not dropped and candidate.word_count < TARGET_MIN_WORDS
        if best is None or _better(candidate, best):
            best = candidate
        if not dropped and not filler and not short:
            best = candidate
            break
        # One reason at a time would waste an attempt: a letter can be both too short and full of
        # filler, and there are only two attempts.
        feedback = " ".join(
            p
            for p in (
                _dropped_feedback(candidate) if dropped else None,
                _short_feedback(candidate) if short else None,
                _filler_feedback(filler) if filler else None,
            )
            if p
        )
        logfire.info(
            "Letter for {job_id} came back with {kept} paragraph(s), {words} words and {filler} filler phrase(s); asking again",
            job_id=inp.job.id,
            kept=len(candidate.paragraphs),
            words=candidate.word_count,
            filler=len(filler),
        )

    assert best is not None  # the loop's first pass either sets it or raises
    stats.dropped = sum(1 for v in best.guard.violations if v.rule == "dropped")
    return best, stats


# ---------------------------------------------------------------------------
# A letter written paragraph by paragraph (`letters.mode: paragraphs`, 20 Nov).
#
# On 20 Nov 2 of 7 letters came out at 130–133 words, and only 1 of 13 `letter.write` calls had
# been cut off. The rest was the guard: a repair could only cut words, lost the company's name
# and quotes on the way, and a paragraph it couldn't fix was deleted with nothing put back. So
# here a paragraph that fails is written again, on its own, told why; one that fails twice is
# built from its own CV lines; and the letter's length is checked after all that, not before.


def _by_paragraphs(session: Session, inp: PackInput, doc: TailoredCV) -> tuple[CoverLetter, LetterStats]:
    stats = LetterStats(mode="paragraphs")
    cv, job = inp.cv, inp.job
    post = job.description_md or ""
    plans = section_plans(cv, doc, inp.evidence, post=post, company=job.company, analysis=inp.analysis, brief=inp.brief)

    def write(p: SectionPlan, feedback: str | None) -> Callable[[Session], Paragraph]:
        return lambda s: _write_paragraph(s, inp, p, feedback)

    first = _in_parallel(session, [write(p, None) for p in plans])
    stats.calls += len(plans)
    stats.failed_calls += sum(isinstance(r, ApiError) for r in first)
    # Every call failing is the provider, the key or the budget, not the letter: the draft fails
    # with that code, as a failed `letter.write` does, rather than shipping four templates.
    if all(isinstance(r, ApiError) for r in first):
        raise next(r for r in first if isinstance(r, ApiError))
    written: dict[str, Paragraph | None] = {
        p.location: r if isinstance(r, Paragraph) else None for p, r in zip(plans, first, strict=True)
    }
    stats.written_words = sum(words(p.text) for p in written.values() if p)

    found = _paragraph_violations(session, inp, plans, {k: v for k, v in written.items() if v})
    failing = [p for p in plans if written[p.location] is None or found.get(p.location)]
    for p in failing:
        for rule in {v.rule for v in found.get(p.location, [])} or {"no reply"}:
            stats.failed_on[rule] = stats.failed_on.get(rule, 0) + 1
    replaced: dict[str, str] = {}  # location → the reason the written paragraph was replaced
    if failing:
        again = _in_parallel(
            session, [write(p, _rewrite_feedback(written[p.location], found.get(p.location, []))) for p in failing]
        )
        stats.calls += len(failing)
        stats.regenerated += len(failing)
        stats.failed_calls += sum(isinstance(r, ApiError) for r in again)
        fresh = {p.location: r for p, r in zip(failing, again, strict=True) if isinstance(r, Paragraph)}
        second = _paragraph_violations(session, inp, [p for p in failing if p.location in fresh], fresh) if fresh else {}
        for p in failing:
            if p.location in fresh and not second.get(p.location):
                written[p.location] = fresh[p.location]
                continue
            written[p.location] = _template(p, inp)
            replaced[p.location] = _reason(second.get(p.location) or found.get(p.location) or [])

    # LTR-01's floor, on the letter as assembled: once, the paragraph furthest under its own budget.
    total = sum(words(p.text) for p in written.values() if p)
    if total < TARGET_MIN_WORDS:
        p = max(plans, key=lambda p: p.words[0] - words(written[p.location].text if written[p.location] else ""))
        current = written[p.location]
        stats.calls += 1
        stats.lengthened += 1
        longer = _in_parallel(session, [write(p, _length_feedback(current, p, total))])[0]
        if isinstance(longer, ApiError):
            stats.failed_calls += 1
        elif words(longer.text) > words(current.text if current else "") and not _paragraph_violations(
            session, inp, [p], {p.location: longer}
        ).get(p.location):
            written[p.location] = longer
            replaced.pop(p.location, None)
        logfire.info(
            "Letter for {job_id} came to {total} words; paragraph {n} written again to its budget",
            job_id=job.id,
            total=total,
            n=SECTIONS.index(p.section) + 1,
        )
    # And its ceiling (20 Nov: 2 of 20 eval letters came to 233 and 250). The paragraph furthest
    # over its own budget is written again, told how far; kept only if it comes back shorter and
    # clean. Up to two paragraphs (21 Nov: one rewrite left 4 of 8 live letters at 231–240).
    tried: set[str] = set()
    while total > TARGET_MAX_WORDS and len(tried) < 2:
        over = [p for p in plans if p.location not in tried and written[p.location]]
        if not over:
            break
        p = max(over, key=lambda p: words(written[p.location].text) - p.words[1])
        tried.add(p.location)
        current = written[p.location]
        stats.calls += 1
        stats.shortened += 1
        shorter = _in_parallel(session, [write(p, _length_feedback(current, p, total, over=True))])[0]
        if isinstance(shorter, ApiError):
            stats.failed_calls += 1
        elif words(shorter.text) < words(current.text) and not _paragraph_violations(
            session, inp, [p], {p.location: shorter}
        ).get(p.location):
            written[p.location] = shorter
        logfire.info(
            "Letter for {job_id} came to {total} words; paragraph {n} written again to its budget",
            job_id=job.id,
            total=total,
            n=SECTIONS.index(p.section) + 1,
        )
        total = sum(words(q.text) for q in written.values() if q)
    stats.templated = len(replaced)
    return _assemble(inp, plans, written, replaced, repaired=bool(failing)), stats


def _in_parallel[T](session: Session, calls: list[Callable[[Session], T]]) -> list[T | ApiError]:
    """Each call's result or the `ApiError` it raised, in order.

    Concurrent on Postgres, each call in its own unit of work so its `llm_calls` row commits with
    it — a session is not shared between threads. One after another in the caller's session on
    SQLite, as `scoring_workers` is: a second writer there waits on the one write lock.
    """

    def safe(call: Callable[[Session], T], s: Session) -> T | ApiError:
        try:
            return call(s)
        except ApiError as exc:
            return exc

    from ..settings import get_settings

    workers = min(len(calls), max(1, wera().letters.workers))
    # The demo answers from code, so there is no wait to overlap — and the demo's packs are drafted
    # while the database is still being seeded, inside one uncommitted transaction a second unit of
    # work can't see: on Postgres every paragraph failed as "not set up yet" (20 Nov).
    if workers <= 1 or session.get_bind().dialect.name == "sqlite" or get_settings().wera_demo_mode:
        return [safe(c, session) for c in calls]

    def own(call: Callable[[Session], T]) -> T | ApiError:
        with transaction() as s:
            return safe(call, s)

    # Each in a copy of this context, so its `llm` span sits under the letter's in the trace.
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="letter") as pool:
        futures = [pool.submit(contextvars.copy_context().run, own, c) for c in calls]
        return [f.result() for f in futures]


def _write_paragraph(session: Session, inp: PackInput, p: SectionPlan, feedback: str | None) -> Paragraph:
    """One `letter.paragraph` call, from its plan alone: its brief, its lines, its quotes."""
    lines = cv_line_index(inp.cv)
    shown = [i for i in p.line_ids if i in lines]
    quotes = "\n".join(f'- {q.label}: "{q.text}"' for q in p.quotes)
    out = structured(
        session,
        "letter.paragraph",
        LetterParagraphOutput,
        {
            "brief": PARAGRAPH_BRIEFS[p.section],
            "budget": f"{p.words[0]}–{p.words[1]}",
            "job": job_header(inp),
            "lines": "\n".join(f"[{i}] {lines[i]['text']}" for i in shown) or "(none: this paragraph cites nothing)",
            "facts": _facts(inp.cv),
            "quotes": untrusted("the employer's words", quotes)
            if quotes
            else "(none: say nothing about the company beyond its name)",
            "keywords": section_keywords_text(p, list(inp.evidence.culture)),
            # Only where the brief asks for it: given to every paragraph, the proof ended on the
            # applicant's working arrangement (`wera_agent.evaluate letters`, 20 Nov).
            "wants": _what_they_want(inp) if p.section == "strategic_alignment_and_culture" else "not needed here",
            "note": _letter_note(inp.note, feedback),
        },
        context={"cv": inp.cv, "plan": p},
        demo_input=inp,
    )
    # Only the lines it was shown; none cited means it drew on all of them, and the guard holds it
    # to all of them — which is what citing nothing used to be dropped for not doing.
    cited = [i for i in out.source_ids if i in shown] or shown
    return Paragraph(text=" ".join(out.text.split()), source_ids=cited, section=p.section, theme=None)


def _facts(cv: MasterCV) -> str:
    """What any paragraph may state without citing a line (`vocab.backing_text`'s facts)."""
    parts = [
        f"Headline: {cv.person.headline}" if cv.person.headline else "",
        "Education: " + "; ".join(f"{e.degree}, {e.institution}" for e in cv.education) if cv.education else "",
        "Roles: " + "; ".join(f"{r.title} at {r.org}" for r in cv.experience) if cv.experience else "",
        "Skills: " + ", ".join(s.name for s in cv.skills) if cv.skills else "",
    ]
    return "\n".join(p for p in parts if p) or "(none)"


def _paragraph_violations(
    session: Session, inp: PackInput, plans: list[SectionPlan], written: dict[str, Paragraph]
) -> dict[str, list[Violation]]:
    """The guard over the given paragraphs: the regex rules and the institution check, one
    `guard.claims` call over all of them, filler, and quotes the post doesn't contain.

    The employer's words are masked first and restored in what is reported, so a number in a
    quoted sentence never reads as the applicant's — and, unlike `_without`, nothing is lost: the
    guard only decides, and the paragraph it passes is the one that was written, words and all.

    Each paragraph is held to every line it was shown, not only the ones it cited: it can only have
    drawn on those, and a claim none of them carries fails either way.
    """
    if not written:
        return {}
    by_location = {p.location: p for p in plans}
    post = inp.job.description_md or ""
    site = inp.brief.site.text if inp.brief and inp.brief.site else ""
    items: list[tuple[str, Cited]] = []
    masks: dict[str, dict[str, str]] = {}
    for location, para in written.items():
        p = by_location[location]
        text, masks[location] = mask(para.text, _employer_words(inp, para.text, [q.text for q in p.quotes]))
        items.append((location, Cited(text=text, source_ids=list(p.line_ids) or para.source_ids)))
    closes = {loc for loc in written if by_location[loc].section == "call_to_action_and_close"}
    terms = [k.term for k in inp.evidence.keywords]
    found = [
        *check_cited(items, inp.cv, terms, unsourced_ok=closes, names=True).violations,
        *claim_violations(session, items, inp.cv),
    ]
    phrases = [p.strip().lower() for p in wera().letter_banned_phrases if p.strip()]
    for location, para in written.items():
        found += [
            Violation(location=location, rule="filler", detail=f'"{h}" reads like a template')
            for h in phrases
            if h in para.text.lower()
        ]
        found += [
            Violation(location=location, rule="quotes", detail=f'"{q}" is in quotation marks, and the post doesn\'t say it')
            for q in invented_quotes(para.text, post, site, inp.cv)
        ]
        if (said := stated_preference(para.text, inp.profile)) is not None:
            found.append(
                Violation(location=location, rule="claims", detail=f'"{said}" is a preference your profile doesn\'t state')
            )
    out: dict[str, list[Violation]] = {}
    for v in found:
        v.detail = unmask(v.detail, masks.get(v.location or "", {}))
        out.setdefault(v.location or "", []).append(v)
    return out


# A sentence in which the applicant prefers a way of working (20 Nov).
_PREFERS = re.compile(r"\b(?:my|I)\b[^.!?]{0,40}\b(?:prefer\w*|preference)\b[^.!?]*", re.I)
_WORK_PATTERN = re.compile(
    r"\b(?:hybrid|remote|flexib\w*|office|on-?site|from home|working (?:model|pattern|arrangement)s?)\b", re.I
)


def stated_preference(text: str, profile: object | None) -> str | None:
    """The words in which a paragraph says how the applicant prefers to work, when their profile
    says no such thing (20 Nov). A profile that is remote-only or office-only has said it."""
    remote = getattr(profile, "remote", "any")
    for m in _PREFERS.finditer(text):
        sentence = m.group(0)
        if not _WORK_PATTERN.search(sentence):
            continue
        if remote == "remote_only" and re.search(r"\b(?:remote|from home|flexib\w*|hybrid)\b", sentence, re.I):
            continue
        if remote == "no_remote" and re.search(r"\b(?:office|on-?site)\b", sentence, re.I):
            continue
        return sentence.strip()[:120]
    return None


def _employer_words(inp: PackInput, text: str, allowed: list[str]) -> list[str]:
    """What in this paragraph is the employer speaking, not a claim about the applicant."""
    post = inp.job.description_md or ""
    site = inp.brief.site.text if inp.brief and inp.brief.site else ""
    return [
        *allowed,
        inp.evidence.job_quote,
        *title_forms(inp.job.title),
        inp.job.company,
        *_context_quotes(inp),
        *post_quotes(text, post),
        *(post_quotes(text, site) if site else []),
    ]


def title_forms(title: str) -> list[str]:
    """The role as a letter names it: the title, and the title before its first qualifier.
    "Investment Banking Analyst, Consumer & Retail" is "the Investment Banking Analyst role" in a
    close, and on 20 Nov that shorter form was read as a skill the CV lacks ("Investment
    Banking") and the close was replaced."""
    short = re.split(r",| [-–—] | \(| / ", title, maxsplit=1)[0].strip()
    return [title, short] if short and short != title else [title]


def invented_quotes(text: str, post: str, site: str, cv: MasterCV) -> list[str]:
    """Spans a paragraph puts in quotation marks that neither the post, the company's site nor
    the CV says. A quote is a claim about what the employer wrote, and the guard can't check
    one it doesn't know the source of — so a made-up one is read as made up."""
    sources = [" ".join(t.split()).casefold() for t in (post, site, master_cv_text(cv)) if t]
    return [m for m in (" ".join(g.split()) for g in _QUOTED.findall(text)) if not any(quoted_from(m, s) for s in sources)]


def _rewrite_feedback(previous: Paragraph | None, violations: list[Violation]) -> str | None:
    """What a paragraph written again is told: its own words, and what failed in them."""
    if previous is None:
        return None  # the call failed; asking again is the whole of the feedback
    reasons = "; ".join(dict.fromkeys(v.detail for v in violations)) or "a claim the lines it cites don't carry"
    return (
        f"WERA's citation check failed the last version of this paragraph — “{previous.text}” — "
        f"because: {reasons}. Write it again to the same length. Keep what was right; for what "
        "failed, say only what the lines given say, or leave it out — and if that leaves it short, "
        "use another of the lines given, never words of your own."
    )


def _length_feedback(current: Paragraph | None, p: SectionPlan, total: int, *, over: bool = False) -> str:
    n = words(current.text) if current else 0
    if over:
        return (
            f"The letter came to {total} words, over the {TARGET_MAX_WORDS} it may run to, and this paragraph "
            f"to {n}. Write it at {p.words[0]}–{p.words[1]} words: keep the strongest line and its proof, "
            "and drop what repeats the rest of the letter."
        )
    return (
        f"The letter came to {total} words, under the {TARGET_MIN_WORDS} it needs, and this paragraph "
        f"to {n}. Write it at {p.words[0]}–{p.words[1]} words, using more of the lines given — "
        "never by padding or by adding what they don't say."
    )


def _reason(violations: list[Violation]) -> str:
    """The first failure, in the person's terms (`nodes.guard.WHY`)."""
    from .guard import WHY

    rules = {v.rule for v in violations}
    extra = {"filler": "phrases that read like a template", "quotes": "a quote the post doesn't contain"}
    return next(
        (WHY.get(r) or extra[r] for r in ("numbers", "terms", "claims", "names", "quotes", "sources", "filler") if r in rules),
        "no reply WERA could use",
    )


def _template(p: SectionPlan, inp: PackInput) -> Paragraph:
    text, cited = template_paragraph(
        p, inp.cv, title=inp.job.title, company=inp.job.company, intermediary=dedupe.is_intermediary(inp.job.company)
    )
    return Paragraph(text=text, source_ids=cited, section=p.section, theme=None)


def _assemble(
    inp: PackInput, plans: list[SectionPlan], written: dict[str, Paragraph | None], replaced: dict[str, str], *, repaired: bool
) -> CoverLetter:
    """The letter from its paragraphs, with the greeting and sign-off made here (LTR-05)."""
    cv, job = inp.cv, inp.job
    post = job.description_md or ""
    paragraphs = [para for p in plans if (para := written[p.location]) is not None]
    text = " ".join(p.text for p in paragraphs)
    # A quote ending a sentence loses its full stop inside the quotation marks.
    quotes = [q for p in plans for q in p.quotes if q.text.rstrip(".") in text]

    def used(*kinds: str) -> str:
        """The quote as the letter has it, so it is still a substring of both letter and post."""
        found = next((q.text for kind in kinds for q in quotes if q.kind == kind), "")
        return found if found in text else found.rstrip(".")

    contact = named_contact(post)
    violations = [
        Violation(
            location=location,
            rule="replaced",
            detail=f"WERA wrote paragraph {int(location[1:]) + 1} from your CV's own lines instead: the one it drafted "
            f"had {why}, and writing it again didn't fix that. It is plainer than the rest; worth a read.",
        )
        for location, why in sorted(replaced.items())
    ]
    return CoverLetter(
        job_id=job.id,
        greeting=f"Dear {contact}," if contact else "Dear Hiring Team,",
        paragraphs=paragraphs,
        signoff=f"Best regards,\n{display_case(cv.person.name)}",
        # LTR-02: the post's words the letter really quotes, of those it was given; else any
        # other span of the post a paragraph quoted.
        company_fact=CompanyFact(
            quote=used("company", "requirement") or next((q for para in paragraphs for q in post_quotes(para.text, post)), ""),
            policy_quote=used("policy"),
            site_quote=used("site"),
        ),
        word_count=words(text),
        guard=GuardResult(pass_=not violations, violations=violations, repaired=repaired),
        hook_type="Introduction",
    )


# Tokens the employer's words are held as while the guard reads a paragraph. Words, not numbers
# or letters: a digit would read as an uncited figure, and a lone "C" or "R" is a language.
_TOKENS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
]


def mask(text: str, phrases: list[str]) -> tuple[str, dict[str, str]]:
    """The text with each phrase held as a token, and the tokens to put back (20 Nov).

    Longest first, so a quote that contains the company's name is held whole. Past the last
    token a phrase is taken out as `_without` did, which loses it from a repair but never makes
    it a claim.
    """
    mapping: dict[str, str] = {}
    for phrase in sorted({p for p in phrases if p and p.strip()}, key=len, reverse=True):
        if phrase not in text:
            continue
        if len(mapping) == len(_TOKENS):
            text = "".join(text.split(phrase))
            continue
        token = f"⟦employer-{_TOKENS[len(mapping)]}⟧"
        mapping[token] = phrase
        text = text.replace(phrase, token)
    return text, mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    for token, phrase in mapping.items():
        text = text.replace(token, phrase)
    return text


def _better(candidate: CoverLetter, best: CoverLetter) -> bool:
    """Which of two imperfect letters to keep.

    Length first: a letter missing a paragraph is missing an argument, and no amount of good
    phrasing replaces it. Then reaching LTR-01's word floor, then fewer filler phrases, then the
    longer — so a retry that came back shorter never replaces the letter it was meant to improve.
    """
    if len(candidate.paragraphs) != len(best.paragraphs):
        return len(candidate.paragraphs) > len(best.paragraphs)
    reached = (candidate.word_count >= TARGET_MIN_WORDS, best.word_count >= TARGET_MIN_WORDS)
    if reached[0] != reached[1]:
        return reached[0]
    filler = (len(banned_phrases(candidate)), len(banned_phrases(best)))
    if filler[0] != filler[1]:
        return filler[0] < filler[1]
    return candidate.word_count > best.word_count


def banned_phrases(letter: CoverLetter) -> list[str]:
    """LTR-04: the configured filler phrases this letter actually contains.

    Checked after the guard rather than before, because the guard rewrites paragraphs and a
    repair can introduce exactly the sort of hedging flannel this is looking for.
    """
    phrases = [p.strip() for p in wera().letter_banned_phrases if p.strip()]
    if not phrases:
        return []
    text = " ".join([letter.greeting, *(p.text for p in letter.paragraphs), letter.signoff]).lower()
    return [p for p in phrases if p.lower() in text]


def _filler_feedback(hits: list[str]) -> str:
    """Quote the phrases back. "Be less formal" is not actionable; "lose these words" is."""
    listed = "; ".join(f'"{h}"' for h in hits[:6])
    return (
        f"Your last letter used {len(hits)} phrase(s) that make it read like a template: {listed}. "
        "Write it again without them — say the specific thing those phrases were standing in for, "
        "or cut the sentence."
    )


def _what_they_want(inp: PackInput) -> str:
    """What the applicant is looking for, so the company paragraph has something true to connect to.

    The letter has to say why *this* company suits *this* person, and the honest material for the
    second half of that is the person's own stated preferences — not a guess at their values.
    Without this the model has only the CV, which says what they have done and nothing about what
    they want next.
    """
    profile = inp.profile
    if profile is None:
        return "not stated"
    remote = {
        "any": "open to remote, hybrid or on-site",
        "remote_only": "remote only",
        "no_remote": "on-site or hybrid, not remote",
    }
    parts = [
        f"Working arrangement: {remote.get(profile.remote, profile.remote)}",
        f"Levels: {', '.join(profile.seniority)}" if profile.seniority else "",
        f"Places: {', '.join([*profile.locations, *profile.countries])}" if (profile.locations or profile.countries) else "",
    ]
    return "\n".join(p for p in parts if p) or "not stated"


def _letter_note(note: str | None, feedback: str | None) -> str:
    parts = [p for p in (note, feedback) if p]
    return "\n".join(parts) or "nothing in particular"


def _short_feedback(candidate: CoverLetter) -> str:
    """The count, and where the words go. "Write more" is a total the model undershot once
    already; which paragraphs are thin, and what to fill them with, is something it can act on."""
    counts = ", ".join(f"paragraph {i} has {len(p.text.split())}" for i, p in enumerate(candidate.paragraphs, 1))
    missing = (
        " It had no separate paragraph on why this company; write all four."
        if not any(p.section == "strategic_alignment_and_culture" for p in candidate.paragraphs)
        else ""
    )
    return (
        f"Your last letter was {candidate.word_count} words ({counts}); it needs {TARGET_MIN_WORDS}–{TARGET_MAX_WORDS}.{missing} "
        "Lengthen the thinnest paragraphs to their budgets by citing another CV line that answers "
        "what the post asks for — never by padding or by adding what the lines don't say."
    )


def _dropped_feedback(candidate: CoverLetter) -> str:
    """What to tell the model about its last attempt, in terms of its own output."""
    cuts = [v for v in candidate.guard.violations if v.rule == "dropped"] or candidate.guard.violations
    reasons = [f"paragraph {_paragraph_number(v.location)}: {v.detail}" for v in cuts] or ["a claim the CV doesn't carry"]
    left = (
        f"{len(candidate.paragraphs)} — too few"
        if len(candidate.paragraphs) < MIN_PARAGRAPHS
        else f"{len(candidate.paragraphs)} paragraphs and {candidate.word_count} words, under the {TARGET_MIN_WORDS} a letter needs"
    )
    return (
        f"Your last letter lost {len(cuts) or 1} paragraph(s) to WERA's citation check, leaving {left}. "
        f"Reasons given: {' '.join(reasons)} "
        "Write all 4 paragraphs again, each to its budget, where every claim is carried by the CV lines you cite: "
        "for a paragraph that was cut, cite different lines that do say it, or say less."
    )


def _paragraph_number(location: str | None) -> str:
    """`p1` as the person counts it, "2"."""
    if location and location.startswith("p") and location[1:].isdigit():
        return str(int(location[1:]) + 1)
    return "?"


def _guard_letter(session: Session, out: LetterOutput, inp: PackInput) -> CoverLetter:
    """One model reply, guarded and assembled."""
    cv = inp.cv
    paragraphs = [
        Paragraph(text=p.text, source_ids=p.source_ids, section=p.section, theme=p.theme or None) for p in out.paragraphs
    ]
    # LTR-02/06: only a quote that really is in the post counts as one. Both are checked the same
    # way, because the company half of a letter is where a model is most tempted to invent — it
    # has no CV to be held to there, so the post is the only thing that can hold it.
    post = inp.job.description_md or ""
    quote = out.company_quote if out.company_quote and out.company_quote in post else ""
    policy = out.policy_quote if out.policy_quote and out.policy_quote in post else ""
    # 18 Nov: a fact from the company's own site counts only if it is word for word in the page
    # the brief read and stored — the same rule as the post's, against the page instead.
    site_text = inp.brief.site.text if inp.brief and inp.brief.site else ""
    site = out.site_quote if out.site_quote and site_text and out.site_quote in site_text else ""
    # The guard checks what the letter claims about the applicant. Words quoted from the post —
    # the company fact, the role, the company name — are the employer's, not a claim about the
    # applicant, so they are taken out before checking. Otherwise a number inside a quoted
    # sentence reads as an uncited figure and a good paragraph gets dropped.
    quoted = [
        quote,
        policy,
        inp.evidence.job_quote,
        *title_forms(inp.job.title),
        inp.job.company,
        site,
        *_context_quotes(inp),
        *(q for p in paragraphs for q in post_quotes(p.text, post)),
        *(q for p in paragraphs for q in (post_quotes(p.text, site_text) if site_text else [])),
    ]
    # Held as tokens rather than cut out (20 Nov): a repair used to be handed the paragraph without
    # them and printed without them, so a repaired paragraph lost the company's name and its quote.
    masks = [mask(p.text, quoted) for p in paragraphs]
    result, decisions = guard_and_repair(
        session,
        [(f"p{i}", Cited(text=masks[i][0], source_ids=p.source_ids)) for i, p in enumerate(paragraphs)],
        cv,
        [k.term for k in inp.evidence.keywords],
        # The letter is the one document whose prose a model writes, so it is the one held to the
        # claim check as well as the regex rules.
        claims=True,
        # The close asks for a conversation; it has nothing to cite. It was being dropped for
        # "no master-CV source", and the claim check now covers what a close could get wrong.
        # The company paragraph too, since the opening took "why this company" (14 Nov): what the
        # post says about how the company works needs no CV line, and the claim check still cuts
        # one that says something about the applicant its lines don't carry.
        unsourced_ok={
            f"p{i}"
            for i, p in enumerate(paragraphs)
            if p.section in ("call_to_action_and_close", "strategic_alignment_and_culture")
        },
        names=True,
    )
    for v in result.violations:
        v.detail = unmask(v.detail, masks[int(v.location[1:])][1] if v.location[1:].isdigit() else {})
    # A paragraph the guard accepted keeps its full wording, quote included. A repaired one keeps
    # the section and theme of the paragraph it replaces: the repair fixes a claim, not its place.
    kept = [_repaired(p, decisions[f"p{i}"], masks[i][1]) if f"p{i}" in decisions else p for i, p in enumerate(paragraphs)]
    # LTR-02: the letter always opens by introducing the applicant. When the guard cuts the
    # model's opening — 3 of 6 on 14 Nov, each for a claim like "which aligns with my goal" — the
    # letter used to start on the stack instead, the thing the opening exists to put second.
    if paragraphs and paragraphs[0].section == "opening_hook" and kept[0] is None:
        kept[0] = plain_introduction(inp, quote)
        for v in result.violations:
            if v.location == "p0" and v.rule == "dropped":
                v.detail += " WERA opened with a plain introduction from your CV instead."
    paragraphs = [p for p in kept if p is not None]
    text = " ".join(p.text for p in paragraphs)
    return CoverLetter(
        job_id=inp.job.id,
        greeting=out.greeting,
        paragraphs=paragraphs,
        # The name from the CV, in ordinary case (20 Nov): a CV that writes "ELEANOR VANCE" in its
        # heading signs its letters "Eleanor Vance".
        signoff=f"{out.signoff.partition(chr(10))[0]}\n{display_case(cv.person.name)}",
        company_fact=CompanyFact(quote=quote, policy_quote=policy, site_quote=site),
        word_count=words(text),
        guard=result,
        hook_type=out.hook_type,
    )


def plain_introduction(inp: PackInput, quote: str) -> Paragraph:
    """An opening with nothing in it to check: the CV's own words, the post's, and the role.

    Built in code from verbatim fields — the first education entry (or the headline), the company
    quote the letter already verified against the post, and the job title — so there is no claim
    about the applicant for the guard to doubt. Plainer than a written one, and always there.
    """
    cv, job = inp.cv, inp.job
    # Citing the education entry it quotes (20 Nov, when entries became citable lines).
    sentences: list[str] = []
    cited: list[str] = []
    if cv.education:
        cited.append(cv.education[0].id)
        first = cv.education[0]
        sentences.append(f"I studied {no_stop(first.degree)} at {no_stop(first.institution)}.")
    elif cv.person.headline:
        sentences.append(f"{no_stop(cv.person.headline)}.")
    if quote:
        sentences.append(f"What drew me to {job.company} is how your post puts it: “{no_stop(quote)}”.")
    sentences.append(f"The {job.title} role is the work I want to do next.")
    return Paragraph(text=" ".join(sentences), source_ids=cited, section="opening_hook", theme=None)


def _context_quotes(inp: PackInput) -> list[str]:
    """The strategic-context excerpts the letter was given. They are the post's words, checked
    against it already (`QuotedContext`), so like `companyQuote` they are not claims to guard."""
    if inp.analysis is None:
        return []
    return [v for v in inp.analysis.strategic_context.model_dump(by_alias=False).values() if v]


def _repaired(original: Paragraph, replacement: Cited | None, mapping: dict[str, str] | None = None) -> Paragraph | None:
    """The repair in the original's place, with the employer's words it held as tokens put back."""
    if replacement is None:
        return None
    fields = replacement.model_dump(by_alias=False)
    fields["text"] = unmask(fields["text"], mapping or {})
    return Paragraph(**fields, section=original.section, theme=original.theme)


# Text in quotation marks, straight or curly: what a letter says in the post's own words.
_QUOTED = re.compile(r"[\"“”]([^\"“”]{12,400})[\"“”]")


def post_quotes(text: str, post: str) -> list[str]:
    """The spans a paragraph puts in quotation marks that are word for word in the post (14 Nov).

    The opening now says why this company, and said in the model's own words it reused the post's
    keywords — "a scale that demands robust, full-stack engineering" — which the citation rules
    read as skills the applicant claims without a line. Quoted exactly, it is the employer
    speaking, like `companyQuote`, and is taken out before the guard reads the paragraph. Only a
    span the post really contains counts: a quote the post doesn't have is still checked.
    """
    flat = " ".join(post.split()).casefold()
    return [m for m in (" ".join(g.split()) for g in _QUOTED.findall(text)) if quoted_from(m, flat)]


# What a quotation may add to the words it quotes: the sentence's punctuation at either end, and
# an ellipsis where it skips some. Measured on 20 Nov's eval: 8 of 24 paragraphs written again
# had quoted the post with a full stop inside the marks, or with "…" between two exact pieces.
_ENDS = ".,;:!?'’\"“” "
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")


def quoted_from(quote: str, flat_source: str) -> bool:
    """Whether a quotation is word for word in a source (already flattened and case-folded)."""
    pieces = [p.strip(_ENDS).casefold() for p in _ELLIPSIS.split(quote)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(p in flat_source for p in pieces)
