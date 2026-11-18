"""Upskills (UPSK-01…04): what the jobs a person is shown ask for that their CV doesn't show.

Every scored job's fit report already carries the post's keywords (`FitEvidence.keywords`, from
`keywords.extract`), and the master CV says which it backs (`vocab.master_cv_text`, the same test
the ATS report uses for `in_master_cv`). So this is arithmetic over rows WERA already has: no
model call, no fetch, and as current as the last search.

* **Which jobs count:** scored ones, seen in the window (60 days by default), excluding what WERA
  hid as not a posting or as a duplicate — those aren't jobs anyone could apply to. A job the
  person shortlisted or applied to counts double: what they act on says more about what they want.
* **What counts as missing:** a keyword the master CV doesn't contain under any spelling that
  counts as it, grouped by synonym so "GCP" and "Google Cloud Platform" are one skill. Ways of
  working and generic traits are left out (`NOT_A_SKILL`): they are culture, not a course to take.
* **Quick wins:** keywords the CV *does* back in a line but doesn't list as a skill — the
  cheapest ATS points there are (ATSC-07), and the person's call to claim.
"""

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import parse_ms
from ..db.models import ApplicationRow, FitReportRow, JobRow
from ..domain.vocab import NOT_A_SKILL, cv_line_index, group_key, master_cv_text, skill_text, text_has_term
from ..schemas.cv import MasterCV
from ..schemas.upskills import CategoryCoverage, Coverage, QuickWin, SkillDemand, Upskill, UpskillJob, UpskillsReport

DAY_MS = 24 * 60 * 60 * 1000
WINDOW_DAYS = 60
TREND_DAYS = 14
TOP = 25
EXAMPLES = 3
# The most-asked skills, whatever the CV says about them: enough for the page to chart its top 15
# within any one category.
DEMAND = 40
# The counters each status adds to, in `Coverage`'s field names.
TALLY = {"covered": "covered", "quick_win": "quick_wins", "missing": "missing"}

# Keywords that leak past `keywords.extract`'s soft-skill bucket and aren't something to learn.
# Measured on the real reports (14 Nov): "problem solving" and "communication skills" were in the
# top fifteen "missing skills", ahead of real tools.

# Skills close enough that someone with one is most of the way to the other. Only shown when the
# CV has the near one, as the shortest way in.
NEAR: dict[str, tuple[str, ...]] = {
    "aws": ("azure", "gcp"), "azure": ("aws", "gcp"), "gcp": ("aws", "azure"),
    "terraform": ("cloudformation", "pulumi"), "cloudformation": ("terraform",), "pulumi": ("terraform",),
    "kubernetes": ("docker",), "ci/cd": ("version control", "docker"),
    "pytorch": ("tensorflow", "jax", "machine learning"), "tensorflow": ("pytorch", "machine learning"),
    "javascript": ("typescript",), "typescript": ("javascript",), "react": ("vue", "angular", "javascript"),
    "vue": ("react", "javascript"), "angular": ("react", "typescript"),
    "postgresql": ("relational databases", "mysql"), "mysql": ("relational databases", "postgresql"),
    "observability": ("linux",), "linux": ("bash",), "java": ("kotlin", "c# / .net"), "kotlin": ("java",),
    "c# / .net": ("java",), "go": ("rust", "c++"), "rust": ("go", "c++"), "c++": ("rust", "go"),
    "machine learning": ("python", "pytorch"), "deep learning": ("pytorch", "machine learning"),
}  # fmt: skip


def report(session: Session, cv: MasterCV, *, now: int, days: int = WINDOW_DAYS) -> UpskillsReport:
    since = now - days * DAY_MS
    recent_from = now - TREND_DAYS * DAY_MS
    previous_from = now - 2 * TREND_DAYS * DAY_MS
    master = master_cv_text(cv)
    lines = cv_line_index(cv)
    applied = set(session.scalars(select(ApplicationRow.job_id).where(ApplicationRow.status != "discarded")))

    rows = session.execute(select(FitReportRow.evidence, JobRow).join(JobRow, JobRow.id == FitReportRow.job_id)).all()
    considered = 0
    # Jobs seen in each trend period, every skill counted: the denominator that makes a trend a
    # change in demand rather than a change in how much WERA searched (14 Nov: counted raw,
    # nearly every skill read "falling" because the fortnight before had more searches).
    in_recent = in_previous = 0
    # Per skill: how posts spelled it, which jobs, how many marked it required, and its weight.
    spelled: dict[str, Counter] = defaultdict(Counter)
    asked: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    categories: dict[str, Counter] = defaultdict(Counter)
    for evidence, job in rows:
        hidden = str(job.data.get("hiddenReason") or "")
        if hidden.startswith(("Not a job posting", "Duplicate")):
            continue
        seen = parse_ms(job.first_seen) if job.first_seen else 0
        if seen < since:
            continue
        considered += 1
        in_recent += seen >= recent_from
        in_previous += previous_from <= seen < recent_from
        counts_double = job.id in applied or job.status in ("shortlisted", "in_application")
        for k in (evidence or {}).get("keywords") or []:
            term = str(k.get("term") or "").strip()
            if not term or term.lower() in NOT_A_SKILL:
                continue
            key = group_key(term)
            if key in NOT_A_SKILL:
                continue
            spelled[key][term] += 1
            if k.get("category"):
                categories[key][k["category"]] += 1
            entry = asked[key].setdefault(job.id, {"required": False, "job": job, "seen": seen, "double": counts_double})
            entry["required"] = entry["required"] or k.get("weight") == "required"

    missing: list[Upskill] = []
    wins: list[QuickWin] = []
    demand: list[SkillDemand] = []
    coverage = Coverage()
    by_category: dict[str, CategoryCoverage] = {}
    for key, jobs in asked.items():
        term = spelled[key].most_common(1)[0][0]
        share = len(jobs) / considered if considered else 0.0
        category = categories[key].most_common(1)[0][0] if categories[key] else None
        listed = any(text_has_term(skill_text(s), term) for s in cv.skills)
        status = ("covered" if listed else "quick_win") if text_has_term(master, term) else "missing"
        demand.append(SkillDemand(term=term, category=category, jobs=len(jobs), share=round(share, 3), status=status))
        field = TALLY[status]
        setattr(coverage, field, getattr(coverage, field) + len(jobs))
        bucket = by_category.setdefault(category or "other", CategoryCoverage(category=category or "other"))
        setattr(bucket, field, getattr(bucket, field) + len(jobs))
        if status == "quick_win":
            line = next((v["text"] for v in lines.values() if text_has_term(v["text"], term)), "")
            wins.append(QuickWin(term=term, category=category, jobs=len(jobs), share=round(share, 3), line=line[:160]))
        if status != "missing":
            continue
        score = sum((2 if e["required"] else 1) * (2 if e["double"] else 1) for e in jobs.values())
        recent = sum(1 for e in jobs.values() if e["seen"] >= recent_from)
        previous = sum(1 for e in jobs.values() if previous_from <= e["seen"] < recent_from)
        examples: list[dict[str, Any]] = []
        for e in sorted(jobs.values(), key=lambda e: (not e["double"], not e["required"], -e["seen"])):
            if len(examples) < EXAMPLES and e["job"].company not in {x["job"].company for x in examples}:
                examples.append(e)  # one job a company, so three examples are three employers
        missing.append(
            Upskill(
                term=term,
                spellings=sorted(spelled[key]),
                category=category,
                jobs=len(jobs),
                share=round(share, 3),
                required_share=round(sum(e["required"] for e in jobs.values()) / len(jobs), 3),
                score=float(score),
                trend=_trend(recent / in_recent if in_recent else 0.0, previous / in_previous if in_previous else 0.0, recent),
                recent=recent,
                previous=previous,
                close_to=list(dict.fromkeys(_as_written(cv, near) for near in NEAR.get(key, ()) if text_has_term(master, near))),
                examples=[UpskillJob(id=e["job"].id, title=e["job"].title, company=e["job"].company) for e in examples],
            )
        )
    missing.sort(key=lambda u: (-u.score, -u.jobs, u.term.lower()))
    wins.sort(key=lambda w: (-w.jobs, w.term.lower()))
    demand.sort(key=lambda d: (-d.jobs, d.term.lower()))
    order = {"hard_skill": 0, "tool": 1, "methodology": 2, "other": 3}
    return UpskillsReport(
        window_days=days,
        jobs_considered=considered,
        missing=missing[:TOP],
        quick_wins=wins[:TOP],
        demand=demand[:DEMAND],
        coverage=coverage,
        by_category=sorted(by_category.values(), key=lambda c: order[c.category]),
    )


def _as_written(cv: MasterCV, term: str) -> str:
    """A related skill as the CV spells it ("Docker", not the group key "docker")."""
    return next((s.name for s in cv.skills if text_has_term(skill_text(s), term)), term.title() if term.islower() else term)


def _trend(recent_share: float, previous_share: float, recent: int) -> str:
    """Of each fortnight's jobs, the share asking — a quarter more or less is a move."""
    if previous_share == 0:
        return "new" if recent else "steady"
    if recent_share >= previous_share * 1.25:
        return "rising"
    if recent_share <= previous_share * 0.75:
        return "falling"
    return "steady"
