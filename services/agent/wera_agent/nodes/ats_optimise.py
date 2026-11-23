"""The CV-for-ATS optimiser (ATSC-10, 22 Nov): on a redraft, a few bullets reworded to carry
keywords the master CV already backs, each one checked, each one shown to the person.

The ATS report says which of the post's keywords the CV backs but this pack doesn't show
(`AtsKeyword.in_master_cv` and not `matched`). The skills line is covered in code
(`place_required`, `reconcile_skills`, within the 15-item limit). What code can't do is put a
keyword into a line of work, which is where a parser and a recruiter both weigh it most
(`ats_score`'s "Shown in your work" part): that takes merging two of the CV's own lines into one
sentence, and that is the one thing this asks a model for.

A round, at most `optimise.max_rounds` of them and `optimise.max_calls` calls in all:

1. Measure: `build_ats` on the CV as it stands.
2. Targets: the missing backed keywords, required first, at most `optimise.max_targets`, each
   with the master lines in a shown role that carry it and up to three of that role's bullets.
3. One `cv.optimise` call: each bullet rewritten, with the lines it cites and the keyword.
4. Every rewrite checked (`_check`): the citation guard (`check_cited`) against its cited lines;
   the keyword rule — any keyword or skill it adds must be in the lines it cites, so it can't add
   a skill, even one the skills list has; its length; and the page estimate. A rewrite that
   fails keeps its original line, never deletes it.
5. Measure again. A round that didn't raise the score is undone; one that raised it by less than
   `optimise.min_gain` is kept and ends the loop.

A rewrite is stored as a `composed` line (TAIL-01's one relaxation, `Store._assert_verbatim`) with
its sources, and the before-and-after goes in the `optimise` document for the review screen.
"""

import math
from dataclasses import dataclass, field

import logfire
from sqlalchemy.orm import Session

from ..config import wera
from ..domain.guard import COMMON_TECH, check_cited
from ..domain.vocab import cv_line_index, text_has_term
from ..errors import ApiError
from ..llm.gateway import structured
from ..llm.prompts import untrusted
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, Job
from ..schemas.llm_outputs import OptimisedBullet, OptimiseOutput
from ..schemas.pack import (
    AtsKeyword,
    Cited,
    CoverLetter,
    OptimiseChange,
    OptimiseRefusal,
    OptimiseReport,
    OptimiseRound,
    TailoredCV,
)
from .ats import build_ats, estimate_pages, place_required, reconcile_skills

# "Up to 2–3 bullets" a round: enough to move the score, few enough to read as the person's CV.
MAX_BULLETS = 3
# Candidate bullets per target, and keyword lines shown for each.
MAX_CANDIDATES = 3
MAX_LINES = 2
# `estimate_pages`' characters per printed bullet line. A rewrite may take no more printed lines
# than the bullet did, or two, whichever is more.
LINE_CHARS = 80
MIN_LINES = 2


@dataclass
class Target:
    keyword: AtsKeyword
    role_id: str
    lines: list[str]  # master-CV line ids, in that role, that carry the keyword
    bullets: list[str]  # locations of the bullets the model may rewrite for it


@dataclass
class Shown:
    location: str
    role_id: str
    bullet: Cited


@dataclass
class _Run:
    calls: int = 0
    changes: list[OptimiseChange] = field(default_factory=list)
    refused: list[OptimiseRefusal] = field(default_factory=list)
    rounds: list[OptimiseRound] = field(default_factory=list)


def _shown(doc: TailoredCV) -> dict[str, Shown]:
    """Every bullet by the location the review screen edits it at (`nodes.tailor.cv_locations`)."""
    out: dict[str, Shown] = {}
    for si, section in enumerate(doc.sections):
        for ii, item in enumerate(section.items):
            for bi, bullet in enumerate(item.bullets):
                loc = f"s{si}.i{ii}.b{bi}"
                out[loc] = Shown(loc, item.role_id, bullet)
    return out


def _at(location: str) -> tuple[int, int, int]:
    si, ii, bi = (int(p[1:]) for p in location.split("."))
    return si, ii, bi


def _printed_lines(text: str) -> int:
    return max(1, math.ceil(len(text) / LINE_CHARS))


def max_chars(original: str) -> int:
    """The longest a rewrite of `original` may be."""
    return max(MIN_LINES, _printed_lines(original)) * LINE_CHARS


def targets(doc: TailoredCV, cv: MasterCV, keywords: list[AtsKeyword], *, limit: int) -> list[Target]:
    """The keywords missing from this CV's lines of work that the master CV backs in a line of a
    role this CV shows, each with bullets of that role to carry it.

    "Missing" is missing from the bullets, which is where `ats_score`'s "Shown in your work" part
    looks: a keyword not on the CV at all comes first, then one only its skills list carries
    (`reconcile_skills` put it there, and a recruiter weighs it less there) and that part of the
    score counts — required before nice-to-have in each. A bullet may be offered for more than one
    keyword (the model rewrites it once at most); a line the person edited or WERA already
    reworded is never offered."""
    owner = {b.id: r.id for r in [*cv.experience, *cv.projects] for b in r.bullets}
    master = {b.id: b.text for r in [*cv.experience, *cv.projects] for b in r.bullets}
    shown = _shown(doc)
    by_role: dict[str, list[Shown]] = {}
    for s in shown.values():
        by_role.setdefault(s.role_id, []).append(s)
    in_lines = "\n".join(s.bullet.text for s in shown.values())
    # A keyword already on the CV moves the score only if "Shown in your work" counts it: the
    # matched required keywords, or every matched one when none is required (`ats_score`).
    counted = {k.term for k in keywords if k.matched and k.weight == "required"} or {k.term for k in keywords if k.matched}
    missing = sorted(
        (k for k in keywords if k.in_master_cv and not text_has_term(in_lines, k.term) and (not k.matched or k.term in counted)),
        key=lambda k: (k.matched, k.weight != "required"),
    )
    matched = [k.term for k in keywords if k.matched]
    out: list[Target] = []
    for keyword in missing:
        if len(out) >= limit:
            break
        carriers = [lid for lid, text in master.items() if owner[lid] in by_role and text_has_term(text, keyword.term)]
        for role_id in dict.fromkeys(owner[lid] for lid in carriers):
            free = [s for s in by_role[role_id] if not s.bullet.edited_by_user and not s.bullet.composed and s.bullet.source_ids]
            if not free:
                continue
            # The lines that carry fewest of the post's matched keywords first: rewording them puts
            # least at risk.
            free.sort(key=lambda s: sum(text_has_term(s.bullet.text, t) for t in matched))
            chosen = [s.location for s in free[:MAX_CANDIDATES]]
            out.append(Target(keyword, role_id, [lid for lid in carriers if owner[lid] == role_id][:MAX_LINES], chosen))
            break
    return out


def _targets_text(found: list[Target], doc: TailoredCV, cv: MasterCV) -> str:
    lines = cv_line_index(cv)
    shown = _shown(doc)
    blocks = []
    for t in found:
        rows = [f"Keyword: {t.keyword.term} ({t.keyword.weight})", "Lines that carry it:"]
        rows += [f"  [{lid}] {lines[lid]['text']}" for lid in t.lines]
        rows.append("Bullets (rewrite at most one for this keyword):")
        for loc in t.bullets:
            b = shown[loc].bullet
            rows.append(f"  {loc} [{b.source_ids[0]}] (at most {max_chars(b.text)} characters) {b.text}")
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def _vocabulary(cv: MasterCV, evidence: FitEvidence) -> list[str]:
    """What the keyword rule watches for: the post's keywords, the CV's skills and their aliases,
    and the guard's common tools."""
    return [*(k.term for k in evidence.keywords), *(t for s in cv.skills for t in [s.name, *(s.aliases or [])]), *COMMON_TECH]


def check(
    proposal: OptimisedBullet,
    original: Shown,
    target: Target,
    *,
    cv: MasterCV,
    evidence: FitEvidence,
) -> tuple[Cited | None, OptimiseRefusal | None]:
    """The rewrite as a line to store, or why it can't be one. Nothing here is a model's opinion."""
    loc, keyword = original.location, target.keyword.term

    def refuse(rule: str, detail: str) -> tuple[None, OptimiseRefusal]:
        return None, OptimiseRefusal(location=loc, keyword=keyword, rule=rule, detail=detail)

    text = " ".join(proposal.text.split())
    before = original.bullet.text
    if proposal.keyword.strip().casefold() != keyword.casefold():
        return refuse("keyword", f'Asked for "{keyword}", wrote for "{proposal.keyword}"')
    if not text_has_term(text, keyword) or text_has_term(before, keyword):
        return refuse("keyword", f'The rewrite doesn\'t carry "{keyword}"')
    own = list(original.bullet.source_ids)
    allowed = {*own, *target.lines}
    sources = list(dict.fromkeys([*own, *(s.strip() for s in proposal.sources)]))
    stray = [s for s in sources if s not in allowed]
    if stray:
        return refuse("sources", f"Cites {', '.join(stray)}, which it wasn't given")
    result = check_cited([(loc, Cited(text=text, source_ids=sources))], cv, [k.term for k in evidence.keywords])
    if not result.pass_:
        v = result.violations[0]
        return refuse(v.rule, v.detail)
    # The keyword rule, stricter than the guard: a term the rewrite adds must be in the lines it
    # cites. The guard also accepts a term from the CV's skills list, which is right for a line
    # selected from the master; for a line WERA reworded it would let a skill into a sentence about
    # work that never used it.
    lines = cv_line_index(cv)
    cited = "\n".join(lines[i]["text"] for i in sources)
    for term in dict.fromkeys(_vocabulary(cv, evidence)):
        if text_has_term(text, term) and not text_has_term(before, term) and not text_has_term(cited, term):
            return refuse("unbacked", f'"{term}" isn\'t in the lines it cites')
    if len(text) > max_chars(before):
        return refuse("length", f"{len(text)} characters, over the {max_chars(before)} this line has")
    return Cited(text=text, source_ids=sources, composed=True), None


def optimise(
    session: Session,
    doc: TailoredCV,
    *,
    cv: MasterCV,
    evidence: FitEvidence,
    job: Job,
    cv_pages: int,
    note: str | None,
    letter: CoverLetter | None = None,
) -> tuple[TailoredCV, OptimiseReport]:
    """The optimised CV and what was done to it. `doc` is not changed."""
    cfg = wera().optimise
    doc = doc.model_copy(deep=True)
    # Skills first, in code: what a skills line can carry costs no call.
    doc = place_required(doc, cv=cv, evidence=evidence)
    doc = reconcile_skills(doc, cv=cv, evidence=evidence, cv_pages=cv_pages, threshold=wera().ats_threshold)

    def measure(d: TailoredCV) -> tuple[float, list[AtsKeyword]]:
        report = build_ats(d, cv=cv, evidence=evidence, cv_pages=cv_pages, job=job, letter=letter)
        return float(report.score or 0.0), report.keywords

    start, keywords = measure(doc)
    run = _Run()
    score = start
    stopped = "max_rounds"
    for number in range(1, cfg.max_rounds + 1):
        if run.calls >= cfg.max_calls:
            stopped = "max_calls" if cfg.max_calls > 0 else "off"
            break
        found = targets(doc, cv, keywords, limit=cfg.max_targets)
        if not found:
            stopped = "no_targets"
            break
        shown = _shown(doc)
        owner: dict[str, list[Target]] = {}
        for t in found:
            for loc in t.bullets:
                owner.setdefault(loc, []).append(t)
        run.calls += 1
        try:
            out = structured(
                session,
                "cv.optimise",
                OptimiseOutput,
                {
                    "targets": _targets_text(found, doc, cv),
                    "keywords": untrusted("the post's keywords", ", ".join(k.term for k in evidence.keywords)),
                    "note": untrusted("change note", note) if note and note.strip() else "(none)",
                },
                context={
                    "cv": cv,
                    "bullets": set(owner),
                    # For the demo responder (DEMO-02), which has no model to read the text.
                    "optimise": [
                        {
                            "keyword": t.keyword.term,
                            "lines": t.lines,
                            "bullets": [
                                {"bullet": loc, "text": shown[loc].bullet.text, "sources": shown[loc].bullet.source_ids}
                                for loc in t.bullets
                            ],
                        }
                        for t in found
                    ],
                },
            )
        except ApiError as exc:
            logfire.warn("The ATS optimiser's call failed for {job_id}: {code}", job_id=job.id, code=exc.code)
            stopped = "error"
            break

        candidate = doc.model_copy(deep=True)
        pages_cap = max(float(cv_pages), estimate_pages(doc, cv))
        made: list[OptimiseChange] = []
        refused = 0
        for proposal in out.bullets:
            if len(made) >= MAX_BULLETS:
                break
            if proposal.bullet not in owner:
                continue
            original, offered = shown[proposal.bullet], owner[proposal.bullet]
            # The target it says it wrote for, among those this bullet was offered for.
            target = next((t for t in offered if t.keyword.term.casefold() == proposal.keyword.strip().casefold()), offered[0])
            if any(c.keyword == target.keyword.term for c in made):
                continue  # one bullet a keyword is enough; a second reads as stuffing
            line, refusal = check(proposal, original, target, cv=cv, evidence=evidence)
            if line is not None:
                si, ii, bi = _at(original.location)
                candidate.sections[si].items[ii].bullets[bi] = line
                if estimate_pages(candidate, cv) > pages_cap:
                    candidate.sections[si].items[ii].bullets[bi] = original.bullet.model_copy()
                    line, refusal = (
                        None,
                        OptimiseRefusal(
                            location=original.location,
                            keyword=target.keyword.term,
                            rule="pages",
                            detail=f"It would take the CV past {cv_pages} page{'s' if cv_pages > 1 else ''}",
                        ),
                    )
            if line is None:
                assert refusal is not None
                run.refused.append(refusal)
                refused += 1
                continue
            made.append(
                OptimiseChange(
                    location=original.location,
                    role_id=original.role_id,
                    before=original.bullet.text,
                    after=line.text,
                    keyword=target.keyword.term,
                    reason=proposal.reason,
                    source_ids=line.source_ids,
                )
            )

        after, after_keywords = measure(candidate) if made else (score, keywords)
        gain = after - score
        kept = bool(made) and gain > 0
        run.rounds.append(
            OptimiseRound(
                round=number,
                score_before=score,
                score_after=after,
                targets=[t.keyword.term for t in found],
                rewritten=len(made),
                refused=refused,
                kept=kept,
            )
        )
        if kept:
            doc, score, keywords = candidate, after, after_keywords
            run.changes += made
        if gain < cfg.min_gain:
            stopped = "no_gain"
            break
    else:
        stopped = "max_rounds" if cfg.max_rounds > 0 else "off"

    report = OptimiseReport(
        score_before=start,
        score_after=score,
        rounds=run.rounds,
        changes=run.changes,
        refused=run.refused,
        calls=run.calls,
        stopped=stopped,  # type: ignore[arg-type] — one of the Literal's values above
    )
    logfire.info(
        "ATS optimiser: {before} → {after} for {job_id}, {changed} line(s) reworded, {refused} refused",
        before=start,
        after=score,
        job_id=job.id,
        changed=len(run.changes),
        refused=len(run.refused),
        calls=run.calls,
        stopped=stopped,
    )
    return doc, report
