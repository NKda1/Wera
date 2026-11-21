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
* **Degrees** (UPSK-05, 20 Nov) are never skills. Each post's degree ask (`FitEvidence.education`)
  is judged against the CV's education in code (`domain.degrees`), and the programmes above the
  CV's level that the field names are listed — from the postings, else `config/degrees.yaml`.
  Degree-like terms in reports read before that (`degrees.looks_like_degree`) are dropped.
* **A poor match teaches most** (20 Nov, the user: "the lower a job's ATS or fit score, the more
  its keywords matter"). Each job's keywords weigh its `lift`: 1 for a strong match, rising
  smoothly to 2 for a poor one, so a gap that poorly matched jobs keep naming climbs the list.
  The lift changes importance and order only — every "N jobs ask for it" stays a plain count.
* **Each view carries its own charts** (20 Nov). The report's `demand`, `coverage` and
  `by_category` count the scored jobs; `with_near_misses` has the same three with the near misses
  in (UPSK-06), split by source, so the page's switch moves the charts as well as the table.
"""

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import iso, parse_ms
from ..config import degrees as degrees_file
from ..config import wera
from ..db import state
from ..db.models import ApplicationRow, DocumentRow, FitReportRow, JobRow, JobSignalRow
from ..domain import degrees, role_family
from ..domain.scoring import DEFAULT_WEIGHTS, fit_total, freshness_score
from ..domain.vocab import cv_line_index, group_key, master_cv_text, not_a_skill, skill_text, text_has_term
from ..errors import ApiError
from ..schemas.cv import MasterCV
from ..schemas.jobs import EducationAsk, FitEvidence, FitWeights
from ..schemas.upskills import (
    CategoryCoverage,
    Coverage,
    EducationDemand,
    FamilyShare,
    LevelDemand,
    NearMissJob,
    NearMissView,
    Programme,
    QuickWin,
    SkillDemand,
    Upskill,
    UpskillJob,
    UpskillsReport,
)

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

# How much a job's keywords matter by how well the CV matched it (20 Nov). Every constant of the
# curve is here. A match is read on a 0–1 scale: a scored job's fit total over 10, or its drafted
# pack's ATS score over 100 (its keyword coverage on packs from before the score) when that is
# lower — whichever signal is weaker, because either one low says the CV is short of this post.
# A near miss has no fit score, so its CV similarity stands in, on its own range: the near-miss
# floor (0.70) is as weak as matches get, 0.90 as strong.
#
# The curve is a smoothstep from 1 at a strong match to `MAX_LIFT` at a weak one: flat at both
# ends, so a fit of 8.6 and 9.4 weigh the same and so do 2 and 3.5, and steepest in the middle,
# where a point of fit says most. A straight ramp would kink at both ends and make the order of
# two gaps turn on a tenth of a point there. Fit 9 → 1.0, 8 → 1.07, 7 → 1.26, 6 → 1.5, 5 → 1.74,
# a fit-hidden 4 → 1.93, 3 or less → 2. A near miss starts from `near_miss_weight` (0.5), so at
# any similarity it weighs half what a scored job as close would: 0.5 at 0.90, 1.0 at 0.70.
STRONG_MATCH = 0.9
WEAK_MATCH = 0.3
STRONG_SIMILARITY = 0.90
WEAK_SIMILARITY = 0.70
MAX_LIFT = 2.0
# A job "you matched poorly", for the page's marker: halfway up the curve (fit 6, similarity 0.80).
POOR = 1.5


def lift(match: float | None, *, strong: float = STRONG_MATCH, weak: float = WEAK_MATCH) -> float:
    """How much more a job's keywords count than a strong match's: 1 to `MAX_LIFT`, rising as the
    match falls (see the constants above). An unknown match weighs 1 — no news is no lift."""
    if match is None:
        return 1.0
    t = min(max((strong - match) / (strong - weak), 0.0), 1.0)
    return round(1 + (MAX_LIFT - 1) * t * t * (3 - 2 * t), 3)


def _match(evidence: dict[str, Any] | None, job: JobRow, ats: float | None, weights: FitWeights, now: int) -> float | None:
    """A scored job's match, 0–1: its fit total over 10 as the Jobs screen shows it, or its
    pack's ATS measure when that is lower. None when neither can be read."""
    try:
        fit = FitEvidence.model_validate(evidence or {})
    except ValueError:
        return ats
    rough = (job.data or {}).get("descriptionStatus") == "missing"
    total = fit_total(fit, freshness_score(job.posted_at, now), weights, rough) / 10
    return min(total, ats) if ats is not None else total


def _ats_by_job(session: Session) -> dict[str, float]:
    """Each job's latest drafted pack's ATS measure, 0–1: the score over 100, else its keyword
    coverage (packs from before ATSC-07's score). Read only to weigh its keywords."""
    found: dict[str, float] = {}
    rows = session.execute(
        select(ApplicationRow.job_id, DocumentRow.content)
        .join(DocumentRow, DocumentRow.application_id == ApplicationRow.id)
        .where(DocumentRow.kind == "ats")
        .order_by(DocumentRow.id)
    ).all()
    for job_id, content in rows:
        score, coverage = (content or {}).get("score"), (content or {}).get("coverage")
        if score is not None:
            found[job_id] = float(score) / 100
        elif coverage is not None:
            found[job_id] = float(coverage)
    return found


def _weights(session: Session) -> FitWeights:
    try:
        return FitWeights.model_validate(state.settings_data(session)["weights"])
    except (ApiError, KeyError, ValueError):
        return DEFAULT_WEIGHTS


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


def report(
    session: Session,
    cv: MasterCV,
    *,
    now: int,
    days: int = WINDOW_DAYS,
    field: str | None = None,
    titles: list[str] | None = None,
    family: str | None = None,
) -> UpskillsReport:
    """`field` is the search plan's field ("investment banking and corporate finance"), which picks
    the starter programmes while the postings have named too few.

    UPSK-07 (21 Nov): `titles` are the person's target titles, which say their own role family;
    `family` limits the report to the jobs of one family ("quantitative"), every count with it."""
    mine = role_family.own_family(titles or [])
    seen_families: Counter = Counter()
    near_families: Counter = Counter()
    since = now - days * DAY_MS
    recent_from = now - TREND_DAYS * DAY_MS
    previous_from = now - 2 * TREND_DAYS * DAY_MS
    master = master_cv_text(cv)
    lines = cv_line_index(cv)
    applied = set(session.scalars(select(ApplicationRow.job_id).where(ApplicationRow.status != "discarded")))
    weights = _weights(session)
    ats = _ats_by_job(session)

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
    education_asks: list[tuple[EducationAsk, JobRow]] = []
    for evidence, job in rows:
        hidden = str(job.data.get("hiddenReason") or "")
        if hidden.startswith(("Not a job posting", "Duplicate")):
            continue
        seen = parse_ms(job.first_seen) if job.first_seen else 0
        if seen < since:
            continue
        kind = role_family.family_of(job.title)
        seen_families[kind] += 1
        if family and kind != family:
            continue
        considered += 1
        in_recent += seen >= recent_from
        in_previous += previous_from <= seen < recent_from
        counts_double = job.id in applied or job.status in ("shortlisted", "in_application")
        # 20 Nov: a fit-hidden job (under the bar, "Fit 3.2, below your bar of 4") counts like any
        # other, and weighs the most: its gaps are why it scored under the bar.
        lifted = lift(_match(evidence, job, ats.get(job.id), weights, now))
        if (asked_for := (evidence or {}).get("education")) is not None:
            education_asks.append((EducationAsk.model_validate(asked_for), job))
        for k in (evidence or {}).get("keywords") or []:
            term = str(k.get("term") or "").strip()
            if not term or not_a_skill(term) or degrees.looks_like_degree(term):
                continue
            key = group_key(term)
            spelled[key][term] += 1
            if k.get("category"):
                categories[key][k["category"]] += 1
            entry = asked[key].setdefault(
                job.id, {"required": False, "job": job, "seen": seen, "double": counts_double, "lift": lifted, "family": kind}
            )
            entry["required"] = entry["required"] or k.get("weight") == "required"

    # UPSK-06 (20 Nov): the jobs the CV nearly matched, at `near_miss_weight` each. One already
    # scored (unhidden since, say) counts once, from its fit report.
    near_weight = wera().upskills.near_miss_weight
    scored_ids = {job.id for _evidence, job in rows}
    nearly: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    near_asks: list[EducationAsk] = []
    near_considered = near_recent = near_previous = 0
    for signal in session.scalars(select(JobSignalRow).where(JobSignalRow.seen_at >= iso(since))).all():
        if signal.job_id and signal.job_id in scored_ids:
            continue
        kind = role_family.family_of(signal.title)
        near_families[kind] += 1
        if family and kind != family:
            continue
        seen = parse_ms(signal.seen_at)
        near_considered += 1
        near_recent += seen >= recent_from
        near_previous += previous_from <= seen < recent_from
        # A near miss has no fit score: its CV similarity says how close it came (20 Nov).
        lifted = lift(signal.similarity, strong=STRONG_SIMILARITY, weak=WEAK_SIMILARITY)
        if signal.education:
            near_asks.append(EducationAsk.model_validate(signal.education))
        for k in signal.keywords or []:
            term = str(k.get("term") or "").strip()
            if not term or not_a_skill(term) or degrees.looks_like_degree(term):
                continue
            key = group_key(term)
            spelled[key][term] += 1
            if k.get("category"):
                categories[key][k["category"]] += 1
            entry = nearly[key].setdefault(
                signal.key, {"required": False, "signal": signal, "seen": seen, "near": True, "lift": lifted, "family": kind}
            )
            entry["required"] = entry["required"] or k.get("weight") == "required"

    def term_of(key: str) -> str:
        return spelled[key].most_common(1)[0][0]

    def category_of(key: str) -> str | None:
        return categories[key].most_common(1)[0][0] if categories[key] else None

    def status_of(key: str) -> str:
        term = term_of(key)
        listed = any(text_has_term(skill_text(s), term) for s in cv.skills)
        return ("covered" if listed else "quick_win") if text_has_term(master, term) else "missing"

    def market(
        near: dict[str, dict[str, dict[str, Any]]],
    ) -> tuple[list[SkillDemand], Coverage, list[CategoryCoverage], list[QuickWin], list[str]]:
        """One view's chart data, quick wins and gap keys: every skill the jobs on the list ask
        for, and with `near` the near misses' too, by where the CV stands on it (20 Nov).

        Counts stay counts — `jobs`, `near_misses` and the coverage tallies are jobs asking. The
        importance a row is drawn and ordered by is each job's lift, a near miss at
        `near_weight`; a covered skill weighs its plain count, since a skill the CV already lists
        isn't what a poor match teaches."""
        demand: list[SkillDemand] = []
        coverage = Coverage()
        by_category: dict[str, CategoryCoverage] = {}
        wins: list[QuickWin] = []
        gaps: list[str] = []
        for key in dict.fromkeys([*asked, *near]):
            jobs, close = asked.get(key, {}), near.get(key, {})
            if not jobs and not close:
                continue
            term, category, status = term_of(key), category_of(key), status_of(key)
            weigh = (lambda e: 1.0) if status == "covered" else (lambda e: e["lift"])
            importance = round(sum(weigh(e) for e in jobs.values()), 3)
            near_importance = round(near_weight * sum(weigh(e) for e in close.values()), 3)
            share = round(len(jobs) / considered, 3) if considered else 0.0
            demand.append(
                SkillDemand(
                    term=term,
                    category=category,
                    jobs=len(jobs),
                    near_misses=len(close),
                    share=share,
                    importance=importance,
                    near_importance=near_importance,
                    poor=sum(e["lift"] >= POOR for e in [*jobs.values(), *close.values()]),
                    status=status,  # type: ignore[arg-type]
                )
            )
            tally = TALLY[status]
            bucket = by_category.setdefault(category or "other", CategoryCoverage(category=category or "other"))  # type: ignore[arg-type]
            for counted in (coverage, bucket):
                setattr(counted, tally, getattr(counted, tally) + len(jobs))
                setattr(counted, f"near_{tally}", getattr(counted, f"near_{tally}") + len(close))
            if status == "quick_win":
                line = next((v["text"] for v in lines.values() if text_has_term(v["text"], term)), "")
                wins.append(
                    QuickWin(
                        term=term,
                        category=category,
                        jobs=len(jobs),
                        share=share,
                        line=line[:160],
                        near_misses=len(close),
                        importance=round(importance + near_importance, 3),
                    )
                )
            if status == "missing":
                gaps.append(key)
        demand.sort(key=lambda d: (-(d.importance + d.near_importance), -(d.jobs + d.near_misses), d.term.lower()))
        wins.sort(key=lambda w: (-w.importance, -w.jobs, w.term.lower()))
        order = {"hard_skill": 0, "tool": 1, "methodology": 2, "qualification": 3, "other": 4}
        return demand[:DEMAND], coverage, sorted(by_category.values(), key=lambda c: order[c.category]), wins[:TOP], gaps

    def gap(
        key: str, jobs: dict[str, dict[str, Any]], near: dict[str, dict[str, Any]], recent_of: int, previous_of: int
    ) -> Upskill:
        """One skill the CV doesn't show: the jobs on the list asking, and the near misses."""
        term = term_of(key)
        everyone = [*jobs.values(), *near.values()]
        score = sum((2 if e["required"] else 1) * (2 if e["double"] else 1) * e["lift"] for e in jobs.values())
        score += near_weight * sum((2 if e["required"] else 1) * e["lift"] for e in near.values())
        recent = sum(1 for e in everyone if e["seen"] >= recent_from)
        previous = sum(1 for e in everyone if previous_from <= e["seen"] < recent_from)
        examples: list[dict[str, Any]] = []
        for e in sorted(jobs.values(), key=lambda e: (not e["double"], not e["required"], -e["seen"])):
            if len(examples) < EXAMPLES and e["job"].company not in {x["job"].company for x in examples}:
                examples.append(e)  # one job a company, so three examples are three employers
        near_examples: list[NearMissJob] = []
        for e in sorted(near.values(), key=lambda e: (not e["required"], -(e["signal"].similarity or 0.0))):
            one = e["signal"]
            if len(near_examples) < EXAMPLES and one.company not in {x.company for x in near_examples}:
                near_examples.append(NearMissJob(title=one.title, company=one.company, url=one.url, job_id=one.job_id))
        on_list, nearly_by = Counter(e["family"] for e in jobs.values()), Counter(e["family"] for e in near.values())
        kinds = sorted(set(on_list) | set(nearly_by), key=lambda f: -(on_list[f] + near_weight * nearly_by[f]))
        weight = {f: on_list[f] + near_weight * nearly_by[f] for f in kinds}
        total = sum(weight.values())
        main = kinds[0] if kinds else None
        return Upskill(
            term=term,
            spellings=sorted(spelled[key]),
            families=[
                FamilyShare(family=f, label=role_family.LABELS[f], jobs=on_list[f], near_misses=nearly_by[f]) for f in kinds
            ],
            # Mostly asked outside the person's own field: more than half its weight from one other family.
            outside_field=bool(mine and main and main != mine and total and weight[main] / total > 0.5),
            category=category_of(key),
            jobs=len(jobs),
            near_misses=len(near),
            share=round(len(jobs) / considered, 3) if considered else 0.0,
            required_share=round(sum(e["required"] for e in everyone) / len(everyone), 3),
            score=round(float(score), 3),
            poor=sum(e["lift"] >= POOR for e in everyone),
            trend=_trend(recent / recent_of if recent_of else 0.0, previous / previous_of if previous_of else 0.0, recent),
            recent=recent,
            previous=previous,
            close_to=list(dict.fromkeys(_as_written(cv, other) for other in NEAR.get(key, ()) if text_has_term(master, other))),
            examples=[UpskillJob(id=e["job"].id, title=e["job"].title, company=e["job"].company) for e in examples],
            near_miss_examples=near_examples,
        )

    def ranked(gaps: list[Upskill]) -> list[Upskill]:
        return sorted(gaps, key=lambda u: (-u.score, -u.jobs, u.term.lower()))[:TOP]

    demand, coverage, by_category, wins, missing_keys = market({})
    missing = ranked([gap(key, asked[key], {}, in_recent, in_previous) for key in missing_keys])
    view: NearMissView | None = None
    if near_considered:
        # With the near misses: every skill either asks for, near-miss-only ones included, its
        # CV status the same test as above — and the charts recounted to match (20 Nov).
        near_demand, near_coverage, near_categories, near_wins, near_keys = market(nearly)
        view = NearMissView(
            near_misses=near_considered,
            jobs_considered=considered,
            near_weight=near_weight,
            missing=ranked(
                [
                    gap(key, asked.get(key, {}), nearly.get(key, {}), in_recent + near_recent, in_previous + near_previous)
                    for key in near_keys
                ]
            ),
            quick_wins=near_wins,
            demand=near_demand,
            coverage=near_coverage,
            by_category=near_categories,
            education=_education(education_asks, cv, field, near=near_asks),
        )
    return UpskillsReport(
        window_days=days,
        jobs_considered=considered,
        missing=missing,
        quick_wins=wins,
        demand=demand,
        coverage=coverage,
        by_category=by_category,
        education=_education(education_asks, cv, field),
        own_family=mine,
        families=[
            FamilyShare(family=f, label=role_family.LABELS[f], jobs=seen_families[f], near_misses=near_families[f])
            for f in sorted(set(seen_families) | set(near_families), key=lambda f: -(seen_families[f] + near_families[f]))
        ],
        family=family,
        with_near_misses=view,
    )


def _education(
    asks: list[tuple[EducationAsk, JobRow]], cv: MasterCV, field: str | None, *, near: list[EducationAsk] | None = None
) -> EducationDemand | None:
    """UPSK-05: the degrees the jobs ask for, judged against the CV, and the programmes above its
    level that the field names. None when no job asked for a degree.

    `near` (UPSK-06): the near misses' degree asks, counted with the jobs' — a posting the CV nearly
    matched says as much about the degrees the field wants — but never an example, which links to a
    job on the list."""
    judged = [(ask, job, degrees.meets(ask, cv)) for ask, job in [*asks, *((a, None) for a in near or [])]]
    judged = [(ask, job, verdict) for ask, job, verdict in judged if verdict is not None]
    if not judged:
        return None
    own = degrees.cv_level(cv)
    by_level = Counter(ask.level for ask, _job, _v in judged if ask.level in ("bachelor", "master", "phd"))
    above = degrees.RANK.get(own, 0)
    named: Counter = Counter()
    for ask, _job, _verdict in judged:
        if degrees.RANK.get(ask.level, 0) <= above:
            continue
        for subject in ask.subjects:
            if subject.strip() and not degrees.looks_like_vague_subject(subject):
                named[degrees.programme_name(ask.level, subject.strip().title() if subject.islower() else subject.strip())] += 1
    starter = degrees_file()
    programmes = [Programme(name=name, mentions=n, source="postings") for name, n in named.most_common(8)]
    if sum(named.values()) < starter.starter_until:
        have = {p.name.lower() for p in programmes}
        programmes += [
            Programme(name=name, mentions=0, source="starter") for name in starter.for_field(field) if name.lower() not in have
        ]
    gaps = [job for _ask, job, verdict in judged if verdict != "met" and job is not None]
    examples: list[UpskillJob] = []
    for job in gaps:
        if len(examples) < EXAMPLES and job.company not in {e.company for e in examples}:
            examples.append(UpskillJob(id=job.id, title=job.title, company=job.company))
    return EducationDemand(
        cv_level=own,
        jobs_asking=len(judged),
        met=sum(v == "met" for _a, _j, v in judged),
        level_gaps=sum(v == "level_gap" for _a, _j, v in judged),
        subject_gaps=sum(v == "subject_gap" for _a, _j, v in judged),
        levels=[
            LevelDemand(
                level=level,  # type: ignore[arg-type]
                jobs=n,
                share=round(n / len(judged), 3),
                met=degrees.RANK.get(own, 0) >= degrees.RANK[level],
            )
            for level, n in sorted(by_level.items(), key=lambda kv: degrees.RANK[kv[0]])
        ],
        programmes=programmes,
        field=field,
        gap_examples=examples,
        near_misses=sum(job is None for _a, job, _v in judged),
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
