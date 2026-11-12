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

from dataclasses import dataclass

from ..domain.vocab import backing_text, cv_line_index, text_has_term
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, JobKeyword
from ..schemas.pack import JobAnalysis

# What each slot is for, in the template's own terms (`cover_letter_template.body_segments`).
SLOTS = {
    1: "opening — core technical skill",
    2: "opening — secondary technical skill",
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
PARAGRAPH = {1: "Opening", 2: "Opening", 3: "Proof", 4: "Proof", 6: "Why this company", 7: "Why this company"}


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


def context_text(analysis: JobAnalysis | None) -> str:
    """The strategic context, quote by quote. A field the post doesn't state is left out, so its
    clause in the template is left out with it — the letter can't fill what isn't there."""
    if analysis is None:
        return "Not available for this post. Ground the company half in `companyQuote` alone."
    stated = {k: v for k, v in analysis.strategic_context.model_dump(by_alias=False).items() if v}
    if not stated:
        return "The post states none of these. Ground the company half in `companyQuote` alone."
    return "\n".join(f'- {name}: "{quote}"' for name, quote in stated.items())
