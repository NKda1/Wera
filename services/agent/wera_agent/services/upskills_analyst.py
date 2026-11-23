"""The Upskills analyst (UPSK-08, 22 Nov): labels and explains the gaps, never counts them.

Upskills is arithmetic over the posts' keywords (`services.upskills`), which is why it is cheap
and consistent, and why it explains nothing: "financial models" and "financial modelling" were
two gaps, a trait could slip past `not_a_skill`, and a row said how many jobs ask for a thing but
not what would show it on a CV. One fast-tier call (`upskills.analyse`, reasoning off) a search
labels the gap terms not yet labelled for this CV version, at most `upskills.analyst_terms` (40):

* `same_as` — another term of the report it is a spelling or plain synonym of;
* `kind` — skill, qualification, tool or trait;
* `evidence` — one short line on what would close the gap;
* `family_note` — optionally, who mostly asks.

**What is sent** is the report's terms and the posts' words about them — the spellings seen, the
category in code, the role families asking (UPSK-07), one short line from a posting — wrapped as
untrusted data. Where the CV stands on a term (missing, or a line but not the skills list) comes
from code; no CV text is sent.

**Kept** in `skill_notes` (migration 0018), one row per term group key and CV version, so a term
is never sent twice and a search with nothing new makes no call. A term the reply leaves out isn't
kept, and is asked again next search.

**Applied in code** by `upskills.report`, which checks each field again: the counts, the lifts,
the charts and the order stay arithmetic.

**A failure costs the notes, never the search**: the call runs after the search, on the near-miss
thread (`graphs.search_pipeline._near_misses`), and `after_search` logs anything that goes wrong
rather than raising it.
"""

from dataclasses import dataclass, field
from typing import Any

import logfire
from sqlalchemy.orm import Session

from ..clock import iso
from ..config import wera
from ..db.models import JobRow, SkillNoteRow
from ..db.session import transaction
from ..domain.vocab import group_key, text_has_term
from ..errors import ApiError
from ..llm.gateway import PENDING, structured
from ..llm.prompts import untrusted
from ..schemas.cv import MasterCV
from ..schemas.llm_outputs import UpskillsNotes
from ..schemas.upskills import QuickWin, Upskill, UpskillsReport
from . import upskills

TASK = "upskills.analyse"
# One line from a posting per term, this long at most: enough to show how the post uses it.
EXAMPLE_CHARS = 160
# Other report terms named for `same_as` only, beside the batch: the ones a merge could point at.
OTHER_TERMS = 60
STATUS_TEXT = {"missing": "not on the CV", "quick_win": "on the CV, not in skills"}


@dataclass
class Term:
    key: str  # the group key Upskills counts it under, and the cache's key
    term: str
    spellings: list[str]
    category: str | None
    status: str  # "missing" or "quick_win"
    families: list[str] = field(default_factory=list)
    example: str = ""


@dataclass
class Analysed:
    """What one pass did, for the logs and the CLI."""

    pending: int = 0  # gap terms not labelled for this CV version
    sent: int = 0  # sent in the one call (or, on a dry run, that would be)
    labelled: int = 0
    merged: int = 0
    error: str | None = None
    terms: list[str] = field(default_factory=list)


def _example(session: Session, job_ids: list[str], spellings: list[str]) -> str:
    """The first line of an asking job's post that names the term, cut to `EXAMPLE_CHARS`."""
    for job_id in job_ids:
        row = session.get(JobRow, job_id)
        text = str(((row.data if row else None) or {}).get("descriptionMd") or "")
        for line in text.splitlines():
            line = line.strip(" -*•\t")
            if line and any(text_has_term(line, s) for s in spellings):
                return line if len(line) <= EXAMPLE_CHARS else line[: EXAMPLE_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return ""


def _gaps(report: UpskillsReport) -> list[Upskill | QuickWin]:
    """The report's gap rows, most important first: the gaps (with the near misses, then
    without), then the quick wins. One a term."""
    view = report.with_near_misses
    rows: list[Upskill | QuickWin] = [
        *(view.missing if view else []),
        *report.missing,
        *(view.quick_wins if view else []),
        *report.quick_wins,
    ]
    out: dict[str, Upskill | QuickWin] = {}
    for row in rows:
        out.setdefault(group_key(row.term), row)
    return list(out.values())


def pending(session: Session, cv: MasterCV, *, now: int, cap: int) -> tuple[list[Term], list[str], int]:
    """The gap terms to send, up to `cap`; the report's other terms, for `same_as`; and how many
    gap terms are unlabelled in all. Built from the report as it stands, notes applied."""
    report = upskills.report(session, cv, now=now)
    labelled = set(upskills.notes_for(session, cv.version))
    fresh = [row for row in _gaps(report) if group_key(row.term) not in labelled]
    batch: list[Term] = []
    for row in fresh[:cap]:
        spellings = list(dict.fromkeys([row.term, *getattr(row, "spellings", [])]))
        if isinstance(row, Upskill):
            ids = [e.id for e in row.examples] + [e.job_id for e in row.near_miss_examples if e.job_id]
            families = [f"{f.label.lower()} {f.jobs + f.near_misses}" for f in row.families[:3]]
            status = "missing"
        else:
            ids, families, status = [], [], "quick_win"
        batch.append(
            Term(
                key=group_key(row.term),
                term=row.term,
                spellings=spellings[:6],
                category=row.category,
                status=status,
                families=families,
                example=_example(session, ids, spellings),
            )
        )
    sent = {t.key for t in batch}
    view = report.with_near_misses
    names = [*(d.term for d in (view.demand if view else report.demand)), *(r.term for r in _gaps(report))]
    others = [n for n in dict.fromkeys(names) if group_key(n) not in sent][:OTHER_TERMS]
    return batch, others, len(fresh)


def prompt_block(batch: list[Term], others: list[str]) -> str:
    """The terms as the prompt reads them, every word from a post inside the untrusted block."""
    lines: list[str] = []
    for t in batch:
        lines.append(f"- term: {t.term}")
        if len(t.spellings) > 1:
            lines.append(f"  spellings: {', '.join(t.spellings)}")
        lines.append(f"  status: {STATUS_TEXT[t.status]}")
        lines.append(f"  category: {t.category or 'none'}")
        if t.families:
            lines.append(f"  asked by: {', '.join(t.families)}")
        if t.example:
            lines.append(f"  example: {t.example}")
    if others:
        lines += ["", "Other report terms (for same_as only, no note): " + "; ".join(others)]
    return untrusted("Upskills terms and lines from job posts", "\n".join(lines))


def _model(session: Session) -> str:
    """The model that answered: this unit of work's newest successful call of the task, which
    the gateway keeps on the session until it commits (`gateway._record`); `structured` returns
    only the reply."""
    calls = [c for c in session.info.get(PENDING, []) if c.get("task") == TASK and c.get("ok")]
    return str(calls[-1]["model"] if calls else "unknown")[:128]


def analyse(cv: MasterCV, *, now: int, dry_run: bool = False) -> Analysed:
    """One pass: the unlabelled gap terms of this CV version, in one call at most, kept.

    Raises nothing the model can cause: an `ApiError` (budget, a refused or bad reply) is logged
    and costs this pass its notes. A dry run counts what it would send and calls nothing."""
    out = Analysed()
    cap = wera().upskills.analyst_terms
    if cap <= 0:
        return out
    with transaction() as session:
        batch, others, out.pending = pending(session, cv, now=now, cap=cap)
        out.sent, out.terms = len(batch), [t.term for t in batch]
        if dry_run or not batch:
            return out
        by_name: dict[str, Term] = {}
        for t in batch:
            for name in [t.term, *t.spellings]:
                by_name.setdefault(name.lower(), t)
        # `same_as` may name a batch term (under any of its spellings) or another term of the report.
        known = {
            **{group_key(n).lower(): group_key(n) for n in others},
            **{t.key.lower(): t.key for t in batch},
            **{k: t.key for k, t in by_name.items()},
        }
        try:
            reply = structured(
                session,
                TASK,
                UpskillsNotes,
                {"terms": prompt_block(batch, others)},
                context={
                    "terms": list(by_name),
                    # For the demo responder only (DEMO-02); the real path never reads these.
                    "upskills": [{"term": t.term, "category": t.category, "status": t.status} for t in batch],
                },
            )
        except ApiError as exc:
            logfire.warn("Upskills analyst: no notes this time ({code})", code=exc.code)
            out.error = exc.code
            return out
        model, at = _model(session), iso(now)
        done: set[str] = set()
        for note in reply.notes:
            t = by_name.get(note.term.strip().lower())
            if t is None or t.key in done:
                continue
            done.add(t.key)
            same = note.same_as and (known.get(note.same_as.strip().lower()) or known.get(group_key(note.same_as).lower()))
            same = same if same and same != t.key else None
            out.merged += same is not None
            session.merge(
                SkillNoteRow(
                    term=t.key,
                    cv_version=cv.version,
                    kind=note.kind,
                    same_as=same,
                    evidence=note.evidence,
                    family_note=note.family_note,
                    model=model,
                    created_at=at,
                )
            )
        out.labelled = len(done)
    logfire.info(
        "Upskills analyst labelled {labelled} of {sent} gap terms ({merged} merged, {pending} were new)",
        labelled=out.labelled,
        sent=out.sent,
        merged=out.merged,
        pending=out.pending,
    )
    return out


def after_search(cv: Any, *, now: int) -> Analysed | None:
    """The pass a search runs on its near-miss thread: never raises, so a failure costs the
    notes and never the search."""
    if cv is None or wera().upskills.analyst_terms <= 0:
        return None
    try:
        return analyse(cv, now=now)
    except Exception:  # noqa: BLE001 — labels for Upskills, never anything a search waits on
        logfire.exception("Upskills analyst failed")
        return None
