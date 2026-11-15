"""Tailored CV (TAIL-01…06) and cover letter (LTR-01…05).

Each node turns one model reply into the stored contract, then puts every written line through
the citation guard and its repair pass. What the guard can't vouch for is dropped and listed,
so the review screen can show it.

The job description reaches the model inside an untrusted-data block (§9.3): it is the one
input here that an employer — or someone posting as one — controls.
"""

import re

import logfire
from sqlalchemy.orm import Session

from ..config import wera
from ..domain import dedupe
from ..domain.vocab import cv_line_index, master_cv_text, text_has_term
from ..errors import ApiError
from ..llm.demo import PackInput, no_stop, words
from ..llm.gateway import structured
from ..llm.prompts import cv_text, untrusted
from ..schemas.cv import MasterCV, Summary
from ..schemas.llm_outputs import LetterOutput, SummaryDraft, TailorSelection
from ..schemas.pack import Cited, CompanyFact, CoverLetter, CvItem, CvSection, Paragraph, TailoredCV
from .guard import guard_and_repair
from .letter_plan import context_text, keywords_text, plan

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
    job = inp.job
    header = f"Job: {job.title} at {job.company} ({job.location.raw})"
    if dedupe.is_intermediary(job.company):
        # Said outright, so the letter names the employer when the post does and otherwise
        # writes to the hiring team — not to the board as if it were hiring.
        header = (
            f"Job: {job.title} ({job.location.raw}), posted by {job.company} — a job board or agency, not the "
            "employer. Name the employer only if the post does; never write to the board as the employer."
        )
    description = (job.description_md or "")[: wera().job_description_limit]
    return f"{header}\n{untrusted('job post', description)}"


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


def letter(session: Session, inp: PackInput, doc: TailoredCV) -> CoverLetter:
    """LTR-01…05: 3–4 paragraphs, one company fact quoted from the post, every claim cited.

    The guard can drop a paragraph the CV doesn't back, and dropping one from a four-paragraph
    letter leaves three — but dropping two leaves a letter too short to send (LTR-01). So a
    letter that comes back under the minimum is asked for once more, told what was left out and
    why. If the second one is no better, the fuller of the two is kept with its violations
    showing, because a short letter the person can see the problem with beats a silent one.
    """
    cv = inp.cv
    tailored = "\n".join(f"- {b.text}" for _, b in cv_locations(doc))
    keywords = keywords_text(plan(inp.evidence, cv))
    context = context_text(inp.analysis)
    best: CoverLetter | None = None
    feedback: str | None = None

    for _attempt in range(MAX_LETTER_ATTEMPTS):
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
        candidate = _guard_letter(session, out, inp)
        filler = banned_phrases(candidate)
        dropped = len(candidate.paragraphs) < MIN_PARAGRAPHS
        # What the model wrote, not what the guard left: a paragraph cut for an unbacked claim is
        # the guard working (and shown to the person), and asking again would pay twice for it.
        written = sum(len(p.text.split()) for p in out.paragraphs)
        short = not dropped and written < TARGET_MIN_WORDS
        if not dropped and not filler and not short:
            return candidate
        if best is None or _better(candidate, best):
            best = candidate
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

    return best  # type: ignore[return-value] — the loop always runs at least once


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
    reasons = [v.detail for v in candidate.guard.violations] or ["a claim the CV doesn't carry"]
    return (
        f"Your last letter lost {len(reasons)} paragraph(s) to WERA's citation check, leaving "
        f"{len(candidate.paragraphs)} — too few. Reasons given: {' '.join(reasons)} "
        f"Write {MIN_PARAGRAPHS} or 4 paragraphs where every claim is carried by the CV lines you cite."
    )


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
    # The guard checks what the letter claims about the applicant. Words quoted from the post —
    # the company fact, the role, the company name — are the employer's, not a claim about the
    # applicant, so they are taken out before checking. Otherwise a number inside a quoted
    # sentence reads as an uncited figure and a good paragraph gets dropped.
    quoted = [
        quote,
        policy,
        inp.evidence.job_quote,
        inp.job.title,
        inp.job.company,
        *_context_quotes(inp),
        *(q for p in paragraphs for q in post_quotes(p.text, post)),
    ]
    result, decisions = guard_and_repair(
        session,
        [(f"p{i}", Cited(text=_without(p.text, quoted), source_ids=p.source_ids)) for i, p in enumerate(paragraphs)],
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
    )
    # A paragraph the guard accepted keeps its full wording, quote included. A repaired one keeps
    # the section and theme of the paragraph it replaces: the repair fixes a claim, not its place.
    kept = [_repaired(p, decisions[f"p{i}"]) if f"p{i}" in decisions else p for i, p in enumerate(paragraphs)]
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
        signoff=out.signoff,
        company_fact=CompanyFact(quote=quote, policy_quote=policy),
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
    # No source ids: an education entry isn't a citable line (only its notes are), and the
    # paragraph quotes its fields rather than claiming anything they would need to back.
    sentences: list[str] = []
    if cv.education:
        first = cv.education[0]
        sentences.append(f"I studied {no_stop(first.degree)} at {no_stop(first.institution)}.")
    elif cv.person.headline:
        sentences.append(f"{no_stop(cv.person.headline)}.")
    if quote:
        sentences.append(f"What drew me to {job.company} is how your post puts it: “{no_stop(quote)}”.")
    sentences.append(f"The {job.title} role is the work I want to do next.")
    return Paragraph(text=" ".join(sentences), source_ids=[], section="opening_hook", theme=None)


def _context_quotes(inp: PackInput) -> list[str]:
    """The strategic-context excerpts the letter was given. They are the post's words, checked
    against it already (`QuotedContext`), so like `companyQuote` they are not claims to guard."""
    if inp.analysis is None:
        return []
    return [v for v in inp.analysis.strategic_context.model_dump(by_alias=False).values() if v]


def _repaired(original: Paragraph, replacement: Cited | None) -> Paragraph | None:
    if replacement is None:
        return None
    return Paragraph(**replacement.model_dump(by_alias=False), section=original.section, theme=original.theme)


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
    return [m for m in (" ".join(g.split()) for g in _QUOTED.findall(text)) if m.casefold() in flat]


def _without(text: str, quoted: list[str]) -> str:
    for phrase in quoted:
        if phrase:
            text = "".join(text.split(phrase))
    return text
