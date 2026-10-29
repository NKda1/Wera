"""`wera` command: serve the agent, reset the database, export schemas, run the golden eval."""

import argparse
import json
import os
import sys
from pathlib import Path

# `ignore_no_config` is set in pyproject.toml, but Logfire reads that from the *working
# directory* — so running `uv run --project services/agent wera …` from the repo root, which is
# the documented way to do it, finds no config and warns on every call. The warning is correct
# and useless here (a CLI run has no tracing to configure), and it printed straight through the
# eval's progress bar. Set before anything imports logfire.
os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wera", description="WERA agent service")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the agent API (loopback only by default)")
    serve.add_argument("--reload", action="store_true")
    reset = sub.add_parser("reset", help="delete this mode's database and seed it again (PRIV-05)")
    reset.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    sub.add_parser("migrate", help="apply database migrations")
    schemas = sub.add_parser("schemas", help="export the Pydantic contracts to packages/schemas/json")
    schemas.add_argument("--check", action="store_true", help="fail if the committed files are stale")
    supabase = sub.add_parser("supabase", help="export the Postgres schema to supabase/schema.sql")
    supabase.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    disc = sub.add_parser("discover", help="search the ATS vendors for boards hiring for your titles (ATS-05)")
    disc.add_argument(
        "--title", action="append", metavar="TITLE", help="search for this title; repeatable (default: your search profile's)"
    )
    disc.add_argument("--dry-run", action="store_true", help="show what would change without writing companies.yaml")
    ev = sub.add_parser("eval", help="score the golden set and report agreement with the hand scores")
    ev.add_argument("--model", help="model id to score with (default: the configured scoring tier)")
    ev.add_argument("--cv", type=Path, help="CV to score against, as YAML or JSON (default: the seeded demo CV)")
    ev.add_argument("--out", type=Path, help="where to write the report (default: data/eval/eval-<model>.md)")
    ev.add_argument("--only", action="append", metavar="ID", help="score one golden ad; repeatable")
    ev.add_argument(
        "--packs",
        action="store_true",
        help="draft real packs instead of scoring: measures the writing tier (cv.tailor, letter.write)",
    )
    ev.add_argument("--ads", type=int, default=5, metavar="N", help="how many ads --packs drafts for (default 5)")
    ev.add_argument(
        "--strict",
        action="store_true",
        # argparse runs help text through %-formatting, so a literal percent sign has to be doubled.
        help="exit non-zero unless every ad scored and agreement is at least 80%% (SPEC §12.2)",
    )
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        return _serve(args.reload)  # uvicorn builds the app, and `create_app` configures both

    # Every other command runs in this process, so it configures its own logging and tracing.
    # Without this `wera eval` and `wera discover` — the two commands that spend money on model
    # and Firecrawl calls — were the only ones producing no spans at all.
    from .logging import configure as configure_logging
    from .observability import configure as configure_observability

    configure_logging()
    configure_observability()
    try:
        return _dispatch(args)
    finally:
        # A CLI process can exit faster than the exporter's own batching, and the eight return
        # paths below include two non-zero ones (`--strict`, `--check`). Logfire registers an
        # atexit flush, but relying on it makes "did this run's telemetry arrive?" a race, and
        # the run that matters most is the one that just failed. Flushing here makes it a fact.
        _flush_telemetry()


def _flush_telemetry() -> None:
    """Push whatever is buffered before the process goes away. Never fails the command."""
    import logfire

    try:
        if not logfire.force_flush(timeout_millis=5_000):
            print("Telemetry didn't finish uploading before exit.", file=sys.stderr)
    except Exception:  # noqa: BLE001 — a command's exit code is about the command, not its traces
        pass


def _dispatch(args: argparse.Namespace) -> int:
    """Run the chosen command. Split from `main` so every exit path gets the flush above."""
    if args.cmd == "reset":
        return _reset(args.yes)
    if args.cmd == "migrate":
        from .migrate import upgrade

        upgrade()
        print("Database is up to date.")
        return 0
    if args.cmd == "schemas":
        from .schema_export import stale, write

        if args.check:
            out = stale()
            if out:
                print(f"Stale schema files: {', '.join(out)}. Run `uv run wera schemas`.", file=sys.stderr)
                return 1
            print("Schemas are up to date.")
            return 0
        for p in write():
            print(f"wrote {p}")
        return 0
    if args.cmd == "supabase":
        from .supabase_export import stale, write

        if args.check:
            if stale():
                print("supabase/schema.sql is stale. Run `uv run wera supabase`.", file=sys.stderr)
                return 1
            print("Supabase schema is up to date.")
            return 0
        print(f"wrote {write()}")
        return 0
    if args.cmd == "discover":
        return _discover(args.title, args.dry_run)
    if args.cmd == "eval":
        if args.packs:
            return _eval_packs(args.model, args.cv, args.out, args.ads, args.strict)
        return _eval(args.model, args.cv, args.out, args.only, args.strict)
    return 2


def _discover(titles: list[str] | None, dry_run: bool) -> int:
    """ATS-05 on demand. The scheduled pass runs weekly; this is for setting up, and for
    seeing what a change to your titles would actually find before committing to it."""
    from datetime import UTC, datetime

    from .clock import iso, now_ms
    from .config import companies_file, wera, write_companies
    from .db.session import transaction
    from .errors import ApiError
    from .graphs.intake import watch_titles
    from .settings import get_settings
    from .sources import ats_discovery, firecrawl_search

    env = get_settings()
    if env.wera_demo_mode:
        print("Demo mode makes no outbound requests (DEMO-02). Set WERA_DEMO_MODE=false to search.", file=sys.stderr)
        return 1
    if env.firecrawl_api_key is None:
        print("Board discovery needs FIRECRAWL_API_KEY in .env.local.", file=sys.stderr)
        return 1

    now = now_ms()
    file = companies_file()
    try:
        with transaction() as session:
            from .services.store import Store

            profile = Store(session, now).get_profile()
    except ApiError as exc:
        print(f"Couldn't read your search profile: {exc.message}", file=sys.stderr)
        return 1

    wanted = [t for t in (titles or watch_titles(profile)) if t.strip()]
    if not wanted:
        print("No titles to search for. Add target titles to your search profile.", file=sys.stderr)
        return 1

    print(f"Searching Greenhouse, Lever and Ashby for: {', '.join(wanted)}")
    merged, outcome = ats_discovery.discover(
        wanted,
        profile.locations,
        file.companies,
        max_queries=wera().discovery_max_queries,
        max_companies=file.max_companies,
        credits_per_search=firecrawl_search.SEARCH_CREDITS,
        today=datetime.fromtimestamp(now / 1000, tz=UTC).date(),
    )

    before = {c.board for c in file.companies}
    added = [c for c in merged if c.board not in before]
    dropped = [c for c in file.companies if c.board not in {c.board for c in merged}]
    for company in added:
        print(f"  + {company.vendor:10} {company.token:28} {company.name}  (matched {company.matched!r})")
    for company in dropped:
        print(f"  - {company.vendor:10} {company.token:28} {company.name}  (feed no longer answers)")
    if not added and not dropped:
        print("  nothing changed")

    print(
        f"\n{outcome.queries} search(es) · {outcome.credits_used} Firecrawl credits · "
        f"{outcome.checked} board(s) checked, {outcome.rejected} rejected"
    )
    print(f"Watchlist: {len(file.companies)} → {len(merged)} company/companies")
    for message in outcome.errors:
        print(f"  ! {message}", file=sys.stderr)

    if dry_run:
        print("\nDry run: config/companies.yaml was not written.")
        return 0
    print(f"wrote {write_companies(file.model_copy(update={'companies': merged}))}")
    with transaction() as session:
        from .services.store import Store

        Store(session, now).set_ats_discovered_at(iso(now))
    return 0


def _eval_packs(model: str | None, cv: Path | None, out: Path | None, ads: int, strict: bool) -> int:
    """SCH-05 for the writing tier. Drafts real packs and reports what came back whole.

    There is no agreement figure here and there shouldn't be: nobody hand-scores prose. What is
    checkable is whether the call returned the right shape, whether it selected anything, and
    whether its citations hold — which covers every writing-tier failure that has reached a
    person so far.
    """
    from . import evaluate
    from .errors import ApiError

    try:
        report = evaluate.run_packs(model=model, cv_path=cv, ads=ads)
    except ApiError as exc:
        print(f"Couldn't run the pack eval: {exc.message}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(f"Couldn't run the pack eval: {exc}", file=sys.stderr)
        return 1

    path = out or evaluate.packs_out(report.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(evaluate.packs_as_markdown(report), encoding="utf-8")
    data = path.with_suffix(".json")
    data.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")

    print(f"Writing model:   {report.model}")
    print(f"CV:              version {report.cv_version} — {report.cv_source}")
    print(f"Packs:           {len(report.rows)} attempted, {len(report.completed)} complete")
    print(f"Success rate:    {report.success_rate:.0f}%  (every pack must come back whole)")
    print(f"Guard pass:      {report.guard_pass_rate:.0f}%")
    print(f"Bullets/pack:    {report.mean_bullets:.1f}   ATS coverage: {report.mean_coverage:.0%}")
    print(f"Cost:            ${report.cost_usd:.4f} over {report.calls} call(s)")
    print(f"Report:          {path}")
    print(f"                 {data}")
    for code, count in sorted(report.failures.items(), key=lambda kv: -kv[1]):
        print(f"  {code} × {count}", file=sys.stderr)
    if strict and not report.passed:
        return 1
    return 0


def _eval(model: str | None, cv: Path | None, out: Path | None, only: list[str] | None, strict: bool) -> int:
    """Score the golden set, write the report, and print the three numbers that matter."""
    from . import evaluate
    from .errors import ApiError

    try:
        report = evaluate.run(model=model, cv_path=cv, only=only)
    except ApiError as exc:
        # A bad --cv file comes back as the same error the CV page would show. On a command line
        # that should read as one line, not a traceback.
        print(f"Couldn't run the eval: {exc.message}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(f"Couldn't run the eval: {exc}", file=sys.stderr)
        return 1

    path, data = evaluate.write_report(report, out or evaluate.default_out(report.model))
    agreement = "—" if report.agreement is None else f"{report.agreement:.1f}%"
    recall = "—" if report.keyword_recall is None else f"{report.keyword_recall * 100:.1f}%"
    print(f"Model:           {report.model}{' (demo responders, no network)' if report.demo_mode else ''}")
    print(f"CV:              version {report.cv_version} — {report.cv_source}")
    print(f"Ads:             {len(report.rows)} ({len(report.compared)} hand-scored, {len(report.unscored)} awaiting a score)")
    print(f"Agreement ±1.5:  {agreement} (SPEC §12.2 wants ≥80%)")
    print(f"Keyword recall:  {recall}")
    print(f"Cost:            ${report.cost_usd:.4f} over {report.calls} call(s)")
    print(f"Report:          {path}")
    print(f"                 {data}")

    for row in report.failed:
        print(f"Couldn't score {row.id}: {row.error}", file=sys.stderr)
    if report.unscored and not report.compared:
        print(
            "Nothing is hand-scored yet, so there is no agreement figure. "
            "Fill in fixtures/golden/hand_scores.yaml and run this again.",
            file=sys.stderr,
        )
    if strict and not report.passes:
        print("Eval did not pass (SPEC §12.2).", file=sys.stderr)
        return 1
    return 0


def _serve(reload: bool) -> int:
    import uvicorn

    from .settings import get_settings

    s = get_settings()
    if s.agent_token is None:
        print("AGENT_TOKEN isn't set. Start WERA with `pnpm dev`, which sets it for both processes.", file=sys.stderr)
        return 1
    uvicorn.run(
        "wera_agent.main:create_app", factory=True, host=s.agent_host, port=s.agent_port, reload=reload, log_level="warning"
    )
    return 0


def _reset(yes: bool) -> int:
    from sqlalchemy.engine import make_url

    from .db.models import Base
    from .db.session import engine, reset_caches
    from .main import ensure_database
    from .settings import get_settings

    s = get_settings()
    url = make_url(s.db_url)
    label = "demo" if s.wera_demo_mode else "your real"
    if not yes and not s.wera_demo_mode:
        answer = input(
            f"This deletes everything in {label} database ({url.render_as_string(hide_password=True)}). Type 'reset' to continue: "
        )
        if answer.strip() != "reset":
            print("Cancelled.")
            return 1
    Base.metadata.drop_all(engine())
    with engine().begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    _drop_checkpoints()
    reset_caches()
    ensure_database()
    print(f"Reset {label} database and seeded it.")
    return 0


def _drop_checkpoints() -> None:
    """Gate threads belong to the data that was just deleted, so they go with it."""
    from pathlib import Path

    from .graphs.checkpointer import checkpoint_target

    kind, target = checkpoint_target()
    if kind == "sqlite" and target != ":memory:":
        for suffix in ("", "-wal", "-shm"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
