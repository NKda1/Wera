"""Moving an SQLite install into Postgres (REL-04): every row, and every paused gate.

WERA ran on SQLite until 12 Nov 2026; since then Postgres is the only database outside the unit
tests. This is the one-way trip for the data an SQLite install already holds — `wera db
import-sqlite data/wera.db` — and it is written to be safe to point at a real install:

* **The source is opened read-only**, at the SQLite level, so it stays the fallback until someone
  deletes it. With no write-ahead log left to read it is opened `immutable`, which also stops
  SQLite making the `-shm` and `-wal` files a reader of a WAL-mode database otherwise creates.
* **The target must be empty.** Importing into a database that already has a CV or jobs would
  merge two people's worlds (or one person's twice), and there is no right way to do that.
* **The gate threads come too.** An application waiting at Gate 2 is a LangGraph thread in the
  `.graph` file beside the database, not a row. Copy only the rows and the application says
  "ready for review" over a thread that no longer exists — the draft can't be resumed. Each
  checkpoint is replayed into Postgres with its parent and its pending writes, so the thread is
  the same thread.
* **Nothing is reported as done until it has been compared.** Row counts per table, the JSON of
  sampled jobs and every application, and the latest checkpoint of every thread.

The row copy is one transaction, so a failure leaves the target empty and the command can simply
be run again. The checkpoint copy is idempotent (the saver upserts), so it runs first.
"""

import asyncio
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Integer, create_engine, func, insert, inspect, select, text
from sqlalchemy.engine import make_url

from .models import ApplicationRow, Base, JobRow
from .session import make_engine

BATCH = 500
# How many jobs have their whole JSON compared after the copy. Every application is compared.
SAMPLE = 50


@dataclass
class ImportReport:
    """What was found, copied and checked. `problems` empty means the copy is faithful."""

    rows: dict[str, int] = field(default_factory=dict)
    threads: int = 0
    checkpoints: int = 0
    writes: int = 0
    compared_jobs: int = 0
    compared_applications: int = 0
    problems: list[str] = field(default_factory=list)


class ImportRefused(Exception):
    """The import can't start: what is wrong, in words a person can act on."""


def run(source: Path, target_url: str, *, graph: Path | None = None, dry_run: bool = False) -> ImportReport:
    """Copy `source` (an SQLite file) and its gate threads into the Postgres at `target_url`."""
    from ..migrate import upgrade

    graph = graph or Path(f"{source}.graph")
    if not source.is_file():
        raise ImportRefused(f"There is no SQLite database at {source}.")
    if make_url(target_url).get_backend_name() != "postgresql":
        raise ImportRefused("The target must be a Postgres database (DATABASE_URL=postgresql://…).")

    src = _read_only(source)
    dst = make_engine(target_url)
    report = ImportReport()
    try:
        _check_source_is_current(src)
        report.rows = {t.name: _count(src, t) for t in Base.metadata.sorted_tables}
        _check_target_is_empty(dst)
        if graph.is_file():
            report.threads, report.checkpoints, report.writes = asyncio.run(_count_checkpoints(graph))
        if dry_run:
            return report

        upgrade(target_url)
        if graph.is_file():
            asyncio.run(_copy_checkpoints(graph, target_url))
        _copy_rows(src, dst)
        _verify_rows(src, dst, report)
        if graph.is_file():
            report.problems += asyncio.run(_verify_checkpoints(graph, target_url))
        return report
    finally:
        src.dispose()
        dst.dispose()


# ---------------------------------------------------------------------------
# Checks before anything is written


def _ro_uri(path: Path) -> str:
    """A `file:` URI SQLite itself holds to read-only, not a promise made in this module.

    `immutable` when there is no write-ahead log to read: SQLite then creates nothing beside the
    file (a reader of a WAL-mode database otherwise makes `-shm` and `-wal`). When there is one —
    the app stopped without checkpointing — `mode=ro`, which reads it, because `immutable` would
    silently skip the latest writes.
    """
    wal = Path(f"{path}-wal")
    mode = "mode=ro" if wal.is_file() and wal.stat().st_size > 0 else "immutable=1"
    return f"file:{path.resolve()}?{mode}"


def _read_only(path: Path) -> Engine:
    # No WAL pragma either, which `make_engine` would set: that is a write.
    return create_engine(f"sqlite:///{_ro_uri(path)}&uri=true")


def _check_source_is_current(src: Engine) -> None:
    """The SQLite file must be at the same migration as the code, or its rows won't fit."""
    from ..migrate import head as code_head

    head = code_head()
    with src.connect() as conn:
        found = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if found != head:
        raise ImportRefused(
            f"The SQLite database is at migration {found}, the code at {head}. "
            "Bring it up to date first: DATABASE_URL=sqlite:///<path> uv run wera migrate"
        )


def _check_target_is_empty(dst: Engine) -> None:
    present = set(inspect(dst).get_table_names())
    with dst.connect() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name in present and conn.execute(select(func.count()).select_from(table)).scalar():
                raise ImportRefused(
                    f"The target database already has rows in `{table.name}`. Import only into an "
                    "empty database — drop and re-create it, or use a new one."
                )


def _count(engine: Engine, table: Any) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar() or 0


# ---------------------------------------------------------------------------
# Rows


def _copy_rows(src: Engine, dst: Engine) -> None:
    """Every table in foreign-key order, in one transaction, read through the models' own types —
    so JSON comes back as dicts, booleans as booleans, and the target's types do the rest."""
    with src.connect() as reading, dst.begin() as writing:
        for table in Base.metadata.sorted_tables:
            result = reading.execute(select(table))
            while chunk := result.mappings().fetchmany(BATCH):
                writing.execute(insert(table), [dict(row) for row in chunk])
        # Ids were copied as they were, so each serial column's sequence is still at 1. Without
        # this the next `llm_calls` row would collide with the first one imported.
        for table in Base.metadata.sorted_tables:
            for column in table.primary_key.columns:
                if isinstance(column.type, Integer):
                    writing.execute(
                        text(
                            f"SELECT setval(seq, coalesce((SELECT max({column.name}) FROM {table.name}), 1), "
                            f"(SELECT max({column.name}) FROM {table.name}) IS NOT NULL) "
                            "FROM pg_get_serial_sequence(:t, :c) AS seq WHERE seq IS NOT NULL"
                        ),
                        {"t": table.name, "c": column.name},
                    )


def _verify_rows(src: Engine, dst: Engine, report: ImportReport) -> None:
    for table in Base.metadata.sorted_tables:
        copied = _count(dst, table)
        if copied != report.rows[table.name]:
            report.problems.append(f"{table.name}: {report.rows[table.name]} rows in SQLite, {copied} in Postgres")

    with src.connect() as a, dst.connect() as b:
        ids = list(a.execute(select(JobRow.id)).scalars())
        sample = random.Random(0).sample(ids, min(SAMPLE, len(ids)))
        for job_id in sample:
            if (
                a.execute(select(JobRow.data).where(JobRow.id == job_id)).scalar()
                != b.execute(select(JobRow.data).where(JobRow.id == job_id)).scalar()
            ):
                report.problems.append(f"job {job_id}: its JSON differs after the copy")
        report.compared_jobs = len(sample)

        apps = a.execute(select(ApplicationRow)).mappings().all()
        for app in apps:
            copied = b.execute(select(ApplicationRow).where(ApplicationRow.id == app["id"])).mappings().first()
            if copied is None or dict(copied) != dict(app):
                report.problems.append(f"application {app['id']}: differs after the copy")
        report.compared_applications = len(apps)


# ---------------------------------------------------------------------------
# Gate threads


def _pg_conninfo(url: str) -> str:
    """psycopg wants `postgresql://`, not SQLAlchemy's `postgresql+psycopg://`."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _source_tuples(graph: Path) -> list[Any]:
    """Every checkpoint in the file, oldest first, each with its pending writes."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    # Read-only at the SQLite level, as for the rows. `setup()` is not called: it would write.
    async with aiosqlite.connect(_ro_uri(graph), uri=True) as connection:
        saver = AsyncSqliteSaver(connection)
        saver.is_setup = True
        found = [t async for t in saver.alist(None)]
    # `alist` is newest first; parents before children reads better and costs nothing.
    return sorted(found, key=lambda t: t.checkpoint["id"])


async def _count_checkpoints(graph: Path) -> tuple[int, int, int]:
    tuples = await _source_tuples(graph)
    threads = {(t.config["configurable"]["thread_id"], t.config["configurable"]["checkpoint_ns"]) for t in tuples}
    return len(threads), len(tuples), sum(len(t.pending_writes or []) for t in tuples)


async def _copy_checkpoints(graph: Path, target_url: str) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    tuples = await _source_tuples(graph)
    async with AsyncPostgresSaver.from_conn_string(_pg_conninfo(target_url)) as saver:
        await saver.setup()
        for tup in tuples:
            where = tup.config["configurable"]
            # The parent goes in as the config's checkpoint id: that is how the saver records it.
            parent = tup.parent_config or {
                "configurable": {"thread_id": where["thread_id"], "checkpoint_ns": where["checkpoint_ns"]}
            }
            # Every channel version, so every value this checkpoint holds gets its blob. A blob
            # another checkpoint already stored at the same version is the same value, and the
            # saver keeps the first.
            await saver.aput(parent, tup.checkpoint, tup.metadata, dict(tup.checkpoint["channel_versions"]))
            by_task: dict[str, list[tuple[str, Any]]] = defaultdict(list)
            for task_id, channel, value in tup.pending_writes or []:
                by_task[task_id].append((channel, value))
            for task_id, writes in by_task.items():
                await saver.aput_writes(tup.config, writes, task_id)


async def _verify_checkpoints(graph: Path, target_url: str) -> list[str]:
    """Each thread's latest checkpoint, read back from both, must be the same checkpoint."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    problems: list[str] = []
    tuples = await _source_tuples(graph)
    latest: dict[tuple[str, str], Any] = {}
    for tup in tuples:
        where = tup.config["configurable"]
        latest[(where["thread_id"], where["checkpoint_ns"])] = tup
    async with AsyncPostgresSaver.from_conn_string(_pg_conninfo(target_url)) as saver:
        for (thread_id, ns), want in latest.items():
            got = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}})
            if got is None:
                problems.append(f"thread {thread_id}: missing in Postgres")
            elif got.checkpoint["id"] != want.checkpoint["id"]:
                problems.append(f"thread {thread_id}: latest checkpoint {got.checkpoint['id']}, wanted {want.checkpoint['id']}")
            elif got.checkpoint["channel_values"] != want.checkpoint["channel_values"]:
                problems.append(f"thread {thread_id}: its state differs after the copy")
            elif sorted(map(repr, got.pending_writes or [])) != sorted(map(repr, want.pending_writes or [])):
                problems.append(f"thread {thread_id}: its pending writes differ after the copy")
    return problems
