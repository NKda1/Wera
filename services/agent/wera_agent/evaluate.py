"""The golden eval: does this model still agree with the person's own judgement?

Model ids go stale within weeks, and a swap is cheap to make and expensive to get wrong — a
scoring tier that reads two points high puts the wrong ten jobs at the top of every morning's
shortlist, and nothing in the product would say so. This is the check that runs before a swap
(ARCHITECTURE §11, SPEC §12.2).

Twenty ads in `fixtures/golden/jobs.json` are scored by hand once, in
`fixtures/golden/hand_scores.yaml`. This module re-scores them with a model and reports:

* **agreement** — the share of hand-scored ads whose computed total is within ±1.5 of the hand
  score (SPEC §12.2 wants ≥80%);
* **keyword recall** — the share of the expected terms that `keywords.extract` found;
* **what it cost**, from the `llm_calls` rows the gateway wrote.

Two things it deliberately does *not* do:

* **It never touches the real database.** Every run gets a throwaway SQLite file, seeded the way
  a fresh install is. A fit report belongs to a job, so the ads have to be stored to be scored,
  and storing twenty fictional vacancies in someone's real shortlist is exactly the confusion
  DEMO-01 exists to avoid. The flip side is that a live eval's spend is not added to the weekly
  budget, so the report prints the cost for the person to read themselves.
* **It does not run the filters or the pre-filter.** Every ad is scored, including the ones
  intake would have dropped for free (MATCH-01, MATCH-05). The point is to measure the scoring
  tier's judgement, and a job that never reaches a model tells you nothing about it.

The scoring path itself is the real one: `nodes.fit`, through `llm.gateway.structured()`, with
the same prompts, the same schema and the same cache key. Demo mode answers from the demo
responders instead of the network, which is what makes this runnable in CI (DEMO-02).
"""

import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _tick(done: int, total: int, label: str = "") -> None:
    """A bar that rewrites itself while the eval works.

    Twenty ads is about three minutes — a keyword call and a scoring call each, both on the
    network — and every second of that used to be silent. A silent process that holds the
    terminal for three minutes reads as a hung one, and gets killed as one.

    On stderr, so stdout stays a clean report that can be piped. Only when a terminal is
    attached, so a CI log doesn't collect three hundred carriage returns.
    """
    if not sys.stderr.isatty() or not total:
        return
    width = 24
    filled = round(width * done / total)
    end = "\n" if done == total else ""
    print(
        f"\r  [{'█' * filled}{'░' * (width - filled)}] {done:>2}/{total}  {label[:44]:<44}",
        end=end,
        file=sys.stderr,
        flush=True,
    )


AGREEMENT_TOLERANCE = 1.5  # SPEC §12.2
PASS_AGREEMENT = 80.0  # the share of ads that must agree, in percent
# The share of expected keywords a live run must find. Below the last measured live run (92%),
# because `hand_scores.yaml` keyword lists are a starting point and a strict gate on a list
# nobody has finished writing would fail for the list's sake.
PASS_RECALL = 0.8

DAY_MS = 24 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# The golden set


@dataclass
class Golden:
    """One ad, plus the hand score it is measured against."""

    id: str
    ad: dict[str, Any]
    hand_total: float | None
    keywords: list[str]
    note: str

    @property
    def rough_only(self) -> bool:
        """SPEC §8.2: no description means the score is a title match, and is capped."""
        return not self.ad.get("description")


def golden_dir() -> Path:
    from .paths import FIXTURES_DIR

    return FIXTURES_DIR / "golden"


def load_golden() -> list[Golden]:
    """The ads and the hand scores, joined by id, in file order.

    An ad with no block in `hand_scores.yaml` loads unscored rather than failing: a new ad
    should be scoreable the moment it is added, and the report says how many are waiting.
    """
    directory = golden_dir()
    ads = json.loads((directory / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    hand = yaml.safe_load((directory / "hand_scores.yaml").read_text(encoding="utf-8")) or {}
    scores = hand.get("scores") or {}

    unknown = sorted(set(scores) - {ad["id"] for ad in ads})
    if unknown:
        raise ValueError(f"hand_scores.yaml scores ads that aren't in jobs.json: {', '.join(unknown)}")

    out: list[Golden] = []
    for ad in ads:
        block = scores.get(ad["id"]) or {}
        total = block.get("total")
        if total is not None and not 0 <= float(total) <= 10:
            raise ValueError(f"{ad['id']}: a hand score has to be between 0 and 10, not {total}")
        out.append(
            Golden(
                id=ad["id"],
                ad=ad,
                hand_total=None if total is None else float(total),
                keywords=[str(k) for k in (block.get("keywords") or [])],
                note=str(block.get("note") or ""),
            )
        )
    return out


def to_job(ad: dict[str, Any], now: int) -> Any:
    """A golden ad as a `Job`. Dates are relative, so freshness is the same on every run."""
    from .domain.text import slug
    from .schemas.jobs import Job

    posted = now - int(ad["postedDaysAgo"] * DAY_MS)
    city = ad.get("city") or ""
    return Job.model_validate(
        {
            "id": ad["id"],
            "dedupeKey": f"golden:{slug(ad['company'])}:{slug(ad['title'])}:{slug(city)}",
            "title": ad["title"],
            "company": ad["company"],
            "location": {
                "city": city or None,
                "country": ad.get("country"),
                "raw": ", ".join(p for p in (city, ad.get("country")) if p),
            },
            "remote": ad.get("remote"),
            "salary": ad.get("salary"),
            "descriptionMd": ad.get("description"),
            "descriptionStatus": "full" if ad.get("description") else "missing",
            "sources": [{"channel": "manual", "detail": "golden-set", "seenAt": _iso(posted)}],
            "language": "en",
            "postedAt": _iso(posted),
            "firstSeen": _iso(posted),
            "lastSeen": _iso(now),
            "status": "new",
            "seniority": ad.get("seniority"),
        }
    )


def _iso(ms: int) -> str:
    from .clock import iso

    return iso(ms)


# ---------------------------------------------------------------------------
# Comparing a hand score to a computed one


def _terms(text: str) -> frozenset[str]:
    """A keyword as a set of comparable tokens.

    Keyword matching has to survive the wording a model chooses: "REST API" and "REST APIs" are
    the same requirement, and "guardrails" is the same as "LLM guardrails" for the purpose of
    asking whether the extractor saw it. Plural 's' is dropped from anything long enough that
    dropping it is safe, which leaves "APIs"/"API" equal without making "as" into "a".
    """
    tokens = re.findall(r"[a-z0-9+#.]+", text.lower())
    return frozenset(t[:-1] if len(t) > 3 and t.endswith("s") else t for t in tokens)


def keyword_hit(expected: str, found: list[str]) -> bool:
    """Whether one expected term is covered by anything the extractor returned.

    A match either way round counts: a found term may be narrower than the expected one
    ("guardrails" for "LLM guardrails") or wider ("Python 3" for "Python"). So does a spelling
    the synonym map accepts: `keywords.extract` writes the post's own token now, so "Postgres"
    for an expected "PostgreSQL" is the extractor being right, not missing it.
    """
    from .domain.vocab import text_has_term

    want = _terms(expected)
    if not want:
        return False
    if any(text_has_term(f, expected) for f in found):
        return True
    return any(want <= have or (have and have <= want) for have in (_terms(f) for f in found))


def recall(expected: list[str], found: list[str]) -> float | None:
    """`None` when nothing is expected — no keywords is not 0% recall, it is no measurement."""
    if not expected:
        return None
    return sum(keyword_hit(term, found) for term in expected) / len(expected)


@dataclass
class Row:
    """One ad's result."""

    id: str
    title: str
    company: str
    hand: float | None
    computed: float | None
    rough_only: bool
    keywords_expected: int
    keywords_found: list[str] = field(default_factory=list)
    keyword_recall: float | None = None
    error: str | None = None
    # The four axes behind the total (§8.2). Without them the report says agreement is 40% and
    # nothing about *which* judgement disagrees, which is the difference between a number and a
    # diagnosis — the seniority rubric was rewritten off a guess for want of these.
    sub_scores: dict[str, float] = field(default_factory=dict)

    @property
    def delta(self) -> float | None:
        if self.hand is None or self.computed is None:
            return None
        return round(self.computed - self.hand, 2)

    @property
    def agrees(self) -> bool | None:
        delta = self.delta
        return None if delta is None else abs(delta) <= AGREEMENT_TOLERANCE

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "hand": self.hand,
            "computed": self.computed,
            "delta": self.delta,
            "agrees": self.agrees,
            "roughOnly": self.rough_only,
            "keywordsExpected": self.keywords_expected,
            "keywordsFound": self.keywords_found,
            "keywordRecall": None if self.keyword_recall is None else round(self.keyword_recall, 4),
            "subScores": self.sub_scores,
            "error": self.error,
        }


@dataclass
class Report:
    model: str
    demo_mode: bool
    cv_version: int
    cv_source: str
    ran_at: str
    rows: list[Row] = field(default_factory=list)
    cost_usd: float = 0.0
    calls: int = 0

    # -- the three numbers the report exists for --

    @property
    def compared(self) -> list[Row]:
        return [r for r in self.rows if r.agrees is not None]

    @property
    def agreement(self) -> float | None:
        """Percent of hand-scored ads within ±1.5. `None` when nothing is hand-scored yet."""
        if not self.compared:
            return None
        return 100.0 * sum(1 for r in self.compared if r.agrees) / len(self.compared)

    @property
    def mean_error(self) -> float | None:
        """Mean absolute error, which says *how* wrong rather than how often."""
        if not self.compared:
            return None
        return sum(abs(r.delta or 0) for r in self.compared) / len(self.compared)

    @property
    def bias(self) -> float | None:
        """Mean signed error: positive means the model scores higher than the person does.

        Worth more than the agreement figure when a model is consistently generous — every job
        reading two points high still ranks them in roughly the right order, but it breaks any
        threshold a person set by eye.
        """
        if not self.compared:
            return None
        return sum(r.delta or 0 for r in self.compared) / len(self.compared)

    @property
    def keyword_recall(self) -> float | None:
        measured = [r.keyword_recall for r in self.rows if r.keyword_recall is not None]
        return None if not measured else sum(measured) / len(measured)

    @property
    def unscored(self) -> list[Row]:
        """Ads with no hand score yet — waiting for the person, not a failure."""
        return [r for r in self.rows if r.hand is None and r.error is None]

    @property
    def failed(self) -> list[Row]:
        """Ads the model couldn't score at all. A harness or provider problem, not a judgement."""
        return [r for r in self.rows if r.error is not None]

    def axis_means(self) -> dict[str, tuple[float, float]]:
        """Each axis's mean score, for the ads that agreed and the ads that did not.

        This is the diagnostic the report lacked. An agreement figure says the scoring is wrong
        somewhere; two columns of axis means say where, and whether the gap is the applicant's
        skills (`core`, `adjacent`), their track record (`experience`) or something structural
        like seniority and location (`eligibility`).
        """
        agreed = [r for r in self.compared if r.agrees and r.sub_scores]
        missed = [r for r in self.compared if not r.agrees and r.sub_scores]
        out: dict[str, tuple[float, float]] = {}
        for axis in ("core", "adjacent", "experience", "eligibility"):
            a = sum(r.sub_scores.get(axis, 0) for r in agreed) / len(agreed) if agreed else 0.0
            m = sum(r.sub_scores.get(axis, 0) for r in missed) / len(missed) if missed else 0.0
            out[axis] = (round(a, 1), round(m, 1))
        return out

    @property
    def passes(self) -> bool:
        """SPEC §12.2, as far as this report can tell: nothing failed, and agreement is ≥80%.

        Two cases can't be judged on agreement and are not failed for it:

        * **no hand scores yet** — the set is waiting for the person, so the only thing measured
          is that every ad scored, and that is what the verdict reflects;
        * **demo mode** — the scores come from the demo responders, which were written to
          exercise the contract, not to match anyone's judgement. A demo run that agreed with
          the hand scores would be a coincidence, and failing CI on it would be noise. The run
          still has to score all twenty ads, which is the regression this catches.

        A live run also has to find the keywords (`PASS_RECALL`). Coverage divides by that list,
        so an extractor that misses half the post makes every pack's ATS score look better than
        it is — and recall was reported here but never held to anything. Not in demo mode: demo
        extraction starts from the CV's own skills, so its recall measures the fixture.
        """
        if self.failed:
            return False
        if self.demo_mode:
            return True
        if self.keyword_recall is not None and self.keyword_recall < PASS_RECALL:
            return False
        return self.agreement is None or self.agreement >= PASS_AGREEMENT

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "demoMode": self.demo_mode,
            "cvVersion": self.cv_version,
            "cvSource": self.cv_source,
            "ranAt": self.ran_at,
            "tolerance": AGREEMENT_TOLERANCE,
            "passAgreement": PASS_AGREEMENT,
            "passRecall": PASS_RECALL,
            "ads": len(self.rows),
            "compared": len(self.compared),
            "unscored": len(self.unscored),
            "failed": len(self.failed),
            "agreementPercent": None if self.agreement is None else round(self.agreement, 1),
            "meanAbsoluteError": None if self.mean_error is None else round(self.mean_error, 2),
            "bias": None if self.bias is None else round(self.bias, 2),
            "keywordRecall": None if self.keyword_recall is None else round(self.keyword_recall, 4),
            "costUsd": round(self.cost_usd, 6),
            "calls": self.calls,
            "passes": self.passes,
            "rows": [r.as_dict() for r in self.rows],
        }


# ---------------------------------------------------------------------------
# Running it


@contextmanager
def _scratch_database(directory: Path) -> Iterator[None]:
    """Point the process at a throwaway database for the length of the block, then put it back.

    The engine and the settings are both cached, so they are cleared after the environment
    changes — otherwise the first thing that already read `DATABASE_URL` keeps the real one.
    Restoring it on the way out matters because an eval can be called from a test or a longer
    process, and leaving a deleted temp file as the database URL would break the next caller.
    """
    from .db.session import reset_caches
    from .settings import get_settings

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _scratch_url(get_settings().db_url, directory)
    get_settings.cache_clear()
    reset_caches()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        reset_caches()


def _scratch_url(configured: str, directory: Path) -> str:
    """Where an eval writes: never the configured database, and always empty when it starts.

    On Postgres, a sibling database on the same server — `wera` evaluates in `wera_eval` — made if
    it doesn't exist and wiped before every run. A sibling rather than a schema inside `wera`: a
    scratch schema is one wrong `search_path` away from writing golden ads into someone's real
    shortlist, and a separate database cannot be. On SQLite (the unit tests), a file in `directory`.
    """
    from sqlalchemy.engine import make_url

    url = make_url(configured)
    if url.get_backend_name() == "sqlite":
        return f"sqlite:///{directory / 'eval.db'}"
    scratch = url.set(database=f"{url.database}_eval")
    _create_and_wipe(url, scratch.database or "")
    return scratch.render_as_string(hide_password=False)


def _create_and_wipe(server: Any, name: str) -> None:
    """Make database `name` on `server`'s Postgres if it isn't there, then empty it."""
    from .db.session import make_engine
    from .migrate import wipe_postgres

    admin = make_engine(server.set(database="postgres").render_as_string(hide_password=False))
    try:
        # CREATE DATABASE can't run inside a transaction block.
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            exists = conn.exec_driver_sql("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).scalar()
            if not exists:
                conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    finally:
        admin.dispose()
    scratch = make_engine(server.set(database=name).render_as_string(hide_password=False))
    try:
        with scratch.begin() as conn:
            wipe_postgres(conn)
    finally:
        scratch.dispose()


def _use_model(session: Any, model: str | None) -> str:
    """Set the scoring tier for this eval and return the model id actually used."""
    from .db import state

    data = state.settings_data(session)
    if model:
        data["models"] = {**data["models"], "scoring": model}
        state.save_settings_data(session, data)
    return data["models"]["scoring"]


def _use_cv(session: Any, cv_path: Path | None) -> tuple[Any, str]:
    """The CV the ads are scored against: the given file, or the one the scratch database seeded.

    The hand scores are a judgement about one CV against one ad, so which CV was used belongs
    in the report — a report that doesn't say would be unreadable a month later.

    A pinned CV is saved into the scratch database rather than only validated, so it goes
    through the same checks the CV page applies (parse, schema, unique bullet ids) and so
    anything reading `store.cv()` during the run sees the CV being evaluated.
    """
    from .services.store import Store

    store = Store(session)
    if cv_path is None:
        return store.cv(), "fixtures/demo/cv.json (seeded)"
    # `rescore=False`: the eval scores the golden set itself and reports what that cost. A
    # background re-score would both race it and bill the run for work nobody asked to measure.
    store.put_cv(store.import_cv_yaml(cv_path.read_text(encoding="utf-8")), rescore=False)
    return store.cv(), str(cv_path)


def _high_water_mark(session: Any) -> int:
    """The last `llm_calls` row id before the eval starts scoring.

    Seeding a demo database drafts the demo packs, and drafting goes through the gateway — so a
    scratch database already has a dozen calls in it before the first ad is scored. Counting
    from here means the reported cost is the eval's own, which is the number a person is
    deciding on.
    """
    from sqlalchemy import func, select

    from .db.models import LlmCallRow

    return int(session.scalar(select(func.max(LlmCallRow.id))) or 0)


def _spend(session: Any, after_id: int) -> tuple[float, int]:
    from sqlalchemy import func, select

    from .db.models import LlmCallRow

    scored = LlmCallRow.id > after_id
    total = session.scalar(select(func.sum(LlmCallRow.cost_usd)).where(scored)) or 0
    calls = session.scalar(select(func.count(LlmCallRow.id)).where(scored)) or 0
    return float(total), int(calls)


def run(*, model: str | None = None, cv_path: Path | None = None, only: list[str] | None = None) -> Report:
    """Score the golden set and return the report. Never writes to the real database."""
    import logfire

    from .clock import iso, now_ms
    from .db.session import transaction
    from .nodes import fit
    from .services import store as st
    from .settings import get_settings

    golden = load_golden()
    if only:
        wanted = set(only)
        missing = sorted(wanted - {g.id for g in golden})
        if missing:
            raise ValueError(f"No golden ad with id: {', '.join(missing)}")
        golden = [g for g in golden if g.id in wanted]

    with tempfile.TemporaryDirectory(prefix="wera-eval-") as tmp, _scratch_database(Path(tmp)):
        from .main import ensure_database

        ensure_database()
        now = now_ms()

        with transaction() as session:
            model_id = _use_model(session, model)
            cv, cv_source = _use_cv(session, cv_path)
            weights = st.Store(session, now).weights()
            mark = _high_water_mark(session)
            jobs = {}
            for g in golden:
                job = to_job(g.ad, now)
                st.save_job(session, job, created_at=iso(now))
                jobs[g.id] = job

        report = Report(
            model=model_id,
            demo_mode=get_settings().wera_demo_mode,
            cv_version=cv.version,
            cv_source=cv_source,
            ran_at=iso(now),
        )

        for index, g in enumerate(golden):
            job = jobs[g.id]
            _tick(index, len(golden), f"{job.title} — {job.company}")
            row = Row(
                id=g.id,
                title=job.title,
                company=job.company,
                hand=g.hand_total,
                computed=None,
                rough_only=g.rough_only,
                keywords_expected=len(g.keywords),
            )
            # One transaction per ad, so a provider failure on the eleventh keeps the first ten.
            with transaction() as session:
                evidence, _scored, code = fit.evidence_for(session, job, cv, model=model_id, now=now)
            if evidence is None:
                row.error = code or "SCORE_FAILED"
            else:
                row.computed = fit.report_for(job, evidence, weights, now).total
                row.sub_scores = {
                    axis: getattr(evidence, axis).score for axis in ("core", "adjacent", "experience", "eligibility")
                }
                row.keywords_found = [k.term for k in evidence.keywords]
                row.keyword_recall = recall(g.keywords, row.keywords_found)
            report.rows.append(row)
            _tick(index + 1, len(golden), f"{job.title} — {job.company}")
            logfire.info(
                "Golden {id}: hand {hand}, computed {computed}",
                id=g.id,
                hand=g.hand_total,
                computed=row.computed,
            )

        from .llm.gateway import flush_unrecorded

        flush_unrecorded()
        with transaction() as session:
            report.cost_usd, report.calls = _spend(session, mark)

    return report


# ---------------------------------------------------------------------------
# The written report


def _n(value: float | None, places: int = 1, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{places}f}{suffix}"


class _Skip(Exception):
    """This ad can't be drafted, and the row already says why."""


@dataclass
class PackRow:
    """One drafted pack, judged on what can be checked without reading the prose."""

    id: str
    title: str
    company: str
    error: str | None = None
    roles: int = 0
    bullets: int = 0
    skills: int = 0
    guard_pass: bool = False
    ats_coverage: float = 0.0
    # The share of what the CV can back that the pack covers — the half of coverage WERA is
    # accountable for, and the one `PackReport.passed` holds to the threshold.
    backed_coverage: float = 0.0
    # ATSC-07: the 0–100 score and the best this CV could reach on the same post.
    ats_score: float = 0.0
    best_score: float = 0.0
    pages: float = 0.0
    letter_words: int = 0
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """A pack that failed, or that came back with nothing in it.

        The empty case has to count as a failure. A real run produced a tailored CV with a
        summary, no bullets and no skills; the guard passed it — it only checks that what is
        present is cited, never that anything is — and the ATS step then scored it 16.7% and
        sent it round the retry loop until the draft gave up. Nothing in the pipeline called
        that a failure, so nothing here may either.
        """
        return self.error is None and self.bullets > 0

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "ok": self.ok}


@dataclass
class PackReport:
    """What the writing tier did, over a handful of ads.

    Deliberately not an agreement report. Nobody can hand-score prose the way they hand-score a
    fit, and the failures that matter here are not matters of taste: the call returned the wrong
    shape, or it selected nothing, or it cited a line that isn't in the CV. Those are checkable,
    and they are exactly the ones that were reaching real packs.
    """

    model: str
    cv_version: int
    cv_source: str
    ran_at: str
    rows: list[PackRow] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    calls: int = 0

    @property
    def completed(self) -> list[PackRow]:
        return [r for r in self.rows if r.ok]

    @property
    def success_rate(self) -> float:
        return 100.0 * len(self.completed) / len(self.rows) if self.rows else 0.0

    @property
    def mean_bullets(self) -> float:
        done = self.completed
        return sum(r.bullets for r in done) / len(done) if done else 0.0

    @property
    def mean_coverage(self) -> float:
        done = self.completed
        return sum(r.ats_coverage for r in done) / len(done) if done else 0.0

    @property
    def mean_backed_coverage(self) -> float:
        done = self.completed
        return sum(r.backed_coverage for r in done) / len(done) if done else 0.0

    @property
    def mean_ats_score(self) -> float:
        done = self.completed
        return sum(r.ats_score for r in done) / len(done) if done else 0.0

    @property
    def mean_best_score(self) -> float:
        done = self.completed
        return sum(r.best_score for r in done) / len(done) if done else 0.0

    @property
    def guard_pass_rate(self) -> float:
        done = self.completed
        return 100.0 * sum(1 for r in done if r.guard_pass) / len(done) if done else 0.0

    @property
    def passed(self) -> bool:
        """Every pack has to come back whole. There is no tolerance here on purpose: a pack that
        failed is one the person cannot send, and a 90% success rate means one application in
        ten silently isn't there.

        And the packs have to cover what the CV can back, on average, to ATSC-03's threshold.
        Coverage was recorded here and never judged, so a run measured at 48% (`packs-deepseek-…`)
        passed exactly as a run at 92% did. Backed coverage rather than raw: a post asking for
        ten things the CV has never heard of is not a pack WERA wrote badly.
        """
        from .config import wera

        whole = bool(self.rows) and len(self.completed) == len(self.rows)
        return whole and self.mean_backed_coverage >= wera().ats_threshold

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "cvVersion": self.cv_version,
            "cvSource": self.cv_source,
            "ranAt": self.ran_at,
            "successRate": round(self.success_rate, 1),
            "guardPassRate": round(self.guard_pass_rate, 1),
            "meanBullets": round(self.mean_bullets, 1),
            "meanAtsCoverage": round(self.mean_coverage, 3),
            "meanBackedCoverage": round(self.mean_backed_coverage, 3),
            "meanAtsScore": round(self.mean_ats_score, 1),
            "meanBestScore": round(self.mean_best_score, 1),
            "failures": self.failures,
            "costUsd": round(self.cost_usd, 4),
            "calls": self.calls,
            "rows": [r.as_dict() for r in self.rows],
        }


def run_packs(*, model: str | None = None, cv_path: Path | None = None, ads: int = 5) -> PackReport:
    """Draft a pack for the best-scoring golden ads and report what came back.

    SCH-05 covered the scoring tier only, which is the tier that works: `fit.score` succeeded on
    40 of 41 real calls while the writing tier managed 35 of 55. Every failure that reached a
    person — the empty CV, the draft parked as `changes_requested` — came from the tier nothing
    measured.

    Only a few ads by default. A pack is four model calls on the slowest tier, so twenty would
    be half an hour and most of a day's budget to learn what five will tell you.
    """
    import logfire

    from .clock import iso, now_ms
    from .db.session import transaction
    from .errors import ApiError
    from .nodes import fit
    from .services import drafting
    from .services import store as st
    from .services.store import Store

    golden = sorted(load_golden(), key=lambda g: -(g.hand_total or 0))[:ads]

    with tempfile.TemporaryDirectory(prefix="wera-pack-") as tmp, _scratch_database(Path(tmp)):
        from .main import ensure_database

        ensure_database()
        now = now_ms()

        with transaction() as session:
            writing = _use_writing_model(session, model)
            scoring = _scoring_model(session)
            cv, cv_source = _use_cv(session, cv_path)
            mark = _high_water_mark(session)

        report = PackReport(model=writing, cv_version=cv.version, cv_source=cv_source, ran_at=iso(now))

        for index, g in enumerate(golden):
            job = to_job(g.ad, now)
            row = PackRow(id=g.id, title=job.title, company=job.company)
            _tick(index, len(golden), f"{job.title} — {job.company}")
            started = time.monotonic()
            app_id = f"pack-{g.id}"
            try:
                with transaction() as session:
                    store = Store(session, now)
                    st.save_job(session, job, created_at=iso(now))
                    # `applications.job_id` is a foreign key with no ORM relationship behind it,
                    # so the unit of work doesn't know the job has to exist first and orders the
                    # inserts by table. Flushing says so.
                    session.flush()
                    store.create_application(app_id, job, status="drafting", created_at=iso(now))
                # A pack is written from the job's fit evidence — the keywords it has to cover
                # and the lines it may cite — so the ad has to be scored before it can be
                # drafted, which is the order Gate 1 puts them in anyway.
                with transaction() as session:
                    evidence, _was, code = fit.evidence_for(session, job, cv, model=scoring, now=now)
                if evidence is None:
                    row.error = code or "SCORE_FAILED"
                    raise _Skip
                # Its own transaction, like a real draft: four slow calls under one write lock
                # would block everything else for the length of the pack.
                with transaction() as session:
                    drafting.draft_pack(session, app_id, ready=True)
                with transaction() as session:
                    pack = Store(session, now)._load_pack(app_id)
                if pack is None:
                    row.error = "NO_PACK"
                else:
                    row.roles = sum(len(sec.items) for sec in pack.cv.sections)
                    row.bullets = sum(len(i.bullets) for sec in pack.cv.sections for i in sec.items)
                    row.skills = len(pack.cv.skills)
                    row.guard_pass = pack.cv.guard.pass_ and pack.letter.guard.pass_
                    row.ats_coverage = pack.ats.coverage
                    row.backed_coverage = pack.ats.backed_coverage
                    row.ats_score = pack.ats.score or 0.0
                    row.best_score = pack.ats.best_score or 0.0
                    row.pages = pack.ats.pages
                    row.letter_words = pack.letter.word_count
            except _Skip:
                pass  # `row.error` is already set by whatever raised it
            except ApiError as exc:
                row.error = exc.code
            except Exception as exc:  # noqa: BLE001 — one bad pack must not end the eval
                row.error = type(exc).__name__
                logfire.warn("Pack eval {id} raised {kind}", id=g.id, kind=type(exc).__name__)
            row.seconds = round(time.monotonic() - started, 1)
            if not row.ok:
                key = row.error or "EMPTY_SELECTION"
                report.failures[key] = report.failures.get(key, 0) + 1
            report.rows.append(row)
            _tick(index + 1, len(golden), f"{job.title} — {job.company}")

        from .llm.gateway import flush_unrecorded

        flush_unrecorded()
        with transaction() as session:
            report.cost_usd, report.calls = _spend(session, mark)

    return report


def _scoring_model(session: Any) -> str:
    """Which model scores the ads before they are drafted. Not configurable here: this eval is
    about the writing tier, and changing both at once would make the result unattributable."""
    from .db import state

    return state.settings_data(session)["models"]["scoring"]


def _use_writing_model(session: Any, model: str | None) -> str:
    """Set the writing tier for this eval and return the model id actually used."""
    from .db import state

    data = state.settings_data(session)
    if model:
        data["models"] = {**data["models"], "writing": model}
        state.save_settings_data(session, data)
    return data["models"]["writing"]


def packs_as_markdown(report: PackReport) -> str:
    verdict = "PASS" if report.passed else "FAIL"
    lines = [
        f"# Pack eval — `{report.model}`",
        "",
        f"**{verdict}** · {report.ran_at}",
        "",
        "| | |",
        "|---|---|",
        f"| Writing model | `{report.model}` |",
        f"| CV | version {report.cv_version} — {report.cv_source} |",
        f"| Packs | {len(report.rows)} attempted, {len(report.completed)} complete |",
        f"| Success rate | **{report.success_rate:.0f}%** (every pack must come back whole) |",
        f"| Guard pass rate | {report.guard_pass_rate:.0f}% |",
        f"| Mean bullets selected | {report.mean_bullets:.1f} |",
        f"| Mean ATS coverage | {report.mean_coverage:.0%} |",
        f"| Mean backed coverage | {report.mean_backed_coverage:.0%} |",
        f"| Mean ATS score | {report.mean_ats_score:.1f} of 100 (best this CV could reach: {report.mean_best_score:.1f}) |",
        f"| Model calls | {report.calls} |",
        f"| Cost | ${report.cost_usd:.4f} |",
        "",
        "## Per pack",
        "",
        "| Ad | Roles | Bullets | Skills | Guard | ATS | Pages | Letter | Secs | Outcome |",
        "|---|---:|---:|---:|:---:|---:|---:|---:|---:|---|",
    ]
    for r in report.rows:
        outcome = "ok" if r.ok else f"**{r.error or 'EMPTY_SELECTION'}**"
        guard = "pass" if r.guard_pass else "—"
        lines.append(
            f"| `{r.id}`<br/>{r.title} — {r.company} | {r.roles} | {r.bullets} | {r.skills} | "
            f"{guard} | {r.ats_coverage:.0%} | {r.pages:.1f} | {r.letter_words} | {r.seconds} | {outcome} |"
        )
    if report.failures:
        lines += ["", "## What went wrong", ""]
        for code, count in sorted(report.failures.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{code}` × {count}")
    lines += [
        "",
        "---",
        "",
        "An empty selection counts as a failure. A tailored CV with a summary and no bullets "
        "passes the guard — it checks that what is present is cited, not that anything is — and "
        "reaches the person as a CV with no CV in it.",
        "",
    ]
    return "\n".join(lines)


def as_markdown(report: Report) -> str:
    """The report a person reads, and the one CI keeps as an artefact."""
    verdict = "PASS" if report.passes else "FAIL"
    lines = [
        f"# Golden eval — `{report.model}`",
        "",
        f"**{verdict}** · {report.ran_at}",
        "",
        "| | |",
        "|---|---|",
        f"| Model | `{report.model}` |",
        f"| Mode | {'demo responders (no network)' if report.demo_mode else 'live model calls'} |",
        f"| CV | version {report.cv_version} — {report.cv_source} |",
        f"| Ads | {len(report.rows)} ({len(report.compared)} hand-scored, "
        f"{len(report.unscored)} awaiting a score, {len(report.failed)} failed) |",
        f"| Agreement within ±{AGREEMENT_TOLERANCE} | **{_n(report.agreement, 1, '%')}** (SPEC §12.2 wants ≥{PASS_AGREEMENT:.0f}%) |",
        f"| Mean absolute error | {_n(report.mean_error, 2)} |",
        f"| Bias (model − person) | {_n(report.bias, 2)} |",
        f"| Keyword recall | {_n(None if report.keyword_recall is None else report.keyword_recall * 100, 1, '%')} |",
        f"| Model calls | {report.calls} |",
        f"| Cost | ${report.cost_usd:.4f} |",
        "",
    ]

    if report.demo_mode:
        lines += [
            "> Demo mode: these scores come from the deterministic demo responders, not from a "
            "model. The run proves the golden set loads, scores and validates end to end — it "
            "says nothing about whether `"
            + report.model
            + "` agrees with anyone. Unset `WERA_DEMO_MODE` and set `LLM_API_KEY` for a real "
            "reading.",
            "",
        ]

    lines += [
        "## Per ad",
        "",
        "| Ad | Hand | Model | Δ | Within ±1.5 | Keyword recall | Note |",
        "|---|---:|---:|---:|:---:|---:|---|",
    ]
    for row in report.rows:
        agrees = "—" if row.agrees is None else ("yes" if row.agrees else "**no**")
        note = row.error or ("rough-only (capped at 6.5)" if row.rough_only else "")
        kw = "—" if row.keyword_recall is None else f"{row.keyword_recall * 100:.0f}% ({row.keywords_expected})"
        lines.append(
            f"| `{row.id}`<br/>{row.title} — {row.company} "
            f"| {_n(row.hand)} | {_n(row.computed)} | {_n(row.delta, 2)} | {agrees} | {kw} | {note} |"
        )

    disagreed = [r for r in report.compared if r.agrees is False]
    if disagreed:
        lines += ["", "## Where it disagreed", ""]
        for row in sorted(disagreed, key=lambda r: -abs(r.delta or 0)):
            direction = "higher" if (row.delta or 0) > 0 else "lower"
            lines.append(
                f"- `{row.id}` **{row.title}** — model scored {_n(row.computed)} against {_n(row.hand)} by hand, {abs(row.delta or 0):.2f} {direction}."
            )

    if report.failed:
        lines += ["", "## Failed to score", ""]
        lines += [f"- `{row.id}` {row.title} — `{row.error}`" for row in report.failed]

    if report.unscored:
        lines += [
            "",
            "## Not hand-scored yet",
            "",
            "Put a number in `fixtures/golden/hand_scores.yaml` for these and run the eval again:",
            "",
        ]
        lines += [f"- `{row.id}` {row.title} — {row.company} (model says {_n(row.computed)})" for row in report.unscored]

    if not report.demo_mode:
        lines += [
            "",
            "---",
            "",
            f"This eval ran against a throwaway database, so the ${report.cost_usd:.4f} above is "
            "**not** counted towards the weekly budget (COST-01). It was spent on the real key.",
        ]

    return "\n".join(lines) + "\n"


def write_report(report: Report, out: Path) -> tuple[Path, Path]:
    """Writes the markdown report and the JSON beside it. Returns both paths."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(as_markdown(report), encoding="utf-8")
    data = out.with_suffix(".json")
    data.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8")
    return out, data


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") or "model"


def default_out(model: str) -> Path:
    from .settings import get_settings

    return get_settings().data_dir / "eval" / f"eval-{slug(model)}.md"


def packs_out(model: str) -> Path:
    from .settings import get_settings

    return get_settings().data_dir / "eval" / f"packs-{slug(model)}.md"


# ---------------------------------------------------------------------------
# Letters: paragraph by paragraph against the one call (`letters.mode`, 20 Nov)
#
# `python -m wera_agent.evaluate letters` — with DATABASE_URL pointing at a `*_eval` database.
# Reads real jobs and CVs from the live database, read-only, and writes nothing there: the model
# calls are recorded in the eval database, and the letters go to the report and nowhere else.
#
# Not the golden set: those twenty ads are one career, and the letter's failures on 20 Nov were
# on a finance CV. Each job's letter is written in both modes from the same tailored CV, the same
# analysis and the same evidence, so the mode is the only thing that differs.

LETTER_TASKS = ("letter.write", "letter.paragraph", "guard.claims", "guard.repair")


@dataclass
class LetterRow:
    job_id: str
    title: str
    company: str
    career: str
    mode: str
    error: str | None = None
    words: int = 0
    paragraphs: int = 0
    calls: int = 0  # every model call the letter made, guard included
    truncations: int = 0  # replies cut off, retried or not
    failures: int = 0  # calls that ended in an error code
    dropped: int = 0
    regenerated: int = 0
    templated: int = 0
    lengthened: int = 0
    shortened: int = 0
    # 20 Nov: whether the letter speaks about visas, sponsorship or the right to work — which it
    # may never do, whatever the post says (LTR-02).
    work_rights: bool = False
    repairs: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0
    guard_pass: bool = False
    failed_on: dict[str, int] = field(default_factory=dict)  # first-round guard failures, by rule
    violations: list[str] = field(default_factory=list)  # what the stored letter shows the person
    text: str = ""


@dataclass
class LettersReport:
    ran_at: str
    writing_model: str
    rows: list[LetterRow] = field(default_factory=list)
    setup_cost_usd: float = 0.0  # tailoring, analysis and scoring, shared by both modes

    def of(self, mode: str, career: str | None = None) -> list[LetterRow]:
        return [r for r in self.rows if r.mode == mode and r.error is None and (career is None or r.career == career)]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_letters(
    *, source_url: str, finance: int = 10, cs: int = 10, finance_cv: int = 9, cs_cv: int = 8, visa: bool = False
) -> LettersReport:
    """Write each job's letter both ways and measure what it took.

    `finance` jobs are the ones the live database scored against the finance CV (version
    `finance_cv`), with their stored evidence; `cs` jobs are software roles, scored here against
    the CS CV (`cs_cv`) since the live scores for that CV were cleared when it was replaced.
    """
    from dataclasses import replace

    import logfire
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    from .clock import iso, now_ms
    from .db import state
    from .db.models import JobRow
    from .db.session import make_engine, transaction
    from .errors import ApiError
    from .llm import gateway
    from .llm.demo import PackInput
    from .main import ensure_database
    from .nodes import fit
    from .nodes.analyse import analyse
    from .nodes.tailor import tailor, write_letter
    from .schemas.cv import MasterCV
    from .schemas.jobs import FitEvidence, Job
    from .schemas.pack import JobAnalysis
    from .schemas.profile import SearchProfile
    from .services.store import current_view
    from .settings import get_settings

    target = make_url(get_settings().db_url)
    if not (target.database or "").endswith("_eval") or make_url(source_url).database == target.database:
        raise SystemExit(f"Refusing to run: DATABASE_URL must name an *_eval database, not {target.database!r}.")
    if get_settings().wera_demo_mode:
        raise SystemExit("Demo mode answers from the demo responders; run this with WERA_DEMO_MODE=false.")
    ensure_database()
    now = now_ms()

    # Every call this process makes, as the gateway records it: other processes may be using the
    # same eval database, so its `llm_calls` table can't say which calls were these.
    seen: list[dict[str, Any]] = []
    publish = gateway._publish

    def keep(row: dict[str, Any]) -> None:
        seen.append(row)
        publish(row)

    gateway._publish = keep  # type: ignore[assignment]

    source = make_engine(source_url)
    with Session(source) as live:
        live.execute(text("SET TRANSACTION READ ONLY"))
        cvs = {
            v: MasterCV.model_validate(d)
            for v, d in live.execute(
                text("SELECT version, data FROM master_cv WHERE version IN (:a, :b)"), {"a": finance_cv, "b": cs_cv}
            )
        }
        models = (live.execute(text("SELECT data FROM settings ORDER BY id LIMIT 1")).scalar() or {}).get("models")
        profile_data = live.execute(text("SELECT data FROM search_profile ORDER BY id DESC LIMIT 1")).scalar()
        profile = SearchProfile.model_validate(profile_data) if profile_data else None
        analyses = {
            job_id: content
            for job_id, content in live.execute(
                text(
                    "SELECT a.job_id, d.content FROM documents d JOIN applications a ON a.id = d.application_id "
                    "WHERE d.kind = 'analysis' ORDER BY d.version"
                )
            )
        }
        scored = live.execute(
            text(
                "SELECT j.id, f.evidence FROM fit_reports f JOIN jobs j ON j.id = f.job_id "
                "WHERE f.cv_version = :v AND length(j.data->>'descriptionMd') > 800 ORDER BY j.first_seen DESC"
            ),
            {"v": finance_cv},
        ).all()
        software = live.execute(
            text(
                "SELECT id FROM jobs WHERE title ~* '(engineer|developer|data scientist)' "
                "AND title !~* '(senior|staff|principal|lead|manager|head|director|sales|hardware|mechanical|civil)' "
                "AND length(data->>'descriptionMd') > 1500 "
                # `visa`: only posts that talk about sponsorship or the right to work, the case a
                # letter once answered with a claim the CV never made (20 Nov).
                + ("AND data->>'descriptionMd' ~* '(visa|sponsor|right to work|work permit)' " if visa else "")
                + "ORDER BY first_seen DESC"
            )
        ).all()

        def distinct(ids: list[str], n: int) -> list[Job]:
            out: list[Job] = []
            if n <= 0:
                return out
            companies: set[str] = set()
            for job_id in ids:
                row = live.get(JobRow, job_id)
                if row is None or row.company.casefold() in companies:
                    continue
                companies.add(row.company.casefold())
                out.append(current_view(Job.model_validate(row.data)))
                if len(out) == n:
                    break
            return out

        finance_jobs = distinct([r[0] for r in scored], finance)
        cs_jobs = distinct([r[0] for r in software], cs)
        evidence = {r[0]: r[1] for r in scored}
        live.rollback()
    source.dispose()

    with transaction() as session:
        data = state.settings_data(session)
        if models:
            data["models"] = {**data["models"], **models}
            state.save_settings_data(session, data)
        writing = data["models"]["writing"]
        scoring = data["models"]["scoring"]
        pages = data.get("cvPages", 1)

    report = LettersReport(ran_at=iso(now), writing_model=writing)
    jobs = [("finance", j, cvs[finance_cv]) for j in finance_jobs] + [("cs", j, cvs[cs_cv]) for j in cs_jobs]
    for index, (career, job, cv) in enumerate(jobs):
        _tick(index, len(jobs), f"{job.title} — {job.company}")
        before = len(seen)
        try:
            with transaction() as session:
                if job.id in evidence:
                    ev = fit.with_posted_keywords(FitEvidence.model_validate(evidence[job.id]), job)
                else:
                    ev = fit.with_posted_keywords(fit.score(session, job, cv, model=scoring), job)
                found = analyses.get(job.id)
                analysis = JobAnalysis.model_validate(found) if found else _try(lambda s=session, j=job, e=ev: analyse(s, j, e))
                inp = PackInput(job=job, evidence=ev, questions=[], cv=cv, cv_pages=pages, profile=profile, analysis=analysis)
                doc = tailor(session, inp)
        except ApiError as exc:
            for mode in ("paragraphs", "single"):
                report.rows.append(LetterRow(job.id, job.title, job.company, career, mode, error=exc.code))
            continue
        report.setup_cost_usd += sum(r["cost_usd"] or 0 for r in seen[before:])
        inp = replace(inp, doc=doc)
        for mode in ("paragraphs", "single"):
            row = LetterRow(job.id, job.title, job.company, career, mode)
            mark = len(seen)
            started = time.monotonic()
            try:
                with transaction() as session:
                    made, stats = write_letter(session, inp, doc, mode=mode)
                row.words, row.paragraphs = made.word_count, len(made.paragraphs)
                row.dropped, row.regenerated, row.templated, row.lengthened, row.shortened = (
                    stats.dropped,
                    stats.regenerated,
                    stats.templated,
                    stats.lengthened,
                    stats.shortened,
                )
                row.guard_pass = made.guard.pass_
                # The role's own title may say "ICAEW Sponsorship"; naming the role is not a claim.
                body = " ".join(p.text for p in made.paragraphs).replace(job.title, " ")
                row.work_rights = bool(_WORK_RIGHTS.search(body))
                row.failed_on = dict(stats.failed_on)
                row.violations = [f"{v.location} {v.rule}: {v.detail}" for v in made.guard.violations]
                row.text = "\n\n".join([made.greeting, *(p.text for p in made.paragraphs), made.signoff])
            except ApiError as exc:
                row.error = exc.code
            except Exception as exc:  # noqa: BLE001 — one bad letter must not end the eval
                row.error = type(exc).__name__
                logfire.warn("Letter eval {id} raised {kind}", id=job.id, kind=type(exc).__name__)
            row.seconds = round(time.monotonic() - started, 1)
            calls = [r for r in seen[mark:] if r["task"] in LETTER_TASKS]
            row.calls = len(calls)
            row.truncations = sum(1 for r in calls if r["code"] == gateway.TRUNCATED)
            row.failures = sum(1 for r in calls if not r["ok"])
            row.repairs = sum(1 for r in calls if r["task"] == "guard.repair")
            row.cost_usd = sum(r["cost_usd"] or 0 for r in calls)
            report.rows.append(row)
        _tick(index + 1, len(jobs), f"{job.title} — {job.company}")

    gateway._publish = publish  # type: ignore[assignment]
    gateway.flush_unrecorded()
    return report


def _try(call: Any) -> Any:
    """A call whose failure leaves the letter without it, as `drafting._analyse` does."""
    from .errors import ApiError

    try:
        return call()
    except ApiError:
        return None


_WORK_RIGHTS = re.compile(
    r"\b(visas?|sponsor(ship|ed)?|right to work|work permit|eligible to work|work authori[sz]ation)\b", re.I
)


def letters_as_markdown(report: LettersReport) -> str:
    modes = ("paragraphs", "single")

    def line(label: str, fn: Any) -> str:
        return f"| {label} | " + " | ".join(fn(m) for m in modes) + " |"

    def words(m: str) -> list[int]:
        return [r.words for r in report.of(m)]

    attempted = {m: [r for r in report.rows if r.mode == m] for m in modes}
    out = [
        "# Letters: paragraph by paragraph vs one call",
        "",
        f"{report.ran_at} · writing model `{report.writing_model}` · "
        f"{len({r.job_id for r in report.rows})} jobs ({len({r.job_id for r in report.rows if r.career == 'finance'})} finance, "
        f"{len({r.job_id for r in report.rows if r.career == 'cs'})} CS)",
        "",
        "| | paragraphs | single |",
        "|---|---:|---:|",
        line("Letters written", lambda m: f"{len(report.of(m))} of {len(attempted[m])}"),
        line("Words, mean", lambda m: f"{_mean(words(m)):.0f}"),
        line("Words, min – max", lambda m: f"{min(words(m), default=0)} – {max(words(m), default=0)}"),
        line("Under 150 words", lambda m: str(sum(1 for w in words(m) if w < 150))),
        line("Over 230 words", lambda m: str(sum(1 for w in words(m) if w > 230))),
        line("Four paragraphs", lambda m: str(sum(1 for r in report.of(m) if r.paragraphs == 4))),
        line("Guard pass", lambda m: str(sum(1 for r in report.of(m) if r.guard_pass))),
        line("Paragraphs dropped", lambda m: str(sum(r.dropped for r in report.of(m)))),
        line("Paragraphs repaired (`guard.repair` calls)", lambda m: str(sum(r.repairs for r in report.of(m)))),
        line("Paragraphs written again", lambda m: str(sum(r.regenerated for r in report.of(m)))),
        line("Paragraphs from the CV's lines (template)", lambda m: str(sum(r.templated for r in report.of(m)))),
        line("Letters lengthened", lambda m: str(sum(r.lengthened for r in report.of(m)))),
        line("Letters shortened", lambda m: str(sum(r.shortened for r in report.of(m)))),
        line("Letters speaking of visas or the right to work", lambda m: str(sum(r.work_rights for r in report.of(m)))),
        line("Model calls", lambda m: str(sum(r.calls for r in attempted[m]))),
        line("Truncated replies", lambda m: str(sum(r.truncations for r in attempted[m]))),
        line("Failed calls", lambda m: str(sum(r.failures for r in attempted[m]))),
        line("Seconds a letter, mean", lambda m: f"{_mean([r.seconds for r in report.of(m)]):.1f}"),
        line("Seconds a letter, max", lambda m: f"{max([r.seconds for r in report.of(m)], default=0):.1f}"),
        line("Cost", lambda m: f"${sum(r.cost_usd for r in attempted[m]):.4f}"),
        "",
        f"Setup shared by both modes (tailoring, analysis, scoring the CS jobs): ${report.setup_cost_usd:.4f}.",
        "",
        "Paragraph mode, why first-round paragraphs were written again: "
        + (
            ", ".join(
                f"{rule} × {n}"
                for rule, n in sorted(
                    {
                        k: sum(r.failed_on.get(k, 0) for r in report.of("paragraphs"))
                        for k in {k for r in report.of("paragraphs") for k in r.failed_on}
                    }.items(),
                    key=lambda kv: -kv[1],
                )
            )
            or "none"
        )
        + ".",
        "",
        "## By career",
        "",
        "| Career | Mode | Letters | Words, mean | Under 150 | Written again | Template | Secs, mean |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for career in ("finance", "cs"):
        for m in modes:
            rows = report.of(m, career)
            out.append(
                f"| {career} | {m} | {len(rows)} | {_mean([r.words for r in rows]):.0f} | "
                f"{sum(1 for r in rows if r.words < 150)} | {sum(r.regenerated for r in rows)} | "
                f"{sum(r.templated for r in rows)} | {_mean([r.seconds for r in rows]):.1f} |"
            )
    out += [
        "",
        "## Per letter",
        "",
        "| Job | Career | Mode | Words | Paras | Calls | Trunc | Dropped | Again | Template | Secs | Cost | Outcome |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in report.rows:
        out.append(
            f"| {r.title} — {r.company} | {r.career} | {r.mode} | {r.words} | {r.paragraphs} | {r.calls} | "
            f"{r.truncations} | {r.dropped} | {r.regenerated} | {r.templated} | {r.seconds} | ${r.cost_usd:.4f} | "
            f"{r.error or ('pass' if r.guard_pass else 'flagged')} |"
        )
    out += ["", "## The letters", ""]
    for r in report.rows:
        if r.text:
            out += [f"### {r.title} — {r.company} ({r.career}, {r.mode}, {r.words} words)", "", r.text, ""]
            out += [f"> {v}" for v in r.violations] + ([""] if r.violations else [])
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The CV-for-ATS optimiser, model against model (ATSC-10, 22 Nov)
#
# `python -m wera_agent.evaluate optimise --models a,b` — with DATABASE_URL pointing at a `*_eval`
# database, as `letters` is. Reads up to `n` approved packs from the live database, read-only (each
# with its job, fit evidence, the master CV it was built from and the change note it was last
# redrafted with), and runs the optimiser on each with each model in turn, the `optimise` tier
# overridden for the run and its fallbacks off, so a row's model is the one that answered. The
# calls are recorded in the eval database; the live one is never written to.

OPTIMISE_TASK = "cv.optimise"
# Drafted packs, reviewed or not (22 Nov): a redraft — where the optimiser runs — happens at review, and
# the live install had 45 packs ready for review and none approved yet.
APPROVED = ("ready_for_review", "changes_requested", "approved", "submitted", "interview", "offer")


@dataclass
class OptimisePack:
    app_id: str
    job: Any  # schemas.jobs.Job
    cv: Any  # schemas.cv.MasterCV
    evidence: Any  # schemas.jobs.FitEvidence
    doc: Any  # schemas.pack.TailoredCV
    letter: Any = None  # schemas.pack.CoverLetter
    cv_pages: int = 1
    note: str | None = None


@dataclass
class OptimiseRow:
    app_id: str
    title: str
    company: str
    model: str
    error: str | None = None
    before: float = 0.0
    after: float = 0.0
    rewritten: int = 0
    refused: int = 0
    refused_by_rule: dict[str, int] = field(default_factory=dict)
    calls: int = 0
    failed_calls: int = 0
    reasoning_tokens: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    call_ms: int = 0  # the model calls' own latency, summed
    seconds: float = 0.0  # the whole optimiser, measuring included
    stopped: str = ""
    models_used: list[str] = field(default_factory=list)  # what `llm_calls` says answered
    changes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def gain(self) -> float:
        return self.after - self.before


@dataclass
class OptimiseEvalReport:
    ran_at: str
    models: list[str]
    rows: list[OptimiseRow] = field(default_factory=list)

    def of(self, model: str) -> list[OptimiseRow]:
        return [r for r in self.rows if r.model == model]


def read_approved_packs(source_url: str, n: int) -> list[OptimisePack]:
    """Up to `n` approved packs from the live database, newest first, read-only."""
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session

    from .db.models import ApplicationRow, DecisionRow, DocumentRow, FitReportRow, JobRow, MasterCvRow
    from .db.session import make_engine
    from .nodes.fit import with_posted_keywords
    from .schemas.cv import MasterCV
    from .schemas.jobs import FitEvidence, Job
    from .schemas.pack import CoverLetter, TailoredCV
    from .services.store import current_view

    out: list[OptimisePack] = []
    source = make_engine(source_url)
    try:
        with Session(source) as live:
            live.execute(text("SET TRANSACTION READ ONLY"))
            pages = int(
                ((live.execute(text("SELECT data FROM settings ORDER BY id LIMIT 1")).scalar() or {}).get("cvPages")) or 1
            )
            apps = live.scalars(
                select(ApplicationRow).where(ApplicationRow.status.in_(APPROVED)).order_by(ApplicationRow.created_at.desc())
            ).all()
            for app in apps:
                if len(out) >= n:
                    break
                docs = live.scalars(
                    select(DocumentRow).where(DocumentRow.application_id == app.id).order_by(DocumentRow.version)
                ).all()
                latest = {d.kind: d.content for d in docs}
                job_row, fit_row = live.get(JobRow, app.job_id), live.get(FitReportRow, app.job_id)
                if "cv" not in latest or job_row is None or fit_row is None:
                    continue
                doc = TailoredCV.model_validate(latest["cv"])
                cv_row = live.get(MasterCvRow, doc.cv_version)
                if cv_row is None:
                    continue
                job = current_view(Job.model_validate(job_row.data))
                note = live.scalar(
                    select(DecisionRow.note)
                    .where(DecisionRow.job_id == app.job_id, DecisionRow.gate == 2, DecisionRow.decision == "request_changes")
                    .order_by(DecisionRow.decided_at.desc())
                    .limit(1)
                )
                out.append(
                    OptimisePack(
                        app_id=app.id,
                        job=job,
                        cv=MasterCV.model_validate(cv_row.data),
                        evidence=with_posted_keywords(FitEvidence.model_validate(fit_row.evidence), job),
                        doc=doc,
                        letter=CoverLetter.model_validate(latest["letter"]) if "letter" in latest else None,
                        cv_pages=pages,
                        note=note,
                    )
                )
            live.rollback()
    finally:
        source.dispose()
    return out


def optimise_packs(packs: list[OptimisePack], models: list[str], *, ran_at: str) -> OptimiseEvalReport:
    """The optimiser on every pack with every model, in the current database. Each pack's CV is
    the approved one as it was stored, so the models start from the same lines."""
    import logfire

    from .config import wera
    from .db.session import transaction
    from .errors import ApiError
    from .llm import gateway
    from .nodes.ats_optimise import optimise

    seen: list[dict[str, Any]] = []
    publish = gateway._publish

    def keep(row: dict[str, Any]) -> None:
        seen.append(row)
        publish(row)

    cfg = wera()
    tier_model, fallbacks = cfg.models.optimise, cfg.fallbacks.get("optimise")
    gateway._publish = keep  # type: ignore[assignment]
    cfg.fallbacks["optimise"] = []
    report = OptimiseEvalReport(ran_at=ran_at, models=models)
    total = len(packs) * len(models)
    try:
        for m_index, model in enumerate(models):
            cfg.models.optimise = model
            for p_index, pack in enumerate(packs):
                label = f"{model} · {pack.job.title} — {pack.job.company}"
                _tick(m_index * len(packs) + p_index, total, label)
                row = OptimiseRow(pack.app_id, pack.job.title, pack.job.company, model)
                mark = len(seen)
                started = time.monotonic()
                try:
                    with transaction() as session:
                        _doc, result = optimise(
                            session,
                            pack.doc,
                            cv=pack.cv,
                            evidence=pack.evidence,
                            job=pack.job,
                            cv_pages=pack.cv_pages,
                            note=pack.note,
                            letter=pack.letter,
                        )
                    row.before, row.after, row.stopped = result.score_before, result.score_after, result.stopped
                    row.rewritten, row.refused = len(result.changes), len(result.refused)
                    for r in result.refused:
                        row.refused_by_rule[r.rule] = row.refused_by_rule.get(r.rule, 0) + 1
                    row.changes = [c.model_dump(mode="json") for c in result.changes]
                except ApiError as exc:
                    row.error = exc.code
                except Exception as exc:  # noqa: BLE001 — one bad pack must not end the eval
                    row.error = type(exc).__name__
                    logfire.warn("Optimiser eval {id} raised {kind}", id=pack.app_id, kind=type(exc).__name__)
                row.seconds = round(time.monotonic() - started, 1)
                calls = [r for r in seen[mark:] if r["task"] == OPTIMISE_TASK]
                row.calls = len(calls)
                row.failed_calls = sum(1 for r in calls if not r["ok"])
                row.reasoning_tokens = sum(r["reasoning_tokens"] or 0 for r in calls)
                row.tokens_in = sum(r["input_tokens"] or 0 for r in calls)
                row.tokens_out = sum(r["output_tokens"] or 0 for r in calls)
                row.cost_usd = sum(r["cost_usd"] or 0 for r in calls)
                row.call_ms = sum(r["latency_ms"] or 0 for r in calls)
                row.models_used = sorted({r["model"] for r in calls})
                report.rows.append(row)
        _tick(total, total, "done")
    finally:
        gateway._publish = publish  # type: ignore[assignment]
        cfg.models.optimise = tier_model
        if fallbacks is None:
            cfg.fallbacks.pop("optimise", None)
        else:
            cfg.fallbacks["optimise"] = fallbacks
        gateway.flush_unrecorded()
    return report


def run_optimise(*, source_url: str, n: int, models: list[str]) -> OptimiseEvalReport:
    """`optimise_packs` over `n` approved live packs, refusing anything but an `*_eval` database."""
    from sqlalchemy.engine import make_url

    from .clock import iso, now_ms
    from .main import ensure_database
    from .settings import get_settings

    target = make_url(get_settings().db_url)
    if not (target.database or "").endswith("_eval") or make_url(source_url).database == target.database:
        raise SystemExit(f"Refusing to run: DATABASE_URL must name an *_eval database, not {target.database!r}.")
    if get_settings().wera_demo_mode:
        raise SystemExit("Demo mode answers from the demo responders; run this with WERA_DEMO_MODE=false.")
    ensure_database()
    packs = read_approved_packs(source_url, n)
    if not packs:
        raise SystemExit("No approved packs in the source database to optimise.")
    return optimise_packs(packs, models, ran_at=iso(now_ms()))


def optimise_as_markdown(report: OptimiseEvalReport) -> str:
    models = report.models

    def line(label: str, fn: Any) -> str:
        return f"| {label} | " + " | ".join(fn(report.of(m)) for m in models) + " |"

    def ok(rows: list[OptimiseRow]) -> list[OptimiseRow]:
        return [r for r in rows if r.error is None]

    def calls(rows: list[OptimiseRow]) -> int:
        return sum(r.calls for r in rows)

    out = [
        "# CV-for-ATS optimiser: model against model (ATSC-10)",
        "",
        f"{report.ran_at} · {len({r.app_id for r in report.rows})} approved packs · reasoning off · fallbacks off",
        "",
        "| | " + " | ".join(f"`{m}`" for m in models) + " |",
        "|---|" + "---:|" * len(models),
        line("Packs run", lambda rows: f"{len(ok(rows))} of {len(rows)}"),
        line("ATS before, mean", lambda rows: f"{_mean([r.before for r in ok(rows)]):.1f}"),
        line("ATS after, mean", lambda rows: f"{_mean([r.after for r in ok(rows)]):.1f}"),
        line("Gain, mean", lambda rows: f"{_mean([r.gain for r in ok(rows)]):+.1f}"),
        line("Packs improved", lambda rows: str(sum(1 for r in ok(rows) if r.gain > 0))),
        line("Bullets rewritten", lambda rows: str(sum(r.rewritten for r in rows))),
        line("Refused by the guard", lambda rows: str(sum(r.refused for r in rows))),
        line(
            "Refused, by rule",
            lambda rows: (
                ", ".join(
                    f"{k} {v}"
                    for k, v in sorted(
                        {k: sum(r.refused_by_rule.get(k, 0) for r in rows) for r in rows for k in r.refused_by_rule}.items()
                    )
                )
                or "none"
            ),
        ),
        line("Model calls", lambda rows: str(calls(rows))),
        line("Failed calls", lambda rows: str(sum(r.failed_calls for r in rows))),
        line("Reasoning tokens", lambda rows: str(sum(r.reasoning_tokens for r in rows))),
        line("Tokens in / out", lambda rows: f"{sum(r.tokens_in for r in rows)} / {sum(r.tokens_out for r in rows)}"),
        line("Cost", lambda rows: f"${sum(r.cost_usd for r in rows):.4f}"),
        line("Seconds a call, mean", lambda rows: f"{(sum(r.call_ms for r in rows) / max(calls(rows), 1)) / 1000:.1f}"),
        line("Seconds a pack, max", lambda rows: f"{max([r.seconds for r in rows], default=0):.1f}"),
        "",
        "## Per pack",
        "",
        "| Pack | Model | Before | After | Rewritten | Refused | Calls | Reasoning | Cost | Secs | Stopped |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in report.rows:
        out.append(
            f"| {r.title} — {r.company} | {r.model} | {r.before:.0f} | {r.after:.0f} | {r.rewritten} | {r.refused} | "
            f"{r.calls} | {r.reasoning_tokens} | ${r.cost_usd:.4f} | {r.seconds} | {r.error or r.stopped} |"
        )
    out += ["", "## The rewrites, for checking by hand", ""]
    for r in report.rows:
        for c in r.changes:
            out += [
                f"### {r.title} — {r.company} · {r.model} · {c['keyword']}",
                "",
                f"- Before: {c['before']}",
                f"- After: {c['after']}",
                f"- Cites: {', '.join(c['sourceIds'])} · {c['reason']}",
                "",
            ]
    return "\n".join(out)


if __name__ == "__main__":
    import argparse

    os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")
    parser = argparse.ArgumentParser(prog="python -m wera_agent.evaluate")
    sub = parser.add_subparsers(dest="cmd", required=True)
    letters = sub.add_parser("letters", help="write each job's letter in both `letters.mode`s and compare")
    letters.add_argument("--source", default="postgresql://localhost:5432/wera", help="the live database, read only")
    letters.add_argument("--finance", type=int, default=10)
    letters.add_argument("--cs", type=int, default=10)
    letters.add_argument("--visa", action="store_true", help="only software posts that mention visas or sponsorship")
    letters.add_argument("--out", type=Path, required=True)
    opt = sub.add_parser("optimise", help="run the CV-for-ATS optimiser on approved packs with each model (ATSC-10)")
    opt.add_argument("--source", default="postgresql://localhost:5432/wera", help="the live database, read only")
    opt.add_argument("--n", type=int, default=15, help="how many approved packs (default 15)")
    opt.add_argument("--models", required=True, help="comma-separated model ids for the optimise tier")
    opt.add_argument("--out", type=Path, required=True)
    # SPEC 3.30: one board search on wera_eval, per step, against a baseline (`search_latency`).
    lat = sub.add_parser("search-latency", help="time one board search on wera_eval, step by step, against a baseline")
    lat.add_argument("--baseline", type=Path, required=True, help="the baseline JSON; written on the first run")
    lat.add_argument("--save-baseline", action="store_true", help="save this run as the new baseline")
    lat.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("WERA_RUN_LABEL", "eval")
    from .logging import configure as configure_logging
    from .observability import configure as configure_observability

    configure_logging()
    configure_observability()
    if args.cmd == "search-latency":
        from . import search_latency

        timed = search_latency.run(baseline_path=args.baseline, save_baseline=args.save_baseline)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(search_latency.as_markdown(timed), encoding="utf-8")
        args.out.with_suffix(".json").write_text(json.dumps(timed.__dict__, indent=2), encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
        sys.exit(1 if timed.slower else 0)
    if args.cmd == "optimise":
        found = run_optimise(source_url=args.source, n=args.n, models=[m.strip() for m in args.models.split(",") if m.strip()])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(optimise_as_markdown(found), encoding="utf-8")
        args.out.with_suffix(".json").write_text(
            json.dumps([{**r.__dict__, "gain": r.gain} for r in found.rows], indent=2), encoding="utf-8"
        )
        print(f"Wrote {args.out}", file=sys.stderr)
        sys.exit(0)
    result = run_letters(source_url=args.source, finance=args.finance, cs=args.cs, visa=args.visa)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(letters_as_markdown(result), encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps([r.__dict__ for r in result.rows], indent=2), encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr)
