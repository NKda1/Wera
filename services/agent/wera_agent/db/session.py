"""Engine and sessions. SQLite by default; Postgres when DATABASE_URL points at it."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from ..settings import get_settings


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            from pathlib import Path

            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

        return engine
    if parsed.drivername == "postgresql":
        url = parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def engine() -> Engine:
    return make_engine(get_settings().db_url)


@lru_cache
def _factory() -> sessionmaker[Session]:
    return sessionmaker(engine(), expire_on_commit=False)


@contextmanager
def transaction() -> Iterator[Session]:
    """One unit of work: commits on success, rolls back on any error."""
    with _factory().begin() as session:
        yield session


def reset_caches() -> None:
    """Tests and `wera reset`: forget the engine so a new DATABASE_URL takes effect."""
    if engine.cache_info().currsize:
        engine().dispose()
    engine.cache_clear()
    _factory.cache_clear()
