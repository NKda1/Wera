"""`evaluate search-latency`: how long one board search takes, step by step, against a baseline
(SPEC 3.30, 22 Nov).

On 22 Nov a search took 553 s, of which the search's own work was about 306 s, and nothing said
where the time went until it was profiled by hand. This runs one real board search on the eval
copy (`wera_eval`, Redis db 5, as `live_two.py` did) and reports:

* **per-step seconds**: the run's own log (planning, job APIs, searching, reading), plus the
  scoring passes and the embedding calls timed here, and the whole search;
* **cache hit rates**: embeddings (memory, Redis, embedded), each job API's answers (`cached`,
  `coalesced`, `fetched`), and Arbeitnow's day of matches;
* **jobs added**, by status.

Against a saved baseline, a step more than 50% slower (and at least a second slower, so noise on a
step of a few hundred milliseconds isn't a regression) is flagged, and the command exits 1. The
first run, or `--save-baseline`, saves the baseline. It never runs on the live database or Redis
db 0: it refuses unless the database is `wera_eval` and Redis isn't db 0.
"""

import json
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SLOWER = 0.5  # flagged when more than this much slower than the baseline
FLOOR_S = 1.0  # and at least this many seconds slower


@dataclass
class LatencyReport:
    at: str
    run_id: str
    status: str
    total_s: float
    steps: dict[str, float]
    caches: dict[str, Any]
    jobs_added: int
    added_by_status: dict[str, int]
    new_jobs: int
    baseline: dict[str, float] = field(default_factory=dict)
    slower: list[dict[str, Any]] = field(default_factory=list)


def compare(
    steps: dict[str, float], baseline: dict[str, float], *, slower: float = SLOWER, floor_s: float = FLOOR_S
) -> list[dict[str, Any]]:
    """The steps more than `slower` slower than the baseline's, by at least `floor_s`."""
    flagged = []
    for step, now in steps.items():
        was = baseline.get(step)
        if was is None:
            continue
        if now > was * (1 + slower) and now - was >= floor_s:
            flagged.append({"step": step, "baseline_s": was, "now_s": now, "ratio": round(now / was, 2) if was else None})
    return flagged


def steps_of(log: list[dict[str, Any]]) -> dict[str, float]:
    """Seconds per phase from a run's log (`intake_runs.log`), summed where a phase ran twice."""
    out: Counter[str] = Counter()
    for entry in log or []:
        if entry.get("ms") is not None:
            out[str(entry.get("phase") or "?")] += int(entry["ms"])
    return {phase: round(ms / 1000, 1) for phase, ms in out.items()}


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 3) if total else None


@contextmanager
def _timed(module: Any, name: str, into: Counter[str], step: str) -> Iterator[None]:
    """Time every call of `module.name` into `into[step]`, for the length of the block."""
    real = getattr(module, name)

    def timed(*a: Any, **k: Any) -> Any:
        started = time.perf_counter()
        try:
            return real(*a, **k)
        finally:
            into[step] += time.perf_counter() - started

    setattr(module, name, timed)
    try:
        yield
    finally:
        setattr(module, name, real)


@contextmanager
def _counting_answers(into: dict[str, Counter[str]]) -> Iterator[None]:
    """How each provider's answers came: `cached`, `coalesced`, `fetched` or `failed`."""
    from .sources.provider_guard import Guard

    real = Guard.shared

    def shared(self: Guard, key: str, fetch: Callable[[], Any]) -> tuple[Any, str]:
        value, how = real(self, key, fetch)
        into.setdefault(self.provider, Counter())[how] += 1
        return value, how

    Guard.shared = shared  # type: ignore[method-assign]
    try:
        yield
    finally:
        Guard.shared = real  # type: ignore[method-assign]


def _check_eval_env() -> None:
    from .settings import get_settings

    s = get_settings()
    if "wera_eval" not in (s.database_url or ""):
        raise SystemExit("search-latency runs on the eval copy only: set DATABASE_URL to …/wera_eval")
    if s.redis_url.rstrip("/").endswith("/0") or s.redis_url.rstrip("/").count("/") < 3:
        raise SystemExit("search-latency never uses Redis db 0: set REDIS_URL to …/5")
    if s.wera_demo_mode:
        raise SystemExit("search-latency measures a real search: set WERA_DEMO_MODE=false")


def one_board_search() -> str:
    """One board search, as "Search now" runs it. Returns its run id."""
    from .db.session import transaction
    from .graphs import intake
    from .services.store import Store

    with transaction() as session:
        ids = Store(session).start_runs(["board_search"], scheduled=False)
    intake.run(["board_search"], ids)
    return ids["board_search"]


def measure(*, search: Callable[[], str] = one_board_search, check_env: bool = True) -> LatencyReport:
    """One search, timed. `search` runs it and returns its run id (tests pass a fake)."""
    from sqlalchemy import select

    from .clock import iso, now_ms
    from .db.models import IntakeRunRow, JobRow
    from .db.session import transaction
    from .graphs import intake
    from .nodes import prefilter
    from .sources.feeds import arbeitnow

    if check_env:
        _check_eval_env()
    with transaction() as session:
        before = set(session.scalars(select(JobRow.id)).all())
    timings: Counter[str] = Counter()
    answers: dict[str, Counter[str]] = {}
    embeds, matched = Counter(prefilter.STATS), Counter(arbeitnow.STATS)
    started = time.perf_counter()
    with (
        _timed(intake, "judge", timings, "scoring"),
        _timed(prefilter, "embed_cached", timings, "embedding"),
        _counting_answers(answers),
    ):
        run_id = search()
    total = time.perf_counter() - started
    embeds = Counter(prefilter.STATS) - embeds
    matched = Counter(arbeitnow.STATS) - matched

    with transaction() as session:
        row = session.get(IntakeRunRow, run_id)
        added = [status for job_id, status in session.execute(select(JobRow.id, JobRow.status)).all() if job_id not in before]
        status, log, new_jobs = (row.status, list(row.log or []), row.new_jobs or 0) if row else ("missing", [], 0)
    steps = steps_of(log) | {k: round(v, 1) for k, v in timings.items()} | {"total": round(total, 1)}
    texts = embeds["texts"]
    caches = {
        "embeddings": {
            **{k: embeds[k] for k in ("texts", "in_memory", "redis", "embedded")},
            "hit_rate": _rate(embeds["in_memory"] + embeds["redis"], texts),
        },
        "answers": {
            provider: {**dict(hows), "hit_rate": _rate(hows["cached"] + hows["coalesced"], sum(hows.values()))}
            for provider, hows in sorted(answers.items())
        },
        "arbeitnow_matches": {
            **{k: matched[k] for k in ("kept", "matched")},
            "hit_rate": _rate(matched["kept"], sum(matched.values())),
        },
    }
    return LatencyReport(
        at=iso(now_ms()),
        run_id=run_id,
        status=status,
        total_s=round(total, 1),
        steps=steps,
        caches=caches,
        jobs_added=len(added),
        added_by_status=dict(Counter(added)),
        new_jobs=new_jobs,
    )


def run(
    *, baseline_path: Path, save_baseline: bool = False, search: Callable[[], str] = one_board_search, check_env: bool = True
) -> LatencyReport:
    """`measure`, compared with the baseline at `baseline_path` — saved there on the first run or
    when asked."""
    report = measure(search=search, check_env=check_env)
    if baseline_path.exists() and not save_baseline:
        saved = json.loads(baseline_path.read_text(encoding="utf-8"))
        report.baseline = saved.get("steps", {})
        report.slower = compare(report.steps, report.baseline)
    else:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return report


def as_markdown(report: LatencyReport) -> str:
    out = [
        f"# Search latency — {report.at}",
        "",
        f"Run {report.run_id} ({report.status}): {report.total_s} s, {report.jobs_added} job(s) added "
        f"({', '.join(f'{n} {s}' for s, n in sorted(report.added_by_status.items())) or 'none'}), {report.new_jobs} new.",
        "",
        "| Step | Seconds | Baseline | |",
        "|---|---:|---:|---|",
    ]
    flagged = {f["step"] for f in report.slower}
    for step, seconds in report.steps.items():
        was = report.baseline.get(step)
        mark = "**slower**" if step in flagged else ""
        out.append(f"| {step} | {seconds} | {'' if was is None else was} | {mark} |")
    out += ["", "## Caches", "", "```json", json.dumps(report.caches, indent=2), "```"]
    if report.slower:
        out += ["", f"**{len(report.slower)} step(s) more than {int(SLOWER * 100)}% slower than the baseline.**"]
    return "\n".join(out)
