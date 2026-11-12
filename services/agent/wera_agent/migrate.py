"""Run Alembic migrations from code (startup, `wera migrate`, tests)."""

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection

from .paths import AGENT_DIR
from .settings import get_settings


def _config(url: str | None = None) -> Config:
    cfg = Config(str(AGENT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(AGENT_DIR / "migrations"))
    cfg.attributes["url"] = url or get_settings().db_url
    return cfg


# Every schema the migrations create (0011 adds the last two). A Postgres database wiped for a
# fresh start — a test's, the eval's scratch — must lose all of them, or the next upgrade finds
# 0011's tables still there and fails.
SCHEMAS = ("public", "telemetry", "analytics")


def wipe_postgres(conn: Connection) -> None:
    """Empty a Postgres database back to a bare `public`, as a new SQLite file would be."""
    conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {', '.join(SCHEMAS)} CASCADE")
    conn.exec_driver_sql("CREATE SCHEMA public")


def empty_analytics(conn: Connection) -> None:
    """Drop everything in `analytics`, keeping the schema — and with it `wera_readonly`'s grants.

    For `wera reset`: the views there depend on the app's tables, so those can't be dropped while
    the views stand, and everything in it is derived from the data being deleted. Migration 0011
    makes it all again.
    """
    rows = conn.exec_driver_sql(
        "select table_name, table_type from information_schema.tables where table_schema = 'analytics'"
    ).all()
    for name, kind in rows:
        conn.exec_driver_sql(f'drop {"view" if kind == "VIEW" else "table"} if exists analytics."{name}" cascade')
    conn.exec_driver_sql("drop function if exists analytics.ts(text)")


def upgrade(url: str | None = None) -> None:
    command.upgrade(_config(url), "head")


def head() -> str | None:
    """The newest migration the code knows, as Alembic names it."""
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(_config()).get_current_head()
