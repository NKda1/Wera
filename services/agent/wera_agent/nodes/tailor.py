"""Tailored CV (TAIL-01…06) and cover letter (LTR-01…05).

Each node turns one model reply into the stored contract, then puts every written line through
the citation guard and its repair pass. What the guard can't vouch for is dropped and listed,
so the review screen can show it.

The job description reaches the model inside an untrusted-data block (§9.3): it is the one
input here that an employer — or someone posting as one — controls.
"""

import logfire
from sqlalchemy.orm import Session

from ..config import wera
from ..llm.demo import PackInput, words
from ..llm.gateway import structured
from ..llm.prompts import cv_text, untrusted
from ..schemas.cv import MasterCV
from ..schemas.llm_outputs import LetterOutput, TailorOutput
from ..schemas.pack import Cited, CompanyFact, CoverLetter, CvItem, CvSection, TailoredCV
from .guard import guard_and_repair

# Roughly how many bullets fit, by CV length. The model is told the budget; `estimate_pages`
# checks what actually came back.
LINES_PER_PAGE = 6

# LTR-01: a letter is 3 or 4 paragraphs. One redraft when the guard leaves fewer — two calls is
# the most a letter is worth, and the person can always ask for changes at Gate 2.
MIN_PARAGRAPHS = 3
MAX_LETTER_ATTEMPTS = 2


def job_block(inp: PackInput) -> str:
    """The job as the model sees it: facts WERA parsed, then the post itself as data."""
    job = inp.job
    header = f"Job: {job.title} at {job.company} ({job.location.raw})"
    return f"{header}\n{untrusted('job post', job.description_md or '')}"


def _keywords(inp: PackInput) -> str:
    required = [k.term for k in inp.evidence.keywords if k.weight == "required"]
    nice = [k.term for k in inp.evidence.keywords if k.weight == "nice"]
    return f"required — {', '.join(required) or 'none'}; nice to have — {', '.join(nice) or 'none'}"


def tailor(session: Session, inp: PackInput, *, hints: list[str], attempt: int) -> TailoredCV:
    """TAIL-01: selects and orders master-CV lines for this job. `hints` are keywords a previous
    ATS check found missing but backed by the CV (ATSC-03)."""
    cv = inp.cv
    note = inp.note or ""
    if hints:
        note = f"{note}\nCover these if the CV supports them: {', '.join(hints)}".strip()
    out = structured(
        session,
        "cv.tailor",
        TailorOutput,
        {
            "cv": cv_text(cv),
            "job": job_block(inp),
            "keywords": _keywords(inp),
            "note": note or "nothing in particular",
            "pages": str(inp.cv_pages),
            "budget": str(inp.cv_pages * LINES_PER_PAGE),
        },
        context={"cv": cv},
        demo_input=inp,
    )

    sections = [
        CvSection(kind=kind, items=[CvItem(role_id=i.role_id, bullets=[_cited(b) for b in i.bullets]) for i in items])
        for kind, items in (("experience", out.experience), ("projects", out.projects))
        if items
    ]
    doc = TailoredCV(
        job_id=inp.job.id,
        cv_version=cv.version,
        summary=_cited(out.summary),
        sections=sections,
        skills=out.skills,
        omitted_bullet_ids=out.omitted_bullet_ids,
        guard={"pass": True, "violations": [], "repaired": False},
    )
    return _guard_cv(session, doc, inp)


def _cited(item) -> Cited:  # noqa: ANN001 — a Cites from llm_outputs
    return Cited(text=item.text, source_ids=item.source_ids)


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
            },
            context={"cv": cv},
            demo_input=inp,
        )
        candidate = _guard_letter(session, out, inp)
        filler = banned_phrases(candidate)
        if len(candidate.paragraphs) >= MIN_PARAGRAPHS and not filler:
            return candidate
        if best is None or _better(candidate, best):
            best = candidate
        # One reason at a time would waste an attempt: a letter can be both too short and full of
        # filler, and there are only two attempts.
        feedback = " ".join(
            p
            for p in (
                _dropped_feedback(candidate) if len(candidate.paragraphs) < MIN_PARAGRAPHS else None,
                _filler_feedback(filler) if filler else None,
            )
            if p
        )
        logfire.info(
            "Letter for {job_id} came back with {kept} paragraph(s) and {filler} filler phrase(s); asking again",
            job_id=inp.job.id,
            kept=len(candidate.paragraphs),
            filler=len(filler),
        )

    return best  # type: ignore[return-value] — the loop always runs at least once


def _better(candidate: CoverLetter, best: CoverLetter) -> bool:
    """Which of two imperfect letters to keep.

    Length first: a letter missing a paragraph is missing an argument, and no amount of good
    phrasing replaces it. Filler only decides between letters of the same length.
    """
    if len(candidate.paragraphs) != len(best.paragraphs):
        return len(candidate.paragraphs) > len(best.paragraphs)
    return len(banned_phrases(candidate)) < len(banned_phrases(best))


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
    paragraphs = [_cited(p) for p in out.paragraphs]
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
    quoted = [quote, policy, inp.evidence.job_quote, inp.job.title, inp.job.company]
    result, decisions = guard_and_repair(
        session,
        [(f"p{i}", Cited(text=_without(p.text, quoted), source_ids=p.source_ids)) for i, p in enumerate(paragraphs)],
        cv,
        [k.term for k in inp.evidence.keywords],
    )
    # A paragraph the guard accepted keeps its full wording, quote included.
    kept = [decisions.get(f"p{i}", p) for i, p in enumerate(paragraphs)]
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
    )


def _without(text: str, quoted: list[str]) -> str:
    for phrase in quoted:
        if phrase:
            text = "".join(text.split(phrase))
    return text
