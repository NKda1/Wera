"""What the cover letter is given to work with, beyond the CV: keywords by template slot, and
what the post says about why the role exists.

The letter template places seven ATS keywords. This decides which seven, and it decides it in
code from two lists the pack already has — the post's keywords (`FitEvidence`) and the CV — so
that the only keywords a letter is ever offered are ones the CV can back. A keyword the CV can't
back is named to the model as one never to claim, and it stays on the review rail's "Missing"
list for the applicant to decide about. That is the whole of "addressed gaps" here: WERA does
not invent a bridge to a skill the CV doesn't show.

Backing follows `domain.guard.check_cited` exactly, so a keyword placed the way it is offered
here passes the guard: a term in the CV's cite-free facts (skills, education, role titles) needs
no citation, and a term that appears only in a line needs that line cited.
"""

import re
from dataclasses import dataclass
from typing import Literal

from ..domain.vocab import backing_text, cv_line_index, text_has_term
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, JobKeyword
from ..schemas.pack import CompanyBrief, JobAnalysis, LetterSection, TailoredCV

# What each slot is for, in the template's own terms (`cover_letter_template.body_segments`).
SLOTS = {
    # Both technical slots argue in the proof paragraph since 14 Nov: the opening introduces the
    # applicant and why this company, before any stack (`prompts.LETTER_WRITE`, paragraph 1).
    1: "proof — core technical skill",
    2: "proof — secondary technical skill",
    3: "proof — core methodology or process",
    4: "proof — tool, software or framework",
    5: "why this company — a way of working the post asks for",
    6: "why this company — industry concept or methodology",
    7: "adjacent skill the applicant has been building, shown in their projects",
}


@dataclass(frozen=True)
class SlotKeyword:
    slot: int
    term: str
    cite: tuple[str, ...]  # line ids that carry the term; empty when it needs no citation


@dataclass(frozen=True)
class LetterPlan:
    keywords: list[SlotKeyword]
    never_claim: list[str]  # the post asks for these and the CV doesn't show them
    culture: list[str]  # the post's own words for how it works; ATS_KEYWORD_5's candidates


def _carrying_lines(cv: MasterCV, term: str) -> list[str]:
    return [i for i, line in cv_line_index(cv).items() if text_has_term(line["text"], term)]


def plan(evidence: FitEvidence, cv: MasterCV) -> LetterPlan:
    """Each backed keyword in at most one slot, required ones first, in the post's own order.

    Slots stay empty rather than take a keyword that doesn't fit them: a letter with five honest
    keywords beats one with seven, two of them wedged into the wrong paragraph.
    """
    facts = backing_text(cv)
    project_lines = {b.id for r in cv.projects for b in r.bullets}
    backed: list[tuple[JobKeyword, tuple[str, ...]]] = []
    never: list[str] = []
    for k in sorted(evidence.keywords, key=lambda k: k.weight != "required"):
        if text_has_term(facts, k.term):
            backed.append((k, ()))
        elif lines := _carrying_lines(cv, k.term):
            backed.append((k, tuple(lines)))
        else:
            never.append(k.term)

    taken: set[str] = set()
    chosen: list[SlotKeyword] = []

    def take(slot: int, pool: list[tuple[JobKeyword, tuple[str, ...]]]) -> None:
        for k, cite in pool:
            if k.term not in taken:
                taken.add(k.term)
                chosen.append(SlotKeyword(slot=slot, term=k.term, cite=cite))
                return

    def of(*categories: str | None) -> list[tuple[JobKeyword, tuple[str, ...]]]:
        return [(k, c) for k, c in backed if k.category in categories]

    # Adjacent first, so it isn't spent on the opening: a skill shown only in project work is the
    # template's "expanding my capabilities in", and only true if no paid role shows it.
    take(7, [(k, c) for k, c in backed if c and set(c) <= project_lines])
    take(1, of("hard_skill", None) + of("tool"))
    take(2, of("hard_skill", None) + of("tool"))
    take(3, of("methodology"))
    take(4, of("tool"))
    take(6, of("methodology"))
    chosen.sort(key=lambda s: s.slot)
    return LetterPlan(keywords=chosen, never_claim=never, culture=list(evidence.culture))


# Which paragraph each slot's keyword belongs in. The prompt is given paragraphs, not slots:
# measured live, seven numbered slots each tied to a paragraph had the writing tier spend 10,787
# output tokens — nearly all of it thinking — on a 264-word letter, cross-referencing numbers.
# The same plan grouped by paragraph is one list per paragraph and nothing to look up.
PARAGRAPH = {1: "Proof", 2: "Proof", 3: "Proof", 4: "Proof", 6: "Why this company", 7: "Why this company"}


def keywords_text(p: LetterPlan) -> str:
    """The plan as the letter prompt reads it: per paragraph, each keyword with what backs it."""

    def item(s: SlotKeyword) -> str:
        note = f"cite {', '.join(s.cite)}" if s.cite else ""
        if s.slot == 7:
            note = f"from the applicant's own projects{'; ' + note if note else ''}"
        return f"{s.term} ({note})" if note else s.term

    groups: dict[str, list[str]] = {}
    for s in p.keywords:
        groups.setdefault(PARAGRAPH[s.slot], []).append(item(s))
    if p.culture:
        groups.setdefault("Why this company", []).append(f"{p.culture[0]} (the post's own words; what they ask for, not a claim)")
    lines = [f"- {name}: {', '.join(terms)}" for name, terms in groups.items()]
    if not lines:
        lines.append("- None the CV can back. Write the letter from the CV lines alone.")
    if p.never_claim:
        lines.append(f"Never claim: {', '.join(p.never_claim)}.")
    return "\n".join(lines)


def context_text(analysis: JobAnalysis | None, brief: CompanyBrief | None = None) -> str:
    """The strategic context, quote by quote. A field the post doesn't state is left out, so its
    clause in the template is left out with it — the letter can't fill what isn't there.

    With a company brief (18 Nov), its facts from the company's own site follow, each with the
    exact words it rests on — already checked against the page, and checked again if quoted."""
    if analysis is None:
        text = "Not available for this post. Ground the company half in `companyQuote` alone."
    else:
        stated = {k: v for k, v in analysis.strategic_context.model_dump(by_alias=False).items() if v}
        text = (
            "\n".join(f'- {name}: "{quote}"' for name, quote in stated.items())
            if stated
            else "The post states none of these. Ground the company half in `companyQuote` alone."
        )
    facts = [i for i in brief.items if i.source == "site"] if brief and brief.site else []
    if facts and brief and brief.site:
        lines = "\n".join(f'- {i.text} — "{i.quote}"' for i in facts)
        text += f"\n\nVerified facts from the company's own site ({brief.site.url}), with their exact words:\n{lines}"
    return text


# ---------------------------------------------------------------------------
# The plan for a letter written paragraph by paragraph (`letters.mode: paragraphs`, 20 Nov).
#
# `LETTER_WRITE` asked one call to decide, while writing, which CV line goes in which paragraph
# and which of the post's words to quote where — and when the guard then cut a paragraph there was
# nothing to put back. Written a paragraph per call, those decisions are made here, in code, from
# what the pack already has: each paragraph is handed its own lines, its own employer quotes, its
# own keywords and its own budget, and can be written again on its own.

# LTR-01's per-paragraph budgets, as `LETTER_WRITE` states them. They add up to more than the
# 150–230 total on purpose: written to these, paragraphs land about a fifth under.
BUDGETS: dict[LetterSection, tuple[int, int]] = {
    "opening_hook": (50, 65),
    "core_value_proposition": (70, 90),
    "strategic_alignment_and_culture": (50, 65),
    "call_to_action_and_close": (25, 40),
}
SECTIONS: tuple[LetterSection, ...] = tuple(BUDGETS)
# Lines per paragraph. Three for the proof — enough to choose the one competency the post most
# needs and to fill 70–90 words with what the lines say — and two for the company paragraph,
# which is mostly the employer's words with one line of the applicant's beside them.
PROOF_LINES = 3
COMPANY_LINES = 2


# The post's words about who may apply — a visa, sponsorship, the right to work. Never offered to
# a paragraph: on 20 Nov's eval a company paragraph quoted "the legal right to work … without the
# need for visa sponsorship" and went on "As a graduate from the University of Birmingham, I meet
# this requirement directly", a claim about work authorisation that no CV line can carry (WERA
# holds none, PRIV-02) and the claim check let through.
WORK_RIGHTS = re.compile(r"visa|sponsor|right to work|work permit|work authori[sz]|citizenship|security clearance", re.I)


@dataclass(frozen=True)
class Quote:
    """Words of the employer's a paragraph may quote: the post's, checked against it already
    (`QuotedContext`, `company_fact`), or the company site's (`CompanyBrief.site`)."""

    label: str  # what the words are, as the prompt names them ("what the company is doing")
    text: str
    kind: Literal["company", "policy", "site", "requirement"]


@dataclass(frozen=True)
class SectionPlan:
    section: LetterSection
    line_ids: tuple[str, ...]  # the only CV lines this paragraph is shown, and so may cite
    quotes: tuple[Quote, ...]
    keywords: tuple[SlotKeyword, ...]
    never_claim: tuple[str, ...]
    words: tuple[int, int]

    @property
    def location(self) -> str:
        """`p0`…`p3`, the guard's name for the paragraph."""
        return f"p{SECTIONS.index(self.section)}"


def _tailored_ids(doc: TailoredCV | None, lines: dict[str, dict[str, str]]) -> list[str]:
    """The tailored CV's bullets in its own order, by the master line each one is."""
    if doc is None:
        return []
    ids = [i for s in doc.sections for item in s.items for b in item.bullets for i in b.source_ids[:1]]
    return [i for i in dict.fromkeys(ids) if i in lines]


def section_plans(
    cv: MasterCV,
    doc: TailoredCV | None,
    evidence: FitEvidence,
    *,
    post: str,
    company: str,
    analysis: JobAnalysis | None = None,
    brief: CompanyBrief | None = None,
) -> list[SectionPlan]:
    """The four paragraphs' plans, in letter order.

    **Lines.** The opening gets the first education entry and the summary — who the applicant is,
    in lines that have ids since 20 Nov, so an opening built on the degree cites it. The proof
    gets the tailored CV's lines that carry its keywords, then the tailored CV's own order, three
    in all. The company paragraph gets the lines that carry its keywords, else the next tailored
    lines the proof didn't take, and the proof's last as the floor: the guard cut every company
    paragraph that cited nothing. The close gets none; it asks for a conversation.

    **Quotes.** Only employer words already verified against their source: `job.analyse`'s
    excerpts (`QuotedContext` holds each to the post), the post sentences `company_fact` and
    `policy_fact` find, and the brief's site facts. What the company is doing goes to the opening,
    what the role needs to the proof, how the company works to the company paragraph.
    """
    from ..llm.demo import company_fact, policy_fact

    lines = cv_line_index(cv)
    letter_plan = plan(evidence, cv)
    tailored = _tailored_ids(doc, lines) or [b.id for r in [*cv.experience, *cv.projects] for b in r.bullets]
    never = tuple(letter_plan.never_claim)

    def carrying(slots: set[int]) -> list[str]:
        """The tailored lines that carry these slots' keywords, else any line that does."""
        out: list[str] = []
        for s in letter_plan.keywords:
            if s.slot in slots and s.cite:
                out.append(next((i for i in s.cite if i in tailored), s.cite[0]))
        return [i for i in dict.fromkeys(out) if i in lines]

    opening = [cv.education[0].id] if cv.education else []
    if cv.summary and cv.summary.text.strip():
        opening.append(cv.summary.id)
    if not opening:
        opening = tailored[:1]
    proof = list(dict.fromkeys([*carrying({1, 2, 3, 4}), *tailored]))[:PROOF_LINES]
    rest = [i for i in tailored if i not in proof]
    fit = list(dict.fromkeys([*(i for i in carrying({5, 6, 7}) if i not in proof), *rest]))[:COMPANY_LINES] or proof[-1:]

    context = analysis.strategic_context if analysis else None

    def quote(label: str, text: str | None, kind: str, source: str) -> list[Quote]:
        text = " ".join((text or "").split())
        flat = " ".join(source.split())
        if not text or text not in flat or WORK_RIGHTS.search(text):
            return []
        return [Quote(label=label, text=text, kind=kind)]  # type: ignore[arg-type]

    doing = [
        *quote("what the company is doing", context.company_initiative_or_market if context else None, "company", post),
        *quote("the problem the role exists to solve", context.company_problem_to_solve if context else None, "company", post),
        *quote("what the company is working towards", context.upcoming_company_goal if context else None, "company", post),
        # The brief's items from the post (18 Nov) are verified against it as the analysis is.
        *(
            q
            for item in (brief.items if brief else [])
            if item.source == "post" and item.topic in ("what_they_do", "why_the_role")
            for q in quote(f"what the post says: {item.text}", item.quote, "company", post)
        ),
    ] or quote("what the post says about the company", company_fact(post, company), "company", post)
    needs = [
        *quote("what the role needs most", context.primary_job_requirement if context else None, "requirement", post),
        *quote("what the post asks for", evidence.job_quote, "requirement", post),
    ]
    site = brief.site.text if brief and brief.site else ""
    works = [
        *quote("a value or principle the company states", context.company_value_or_pillar if context else None, "company", post),
        *quote("how the company works", policy_fact(post), "policy", post),
        *(
            q
            for item in (brief.items if brief else [])
            if item.source == "post" and item.topic == "values"
            for q in quote(f"what the post says: {item.text}", item.quote, "company", post)
        ),
        *(
            q
            for item in (brief.items if brief and site else [])
            if item.source == "site"
            for q in quote(f"from the company's own site: {item.text}", item.quote, "site", site)
        ),
    ]
    # A company paragraph with no words of the employer's to stand on would have to describe the
    # company itself — where letters invent. So it takes the opening's second quote when there is
    # one; with only one, the opening keeps it, and this paragraph is told to say nothing about
    # the company the post doesn't, which it then writes from what the applicant wants.
    if not works and len(doing) > 1:
        works = [doing.pop()]

    # The employer's words about the work or the firm first; its working-pattern policy only when
    # there is nothing else (20 Nov). A policy quote was the company paragraph's whole subject in
    # three live letters, each answering it with a preference the applicant never stated.
    if any(q.kind != "policy" for q in works):
        works = [q for q in works if q.kind != "policy"]

    # One quote, one paragraph: the same words twice in a letter read as a template.
    doing = _unique(doing)[:2]
    needs = [q for q in _unique(needs) if q.text not in {d.text for d in doing}][:2]
    works = [q for q in _unique(works) if q.text not in {d.text for d in [*doing, *needs]}][:3]

    def keywords(slots: set[int]) -> tuple[SlotKeyword, ...]:
        return tuple(s for s in letter_plan.keywords if s.slot in slots)

    return [
        SectionPlan("opening_hook", tuple(opening), tuple(doing), (), never, BUDGETS["opening_hook"]),
        SectionPlan(
            "core_value_proposition", tuple(proof), tuple(needs), keywords({1, 2, 3, 4}), never, BUDGETS["core_value_proposition"]
        ),
        SectionPlan(
            "strategic_alignment_and_culture",
            tuple(fit),
            tuple(works),
            keywords({5, 6, 7}),
            never,
            BUDGETS["strategic_alignment_and_culture"],
        ),
        SectionPlan("call_to_action_and_close", (), (), (), never, BUDGETS["call_to_action_and_close"]),
    ]


def _unique(quotes: list[Quote]) -> list[Quote]:
    seen: set[str] = set()
    return [q for q in quotes if not (q.text in seen or seen.add(q.text))]


def section_keywords_text(p: SectionPlan, culture: list[str]) -> str:
    """One paragraph's keywords as its prompt reads them, each with the line that backs it."""
    items = [f"{s.term} (cite {', '.join(s.cite)})" if s.cite else s.term for s in p.keywords]
    if p.section == "strategic_alignment_and_culture" and culture:
        items.append(f"{culture[0]} (the post's own words; what they ask for, not a claim)")
    out = ", ".join(items) or "None for this paragraph."
    if p.never_claim:
        out += f"\nNever claim: {', '.join(p.never_claim)}."
    return out


# ---------------------------------------------------------------------------
# A paragraph built from the CV's own words, for when the one written for it fails the guard twice.
#
# Before 20 Nov a paragraph that failed was cut, and the letter lost a fifth of its length with it;
# only the opening had a stand-in (`nodes.tailor.plain_introduction`). Every paragraph has one now.
# Each is made of the lines it cites, word for word, and the employer's verified words, so there
# is nothing in it for the guard to doubt — plainer than a written paragraph, flagged to the
# person as such, and never missing.

# Past-tense verbs a CV line commonly opens with that don't end in "-ed", so the line reads as
# a sentence after "I": "I led…", "I built…". Anything else is quoted after a colon instead.
_PAST = frozenset(
    [
        "built",
        "led",
        "ran",
        "wrote",
        "made",
        "won",
        "taught",
        "drove",
        "grew",
        "set",
        "cut",
        "began",
        "took",
        "gave",
        "held",
        "kept",
        "found",
        "chose",
        "brought",
        "sold",
        "spoke",
        "met",
        "rebuilt",
        "rewrote",
        "shipped",
        "co-led",
        "co-founded",
        "oversaw",
        "undertook",
    ]
)


def _sentence(text: str) -> str:
    text = " ".join(text.split()).rstrip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _as_mine(text: str) -> str | None:
    """A CV line as a first-person clause — "Built a FastAPI gateway" → "built a FastAPI gateway"
    — when it opens with a past-tense verb, else None."""
    first = text.split(maxsplit=1)[0] if text.split() else ""
    bare = first.lower().strip(",;:")
    if bare.endswith("ed") or bare in _PAST:
        return f"{first[:1].lower()}{first[1:]}{text[len(first) :]}"
    return None


def _line_sentences(
    cv: MasterCV, ids: tuple[str, ...], lines: dict[str, dict[str, str]], limit: int, room: int = 1000
) -> list[str]:
    """Each line as a sentence of the letter: where it was done, and the line itself — as many
    as fit in `room` words, and always the first."""
    roles = {b.id: r for r in [*cv.experience, *cv.projects] for b in r.bullets}
    projects = {r.id for r in cv.projects}
    out: list[str] = []
    previous = None
    for n, line_id in enumerate(i for i in ids if i in lines):
        if n == limit or (out and len(" ".join(out).split()) + len(lines[line_id]["text"].split()) + 6 > room):
            break
        text = lines[line_id]["text"].strip().rstrip(".")
        role = roles.get(line_id)
        mine = _as_mine(text)
        if role is None:
            out.append(_sentence(text))
        elif role is previous:
            out.append(_sentence(f"There I also {mine}" if mine else f"Also there: {text}"))
        elif role.id in projects:
            # A project's "title" is whatever the CV put there — "Tech Stack: React …" on one
            # parsed CV — so it is named by the project alone.
            out.append(_sentence(f"On {role.org}, I {mine}" if mine else f"On {role.org}: {text}"))
        elif mine:
            out.append(_sentence(f"As {role.title} at {role.org}, I {mine}"))
        else:
            out.append(_sentence(f"From my time as {role.title} at {role.org}: {text}"))
        previous = role
    return out


def template_paragraph(
    p: SectionPlan, cv: MasterCV, *, title: str, company: str, intermediary: bool = False
) -> tuple[str, list[str]]:
    """The paragraph's text and the ids it cites, from its own lines and quotes only."""
    lines = cv_line_index(cv)
    first = next((q for q in p.quotes if q.kind != "site"), None) or (p.quotes[0] if p.quotes else None)
    them = "the team" if intermediary else company
    where = "your site" if first and first.kind == "site" else "your post"
    cited = [i for i in p.line_ids if i in lines]
    if p.section == "opening_hook":
        sentences = []
        if cv.education:
            entry = cv.education[0]
            sentences.append(f"I studied {entry.degree.rstrip('.')} at {entry.institution.rstrip('.')}.")
        if cv.person.headline and not cv.education:
            sentences.append(_sentence(cv.person.headline))
        if first:
            sentences.append(f"What draws me to {them} is how {where} puts it: “{first.text.rstrip('.')}”.")
        sentences.append(f"The {title} role is the work I want to do next.")
        # The summary's first sentence, in the CV's own words, when the budget has room for it.
        summary = cv.summary.text.strip() if cv.summary and cv.summary.id in cited else ""
        said = _sentence(f"In short: {summary.split('. ')[0].rstrip('.')}") if summary else ""
        if said and len(" ".join([*sentences, said]).split()) <= p.words[1]:
            sentences.insert(1 if cv.education else 0, said)
        return " ".join(sentences), cited
    if p.section == "core_value_proposition":
        sentences = [f"Your post asks for “{first.text.rstrip('.')}”." if first else ""]
        room = p.words[1] - len(" ".join(sentences).split())
        sentences += _line_sentences(cv, p.line_ids, lines, limit=PROOF_LINES, room=room)
        return " ".join(s for s in sentences if s), cited
    if p.section == "strategic_alignment_and_culture":
        sentences = [f"{where[0].upper()}{where[1:]} says “{first.text.rstrip('.')}”." if first else ""]
        # Side by side, with no sentence joining them: "which is how I work" is a claim no line
        # carries, and this paragraph exists because the written one made claims like it.
        room = p.words[1] - len(" ".join(sentences).split())
        sentences += _line_sentences(cv, p.line_ids, lines, limit=COMPANY_LINES, room=room)
        return " ".join(s for s in sentences if s), cited
    at = "" if intermediary else f" at {company}"
    return (
        f"I've attached my CV, which has the detail behind this letter. I'd welcome a conversation about the "
        f"{title} role{at}, at whatever time suits you.",
        [],
    )
