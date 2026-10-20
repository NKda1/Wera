"""`wera` command: serve the agent, reset the database, export schemas."""

import argparse
import sys


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
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        return _serve(args.reload)
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
    return 2


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
