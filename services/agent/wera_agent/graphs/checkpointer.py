"""Where a paused graph lives (REL-03).

The checkpointer is what makes Gate 1 and Gate 2 real: a thread stops at `interrupt()`, the
process can exit, and the decision still arrives days later against the same state. SQLite by
default, Postgres when `DATABASE_URL` points at one, chosen the same way the app's own engine is.

It keeps its own connection rather than borrowing the SQLAlchemy session: the graph writes
checkpoints while request handlers write rows, and sharing a transaction would deadlock them.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.engine import make_url

from ..settings import get_settings


def checkpoint_target() -> tuple[str, str]:
    """`("sqlite", path)` or `("postgres", url)`, from the same DATABASE_URL the engine uses.

    On SQLite the checkpoints get their own file. One file would be tidier to back up, but the
    graph writes checkpoints from its own connection while request handlers write rows from
    theirs, and two writers on one SQLite file block each other for no reason.
    """
    url = make_url(get_settings().db_url)
    if url.get_backend_name() == "sqlite":
        database = url.database or ":memory:"
        return "sqlite", database if database == ":memory:" else f"{database}.graph"
    # Postgres handles concurrent writers, so the checkpoint tables can live in the same database.
    return "postgres", url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """An open checkpointer for the life of the block. `setup()` is idempotent."""
    kind, target = checkpoint_target()
    if kind == "sqlite":
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        # Built by hand rather than `from_conn_string`, to set the same busy timeout the app's
        # engine uses: a concurrent writer should wait, not fail.
        async with aiosqlite.connect(target, timeout=15) as connection:
            connection.row_factory = aiosqlite.Row
            saver = AsyncSqliteSaver(connection)
            await saver.setup()
            yield saver
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # needs the `postgres` extra

    async with AsyncPostgresSaver.from_conn_string(target) as saver:
        await saver.setup()
        yield saver
