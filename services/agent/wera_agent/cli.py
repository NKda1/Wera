"""`wera` command: serve the agent, reset the database, export schemas, run the golden eval."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
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
    db = sub.add_parser("db", help="the Postgres database: move an SQLite install in, back it up (REL-04, REL-05)")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    imp = db_sub.add_parser("import-sqlite", help="copy an SQLite install's rows and gate threads into Postgres")
    imp.add_argument("path", type=Path, help="the SQLite database, e.g. data/wera.db")
    imp.add_argument("--graph", type=Path, help="its gate threads (default: <path>.graph)")
    imp.add_argument("--dry-run", action="store_true", help="count what would be copied and check the target; write nothing")
    db_sub.add_parser("backup", help="pg_dump the database to data/backups/ and keep the last 14 days")
    tel = sub.add_parser("telemetry", help="the Logfire archive in Postgres: telemetry.records (OBS-01)")
    tel_sub = tel.add_subparsers(dest="telemetry_cmd", required=True)
    tel_arc = tel_sub.add_parser("archive", help="copy what Logfire holds since the last archive into Postgres")
    tel_arc.add_argument("--since", metavar="AGE", help="re-read this far back, e.g. 7d or 12h (Logfire keeps 30d)")
    tel_sub.add_parser("status", help="how much is archived, from when, and when the archive last ran")
    an = sub.add_parser("analytics", help="the read-only analytics schema (OBS-03)")
    an_sub = an.add_subparsers(dest="analytics_cmd", required=True)
    an_sub.add_parser("refresh", help="recompute analytics.fit_totals from the fit reports")
    schemas = sub.add_parser("schemas", help="export the Pydantic contracts to packages/schemas/json")
    schemas.add_argument("--check", action="store_true", help="fail if the committed files are stale")
    supabase = sub.add_parser("supabase", help="export the Postgres schema to supabase/schema.sql")
    supabase.add_argument("--check", action="store_true", help="fail if the committed file is stale")
    disc = sub.add_parser("discover", help="search the ATS vendors for boards hiring for your titles (ATS-05)")
    disc.add_argument(
        "--title", action="append", metavar="TITLE", help="search for this title; repeatable (default: your search profile's)"
    )
    disc.add_argument("--dry-run", action="store_true", help="show what would change without writing companies.yaml")
    jobs = sub.add_parser("jobs", help="look after the saved jobs")
    jobs_sub = jobs.add_subparsers(dest="jobs_cmd", required=True)
    clear = jobs_sub.add_parser("clear", help="delete every job, application and pack (SRCH-27, testing only)")
    clear.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    audit = jobs_sub.add_parser("audit-listings", help="find saved jobs that are really results pages (SRCH-21)")
    audit.add_argument("--apply", action="store_true", help="hide them (default: only list them)")
    refilter = jobs_sub.add_parser("refilter", help="re-apply your filters to hidden jobs and bring back what they now keep")
    refilter.add_argument("--apply", action="store_true", help="bring them back (default: only list them)")
    tg = sub.add_parser("targets", help="the Strategist's job discovery: Serper searches into the Redis queue (SRCH-20)")
    tg_sub = tg.add_subparsers(dest="targets_cmd", required=True)
    tg_disc = tg_sub.add_parser("discover", help="send the planned queries and queue the postings they find")
    tg_disc.add_argument("--tier", type=int, action="append", metavar="N", help="tiers to send; repeatable (default: 1)")
    tg_disc.add_argument("--no-plan", action="store_true", help="don't refresh a stale Strategist plan (no model call)")
    tg_sub.add_parser("queries", help="print the planned queries without sending any")
    tg_sub.add_parser("list", help="print what is queued for the current plan")
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
    if args.cmd == "db":
        return _import_sqlite(args.path, args.graph, args.dry_run) if args.db_cmd == "import-sqlite" else _backup()
    if args.cmd == "telemetry":
        return _telemetry_archive(args.since) if args.telemetry_cmd == "archive" else _telemetry_status()
    if args.cmd == "analytics":
        return _analytics_refresh()
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
    if args.cmd == "targets":
        return _targets(
            args.targets_cmd,
            tiers=args.tier if args.targets_cmd == "discover" else None,
            plan=not getattr(args, "no_plan", False),
        )
    if args.cmd == "jobs":
        if args.jobs_cmd == "clear":
            return _clear_jobs(args.yes)
        return _refilter(args.apply) if args.jobs_cmd == "refilter" else _audit_listings(args.apply)
    if args.cmd == "eval":
        if args.packs:
            return _eval_packs(args.model, args.cv, args.out, args.ads, args.strict)
        return _eval(args.model, args.cv, args.out, args.only, args.strict)
    return 2


def _targets(cmd: str, *, tiers: list[int] | None, plan: bool) -> int:
    """SRCH-20's first two stages on demand: what the Strategist would search for, sending it, and
    what is waiting for the Harvester. `queries` spends nothing; `discover` spends Serper queries
    (capped by `max_serper_queries`) and no Firecrawl credits."""
    from .clock import now_ms
    from .config import sites, wera
    from .db.session import transaction
    from .errors import ApiError
    from .nodes import strategist
    from .services.store import Store
    from .sources import serper, targets

    try:
        with transaction() as session:
            store = Store(session, now_ms())
            planned, matrix = store.search_queries(refresh=plan and cmd == "discover")
            profile = store.get_profile()
            known = store.known_apply_urls()
            key = store.plan_key()
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1

    if cmd == "queries":
        for q in planned:
            print(f"  tier {q.tier}  {q.source:5}  gl={q.gl or '-'} hl={q.hl}  {q.q}")
        print(
            f"\n{len(planned)} planned quer{'y' if len(planned) == 1 else 'ies'} for plan {key}"
            f"{'' if matrix else ' (no Strategist plan: built from the CV and profile alone)'}."
        )
        return 0

    try:
        queue = targets.TargetQueue(targets.connect(), key)
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    if cmd == "list":
        for t in queue.peek(100):
            print(f"  #{t.rank:<3} {t.source:5} {t.title[:50]:50} {t.url}")
        print(f"\n{queue.size()} posting(s) queued for plan {key}.")
        return 0

    queue.retire_others()
    try:
        client = serper.Serper(max_queries=wera().max_serper_queries)
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    chosen = sorted(set(tiers or [1]))
    print(
        f"Plan {key}: sending tier(s) {chosen} — {sum(1 for q in planned if q.tier in chosen)} queries, "
        f"at most {client.max_queries}."
    )
    found = strategist.discover(planned, chosen, serper=client, queue=queue, known_urls=known, sites=sites(), profile=profile)
    kinds = ", ".join(f"{n} {k}" for k, n in found.kinds.most_common()) or "nothing"
    print(f"Serper:   {client.sent} sent, {client.answered} answered, window {client.window}")
    print(f"Results:  {found.results} ({kinds})")
    refused = f", refused by {', '.join(sorted(found.refused))}" if found.refused else ""
    print(f"Listings: {found.listings_read} read for links, {found.listing_links} posting links found{refused}")
    print(f"Skipped:  {found.known} already saved, {found.excluded} excluded by your profile, {found.blocked} on declined boards")
    print(f"Queued:   {found.queued} new — {queue.size()} waiting for the Harvester")
    for message in found.errors:
        print(f"  ! {message}", file=sys.stderr)
    return 0


def _clear_jobs(yes: bool) -> int:
    """SRCH-27 from the command line: the same operation as Settings' "Clear jobs"."""
    from .db.session import transaction
    from .errors import ApiError
    from .services.store import Store

    try:
        with transaction() as session:
            preview = Store(session).clear_preview()
    except ApiError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    what = f"{preview.jobs} job(s), {preview.applications} application(s) and {preview.documents} document(s)"
    if not yes:
        answer = input(f"This deletes {what}. Your CV, profile and settings stay. Type 'clear' to continue: ")
        if answer.strip() != "clear":
            print("Cancelled.")
            return 1
    with transaction() as session:
        cleared = Store(session).clear_jobs()
    print(f"Cleared {cleared.jobs} job(s), {cleared.applications} application(s) and {cleared.documents} document(s).")
    return 0


def _refilter(apply: bool) -> int:
    """MATCH-01 over the hidden jobs. Lists by default; `--apply` brings them back, unscored."""
    from .db.session import transaction
    from .services.store import Store

    with transaction() as session:
        brought = Store(session).refilter_hidden(apply=apply)
    for job in brought:
        print(f"  {job.title[:52]:52} {job.company[:28]:28} {job.location.raw[:40]}")
    verb = "brought back" if apply else "would come back"
    print(f'\n{len(brought)} hidden job(s) {verb}. They join the backlog that "Find more" scores first.')
    if not apply and brought:
        print("Run again with --apply to bring them back.")
    return 0


def _audit_listings(apply: bool) -> int:
    """SRCH-21 over the jobs already saved. Lists by default; `--apply` hides them."""
    from .db.session import transaction
    from .services.store import Store

    with transaction() as session:
        found = Store(session).audit_listings(apply=apply)
    for item in found:
        print(f"  {item['kind']:8} {item['action']:28} {item['title'][:48]:48} {item['url']}")
    hidden = sum(1 for item in found if item["action"] in ("hidden", "would hide"))
    flagged = sum(1 for item in found if item["action"].startswith("flagged"))
    print(f"\n{len(found)} saved job(s) aren't postings: {hidden} {'hidden' if apply else 'to hide'}, {flagged} flagged.")
    if not apply and hidden:
        print("Run again with --apply to hide them. Nothing is deleted; each can be unhidden from the Jobs screen.")
    return 0


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
    from .config import wera
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
    print(f"Backed coverage: {report.mean_backed_coverage:.0%}  (must reach the {wera().ats_threshold:.0%} ATS threshold)")
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
    PASS_RECALL = evaluate.PASS_RECALL
    agreement = "—" if report.agreement is None else f"{report.agreement:.1f}%"
    recall = "—" if report.keyword_recall is None else f"{report.keyword_recall * 100:.1f}%"
    print(f"Model:           {report.model}{' (demo responders, no network)' if report.demo_mode else ''}")
    print(f"CV:              version {report.cv_version} — {report.cv_source}")
    print(f"Ads:             {len(report.rows)} ({len(report.compared)} hand-scored, {len(report.unscored)} awaiting a score)")
    print(f"Agreement ±1.5:  {agreement} (SPEC §12.2 wants ≥80%)")
    print(f"Keyword recall:  {recall} (live runs must reach {PASS_RECALL:.0%})")
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


def _import_sqlite(path: Path, graph: Path | None, dry_run: bool) -> int:
    """REL-04: an SQLite install's data, into the configured Postgres. Read-only on the source."""
    from sqlalchemy.engine import make_url

    from .db.import_sqlite import ImportRefused, run
    from .settings import get_settings

    target = get_settings().db_url
    shown = make_url(target).render_as_string(hide_password=True)
    try:
        report = run(path, target, graph=graph, dry_run=dry_run)
    except ImportRefused as refused:
        print(str(refused), file=sys.stderr)
        return 1
    print(f"{'Would copy' if dry_run else 'Copied'} into {shown}:")
    for table, count in report.rows.items():
        print(f"  {table:16} {count:7} rows")
    print(f"  {'gate threads':16} {report.threads:7} ({report.checkpoints} checkpoints, {report.writes} pending writes)")
    if dry_run:
        print("\nThe target is empty and the source is current. Run again without --dry-run to copy.")
        return 0
    print(f"\nCompared {report.compared_jobs} sampled jobs, {report.compared_applications} applications and every thread.")
    if report.problems:
        print(f"{len(report.problems)} problem(s):", file=sys.stderr)
        for problem in report.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"Everything matches. {path} is untouched; keep it until you're happy, then delete it yourself.")
    return 0


def _backup() -> int:
    from .db.backup import BackupFailed, backup

    try:
        out = backup()
    except BackupFailed as failed:
        print(str(failed), file=sys.stderr)
        return 1
    print(f"Backed up to {out} ({out.stat().st_size:,} bytes).")
    return 0


def _age(text: str) -> timedelta:
    """`7d`, `12h` or `30m`, for `--since`."""
    units = {"d": "days", "h": "hours", "m": "minutes"}
    number, unit = text[:-1], text[-1:].lower()
    if unit not in units or not number.isdigit():
        raise ValueError(f"{text!r} isn't an age: use a number and d, h or m, like 7d.")
    return timedelta(**{units[unit]: int(number)})


def _utc(at: datetime | None) -> str:
    """Postgres hands timestamps back in the session's zone; these lines say UTC, so convert."""
    return f"{at.astimezone(UTC):%Y-%m-%d %H:%M}" if at else "never"


def _telemetry_archive(since: str | None) -> int:
    from .services import telemetry_archive

    try:
        report = telemetry_archive.archive(since=_age(since) if since else None)
    except (telemetry_archive.ArchiveRefused, ValueError) as refused:
        print(str(refused), file=sys.stderr)
        return 1
    queries = f"{report.queries} quer{'y' if report.queries == 1 else 'ies'}"
    print(f"Read Logfire from {_utc(report.since)} to {_utc(report.until)} UTC in {queries}.")
    print(f"{report.fetched:,} records read, {report.added:,} new.")
    if report.truncated:
        print(f"{len(report.truncated)} one-second window(s) held more than a page; see the warning in Logfire.", file=sys.stderr)
    return 0


def _telemetry_status() -> int:
    from .services import telemetry_archive

    if telemetry_archive.engine().dialect.name != "postgresql":
        print("The archive is kept in Postgres; DATABASE_URL isn't one.", file=sys.stderr)
        return 1
    st = telemetry_archive.status()
    if not st.records:
        print("Nothing archived yet. Run `wera telemetry archive`.")
    else:
        print(f"{st.records:,} records ({st.size}), from {_utc(st.first)} to {_utc(st.last)} UTC.")
        for service, count in st.by_service.items():
            print(f"  {service:24} {count:9,}")
    if st.last_run_at:
        print(f"Last archive {_utc(st.last_run_at)} UTC added {st.last_added:,}; complete up to {_utc(st.archived_until)} UTC.")
    reason = telemetry_archive.refusal()
    if reason:
        print(f"The archive can't run here: {reason}", file=sys.stderr)
    return 0


def _analytics_refresh() -> int:
    from .db.session import transaction
    from .services.analytics import refresh_fit_totals

    with transaction() as session:
        if session.get_bind().dialect.name != "postgresql":
            print("The analytics schema is in Postgres; DATABASE_URL isn't one.", file=sys.stderr)
            return 1
        count = refresh_fit_totals(session)
    print(f"analytics.fit_totals: {count} scored job(s).")
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
            f"This deletes everything in {label} database ({url.render_as_string(hide_password=True)}) apart from the "
            "telemetry archive. Type 'reset' to continue: "
        )
        if answer.strip() != "reset":
            print("Cancelled.")
            return 1
    if engine().dialect.name == "postgresql":
        from .migrate import empty_analytics

        # Derived from the tables about to go, and in the way of dropping them. `telemetry` stays:
        # it is the archive of what Logfire holds (OBS-01), not data this command manages.
        with engine().begin() as conn:
            empty_analytics(conn)
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
    elif kind == "postgres":
        from .db.session import engine

        # LangGraph's own tables, beside WERA's in the same database. `setup()` makes them again
        # on the next start, migrations table included, so dropping all four is a clean slate.
        with engine().begin() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")


if __name__ == "__main__":
    sys.exit(main())
