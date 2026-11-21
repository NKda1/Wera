"""How a CV reads (ATSC-09, 20 Nov): action verbs, numbers, and the length of its parts.

From the JobLeads review of 19 Nov, which read a real CV and said what a recruiter would: lines
should open with an action verb and say how much, the summary should run 50–100 words, and the
skills list should hold 8–15 skills, tailored per job. It flagged "Collaborated in an Agile team
of 6 engineers, participating in daily standups…" as the kind of line to rewrite — a number in
it, but the number is the team, not a result, and the verb says the applicant was present.

**Suggestions only.** Nothing here changes a line, and nothing here can write a number: a line
without one is pointed at, with the result `cv.profile` read in it (`impliedMetrics`) as the
thing to quantify, and the person supplies the figure or leaves the line alone. Not part of the
ATS score either — a parser does not read verbs for strength, and a score that moved with this
card would reward rewording over substance.

The weak-verb → strong-verb idea, and measuring the share of lines that open with an action
verb and the share that state a number, are from Resume-ATS (ideas only: that repository has no
licence, so no code was taken).

**For every kind of CV**, not only engineering ones: the verbs come from sales, care, teaching,
hospitality, finance and research as well as software, because WERA reads all of them.
"""

import re
from collections.abc import Mapping

from ..schemas.cv import MasterCV
from ..schemas.pack import TailoredCV, WritingBullet, WritingReport, WritingTarget
from .cv_profile import metrics_in
from .vocab import normalise_text, role_header

# The JobLeads targets (19 Nov).
SUMMARY_WORDS = (50, 100)
SKILLS_COUNT = (8, 15)

# Openings that say the applicant was there rather than what they did, each with verbs to say it
# instead. Longest first in `_WEAK_RE`, so "worked closely with" wins over "worked".
#
# The suggestions are verbs to consider, never a rewrite: which one is true is the person's to
# say ("Helped launch" may really have been "Co-led", or it may have been "Supported").
WEAK_OPENINGS: dict[str, list[str]] = {
    "was responsible for": ["Led", "Owned", "Managed", "Ran"],
    "were responsible for": ["Led", "Owned", "Managed", "Ran"],
    "responsible for": ["Led", "Owned", "Managed", "Ran"],
    "duties included": ["Led", "Delivered", "Managed", "Ran"],
    "tasked with": ["Delivered", "Led", "Completed", "Executed"],
    "in charge of": ["Led", "Managed", "Directed", "Ran"],
    "participated in": ["Contributed to", "Delivered", "Co-led", "Drove"],
    "participating in": ["Contributed to", "Delivered", "Co-led", "Drove"],
    "collaborated in": ["Partnered with", "Co-delivered", "Contributed to", "Drove"],
    "collaborated on": ["Co-delivered", "Co-built", "Contributed to", "Partnered on"],
    "collaborated with": ["Partnered with", "Co-delivered", "Coordinated with", "Aligned"],
    "involved in": ["Contributed to", "Delivered", "Supported", "Drove"],
    "worked closely with": ["Partnered with", "Coordinated with", "Advised", "Supported"],
    "worked with": ["Partnered with", "Coordinated with", "Advised", "Supported"],
    "worked on": ["Built", "Developed", "Delivered", "Produced"],
    "working on": ["Building", "Developing", "Delivering", "Producing"],
    "helped to": ["Supported", "Enabled", "Co-led", "Delivered"],
    "helped": ["Supported", "Enabled", "Co-led", "Delivered"],
    "helping": ["Supporting", "Enabling", "Co-leading", "Delivering"],
    "assisted with": ["Supported", "Enabled", "Delivered", "Coordinated"],
    "assisted in": ["Supported", "Enabled", "Delivered", "Coordinated"],
    "assisted": ["Supported", "Enabled", "Delivered", "Coordinated"],
    "assisting": ["Supporting", "Enabling", "Delivering", "Coordinating"],
    "handled": ["Managed", "Resolved", "Processed", "Ran"],
    "did": ["Completed", "Delivered", "Carried out", "Produced"],
    "used": ["Applied", "Built", "Automated", "Analysed"],
    "utilised": ["Applied", "Built", "Automated", "Analysed"],
    "utilized": ["Applied", "Built", "Automated", "Analysed"],
}
_WEAK_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(w) for w in sorted(WEAK_OPENINGS, key=len, reverse=True)) + r")(?!\w)")
# Weak phrases that do the same damage after the opening ("…, participating in daily standups").
_WEAK_LATER = ("participating in", "responsible for", "involved in", "helping", "assisting")

# Verbs that open a line of work well, as written in a CV: past tense for a past role, the base
# form for a current one. Irregular forms are listed; regular past forms are recognised by
# `_is_action` too, so this need not hold every verb that ends in "-ed".
ACTION_VERBS = frozenset(
    {
        # making and changing things
        "built", "build", "created", "create", "designed", "design", "developed", "develop", "made", "make",
        "wrote", "write", "launched", "launch", "shipped", "ship", "implemented", "implement", "introduced",
        "established", "founded", "set", "rebuilt", "redesigned", "rewrote", "automated", "automate", "migrated",
        "engineered", "prototyped", "drafted", "authored", "produced", "produce", "composed", "devised",
        # leading and running
        "led", "lead", "ran", "run", "managed", "manage", "directed", "headed", "oversaw", "oversee",
        "coordinated", "coordinate", "organised", "organized", "chaired", "supervised", "mentored", "coached",
        "trained", "taught", "teach", "hired", "recruited", "owned", "own", "spearheaded", "championed",
        # results
        "delivered", "deliver", "achieved", "won", "grew", "grow", "increased", "raised", "reduced", "cut",
        "saved", "improved", "improve", "boosted", "doubled", "tripled", "halved", "accelerated", "lowered",
        "exceeded", "surpassed", "secured", "closed", "sold", "sell", "generated", "expanded", "scaled",
        # analysis and judgement
        "analysed", "analyzed", "analyse", "analyze", "assessed", "audited", "evaluated", "forecast",
        "forecasted", "modelled", "modeled", "researched", "investigated", "identified", "diagnosed", "measured",
        "tested", "validated", "reviewed", "reconciled", "valued", "priced", "underwrote", "benchmarked",
        # people and service
        "advised", "advise", "negotiated", "presented", "pitched", "persuaded", "resolved", "served",
        "supported", "cared", "treated", "nursed", "counselled", "counseled", "guided", "onboarded",
        "partnered", "liaised", "facilitated", "represented", "translated", "interpreted",
        # operations
        "processed", "maintained", "operated", "scheduled", "planned", "prepared", "administered",
        "streamlined", "standardised", "standardized", "optimised", "optimized", "restructured", "drove", "drive",
        "consolidated", "integrated", "deployed", "configured", "monitored", "published", "curated",
    }
)  # fmt: skip
# "-ed" words that open a line without being something the applicant did.
_NOT_ACTIONS = frozenset(
    {"experienced", "skilled", "talented", "motivated", "dedicated", "interested", "involved", "tasked", "exposed",
     "based", "required", "detailed", "qualified", "certified", "licensed", "seasoned", "accomplished", "used",
     "utilised", "utilized", "helped", "assisted", "handled", "collaborated", "participated", "worked"}
)  # fmt: skip
_FIRST_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def _opening(text: str) -> str:
    return normalise_text(text).lstrip("•·-–—*▪◦ ").lower()


def weak_opening(text: str) -> str | None:
    """The weak phrase a line opens with, or carries after a comma, as the line writes it."""
    lowered = _opening(text)
    m = _WEAK_RE.match(lowered)
    if m:
        return m.group(1)
    for phrase in _WEAK_LATER:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered):
            return phrase
    return None


def _is_action(word: str) -> bool:
    w = word.lower().strip("'")
    if w in _NOT_ACTIONS:
        return False
    if w in ACTION_VERBS or (w.endswith("s") and w[:-1] in ACTION_VERBS):
        return True
    # A regular past tense: "Reconciled", "Onboarded". Four letters at least, so "red" isn't one.
    return len(w) > 4 and w.endswith("ed")


def opens_with_action(text: str) -> bool:
    """Whether the line's first word is a verb of doing. Weak openers are verbs but not this."""
    m = _FIRST_WORD.match(_opening(text))
    return bool(m) and _is_action(m.group(0)) and weak_opening(text) is None


def _capitalise(phrase: str, like: str) -> str:
    return phrase[:1].upper() + phrase[1:] if like[:1].isupper() else phrase


def check_bullet(text: str, *, figure: str | None = None) -> tuple[list[str], str | None, list[str], str | None]:
    """One line's issues, its weak phrase, stronger verbs for it, and what a number would measure.

    `figure` is `cv.profile`'s implied result for the line, offered only when the line has no
    number: a line that states one has its result already.
    """
    issues: list[str] = []
    weak = weak_opening(text)
    if weak:
        issues.append("weak_verb")
    elif not opens_with_action(text):
        issues.append("no_verb")
    numbered = bool(metrics_in(text))
    if not numbered:
        issues.append("no_number")
    suggestions = WEAK_OPENINGS.get(weak, []) if weak else []
    shown_weak = None
    if weak:
        start = _opening(text).find(weak)
        source = normalise_text(text).lstrip("•·-–—*▪◦ ")
        shown_weak = source[start : start + len(weak)] if start >= 0 else _capitalise(weak, text)
    return issues, shown_weak, suggestions, (figure if not numbered else None)


def _words(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _target(count: int, bounds: tuple[int, int], noun: str, *, low_hint: str, high_hint: str) -> WritingTarget:
    low, high = bounds
    ok = low <= count <= high
    if ok:
        message = f"{count} {noun}, inside the {low}–{high} a recruiter expects"
    elif count < low:
        message = f"{count} {noun}; aim for {low}–{high}. {low_hint}"
    else:
        message = f"{count} {noun}; aim for {low}–{high}. {high_hint}"
    return WritingTarget(count=count, low=low, high=high, ok=ok, message=message)


def writing_report(doc: TailoredCV, cv: MasterCV, *, implied: Mapping[str, str] | None = None) -> WritingReport:
    """The Writing card for a tailored CV.

    `implied` is `cv.profile`'s implied results by master-CV line id (`CvProfile`'s
    `implied_achievements`): read from the cache, so this costs no model call.
    """
    implied = implied or {}
    roles = {r.id: r for r in [*cv.experience, *cv.projects]}
    flagged: list[WritingBullet] = []
    total = actions = numbered = 0
    for section in doc.sections:
        for item in section.items:
            role = roles.get(item.role_id)
            where = role_header(role) if role else section.title
            for bullet in item.bullets:
                total += 1
                source = bullet.source_ids[0] if bullet.source_ids else None
                issues, weak, suggestions, figure = check_bullet(bullet.text, figure=implied.get(source or ""))
                actions += "weak_verb" not in issues and "no_verb" not in issues
                numbered += "no_number" not in issues
                if issues:
                    flagged.append(
                        WritingBullet(
                            source_id=source,
                            text=bullet.text,
                            where=where,
                            issues=issues,  # type: ignore[arg-type] — the three literals above
                            weak_phrase=weak,
                            suggestions=suggestions,
                            figure_prompt=figure,
                        )
                    )
    # Weak verbs first: they are the rewrite the review asked for. Then lines with a result to
    # quantify, since the person can answer those from memory.
    flagged.sort(key=lambda b: ("weak_verb" not in b.issues, b.figure_prompt is None))
    return WritingReport(
        bullets=flagged,
        bullet_count=total,
        action_share=actions / total if total else 1.0,
        number_share=numbered / total if total else 1.0,
        summary=_target(
            _words(doc.summary.text),
            SUMMARY_WORDS,
            "words in the summary",
            low_hint="Say what you do, for whom, and one result.",
            high_hint="Keep the two or three points this job asks about.",
        ),
        skills=_target(
            len(doc.skills),
            SKILLS_COUNT,
            "skills listed",
            low_hint="Add skills your CV's lines already show.",
            high_hint="Keep the ones this post asks for.",
        ),
    )
