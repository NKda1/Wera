"""REL-04: Postgres is WERA's database, and an SQLite install moves into it whole.

The import tests that need a Postgres target run in the Postgres lane (`WERA_TEST_DATABASE_URL`,
as CI's `agent-postgres` job sets) and are skipped on the default SQLite run; everything that can
be checked without one runs on both.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from wera_agent import evaluate
from wera_agent.clock import now_ms
from wera_agent.db import import_sqlite
from wera_agent.db.import_sqlite import ImportRefused
from wera_agent.db.models import JobRow, _without_nul
from wera_agent.db.session import make_engine, transaction
from wera_agent.migrate import upgrade
from wera_agent.services.seed import seed
from wera_agent.services.store import save_job
from wera_agent.settings import Settings

from .test_new_jobs import job

POSTGRES_URL = os.environ.get("WERA_TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="needs WERA_TEST_DATABASE_URL (the Postgres lane)")


class TestWhichDatabase:
    def test_real_runs_and_demos_default_to_their_own_postgres_databases(self):
        assert Settings(wera_demo_mode=False, database_url=None).db_url == "postgresql://localhost/wera"
        assert Settings(wera_demo_mode=True, database_url=None).db_url == "postgresql://localhost/wera_demo"

    def test_a_demo_never_writes_into_the_real_database_url(self):
        """DATABASE_URL in .env.local is the real data; demo mode has DEMO_DATABASE_URL."""
        real, demo = "postgresql://wera_app@localhost/wera", "postgresql://wera_app@localhost/wera_demo"
        assert Settings(wera_demo_mode=True, database_url=real, demo_database_url=demo).db_url == demo
        assert Settings(wera_demo_mode=False, database_url=real, demo_database_url=demo).db_url == real

    def test_the_eval_scratch_database_is_a_sibling_never_the_configured_one(self, tmp_path, monkeypatch):
        made: list[str] = []
        monkeypatch.setattr(evaluate, "_create_and_wipe", lambda server, name: made.append(name))
        scratch = evaluate._scratch_url("postgresql://wera_app:pw@localhost:5432/wera", tmp_path)
        assert scratch == "postgresql://wera_app:pw@localhost:5432/wera_eval" and made == ["wera_eval"]
        assert evaluate._scratch_url("sqlite:///x/wera-test.db", tmp_path) == f"sqlite:///{tmp_path / 'eval.db'}"


class TestNulCharacters:
    """Postgres refuses a NUL in text and jsonb; a scraped page can carry one."""

    def test_every_string_in_a_value_loses_its_nuls(self):
        assert _without_nul({"a\x00": ["x\x00y", {"b": "c\x00"}], "n": 3, "z": None}) == {
            "a": ["xy", {"b": "c"}],
            "n": 3,
            "z": None,
        }

    def test_a_job_with_one_saves_and_reads_back_without_it(self):
        upgrade()
        with transaction() as session:
            save_job(session, job(title="AI\x00 Engineer", description_md="Build things.\x00 " * 30))
        with transaction() as session:
            row = session.scalar(select(JobRow).where(JobRow.id == "job-new"))
            assert row.title == "AI Engineer"
            assert "\x00" not in row.data["descriptionMd"]


class TestTheImportRefuses:
    def test_a_missing_source(self, tmp_path):
        with pytest.raises(ImportRefused, match="no SQLite database"):
            import_sqlite.run(tmp_path / "nope.db", "postgresql://localhost/wera")

    def test_a_target_that_isnt_postgres(self, tmp_path):
        source = _source(tmp_path)
        with pytest.raises(ImportRefused, match="must be a Postgres"):
            import_sqlite.run(source, f"sqlite:///{tmp_path / 'other.db'}")

    def test_a_source_at_an_older_migration(self, tmp_path):
        source = _source(tmp_path)
        engine = make_engine(f"sqlite:///{source}")
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = '0009'"))
        engine.dispose()
        # Refused before the target is ever connected to.
        with pytest.raises(ImportRefused, match="migration 0009"):
            import_sqlite.run(source, "postgresql://localhost/never_connected")


@needs_postgres
class TestTheImport:
    def test_rows_and_a_paused_gate_arrive_and_the_gate_resumes(self, tmp_path):
        source = _source(tmp_path)
        asyncio.run(_pause_a_thread(Path(f"{source}.graph")))
        before = _digest(source), _digest(Path(f"{source}.graph"))

        dry = import_sqlite.run(source, POSTGRES_URL, dry_run=True)
        jobs = dry.rows["jobs"]  # the seed's sample jobs and ours
        assert jobs > 1 and dry.threads == 1 and not dry.problems
        assert "jobs" not in _target_tables()  # a dry run writes nothing

        report = import_sqlite.run(source, POSTGRES_URL)
        assert report.problems == []
        assert report.rows == dry.rows
        assert report.compared_jobs == min(import_sqlite.SAMPLE, jobs)
        with transaction() as session:  # the conftest points DATABASE_URL at the same database
            assert session.scalar(select(JobRow.title).where(JobRow.id == "job-new")) == "AI Engineer"

        # The thread is the same thread: still at its gate, and it resumes on Postgres.
        assert asyncio.run(_resume_on_postgres()) == {"n": 2, "answer": "approved"}
        # Read-only at the SQLite level: nothing changed, and nothing was made beside the files.
        assert (_digest(source), _digest(Path(f"{source}.graph"))) == before
        assert not Path(f"{source}.graph-shm").exists()

    def test_a_second_import_into_the_same_database_is_refused(self, tmp_path):
        source = _source(tmp_path)
        assert import_sqlite.run(source, POSTGRES_URL).problems == []
        with pytest.raises(ImportRefused, match="already has rows"):
            import_sqlite.run(source, POSTGRES_URL)


# ---------------------------------------------------------------------------


def _source(tmp_path: Path) -> Path:
    """An SQLite install at the current migration: seeded, with one job."""
    path = tmp_path / "old.db"
    url = f"sqlite:///{path}"
    upgrade(url)
    engine = make_engine(url)
    with Session(engine) as session, session.begin():
        seed(session, now_ms())
        save_job(session, job())
    engine.dispose()
    return path


class _State(TypedDict):
    n: int
    answer: str


def _graph():  # noqa: ANN202 — LangGraph's builder type
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    g = StateGraph(_State)
    g.add_node("first", lambda s: {"n": s["n"] + 1})
    g.add_node("gate", lambda s: {"answer": interrupt({"gate": 2})})
    g.add_edge(START, "first")
    g.add_edge("first", "gate")
    g.add_edge("gate", END)
    return g


CONFIG = {"configurable": {"thread_id": "job:job-new"}}


async def _pause_a_thread(graph_file: Path) -> None:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(str(graph_file)) as saver:
        await _graph().compile(checkpointer=saver).ainvoke({"n": 1, "answer": ""}, CONFIG)


async def _resume_on_postgres() -> dict:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.types import Command

    async with AsyncPostgresSaver.from_conn_string(import_sqlite._pg_conninfo(POSTGRES_URL or "")) as saver:
        graph = _graph().compile(checkpointer=saver)
        snapshot = await graph.aget_state(CONFIG)
        assert [i.value for i in snapshot.interrupts] == [{"gate": 2}]
        return await graph.ainvoke(Command(resume="approved"), CONFIG)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _target_tables() -> set[str]:
    from sqlalchemy import inspect

    engine = make_engine(POSTGRES_URL or "")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
