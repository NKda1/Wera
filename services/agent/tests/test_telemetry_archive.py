"""OBS-01: Logfire's records, copied into Postgres and kept after Logfire lets them go.

Logfire is replaced by a fake that answers `query_json_rows` from a list, so these run with no
network and no token (DEMO-02). What the archive writes needs Postgres — the `telemetry` schema
exists only there — so those tests run in the Postgres lane (`WERA_TEST_DATABASE_URL`); the
parsing, the windows and the refusals run on both.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from wera_agent.api import scheduler
from wera_agent.cli import _age
from wera_agent.db.session import engine, reset_caches
from wera_agent.migrate import upgrade
from wera_agent.services import telemetry_archive as ta
from wera_agent.settings import get_settings

needs_postgres = pytest.mark.skipif(
    not os.environ.get("WERA_TEST_DATABASE_URL"), reason="needs WERA_TEST_DATABASE_URL (the Postgres lane)"
)

NOW = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)


def span(at: datetime, n: int, *, service: str = "wera-agent", **extra) -> dict:
    """One record as Logfire's query API returns it: timestamps and attributes as text."""
    return {
        "trace_id": f"{n:032x}",
        "span_id": f"{n:016x}",
        "parent_span_id": None,
        "start_timestamp": at.isoformat().replace("+00:00", "Z"),
        "end_timestamp": (at + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        "duration": 2.0,
        "kind": "span",
        "level": 9,
        "span_name": "llm {task}",
        "message": "llm fit.score",
        "service_name": service,
        "deployment_environment": "real",
        "attributes": '{"task": "fit.score", "llm.ok": true}',
        "tags": [],
        **extra,
    }


class FakeLogfire:
    """`query_json_rows` over a list, inclusive at both ends — the archive must not rely on the
    API's own boundaries to keep windows from overlapping or leaving gaps."""

    def __init__(self, records: list[dict], *, fail_on: int | None = None):
        self.records = records
        self.calls: list[tuple[datetime, datetime]] = []
        self.fail_on = fail_on

    def query_json_rows(self, sql, *, min_timestamp, max_timestamp=None, limit=None):
        self.calls.append((min_timestamp, max_timestamp))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError("Logfire unavailable")
        rows = [r for r in self.records if min_timestamp <= ta._when(r["start_timestamp"]) <= max_timestamp]
        return {"columns": [], "rows": rows[:limit]}


@pytest.fixture
def real(monkeypatch):
    """Not demo mode, with a read token: the only conditions the archive runs under."""
    monkeypatch.setenv("WERA_DEMO_MODE", "false")
    monkeypatch.setenv("LOGFIRE_READ_TOKEN", "pylf_v1_eu_test-read-token")
    get_settings.cache_clear()
    reset_caches()
    upgrade()
    yield
    get_settings.cache_clear()
    reset_caches()


def archived() -> list[dict]:
    with engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(ta.RECORDS).order_by(ta.RECORDS.c.start_timestamp)).mappings()]


def watermark() -> datetime | None:
    with engine().connect() as conn:
        return conn.scalar(select(ta.STATE.c.archived_until))


class TestWhatIsRead:
    def test_the_query_leaves_out_spans_that_havent_ended(self):
        assert "kind != 'pending_span'" in ta.QUERY
        assert "attributes" in ta.QUERY and "trace_id" in ta.QUERY

    def test_a_record_arrives_typed(self):
        record = ta._record(span(NOW, 1, level="warn", message="bad\x00byte"))
        assert record["start_timestamp"] == NOW
        assert record["attributes"] == {"task": "fit.score", "llm.ok": True}
        assert record["level"] == 13  # a name becomes OpenTelemetry's severity number
        assert record["message"] == "badbyte"  # Postgres refuses NUL, as for jobs (REL-04)

    def test_a_record_with_no_identity_is_dropped(self):
        assert ta._record({**span(NOW, 1), "span_id": None}) is None

    def test_attributes_that_arent_json_are_kept_not_lost(self):
        assert ta._record(span(NOW, 1, attributes="{not json"))["attributes"] == {"_unparsed": "{not json"}

    def test_windows_cover_the_range_once_and_end_at_its_end(self):
        start = NOW - timedelta(days=2, hours=5)
        windows = list(ta._windows(start, NOW))
        assert windows[0][0] == start and windows[-1][1] == NOW
        assert all(a[1] == b[0] for a, b in zip(windows, windows[1:], strict=False))
        assert all(hi - lo <= ta.WINDOW for lo, hi in windows)

    def test_since_reads_days_hours_and_minutes(self):
        assert _age("7d") == timedelta(days=7) and _age("12h") == timedelta(hours=12) and _age("30m") == timedelta(minutes=30)
        with pytest.raises(ValueError, match="isn't an age"):
            _age("a week")


class TestWhenItRefuses:
    def test_demo_mode(self, monkeypatch):
        monkeypatch.setenv("LOGFIRE_READ_TOKEN", "pylf_v1_eu_x")
        get_settings.cache_clear()
        assert "Demo mode" in ta.refusal()
        with pytest.raises(ta.ArchiveRefused):
            ta.archive(FakeLogfire([]))

    def test_no_read_token(self, monkeypatch):
        monkeypatch.setenv("WERA_DEMO_MODE", "false")
        get_settings.cache_clear()
        assert "LOGFIRE_READ_TOKEN" in ta.refusal()
        assert ta.due() is False  # and the startup catch-up doesn't try

    @pytest.mark.skipif(bool(os.environ.get("WERA_TEST_DATABASE_URL")), reason="the SQLite lane's refusal")
    def test_a_database_that_isnt_postgres(self, monkeypatch):
        monkeypatch.setenv("WERA_DEMO_MODE", "false")
        monkeypatch.setenv("LOGFIRE_READ_TOKEN", "pylf_v1_eu_x")
        get_settings.cache_clear()
        assert "Postgres" in ta.refusal()

    def test_the_scheduled_run_says_why_and_doesnt_raise(self, monkeypatch):
        monkeypatch.setenv("WERA_DEMO_MODE", "false")
        get_settings.cache_clear()
        asyncio.run(scheduler._archive())


@needs_postgres
class TestTheArchive:
    def test_a_backfill_copies_everything_logfire_holds_and_a_second_run_adds_nothing(self, real):
        older = [span(NOW - timedelta(days=d, minutes=m), d * 100 + m) for d in range(0, 29) for m in (30, 45)]
        fresh = span(NOW - timedelta(minutes=5), 9999)  # may still have spans arriving under it
        report = ta.archive(FakeLogfire([*older, fresh]), now=NOW)
        assert report.added == len(archived()) == len(older)
        assert watermark() == NOW - ta.SETTLE

        later = ta.archive(FakeLogfire([*older, fresh]), now=NOW + timedelta(minutes=20))
        assert later.added == 1  # the fresh one, now settled — and nothing twice
        assert ta.archive(FakeLogfire([*older, fresh]), now=NOW + timedelta(minutes=40)).added == 0

    def test_a_record_on_a_window_boundary_is_archived_once(self, real):
        """The fake answers inclusively at both ends, so a record exactly on a boundary comes
        back in two windows; the archive keeps it in one."""
        start = NOW - ta.KEPT_BY_LOGFIRE
        boundary = start + ta.WINDOW
        report = ta.archive(FakeLogfire([span(boundary, 7)]), now=NOW)
        assert report.added == 1 and len(archived()) == 1

    def test_a_full_window_is_split_until_it_isnt(self, real, monkeypatch):
        monkeypatch.setattr(ta, "PAGE", 3)
        at = NOW - timedelta(hours=3)
        records = [span(at + timedelta(minutes=i), i + 1) for i in range(10)]
        fake = FakeLogfire(records)
        report = ta.archive(fake, since=timedelta(hours=4), now=NOW)
        assert report.added == 10 and not report.truncated
        assert len(fake.calls) > 1  # it asked again, smaller

    def test_an_interrupted_backfill_resumes_where_it_stopped(self, real):
        records = [span(NOW - timedelta(days=d, hours=1), d + 1) for d in range(1, 6)]  # one a day
        with pytest.raises(RuntimeError, match="unavailable"):
            ta.archive(FakeLogfire(records, fail_on=3), since=timedelta(days=6), now=NOW)  # two days in
        stopped_at, kept = watermark(), len(archived())
        assert stopped_at is not None and 0 < kept < len(records)

        ta.archive(FakeLogfire(records), now=NOW)
        assert len(archived()) == len(records)

    def test_a_long_span_that_ended_after_the_last_run_still_arrives(self, real):
        """Logfire files a span under when it started, and writes it when it ends."""
        ta.archive(FakeLogfire([]), now=NOW)
        started = NOW - ta.SETTLE - timedelta(minutes=20)  # behind the watermark the run just set
        report = ta.archive(FakeLogfire([span(started, 42)]), now=NOW + timedelta(minutes=30))
        assert report.added == 1

    def test_reading_further_back_never_moves_the_watermark_back(self, real):
        ta.archive(FakeLogfire([]), now=NOW)
        ta.archive(FakeLogfire([]), since=timedelta(days=7), now=NOW - timedelta(hours=2))
        assert watermark() == NOW - ta.SETTLE

    def test_status_and_when_it_is_due(self, real):
        assert ta.due(NOW) is True  # never run
        ta.archive(
            FakeLogfire([span(NOW - timedelta(hours=2), 1), span(NOW - timedelta(hours=1), 2, service="wera-web")]), now=NOW
        )
        st = ta.status()
        assert st.records == 2 and st.by_service == {"wera-agent": 1, "wera-web": 1}
        assert st.last_added == 2 and st.archived_until == NOW - ta.SETTLE
        assert ta.due(NOW + timedelta(hours=12)) is False
        assert ta.due(NOW + timedelta(days=2)) is True

    def test_a_reset_keeps_the_archive_and_rebuilds_the_analytics_views(self, real):
        """`wera reset` deletes the app's data; the archive is Logfire's, and outlives it."""
        from wera_agent.cli import main

        ta.archive(FakeLogfire([span(NOW - timedelta(hours=1), 1)]), now=NOW)
        for _ in range(2):  # and a second time, over a database the first reset rebuilt
            assert main(["reset", "--yes"]) == 0
            reset_caches()
            assert len(archived()) == 1
            with engine().connect() as conn:
                # A real reset seeds no sample jobs; what matters is that the view is there again.
                assert conn.exec_driver_sql("select count(*) from analytics.jobs").scalar() == 0

    def test_the_startup_catch_up_is_scheduled_only_when_due(self, real, monkeypatch):
        async def go(due: bool) -> bool:
            monkeypatch.setattr(ta, "due", lambda: due)
            started = scheduler.start()
            try:
                return started.get_job(scheduler.CATCH_UP_JOB) is not None
            finally:
                scheduler.stop()

        assert asyncio.run(go(True)) is True
        assert asyncio.run(go(False)) is False
