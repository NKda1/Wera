"""OBS-02 and OBS-03: what a person can ask of WERA's data with plain SQL.

The `analytics` views exist only on Postgres (migration 0011), so most of this runs in the
Postgres lane. The one table, `fit_totals`, must agree with the screen to the decimal, because
the whole point of computing it in code (MATCH-04) is that there is only one answer.
"""

import os

import pytest
from sqlalchemy import func, select, text

from wera_agent.cli import main
from wera_agent.db.models import DecisionRow, FitReportRow, IntakeRunRow, JobRow, LlmCallRow
from wera_agent.llm.gateway import write_pending
from wera_agent.services.analytics import FIT_TOTALS, refresh_fit_totals
from wera_agent.services.store import save_job

from .test_new_jobs import job

ON_POSTGRES = bool(os.environ.get("WERA_TEST_DATABASE_URL"))
needs_postgres = pytest.mark.skipif(not ON_POSTGRES, reason="needs WERA_TEST_DATABASE_URL (the Postgres lane)")


def rows(store, sql: str) -> list[dict]:
    return [dict(r) for r in store.s.execute(text(sql)).mappings()]


class TestTheTraceOnAModelCall:
    def test_a_call_made_inside_a_span_records_that_spans_trace(self, store):
        """OBS-02: a row in `llm_calls` can be followed to the run around it."""
        import logfire
        from opentelemetry import trace

        from .test_observability import call_a_model

        logfire.configure(send_to_logfire=False, console=False, inspect_arguments=False)
        with logfire.span("a run"):
            expected = format(trace.get_current_span().get_span_context().trace_id, "032x")
            call_a_model(store)
        write_pending(store.s)
        row = store.s.scalars(select(LlmCallRow).order_by(LlmCallRow.id.desc())).first()
        assert row.trace_id == expected and len(expected) == 32


@pytest.mark.skipif(ON_POSTGRES, reason="the SQLite lane's half")
class TestOnSqlite:
    def test_there_is_nothing_to_refresh_and_the_command_says_why(self, store, capsys):
        assert refresh_fit_totals(store.s) == 0
        assert main(["analytics", "refresh"]) == 1
        assert "Postgres" in capsys.readouterr().err


@needs_postgres
class TestTheViews:
    def test_fit_totals_match_the_jobs_screen(self, store):
        count = refresh_fit_totals(store.s)
        scored = store.s.scalar(select(func.count()).select_from(FitReportRow)) or 0
        assert count == scored > 0
        weights = store.weights()
        for row in store.s.execute(select(FIT_TOTALS)).mappings():
            job_row = store.s.get(JobRow, row["job_id"])
            assert row["total"] == store.scored(store._job(job_row), weights).fit.total

    def test_a_refresh_replaces_rather_than_adds(self, store):
        first = refresh_fit_totals(store.s)
        assert refresh_fit_totals(store.s) == first
        assert len(rows(store, "select job_id from analytics.fit_totals")) == first

    def test_jobs_are_flattened_with_their_host_and_reason(self, store):
        save_job(store.s, job(url="https://www.boards.greenhouse.io/acme/jobs/1"))
        store.s.flush()
        (row,) = rows(store, "select host, channel, city, scored, description_chars from analytics.jobs where id = 'job-new'")
        assert row == {
            "host": "boards.greenhouse.io",
            "channel": "board_search",
            "city": "London",
            "scored": False,
            "description_chars": len("Build things. " * 30),
        }

    def test_the_funnel_follows_a_run_to_the_shortlist(self, store):
        from wera_agent.clock import iso, now_ms

        at = iso(now_ms())
        store.s.add(
            IntakeRunRow(
                id="run-9", channel="board_search", status="succeeded", started_at=at, finished_at=at,
                queries=12, results=40, queued=6, local_pages=5, fallback_pages=1, new_jobs=1, errors=[],
            )
        )  # fmt: skip
        store.upsert_jobs([job()], run_ids={"board_search": "run-9"})
        store.s.add(DecisionRow(job_id="job-new", gate=1, decision="approve", decided_at=at))
        store.s.flush()
        (row,) = rows(store, "select * from analytics.funnel where run_id = 'run-9'")
        assert (row["queries"], row["results"], row["queued"], row["read"], row["accepted"]) == (12, 40, 6, 6, 1)
        assert (row["scored"], row["shortlisted"], row["approved"], row["submitted"]) == (0, 1, 0, 0)

    def test_spend_is_totalled_by_day(self, store):
        from wera_agent.clock import iso, now_ms

        store.s.add(LlmCallRow(task="fit.score", tier="scoring", model="qwen/test", cost_usd=0.25, called_at=iso(now_ms())))
        store.s.flush()
        by_day = sum(r["model_usd"] for r in rows(store, "select model_usd from analytics.spend_daily"))
        assert by_day == pytest.approx(store.s.scalar(select(func.sum(LlmCallRow.cost_usd))))
        assert by_day >= 0.25

    def test_a_malformed_timestamp_reads_as_null_instead_of_failing_the_view(self, store):
        save_job(store.s, job())
        store.s.flush()
        store.s.execute(text("update jobs set posted_at = 'last tuesday' where id = 'job-new'"))
        (row,) = rows(store, "select posted_at from analytics.jobs where id = 'job-new'")
        assert row["posted_at"] is None
