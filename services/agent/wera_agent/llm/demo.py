"""Demo-mode stand-in for the writing tier (DEMO-01), ported from the web app's mock writer.

It behaves like the SPEC §9.3 contract without any network: it selects master-CV bullets
for the job, keeps their text verbatim with real source ids, and computes the ATS report in
code. The real model replaces the selection; the guard and the coverage pass stay.

Verbatim text was always how this responder worked, which is worth noting: the demo was the one
place a tailored CV could not drift, so it was also the one place the bug this responder now
matches — a model re-typing every bullet — never showed up.
"""

import re
from dataclasses import dataclass

from ..config import wera
from ..domain.guard import check_cited
from ..domain.questions import SENSITIVE, kind, links_answer
from ..domain.vocab import master_cv_text, skill_text, text_has_term
from ..nodes.ats import build_ats, reconcile_skills
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
    JobAnalysis,
    Pack,
    Paragraph,
    TailoredCV,
)
from ..schemas.profile import SearchProfile
from .prompts import CLOSE, OPEN


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
    # LTR-06: what the applicant is looking for. The letter has to say why this *company* suits
    # them, and the honest material for that half is their own stated preferences — the CV says
    # what they have done, never what they want next. Optional so an older caller still works.
    profile: SearchProfile | None = None
    # The job-description rubric (`nodes.analyse`), once the draft has one: what the post says
    # about why the role exists, which the letter's company half is written from.
    analysis: JobAnalysis | None = None


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
    # TAIL-07's chain, the same way `nodes.tailor` runs it: the applicant's own summary first,
    # then one composed from their cited lines. The seed path builds documents directly rather
    # than through the gateway, so the fallback has to be here too or the demo would be the one
    # place a CV still went out with no profile on it.
    if cv.summary:
        summary = Cited(text=cv.summary.text, source_ids=[cv.summary.id])
    else:
        # `composed=True` exactly as `nodes.tailor._profile` sets it. Without it the demo seed
        # writes a document the real save path refuses as drifted, which is the one place DEMO-02
        # cannot afford to differ: the demo would be modelling a pack that cannot be stored.
        drafted = _summary_output(inp)
        summary = Cited(text=drafted["text"], source_ids=drafted["sourceIds"], composed=True)
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


#: Words that mark a sentence as being about how a company works rather than what it does.
POLICY_WORDS = (
    "remote",
    "hybrid",
    "four-day",
    "flexible",
    "sponsor",
    "visa",
    "relocation",
    "learning budget",
    "training budget",
    "mentoring",
    "open source",
    "parental leave",
    "equity",
    "pension",
)


def policy_fact(description: str | None) -> str:
    """LTR-06: one sentence from the post about how the company works, quoted verbatim.

    Empty when the post says nothing of the kind, which is common. An absent paragraph is a far
    better outcome than an invented policy — this is the half of a letter with no CV behind it,
    so the post is the only thing holding it to the truth.
    """
    if not description:
        return ""
    for line in (ln.strip(" -*\t") for ln in description.split("\n")):
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        if any(w in lowered for w in POLICY_WORDS):
            match = re.match(r"^.*?[.!?](?=\s|$)", line)
            found = match.group(0) if match else line
            # Must be quotable verbatim, and short enough to read as a quote rather than a block.
            if found and len(found) <= 200 and found in description:
                return found
    return ""


def _matched_skills(inp: PackInput, limit: int = 4) -> list[str]:
    """The master CV's own skill names that this job's keywords ask for.

    The CV's spellings, not the job's: these go into a letter that the citation guard checks, and
    `check_cited` accepts a term that appears in the applicant's skills list. Taking the job's
    wording instead would be a claim about the applicant written in someone else's words.
    """
    out: list[str] = []
    for skill in inp.cv.skills:
        if any(text_has_term(skill_text(skill), k.term) for k in inp.evidence.keywords):
            out.append(skill.name)
        if len(out) == limit:
            break
    return out


def _sentence_list(items: list[str]) -> str:
    """ "a, b and c" — the way a person writes a list inside a sentence."""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _named_contact(description: str | None) -> str | None:
    m = re.search(r"hiring manager(?: is|:)\s+([A-Z][a-z]+ [A-Z][a-z]+)", description or "", re.I)
    return m.group(1) if m else None


def guard_letter(letter: CoverLetter, cv: MasterCV, evidence: FitEvidence, job: Job) -> GuardResult:
    """Claims about the user are checked; quotes and names from the job post are not (LTR-03)."""

    def strip(text: str) -> str:
        for q in [letter.company_fact.quote, letter.company_fact.policy_quote, evidence.job_quote, job.title, job.company]:
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
    paragraphs: list[Paragraph] = []

    # LTR-04: no announcing the application and no thanking them for reading. The real prompt
    # bans both, and a demo that models what the product forbids teaches the wrong thing.
    intro: list[str] = []
    if cv.summary:
        intro.append(f"I'm a {no_stop(cv.summary.text)}, and the {job.title} opening at {job.company} is the work I want next.")
    else:
        intro.append(f"The {job.title} opening at {job.company} is the work I want next.")
    if fact:
        intro.append(f"Your post puts it as “{fact}” — that's the part I'd want to be close to.")
    paragraphs.append(Paragraph(text=" ".join(intro), source_ids=[cv.summary.id] if cv.summary else [], section="opening_hook"))

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
        # Two bullets per role. A third was added when LTR-01 wanted 250–350 words; at 180–260 it
        # pushed letters over the top, and a short letter is now the point.
        if idx == 1:
            overlap = _matched_skills(inp)
            if overlap:
                # Safe past the citation guard: these are the master CV's own skill names, and
                # `check_cited` accepts a term that appears in the applicant's skills list.
                parts.append(f"The overlap with what you list is {_sentence_list(overlap)}.")
        paragraphs.append(
            Paragraph(
                text=" ".join(parts),
                source_ids=[sid for b in item.bullets[:3] for sid in b.source_ids],
                section="core_value_proposition",
            )
        )

    # LTR-06: why this company, grounded in what the post says about how it works and in what the
    # applicant told WERA they want — never in a guess at their values. Folded into the closing
    # paragraph rather than added as its own, because LTR-01 caps the letter at four.
    policy = policy_fact(job.description_md)
    fit: list[str] = [f"The post says “{policy}”, which is the arrangement I'd choose." if policy else ""]
    fit = [f for f in fit if f]
    if inp.profile and inp.profile.remote == "remote_only":
        fit.append("I'm looking for remote work specifically, so that weighs more with me than it might with someone else.")
    elif inp.profile and inp.profile.remote == "no_remote":
        fit.append("I do my best work in a room with the team, so being on-site suits me.")
    elif inp.profile and job.remote:
        fit.append(f"I'm open to remote, hybrid or on-site, so the {job.remote} arrangement you describe works either way.")
    # No "{city} is one of the places I'm searching" sentence any more: it was the longest thing in
    # a close that LTR-01's shorter letter (180–260 words) has no room for.
    closing = " ".join(
        [*fit, f"I'd like to talk about where this would fit at {job.company}. I can make any afternoon next week."]
    )
    extras = items[2].bullets[:2] if len(items) > 2 else []
    if extras:
        role = roles[items[2].role_id]
        said = [f"Outside that, as {role.title} for {role.org}, I {lc_first(no_stop(extras[0].text))}."]
        if len(extras) > 1:
            said.append(f"I also {lc_first(no_stop(extras[1].text))}.")
        said.append(closing)
        paragraphs.append(
            Paragraph(
                text=" ".join(said), source_ids=[sid for b in extras for sid in b.source_ids], section="call_to_action_and_close"
            )
        )
    else:
        paragraphs.append(
            Paragraph(text=closing, source_ids=[cv.summary.id] if cv.summary else [], section="call_to_action_and_close")
        )

    contact = _named_contact(job.description_md)
    letter = CoverLetter(
        job_id=job.id,
        greeting=f"Dear {contact}," if contact else "Dear Hiring Team,",
        paragraphs=paragraphs,
        # One of `LetterOutput`'s three valedictions. This was "Kind regards", which the real path
        # would have normalised away — a demo that models what the product corrects teaches the
        # wrong thing, and `generate_pack` builds this letter directly rather than through the
        # schema, so nothing would have corrected it here.
        signoff=f"Best regards,\n{cv.person.name}",
        company_fact=CompanyFact(quote=fact, policy_quote=policy),
        word_count=sum(words(p.text) for p in paragraphs),
        guard=GuardResult(pass_=True, violations=[], repaired=False),
        hook_type="Company-Mission" if fact else "Core-Metric",
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

    # ATSC-02/03: select, then cover the job's keywords from the CV's own skills. The same two
    # steps the real path takes (`drafting.step_cv`), and for the same reason — the re-tailor
    # loop that used to be here is gone.
    doc = _build_cv(inp, _select_bullets(inp, total, per_role, []))
    doc = reconcile_skills(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, threshold=wera().ats_threshold)
    letter = _build_letter(inp, doc)
    ats = build_ats(doc, cv=inp.cv, evidence=inp.evidence, cv_pages=inp.cv_pages, job=inp.job, letter=letter)

    return Pack(cv=doc, letter=letter, ats=ats, answers=_build_answers(inp, doc), analysis=inp.analysis)


def refresh_ats(pack: Pack, *, cv: MasterCV, evidence: FitEvidence, cv_pages: int, job: Job) -> AtsReport:
    """Recompute the ATS report after the user edits a document (format and coverage only)."""
    return build_ats(pack.cv, cv=cv, evidence=evidence, cv_pages=cv_pages, job=job, letter=pack.letter)


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
    """Selection, as the select tier would return it: ids only, matching `TailorSelection`.

    No bullet text, for the same reason the real schema has none — the demo exercises the real
    contract (DEMO-02), and a responder that still returned text would be the one place where a
    drifted CV was representable.
    """
    total, per_role = _budget(inp)
    chosen = _select_bullets(inp, total, per_role, [])
    cv = inp.cv
    used = {b.id for bs in chosen.values() for b in bs}

    def picks(roles: list[Role]) -> list[dict]:
        return [{"roleId": r.id, "bulletIds": [b.id for b in chosen[r.id]]} for r in roles if chosen.get(r.id)]

    return {
        "summaryId": cv.summary.id if cv.summary else None,
        "experience": picks(cv.experience),
        "projects": picks(cv.projects),
        "skills": _tailor_skills(inp),
        "omittedBulletIds": [b.id for r in [*cv.experience, *cv.projects] for b in r.bullets if b.id not in used],
    }


def _summary_output(inp: PackInput) -> dict:
    """A profile for a CV that has no summary of its own, as `summary.write` would return it
    (TAIL-07).

    Built out of the cited bullets' own words, which is what keeps it past `check_cited`: every
    number in the text is in a line it cites, and every term is either there or stated as a fact
    elsewhere in the CV. The same discipline as the demo letter, for the same reason.
    """
    cv = inp.cv
    total, per_role = _budget(inp)
    chosen = _select_bullets(inp, total, per_role, [])
    picked = [b for r in [*cv.experience, *cv.projects] for b in chosen.get(r.id, [])][:2]

    # What they are, from the CV's own headline or its most recent qualification — never from the
    # job title, which would make the profile unusable the moment it is forwarded.
    if cv.person.headline:
        opener = no_stop(cv.person.headline)
    elif cv.education:
        opener = no_stop(cv.education[0].degree)
    else:
        opener = f"{no_stop(cv.experience[0].title)}" if cv.experience else "Software engineer"

    parts = [f"{opener}."]
    # The bullets as written. Not `.capitalize()`, which lower-cases everything after the first
    # character and turned "FastAPI", "Presidio", "LLMs" and "C# / .NET" into mush — a profile is
    # read for its proper nouns more than for its sentences.
    parts += [f"{no_stop(b.text)}." for b in picked]
    overlap = _matched_skills(inp, limit=3)
    if overlap:
        parts.append(f"Works in {_sentence_list(overlap)}.")
    text = " ".join(parts)
    # The 20–90 word band `SummaryDraft` enforces. Trimming the last sentence is better than
    # trimming mid-clause, which is what a character budget would do.
    while words(text) > 90 and len(parts) > 2:
        parts.pop(-2)
        text = " ".join(parts)
    return {"text": text, "sourceIds": [sid for b in picked for sid in [b.id]]}


def _letter_output(inp: PackInput) -> dict:
    if inp.doc is None:
        raise KeyError("letter.write needs the tailored CV")
    letter = _build_letter(inp, inp.doc)
    return {
        "greeting": letter.greeting,
        "paragraphs": [{**_cites(p), "section": p.section} for p in letter.paragraphs],
        "signoff": letter.signoff,
        "hookType": letter.hook_type,
        "companyQuote": letter.company_fact.quote,
        "policyQuote": letter.company_fact.policy_quote,
    }


def _answers_output(inp: PackInput) -> dict:
    """Only the questions a model would really be asked: the node answers the sensitive and
    link ones itself, so returning them here would break the schema that requires citations."""
    drafted = _build_answers(inp, inp.doc or TailoredCV.model_construct(sections=[]))
    return {
        "answers": [{"id": a.id, "answer": a.answer, "sourceIds": a.source_ids} for a in drafted if kind(a.question) == "draft"],
    }


# Terms a job ad asks for that WERA can recognise without a model, used by the demo's scoring.
DEMO_TECH = (
    "Python",
    "TypeScript",
    "FastAPI",
    "Next.js",
    "PostgreSQL",
    "LangChain",
    "LangGraph",
    "REST APIs",
    "Docker",
    "Kubernetes",
    "AWS",
    "GCP",
    "Azure",
    "React",
    "Django",
    "SQL",
    "LLM guardrails",
    "Prompt engineering",
    "Presidio",
    "evaluation",
    "agents",
)


# How the demo sorts the terms it recognises into the ATS keyword matrix. Anything unlisted is a
# hard skill — a language or a field, which is what the CV's own unlisted skills are.
DEMO_CATEGORY = {
    **dict.fromkeys(
        ("FastAPI", "Next.js", "PostgreSQL", "LangChain", "LangGraph", "Docker", "Kubernetes", "AWS", "GCP", "Azure"),
        "tool",
    ),
    **dict.fromkeys(("React", "Django", "Presidio", "Stripe"), "tool"),
    **dict.fromkeys(("REST APIs", "LLM guardrails", "Prompt engineering", "evaluation", "agents"), "methodology"),
}
# Culture phrases the demo posts really use, so the soft-skill bucket has something true in it.
DEMO_CULTURE = ("curiosity", "ownership", "collaboration", "communication", "mentoring", "end to end", "good docs")


def _demo_keywords(job_text: str, cv: MasterCV) -> list[dict]:
    """ATSC-01, deterministically: the terms the ad actually contains.

    A term the CV also claims is treated as `required` — in the demo that makes coverage
    meaningful, because the keywords a person can match are the ones that matter to them.
    """
    cv_terms = {s.name for s in cv.skills}
    found: list[dict] = []
    for term in (*[s.name for s in cv.skills], *DEMO_TECH):
        if any(term == f["term"] for f in found):
            continue
        if text_has_term(job_text, term):
            weight = "required" if term in cv_terms else "nice"
            found.append({"term": term, "weight": weight, "category": DEMO_CATEGORY.get(term, "hard_skill")})
    culture = [{"term": c, "weight": "nice", "category": "soft_skill"} for c in DEMO_CULTURE if text_has_term(job_text, c)]
    return found[:25] + culture[:8]


def _demo_analysis(post: str) -> dict:
    """`job.analyse`, deterministically: real sentences from the post, or null.

    The company blurb under the first heading stands in for the initiative, and the first bullet
    for the main requirement. Crude, but every value is an excerpt of the post, so the demo
    exercises the same check a real reply is held to.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", post) if p.strip()]
    prose = next((p for p in paragraphs[1:] if not p.startswith(("#", "-"))), None) if paragraphs else None
    blurb = re.split(r"(?<=\.)\s", prose)[0] if prose else None
    bullet = next((m.group(1).strip() for m in re.finditer(r"(?m)^\s*[-*]\s+(.+)$", post)), None)
    lowered = post.lower()
    level = next((lv for lv in ("graduate", "junior", "senior") if lv in lowered), None)
    return {
        "roleProfile": {"department": None, "industryName": None, "experienceLevel": level},
        "strategicContext": {
            "primaryJobRequirement": bullet,
            "companyInitiativeOrMarket": blurb,
            "companyProblemToSolve": None,
            "upcomingCompanyGoal": None,
            "companyValueOrPillar": None,
        },
    }


DEMO_DOMAINS = {
    "AI tooling": ("LangChain", "LangGraph", "LLM guardrails", "Prompt engineering"),
    "Web products": ("Next.js", "TypeScript", "React"),
    "Payments": ("Stripe",),
}
_RESULT_VERBS = re.compile(r"\b(?:reduced|cut|improved|grew|increased|sped up|saved|lowered)\b[^,.;]*", re.IGNORECASE)


def _demo_strategy(context: dict) -> dict:
    """`strategist.plan`, deterministically: every target country, its known boards in file order.

    No search terms. Translating a job title is the part of the Strategist that needs a model, and
    a demo that made them up would be teaching the matrix to trust them.
    """
    from ..nodes.strategist import COUNTRY_LANGUAGES

    boards: dict[str, list[str]] = context.get("boards") or {}
    markets = [
        {
            "country": country,
            "languages": COUNTRY_LANGUAGES.get(country, ["en"]),
            "searchTerms": [],
            "boards": [d for d, served in boards.items() if not served or country in served],
            "proposedBoards": [],
        }
        for country in sorted(context.get("countries") or [])
    ]
    return {"field": "software engineering", "markets": markets}


def _demo_cv_profile(cv: MasterCV) -> dict:
    """`cv.profile`, deterministically, from the CV's own words."""
    skills = {s.name for s in cv.skills}
    text = master_cv_text(cv)
    bullets = [b for r in [*cv.experience, *cv.projects] for b in r.bullets]
    implied = [
        {"bulletId": b.id, "phrase": m.group(0).strip()}
        for b in bullets
        if not re.search(r"\d", b.text) and (m := _RESULT_VERBS.search(b.text))
    ]
    together = [b.id for b in bullets if re.search(r"\bwith the\b", b.text)]
    return {
        "primaryDomains": [d for d, terms in DEMO_DOMAINS.items() if skills & set(terms)],
        "methodologies": [m for m in ("Prompt engineering", "LLM guardrails", "REST APIs") if text_has_term(text, m)],
        "impliedMetrics": implied[:10],
        "strongestCrossFunctionalTrait": {"trait": "Works across teams", "bulletIds": together} if together else None,
    }


def _demo_subscore(job_text: str, cv: MasterCV, terms: list[str], *, floor: float) -> dict:
    """A sub-score from how much of the CV the ad actually asks for."""
    hits = [t for t in terms if text_has_term(job_text, t)]
    score = min(10.0, floor + 2.0 * len(hits))
    quote = next((line.strip() for line in job_text.split("\n") if len(line.strip()) > 40), "")
    ids = [b.id for r in [*cv.experience, *cv.projects] for b in r.bullets if any(text_has_term(b.text, t) for t in hits)]
    return {"score": score, "jobQuote": quote[:200], "cvBulletIds": ids[:4]}


def _fit_score_output(variables: dict, cv: MasterCV) -> dict:
    """MATCH-04 in the demo: four sub-scores with evidence, and no total."""
    job_text = str(variables.get("job") or "")
    core = [s.name for s in cv.skills[:5]]
    adjacent = [s.name for s in cv.skills[5:12]]
    experience = [b.text for r in cv.experience for b in r.bullets][:6]
    quote = next((line.strip() for line in job_text.split("\n") if len(line.strip()) > 40), "")
    return {
        "core": _demo_subscore(job_text, cv, core, floor=2.0),
        "adjacent": _demo_subscore(job_text, cv, adjacent, floor=2.0),
        "experience": _demo_subscore(job_text, cv, experience[:3], floor=3.0),
        # Eligibility is a rules question, not a reading question, so the demo answers it flatly.
        "eligibility": {"score": 7.0, "jobQuote": quote[:200], "cvBulletIds": []},
        "reason": "Matches the CV on the skills this ad asks for.",
        "jobQuote": quote[:200],
        "cvBulletIds": [b.id for r in cv.experience for b in r.bullets][:3],
    }


# Section headings a CV actually uses, mapped to where their entries belong.
_SECTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("experience", re.compile(r"(?i)^\s*(work\s+)?(experience|employment|career history|professional experience)\s*:?\s*$")),
    ("projects", re.compile(r"(?i)^\s*(projects?|portfolio|open[- ]source|personal projects?)\s*:?\s*$")),
    ("education", re.compile(r"(?i)^\s*(education|academic background|qualifications)\s*:?\s*$")),
    ("skills", re.compile(r"(?i)^\s*(skills|technical skills|technologies|tools)\s*:?\s*$")),
    ("languages", re.compile(r"(?i)^\s*(languages)\s*:?\s*$")),
    ("summary", re.compile(r"(?i)^\s*(summary|profile|about me|personal statement)\s*:?\s*$")),
]

_BULLET = re.compile(r"^\s*[-•*‣▪·]\s+")
# "Senior Engineer, Monzo — Jan 2024 – present" and the handful of separators CVs use for it.
_ROLE_LINE = re.compile(r"^(?P<left>.+?)\s*[–—|,]\s*(?P<right>.+?)\s*[–—-]\s*(?P<dates>[^–—-]+)$")
_DATE_RANGE = re.compile(r"(?i)([A-Za-z]{3,9}\.?\s*\d{4}|\d{1,2}[/.]\d{4}|\d{4}-\d{1,2}|\d{4})\s*(?:[–—-]|to)\s*(.+)")
_CONTACT = re.compile(r"(?i)[\w.+-]+@[\w-]+\.\w+|\+?\d[\d\s()-]{7,}\d")
_URL = re.compile(r"(?i)\b((?:https?://|www\.)[^\s,;]+)")


def _demo_sections(text: str) -> dict[str, list[str]]:
    """Split a CV into its headed sections. Anything before the first heading is the header."""
    out: dict[str, list[str]] = {"_header": []}
    current = "_header"
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = next((name for name, pattern in _SECTIONS if pattern.match(line)), None)
        if heading:
            current = heading
            out.setdefault(current, [])
            continue
        out.setdefault(current, []).append(line)
    return out


def _demo_roles(lines: list[str]) -> list[dict]:
    """Group a section's lines into roles: a non-bullet line starts one, bullets join it."""
    roles: list[dict] = []
    for line in lines:
        if _BULLET.match(line):
            if roles:
                roles[-1]["bullets"].append(_BULLET.sub("", line).strip())
            continue
        title, org, start, end = _demo_role_head(line)
        roles.append({"title": title, "org": org, "location": None, "start": start, "end": end, "bullets": []})
    return [r for r in roles if r["title"] and r["org"]]


def _demo_role_head(line: str) -> tuple[str, str, str, str]:
    """ "Title, Org — Mar 2023 – present" → its four parts, best effort."""
    start = end = ""
    dates = _DATE_RANGE.search(line)
    if dates:
        start, end = dates.group(1).strip(), dates.group(2).strip()
        line = line[: dates.start()].rstrip(" ,–—-|")
    parts = [p.strip() for p in re.split(r"\s*[,|]\s*|\s+[–—]\s+|\s+at\s+", line) if p.strip()]
    title = parts[0] if parts else line.strip()
    org = parts[1] if len(parts) > 1 else "Not stated"
    return title, org, start, end


def _demo_terms(lines: list[str]) -> list[str]:
    """A skills section, however it was punctuated."""
    out: list[str] = []
    for line in lines:
        for term in re.split(r"[,;•|/]|\s{2,}", _BULLET.sub("", line)):
            clean = re.sub(r"^\s*[A-Za-z ]+:\s*", "", term).strip(" .")
            if 1 < len(clean) <= 40 and clean.casefold() not in {o.casefold() for o in out}:
                out.append(clean)
    return out[:40]


def _cv_parse_output(variables: dict) -> dict:
    """DEMO-01's stand-in for `cv.parse`: a real, if blunt, CV reader.

    It uses headings, bullet markers and date ranges rather than a model, so demo mode can
    genuinely import a CV someone uploads instead of pretending to. It will miss a two-column
    layout and anything implied rather than written — which is exactly what the real model is
    for, and why the demo says so on the screen.
    """
    text = str(variables.get("document") or "")
    # The prompt wraps the document as untrusted data; strip the markers back off.
    text = text.replace(OPEN, "").replace(CLOSE, "")
    text = re.sub(r"(?m)^\s*\(uploaded CV\)\s*$", "", text)
    sections = _demo_sections(text)

    header = sections.get("_header", [])
    # CV-06: contact details are stored apart from the CV and never parsed into it.
    named = [ln for ln in header if not _CONTACT.search(ln)]
    name = named[0].strip() if named else "Unnamed"
    headline = named[1].strip() if len(named) > 1 and len(named[1]) < 120 else None
    location = next((ln.strip() for ln in named[1:4] if re.search(r"(?i),\s*[a-z]|^[A-Z][a-z]+,", ln)), "Not stated")

    summary = " ".join(sections.get("summary", []))[:600]
    return {
        "name": name,
        "headline": headline,
        "location": location,
        "links": [{"label": "Link", "url": m.group(1)} for ln in header for m in [_URL.search(ln)] if m][:4],
        "summary": summary or None,
        "experience": _demo_roles(sections.get("experience", [])),
        "projects": _demo_roles(sections.get("projects", [])),
        "education": [
            {"degree": parts[0], "institution": parts[1] if len(parts) > 1 else "Not stated", "start": None, "end": None}
            for line in sections.get("education", [])
            if not _BULLET.match(line)
            for parts in [[p.strip() for p in re.split(r"\s*[,|]\s*|\s+[–—]\s+", line) if p.strip()]]
            if parts
        ][:6],
        "skills": _demo_terms(sections.get("skills", [])),
        "languages": [
            {"name": parts[0], "level": parts[1] if len(parts) > 1 else "Not stated"}
            for line in sections.get("languages", [])
            for parts in [[p.strip() for p in re.split(r"[(:—–-]", _BULLET.sub("", line), maxsplit=1)]]
            if parts and parts[0]
        ][:8],
    }


def respond(task: str, variables: dict, context: dict | None, demo_input: PackInput | None = None) -> dict:
    """The demo answer for one §9.3 task.

    `context` carries the master CV; `demo_input` carries the job, evidence and questions for the
    pack tasks. The gateway validates whatever comes back against the same schema as a real reply.
    """
    cv = (context or {}).get("cv")
    if task == "cv.parse":
        # Deliberately does not need the master CV: this is where a *new* one comes from.
        return _cv_parse_output(variables)
    if task == "profile.generate":
        if cv is None:
            raise KeyError("profile.generate needs the master CV in validation context")
        return _profile_proposal(cv)
    if task == "keywords.extract":
        if cv is None:
            raise KeyError("keywords.extract needs the master CV in validation context")
        return {"keywords": _demo_keywords(str(variables.get("job") or ""), cv)}
    if task == "fit.score":
        if cv is None:
            raise KeyError("fit.score needs the master CV in validation context")
        return _fit_score_output(variables, cv)
    if task == "job.analyse":
        return _demo_analysis(str((context or {}).get("post") or ""))
    if task == "cv.profile":
        if cv is None:
            raise KeyError("cv.profile needs the master CV in validation context")
        return _demo_cv_profile(cv)
    if task == "strategist.plan":
        return _demo_strategy(context or {})
    if task == "email.extract":
        # Demo mode never opens a mailbox (DEMO-02), so there is no email to read. Returning
        # nothing is honest; inventing cards would make the demo look like it had an inbox.
        return {"cards": []}
    if task == "guard.claims":
        # The demo writer only ever restates master-CV lines, so it claims nothing they don't —
        # the honest answer, and the one the real check gives a letter that does the same.
        return {"findings": []}
    if task == "guard.repair":
        # The demo writer only ever copies master-CV lines, so there is nothing to repair. Saying
        # so (rather than inventing a fix) is what the real model does when it can't help either.
        return {"item": None}
    if demo_input is None:
        raise KeyError(f"The demo responder for {task!r} needs demo_input=PackInput")
    if task == "cv.tailor":
        return _tailor_output(demo_input)
    if task == "summary.write":
        return _summary_output(demo_input)
    if task == "letter.write":
        return _letter_output(demo_input)
    if task == "answers.draft":
        return _answers_output(demo_input)
    raise KeyError(f"No demo responder for task {task!r}. Add one here, next to the real prompt.")
