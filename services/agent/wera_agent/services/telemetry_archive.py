"""The Logfire archive (OBS-01): every span and log WERA sends to Logfire, kept in Postgres.

Logfire keeps thirty days. Anything worth asking of a year of runs — which stage got slower, how
often a host refused, what a model's failure rate was before a swap — needs the records still to
exist, so they are copied into `telemetry.records` in WERA's own database, where `pg_dump` backs
them up with everything else (REL-05) and the analytics role can read them (OBS-03).

How it reads, and why each rule is there:

* **Through Logfire's query API**, with a project read token (`LOGFIRE_READ_TOKEN`). The write
  token in `.logfire/` can't read, and an organisation API key isn't accepted by the query API.
* **From a watermark**, `telemetry.archive_state.archived_until`, in windows of up to a day. A
  window that comes back holding `PAGE` rows may have been cut off, so it is split in half and
  each half asked again, down to a second. The watermark moves after each window commits, so an
  archive that is interrupted resumes where it stopped.
* **An hour behind the watermark** each run. Logfire writes a span when it *ends*, under the time
  it *started*: a ten-minute search still running at the last archive has a start time the
  watermark has already passed. Re-reading the hour costs a query; the primary key makes the
  overlap free.
* **Up to ten minutes ago**, not now, for spans still arriving.
* **`pending_span` rows are skipped.** They are Logfire's stand-in for a span that hasn't ended,
  and the real span replaces them.

Writes are `on conflict do nothing` on `(trace_id, span_id)`, so the archive can run any number
of times and each record lands once. Never in demo mode: the archive belongs in the real
database, and a demo has its own.
"""

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import logfire
from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    DateTime,
    Float,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db.models import _without_nul
from ..db.session import engine
from ..settings import get_settings

PAGE = 10_000  # rows per query; a window that returns this many is split
WINDOW = timedelta(days=1)
SMALLEST = timedelta(seconds=1)
OVERLAP = timedelta(hours=1)
SETTLE = timedelta(minutes=10)
KEPT_BY_LOGFIRE = timedelta(days=30)
WRITE_BATCH = 1_000
RATE_LIMIT_RETRIES = 3

# What is copied, in Logfire's column names. `log_body`, `otel_events` and `otel_links` are left
# out: WERA sends none, and `attributes` already carries everything a span says about itself.
COLUMNS = (
    "trace_id",
    "span_id",
    "parent_span_id",
    "start_timestamp",
    "end_timestamp",
    "duration",
    "kind",
    "level",
    "span_name",
    "message",
    "service_name",
    "service_version",
    "deployment_environment",
    "otel_scope_name",
    "otel_status_code",
    "otel_status_message",
    "is_exception",
    "exception_type",
    "exception_message",
    "exception_stacktrace",
    "attributes",
    "tags",
)
QUERY = f"select {', '.join(COLUMNS)} from records where kind != 'pending_span'"  # noqa: S608 — constants only

# Migration 0011 creates these; they are declared again here only so inserts are typed.
_meta = MetaData(schema="telemetry")
RECORDS = Table(
    "records",
    _meta,
    Column("trace_id", String(32), primary_key=True),
    Column("span_id", String(16), primary_key=True),
    Column("parent_span_id", String(16)),
    Column("start_timestamp", DateTime(timezone=True)),
    Column("end_timestamp", DateTime(timezone=True)),
    Column("duration", Float),
    Column("kind", String(16)),
    Column("level", SmallInteger),
    Column("span_name", Text),
    Column("message", Text),
    Column("service_name", String(128)),
    Column("service_version", String(64)),
    Column("deployment_environment", String(64)),
    Column("otel_scope_name", Text),
    Column("otel_status_code", String(16)),
    Column("otel_status_message", Text),
    Column("is_exception", Boolean),
    Column("exception_type", Text),
    Column("exception_message", Text),
    Column("exception_stacktrace", Text),
    Column("attributes", JSONB),
    Column("tags", JSONB),
)
STATE = Table(
    "archive_state",
    _meta,
    Column("id", SmallInteger, primary_key=True),
    Column("archived_until", DateTime(timezone=True)),
    Column("last_run_at", DateTime(timezone=True)),
    Column("last_added", Integer),
)

# OpenTelemetry's severity numbers, for a level that arrives as a name rather than a number.
LEVELS = {"trace": 1, "debug": 5, "info": 9, "notice": 10, "warn": 13, "warning": 13, "error": 17, "fatal": 21}


class Query(Protocol):
    """The one method of `logfire.query_client.LogfireQueryClient` the archive uses."""

    def query_json_rows(
        self, sql: str, *, min_timestamp: datetime, max_timestamp: datetime | None = None, limit: int | None = None
    ) -> Any: ...


class ArchiveRefused(Exception):
    """Why the archive can't run here: demo mode, no read token, or not Postgres."""


@dataclass
class ArchiveReport:
    since: datetime
    until: datetime
    queries: int = 0
    fetched: int = 0
    added: int = 0
    # Windows still full at a second wide: rows past `PAGE` in them were not read. Never seen in
    # practice — a second holding ten thousand spans — but counted rather than assumed away.
    truncated: list[datetime] = field(default_factory=list)


@dataclass
class ArchiveStatus:
    records: int
    first: datetime | None
    last: datetime | None
    archived_until: datetime | None
    last_run_at: datetime | None
    last_added: int | None
    by_service: dict[str, int]
    size: str


def refusal() -> str | None:
    """Why `archive()` would refuse right now, in words; None when it can run."""
    s = get_settings()
    if s.wera_demo_mode:
        return "Demo mode has its own database; the archive belongs in the real one. Set WERA_DEMO_MODE=false."
    if s.logfire_read_token is None or not s.logfire_read_token.get_secret_value().strip():
        return (
            "No LOGFIRE_READ_TOKEN in .env.local. Make one in Logfire: the wera-flow project → "
            "Settings → Read tokens → Create read token."
        )
    if engine().dialect.name != "postgresql":
        return "The archive is kept in Postgres; DATABASE_URL isn't one."
    return None


class _WithoutArchiveQueries(logging.Filter):
    """httpx logs every request at INFO. A backfill is forty-odd of them, and each would become a
    Logfire log of its own — records about reading records, archived the next night."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/v2/query" not in record.getMessage()


_QUIET = _WithoutArchiveQueries()


def client() -> Any:
    """A query client for the configured read token. Its region comes from the token."""
    from logfire.query_client import LogfireQueryClient

    httpx_logger = logging.getLogger("httpx")
    if _QUIET not in httpx_logger.filters:
        httpx_logger.addFilter(_QUIET)
    token = get_settings().logfire_read_token
    assert token is not None  # `refusal()` is checked first
    return LogfireQueryClient(read_token=token.get_secret_value())


def archive(source: Query | None = None, *, since: timedelta | None = None, now: datetime | None = None) -> ArchiveReport:
    """Copy what Logfire holds since the watermark into `telemetry.records`.

    `since` re-reads from that long ago instead (`wera telemetry archive --since 7d`); what is
    already archived is skipped by key, and the watermark never moves backwards.
    """
    reason = refusal()
    if reason:
        raise ArchiveRefused(reason)
    now = now or datetime.now(UTC)
    until = now - SETTLE
    with engine().connect() as conn:
        state = conn.execute(select(STATE).where(STATE.c.id == 1)).mappings().first()
    if since is not None:
        start = now - since
    elif state is not None:
        start = state["archived_until"] - OVERLAP
    else:
        start = now - KEPT_BY_LOGFIRE
    start = max(start, now - KEPT_BY_LOGFIRE)  # Logfire has nothing older to give
    report = ArchiveReport(since=start, until=until)
    if source is not None:
        return _archive(source, report, now)
    with client() as owned:
        return _archive(owned, report, now)


def _archive(source: Query, report: ArchiveReport, now: datetime) -> ArchiveReport:
    with logfire.span("telemetry archive", since=report.since.isoformat(), until=report.until.isoformat()):
        for lo, hi in _windows(report.since, report.until):
            rows = list(_fetch(source, lo, hi, report))
            with engine().begin() as conn:
                report.added += _write(conn, rows)
                _move_watermark(conn, hi, now, report.added)
        with engine().begin() as conn:  # also when there was nothing to read: the run still happened
            _move_watermark(conn, report.until, now, report.added)
        logfire.info(
            "Archived {added} new record(s) of {fetched} read, in {queries} quer(ies)",
            added=report.added,
            fetched=report.fetched,
            queries=report.queries,
        )
    return report


def due(now: datetime | None = None) -> bool:
    """Whether the last archive was more than a day ago — the startup catch-up's question."""
    if refusal():
        return False
    now = now or datetime.now(UTC)
    with engine().connect() as conn:
        last = conn.scalar(select(STATE.c.last_run_at).where(STATE.c.id == 1))
    return last is None or now - last > timedelta(days=1)


def status() -> ArchiveStatus:
    """What the archive holds. Postgres only, like the archive itself."""
    with engine().connect() as conn:
        records, first, last = conn.execute(
            select(func.count(), func.min(RECORDS.c.start_timestamp), func.max(RECORDS.c.start_timestamp))
        ).one()
        state = conn.execute(select(STATE).where(STATE.c.id == 1)).mappings().first() or {}
        by_service = dict(
            conn.execute(
                select(RECORDS.c.service_name, func.count()).group_by(RECORDS.c.service_name).order_by(func.count().desc())
            ).all()
        )
        size = conn.exec_driver_sql("select pg_size_pretty(pg_total_relation_size('telemetry.records'))").scalar() or "0"
    return ArchiveStatus(
        records=records,
        first=first,
        last=last,
        archived_until=state.get("archived_until"),
        last_run_at=state.get("last_run_at"),
        last_added=state.get("last_added"),
        by_service={k or "(none)": v for k, v in by_service.items()},
        size=size,
    )


# ---------------------------------------------------------------------------


def _windows(start: datetime, until: datetime) -> Iterator[tuple[datetime, datetime]]:
    lo = start
    while lo < until:
        hi = min(lo + WINDOW, until)
        yield lo, hi
        lo = hi


def _fetch(source: Query, lo: datetime, hi: datetime, report: ArchiveReport) -> Iterator[dict[str, Any]]:
    """The records that started in `[lo, hi)`, splitting the window while it comes back full."""
    rows = _query(source, lo, hi)
    report.queries += 1
    if len(rows) >= PAGE and hi - lo > SMALLEST:
        mid = lo + (hi - lo) / 2
        yield from _fetch(source, lo, mid, report)
        yield from _fetch(source, mid, hi, report)
        return
    if len(rows) >= PAGE:
        report.truncated.append(lo)
        logfire.warn("Telemetry window at {at} still full at one second; rows past {page} were skipped", at=lo, page=PAGE)
    report.fetched += len(rows)
    for row in rows:
        record = _record(row)
        if record is not None and lo <= record["start_timestamp"] < hi:
            yield record


def _query(source: Query, lo: datetime, hi: datetime) -> list[dict[str, Any]]:
    from logfire.query_client import QueryRateLimitedError

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return source.query_json_rows(QUERY, min_timestamp=lo, max_timestamp=hi, limit=PAGE)["rows"]
        except QueryRateLimitedError as limited:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            time.sleep(min(getattr(limited, "retry_after", None) or 5.0, 60.0))
    return []


def _record(row: dict[str, Any]) -> dict[str, Any] | None:
    """One Logfire row as a `telemetry.records` row, or None if it has no identity to key on."""
    if not row.get("trace_id") or not row.get("span_id") or not row.get("start_timestamp"):
        return None
    record = {name: row.get(name) for name in COLUMNS}
    record["start_timestamp"] = _when(record["start_timestamp"])
    record["end_timestamp"] = _when(record["end_timestamp"])
    record["level"] = _level(record["level"])
    record["attributes"] = _json(record["attributes"])
    record["tags"] = _json(record["tags"])
    if record["otel_status_code"] is not None:  # its type isn't documented; the column is text
        record["otel_status_code"] = str(record["otel_status_code"])
    for name, limit in (("service_name", 128), ("service_version", 64), ("deployment_environment", 64), ("kind", 16)):
        if isinstance(record[name], str):
            record[name] = record[name][:limit]
    return _without_nul(record)


def _when(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _level(value: Any) -> int | None:
    if value is None or isinstance(value, int):
        return value
    text = str(value).strip().lower()
    return int(text) if text.isdigit() else LEVELS.get(text)


def _json(value: Any) -> Any:
    """`attributes` arrives as JSON text or already decoded, depending on the column's type."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {"_unparsed": value}
    return value


def _write(conn: Connection, rows: list[dict[str, Any]]) -> int:
    added = 0
    insert = pg_insert(RECORDS).on_conflict_do_nothing(index_elements=["trace_id", "span_id"]).returning(RECORDS.c.span_id)
    for i in range(0, len(rows), WRITE_BATCH):
        added += len(conn.execute(insert, rows[i : i + WRITE_BATCH]).all())
    return added


def _move_watermark(conn: Connection, until: datetime, now: datetime, added: int) -> None:
    """Archived up to `until` — or further, if an earlier run already got further: a re-read with
    `--since` must never move the watermark back and make the next run read it all again."""
    upsert = pg_insert(STATE).values(id=1, archived_until=until, last_run_at=now, last_added=added)
    conn.execute(
        upsert.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "archived_until": func.greatest(STATE.c.archived_until, upsert.excluded.archived_until),
                "last_run_at": upsert.excluded.last_run_at,
                "last_added": upsert.excluded.last_added,
            },
        )
    )
