"""Demo-mode stand-in for the writing tier (DEMO-01), ported from the web app's mock writer.

It behaves like the SPEC §9.3 contract without any network: it selects master-CV bullets
for the job, keeps their text verbatim with real source ids, and computes the ATS report in
code. The real model replaces the selection and wording; the guard, coverage and retries stay.
"""

import re
from dataclasses import dataclass

from ..domain.guard import check_cited
from ..domain.questions import SENSITIVE, kind, links_answer
from ..domain.scoring import should_retry_tailoring
from ..domain.vocab import skill_text, text_has_term
from ..nodes.ats import build_ats
from ..schemas.cv import Bullet, MasterCV, Role
from ..schemas.jobs import FitEvidence, Job
from ..schemas.pack import (
    AnswerItem,
    AtsReport,
    Cited,
    CompanyFact,
    CoverLetter,
    CvItem,
    CvSection,
    GuardResult,
    Pack,
    TailoredCV,
)


@dataclass
class PackInput:
    job: Job
    evidence: FitEvidence
    questions: list[str]
    cv: MasterCV
    cv_pages: int
    note: str | None = None
    # The tailored CV, once a node has one: the letter and the answers are written against it.
    doc: TailoredCV | None = None


def lc_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def no_stop(s: str) -> str:
    return re.sub(r"\.\s*$", "", s)


def words(s: str) -> int:
    return len([w for w in re.split(r"\s+", s) if w])


# ---------------------------------------------------------------------------
# Tailored CV


def _note_hints(note: str | None, cv: MasterCV) -> list[str]:
    if not note:
        return []
    terms = [t for s in cv.skills for t in [s.name, *(s.aliases or [])]]
    return [t for t in terms if text_has_term(note, t)]


def _select_bullets(inp: PackInput, total: int, per_role: int, extra_terms: list[str]) -> dict[str, list[Bullet]]:
    cv, evidence = inp.cv, inp.evidence
    hints = _note_hints(inp.note, cv)

    def score(b: Bullet) -> int:
        text = f"{b.text} {' '.join(b.tags)}"
        n = 0
        for k in evidence.keywords:
            if text_has_term(text, k.term):
                n += 2 if k.weight == "required" else 1
        for t in [*hints, *extra_terms]:
            if text_has_term(text, t):
                n += 4
        if b.id in evidence.cv_bullet_ids:
            n += 3
        return n

    roles = [*cv.experience, *cv.projects]
    candidates = [(role, b, order, score(b)) for role in roles for order, b in enumerate(role.bullets)]
    candidates.sort(key=lambda c: (-c[3], c[2]))  # stable, like Array.prototype.sort

    chosen: dict[str, list[Bullet]] = {}
    count = 0
    for role, b, _order, sc in candidates:
        if count >= total or sc <= 0:
            break
        picked = chosen.setdefault(role.id, [])
        if len(picked) >= per_role:
            continue
        picked.append(b)
        count += 1
    # Keep master-CV order inside each role.
    for role in roles:
        if role.id in chosen:
            chosen[role.id].sort(key=lambda b, r=role: r.bullets.index(b))
    return chosen


def _tailor_skills(inp: PackInput) -> list[str]:
    relevant = [s for s in inp.cv.skills if any(text_has_term(skill_text(s), k.term) for k in inp.evidence.keywords)]
    rest = [s for s in inp.cv.skills if s not in relevant]
    return [s.name for s in [*relevant, *rest][:10]]


def guard_cv(doc: TailoredCV, cv: MasterCV, evidence: FitEvidence) -> GuardResult:
    items: list[tuple[str, Cited]] = []
    if doc.summary.text:
        items.append(("summary", doc.summary))
    for si, s in enumerate(doc.sections):
        for ii, it in enumerate(s.items):
            for bi, b in enumerate(it.bullets):
                items.append((f"s{si}.i{ii}.b{bi}", b))
    return check_cited(items, cv, [k.term for k in evidence.keywords])


def _build_cv(inp: PackInput, chosen: dict[str, list[Bullet]]) -> TailoredCV:
    cv, job = inp.cv, inp.job

    def section(kind: str, roles: list[Role]) -> CvSection:
        return CvSection(
            kind=kind,
            items=[
                CvItem(role_id=r.id, bullets=[Cited(text=b.text, source_ids=[b.id]) for b in chosen[r.id]])
                for r in roles
                if r.id in chosen
            ],
        )

    sections = [s for s in (section("experience", cv.experience), section("projects", cv.projects)) if s.items]
    used = {b.id for bs in chosen.values() for b in bs}
    omitted = [b.id for r in [*cv.experience, *cv.projects] for b in r.bullets if b.id not in used]
    summary = Cited(text=cv.summary.text, source_ids=[cv.summary.id]) if cv.summary else Cited(text="", source_ids=[])
    doc = TailoredCV(
        job_id=job.id,
        cv_version=cv.version,
        summary=summary,
        sections=sections,
        skills=_tailor_skills(inp),
        omitted_bullet_ids=omitted,
        guard=GuardResult(pass_=True, violations=[], repaired=False),
    )
    doc.guard = guard_cv(doc, cv, inp.evidence)
    return doc


# ---------------------------------------------------------------------------
# Cover letter (LTR-01…05)


def company_fact(description: str | None, company: str) -> str:
    if not description:
        return ""
    prose = [ln.strip() for ln in description.split("\n")]
    prose = [ln for ln in prose if ln and not ln.startswith("#") and not ln.startswith("-")]
    line = next((ln for ln in prose if company in ln), prose[0] if prose else "")
    m = re.match(r"^.*?[.!?](?=\s|$)", line)
    return m.group(0) if m else line


def _named_contact(description: str | None) -> str | None:
    m = re.search(r"hiring manager(?: is|:)\s+([A-Z][a-z]+ [A-Z][a-z]+)", description or "", re.I)
    return m.group(1) if m else None


def guard_letter(letter: CoverLetter, cv: MasterCV, evidence: FitEvidence, job: Job) -> GuardResult:
    """Claims about the user are checked; quotes and names from the job post are not (LTR-03)."""

    def strip(text: str) -> str:
        for q in [letter.company_fact.quote, evidence.job_quote, job.title, job.company]:
            if q:
                text = "".join(text.split(q))
        return text

    items = [
        (f"p{i}", Cited(text=strip(p.text), source_ids=p.source_ids, edited_by_user=p.edited_by_user))
        for i, p in enumerate(letter.paragraphs)
    ]
    return check_cited(items, cv, [k.term for k in evidence.keywords])


def _build_letter(inp: PackInput, doc: TailoredCV) -> CoverLetter:
    cv, job, evidence = inp.cv, inp.job, inp.evidence
    roles = {r.id: r for r in [*cv.experience, *cv.projects]}
    fact = company_fact(job.description_md, job.company)
    paragraphs: list[Cited] = []

    intro = [f"I'm applying for the {job.title} role at {job.company}."]
    if fact:
        intro.append(f"Your post describes the company in one line: “{fact}”")
    if cv.summary:
        intro.append(f"I'm a {no_stop(cv.summary.text)}, and that is the kind of work I want to keep doing.")
    paragraphs.append(Cited(text=" ".join(intro), source_ids=[cv.summary.id] if cv.summary else []))

    items = [it for s in doc.sections for it in s.items]
    for idx, item in enumerate(items[:2]):
        role = roles[item.role_id]
        first = item.bullets[0]
        second = item.bullets[1] if len(item.bullets) > 1 else None
        parts = [f"{'As' if idx == 0 else 'Separately, as'} {role.title} at {role.org}, I {lc_first(no_stop(first.text))}."]
        if second:
            parts.append(f"I also {lc_first(no_stop(second.text))}.")
        if idx == 0 and evidence.job_quote and job.description_md and evidence.job_quote in job.description_md:
            parts.append(f"That is close to what you describe as “{evidence.job_quote}”.")
        paragraphs.append(Cited(text=" ".join(parts), source_ids=[sid for b in item.bullets[:2] for sid in b.source_ids]))

    closing = (
        f"I'd welcome the chance to talk about how this experience could help {job.company}, and to hear more about the team. "
        "Thank you for considering my application."
    )
    extra = items[2].bullets[0] if len(items) > 2 and items[2].bullets else None
    if extra:
        role = roles[items[2].role_id]
        paragraphs.append(
            Cited(
                text=f"Outside that, as {role.title} for {role.org}, I {lc_first(no_stop(extra.text))}. {closing}",
                source_ids=extra.source_ids,
            )
        )
    else:
        paragraphs.append(Cited(text=closing, source_ids=[cv.summary.id] if cv.summary else []))

    contact = _named_contact(job.description_md)
    letter = CoverLetter(
        job_id=job.id,
        greeting=f"Dear {contact}," if contact else "Dear Hiring Team,",
        paragraphs=paragraphs,
        signoff=f"Kind regards,\n{cv.person.name}",
        company_fact=CompanyFact(quote=fact),
        word_count=sum(words(p.text) for p in paragraphs),
        guard=GuardResult(pass_=True, violations=[], repaired=False),
    )
    letter.guard = guard_letter(letter, cv, evidence, job)
    return letter


# ---------------------------------------------------------------------------
# Answer pack (ANS-01…04)


def _build_answers(inp: PackInput, doc: TailoredCV) -> list[AnswerItem]:
    cv, job = inp.cv, inp.job
    lines = {b.id: (b, r) for r in [*cv.experience, *cv.projects] for b in r.bullets}
    out: list[AnswerItem] = []
    for i, question in enumerate(inp.questions):
        qid = f"q{i + 1}"
        if SENSITIVE.search(question):
            out.append(AnswerItem(id=qid, question=question, answer=None, source_ids=[], sensitive=True))
        elif kind(question) == "links":
            out.append(AnswerItem(id=qid, question=question, answer=links_answer(cv), source_ids=[], sensitive=False))
        elif re.match(r"^why\b", question, re.I) and cv.summary:
            fact = company_fact(job.description_md, job.company)
            answer = (
                f"{f'{fact} ' if fact else ''}I'm a {no_stop(cv.summary.text)}. "
                f"The {job.title} role is the closest match I've seen to the work I've already done and want to keep doing."
            )
            out.append(AnswerItem(id=qid, question=question, answer=answer, source_ids=[cv.summary.id], sensitive=False))
        elif re.search(r"tell us about|describe|example|a time you", question, re.I):
            first = (
                doc.sections[0].items[0].bullets[0]
                if doc.sections and doc.sections[0].items and doc.sections[0].items[0].bullets
                else None
            )
            hit = lines.get(first.source_ids[0]) if first else None
            if not hit:
                out.append(AnswerItem(id=qid, question=question, answer=None, source_ids=[], sensitive=False))
            else:
                b, r = hit
                out.append(
                    AnswerItem(
                        id=qid,
                        question=question,
                        answer=f"As {r.title} at {r.org}, I {lc_first(no_stop(b.text))}.",
                        source_ids=[b.id],
                        sensitive=False,
                    )
                )
        else:
            out.append(AnswerItem(id=qid, question=question, answer=None, source_ids=[], sensitive=False))
    return out


# ---------------------------------------------------------------------------


def generate_pack(inp: PackInput) -> Pack:
    short = bool(re.search(r"\b(short|shorter|trim|tighter|one page|1 page)\b", inp.note or "", re.I))
    total = 5 if inp.cv_pages == 1 else 9
    per_role = 2 if inp.cv_pages == 1 or short else 3
    if short:
        total -= 1

    # ATSC-03: re-tailor with missing-but-backed keywords as hints, max 2 retries.
    attempt = 1
    extra: list[str] = []
    doc = _build_cv(inp, _select_bullets(inp, total, per_role, extra))
    ats = build_ats(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, job=inp.job, attempt=attempt)
    while should_retry_tailoring(ats) and attempt < 3:
        attempt += 1
        extra = [k.term for k in ats.keywords if not k.matched and k.in_master_cv]
        total += 1
        doc = _build_cv(inp, _select_bullets(inp, total, per_role + 1, extra))
        ats = build_ats(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, job=inp.job, attempt=attempt)

    return Pack(cv=doc, letter=_build_letter(inp, doc), ats=ats, answers=_build_answers(inp, doc))


def refresh_ats(pack: Pack, *, cv: MasterCV, evidence: FitEvidence, cv_pages: int, job: Job) -> AtsReport:
    """Recompute the ATS report after the user edits a document (format and coverage only)."""
    return build_ats(pack.cv, cv=cv, evidence=evidence, cv_pages=cv_pages, job=job, attempt=pack.ats.attempt)


# ---------------------------------------------------------------------------
# Task responders (DEMO-02)
#
# `llm.gateway.structured()` calls these instead of a model when demo mode is on. They return
# the same dicts a model would, and the gateway validates them against the same schema, so the
# demo path exercises the real contract rather than bypassing it.


SENIORITY_WORDS = re.compile(r"^(senior|staff|principal|lead|head of|junior|graduate|intern)\s+", re.I)


def _target_titles(cv: MasterCV) -> list[str]:
    """Titles the CV supports, from its own role titles, with seniority words stripped."""
    out: list[str] = []
    for role in cv.experience:
        title = SENIORITY_WORDS.sub("", role.title).strip()
        # "AI Intern" → "AI Engineer": a demo stand-in for what the model would reason about.
        title = re.sub(r"\bIntern\b", "Engineer", title, flags=re.I)
        if title and title not in out:
            out.append(title)
    for skill in cv.skills[:2]:
        candidate = f"{skill.name} Engineer"
        if candidate not in out:
            out.append(candidate)
    return out[:8]


def _profile_proposal(cv: MasterCV) -> dict:
    return {
        "titles": _target_titles(cv),
        "skills": [s.name for s in cv.skills[:12]],
        "locations": [cv.person.location],
        "remote": "any",
        "seniority": ["graduate", "junior", "entry"],
        "exclude": ["Senior", "Staff", "Principal", "Lead"],
        "recency": "7d",
    }


def _budget(inp: PackInput) -> tuple[int, int]:
    """How many lines fit, and how many per role — the Phase 1 rule, kept."""
    short = bool(re.search(r"\b(short|shorter|trim|tighter|one page|1 page)\b", inp.note or "", re.I))
    total = 5 if inp.cv_pages == 1 else 9
    per_role = 2 if inp.cv_pages == 1 or short else 3
    return (total - 1 if short else total), per_role


def _cites(item: Cited) -> dict:
    return {"text": item.text, "sourceIds": item.source_ids}


def _tailor_output(inp: PackInput) -> dict:
    """Selection, as the writing tier would return it. Keyword hints ride in on the note, which
    `_note_hints` already reads, so the ATS retry loop needs nothing extra here."""
    total, per_role = _budget(inp)
    doc = _build_cv(inp, _select_bullets(inp, total, per_role, []))
    items = {s.kind: [{"roleId": i.role_id, "bullets": [_cites(b) for b in i.bullets]} for i in s.items] for s in doc.sections}
    return {
        "summary": _cites(doc.summary),
        "experience": items.get("experience", []),
        "projects": items.get("projects", []),
        "skills": doc.skills,
        "omittedBulletIds": doc.omitted_bullet_ids,
    }


def _letter_output(inp: PackInput) -> dict:
    if inp.doc is None:
        raise KeyError("letter.write needs the tailored CV")
    letter = _build_letter(inp, inp.doc)
    return {
        "greeting": letter.greeting,
        "paragraphs": [_cites(p) for p in letter.paragraphs],
        "signoff": letter.signoff,
        "companyQuote": letter.company_fact.quote,
    }


def _answers_output(inp: PackInput) -> dict:
    """Only the questions a model would really be asked: the node answers the sensitive and
    link ones itself, so returning them here would break the schema that requires citations."""
    drafted = _build_answers(inp, inp.doc or TailoredCV.model_construct(sections=[]))
    return {
        "answers": [{"id": a.id, "answer": a.answer, "sourceIds": a.source_ids} for a in drafted if kind(a.question) == "draft"],
    }


def respond(task: str, variables: dict, context: dict | None, demo_input: PackInput | None = None) -> dict:
    """The demo answer for one §9.3 task.

    `context` carries the master CV; `demo_input` carries the job, evidence and questions for the
    pack tasks. The gateway validates whatever comes back against the same schema as a real reply.
    """
    cv = (context or {}).get("cv")
    if task == "profile.generate":
        if cv is None:
            raise KeyError("profile.generate needs the master CV in validation context")
        return _profile_proposal(cv)
    if task == "guard.repair":
        # The demo writer only ever copies master-CV lines, so there is nothing to repair. Saying
        # so (rather than inventing a fix) is what the real model does when it can't help either.
        return {"item": None}
    if demo_input is None:
        raise KeyError(f"The demo responder for {task!r} needs demo_input=PackInput")
    if task == "cv.tailor":
        return _tailor_output(demo_input)
    if task == "letter.write":
        return _letter_output(demo_input)
    if task == "answers.draft":
        return _answers_output(demo_input)
    raise KeyError(f"No demo responder for task {task!r}. Add one here, next to the real prompt.")
