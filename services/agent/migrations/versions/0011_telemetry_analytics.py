"""telemetry and analytics

Three things, all for looking back at what WERA did (OBS-01…03):

* `llm_calls.trace_id`, on both dialects. A row said what a call cost; nothing said which run,
  request or draft it was part of. With the trace id, a call joins the spans around it — in
  Logfire, and in `telemetry.records` once archived.
* **`telemetry`** (Postgres only): the Logfire archive. Logfire keeps thirty days; this keeps
  everything after that. Written by `services/telemetry_archive.py` and nothing else.
* **`analytics`** (Postgres only): read-only views over the app's tables, flattened out of the
  JSON so a viewer or a notebook can ask a question in plain SQL, and `fit_totals`, the one table
  here — a total is computed in code (MATCH-04), so it is copied in by `wera analytics refresh`
  rather than re-implemented in SQL.

The schemas are left out on SQLite, which has none and only ever runs the unit tests (REL-04).
Views are replaced by the migration that changes them: they hold no data, so dropping and
recreating one costs nothing, and `wera_readonly` keeps its access through default privileges.

`wera reset` drops the app's tables and runs every migration again. It empties `analytics`
first (its views depend on those tables) and leaves `telemetry` alone — the archive is a copy of
Logfire, not app data — which is why the telemetry tables are created only if they're missing.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-12 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Timestamps are ISO 8601 text in the app's tables (`Iso` in db/models.py). Read through this
# rather than a bare cast, so one malformed value reads as null instead of failing every query
# that touches the column.
TS = """
create or replace function analytics.ts(value text) returns timestamptz
language sql stable as $$
    select case when value ~ '^\\d{4}-\\d{2}-\\d{2}' then value::timestamptz end
$$
"""

JOBS = """
create view analytics.jobs as
select
    j.id,
    j.title,
    j.company,
    j.status,
    j.data ->> 'hiddenReason' as hidden_reason,
    -- The rule, without the term it matched: "Title contains an excluded term".
    nullif(split_part(j.data ->> 'hiddenReason', ':', 1), '') as hidden_rule,
    j.data -> 'sources' -> 0 ->> 'channel' as channel,
    j.data -> 'sources' -> 0 ->> 'detail' as source,
    jsonb_array_length(coalesce(j.data -> 'sources', '[]'::jsonb)) as source_count,
    j.data -> 'ats' ->> 'vendor' as ats_vendor,
    j.data -> 'ats' ->> 'boardToken' as ats_board,
    regexp_replace(lower(substring(j.data ->> 'applyUrl' from '^[a-zA-Z]+://([^/?#]+)')), '^www\\.|:\\d+$', '', 'g') as host,
    j.data -> 'foundBy' ->> 'runId' as found_by_run,
    j.data ->> 'via' as via,
    j.data -> 'location' ->> 'city' as city,
    j.data -> 'location' ->> 'country' as country,
    j.data ->> 'remote' as remote,
    j.data ->> 'seniority' as seniority,
    j.data ->> 'language' as language,
    (j.data -> 'salary' ->> 'min')::numeric as salary_min,
    (j.data -> 'salary' ->> 'max')::numeric as salary_max,
    j.data -> 'salary' ->> 'currency' as salary_currency,
    j.data -> 'salary' ->> 'period' as salary_period,
    j.data ->> 'descriptionStatus' as description_status,
    length(j.data ->> 'descriptionMd') as description_chars,
    analytics.ts(j.posted_at) as posted_at,
    j.data ->> 'postedPrecision' as posted_precision,
    analytics.ts(j.first_seen) as first_seen,
    analytics.ts(j.data ->> 'lastSeen') as last_seen,
    analytics.ts(j.data ->> 'lastConfirmedAt') as last_confirmed_at,
    analytics.ts(j.data ->> 'closedAt') as closed_at,
    f.job_id is not null as scored
from public.jobs j
left join public.fit_reports f on f.job_id = j.id
"""

RUNS = """
create view analytics.runs as
select
    r.id,
    r.channel,
    r.status,
    analytics.ts(r.started_at) as started_at,
    analytics.ts(r.finished_at) as finished_at,
    -- From the timestamps: `duration_ms` only paces a demo run's progress bar.
    extract(epoch from analytics.ts(r.finished_at) - analytics.ts(r.started_at)) as seconds,
    r.queries,
    r.results,
    r.queued,
    r.local_pages + r.fallback_pages as pages_read,
    r.local_pages,
    r.fallback_pages,
    r.new_jobs,
    r.credits_used,
    r.credits_saved,
    -- Why a search stopped short (SRCH-26): the first error is the reason the run gives.
    r.errors -> 0 ->> 'code' as stop_code,
    r.errors -> 0 ->> 'message' as stop_reason,
    jsonb_array_length(coalesce(r.errors, '[]'::jsonb)) as error_count,
    -- Model calls are not tagged with a run, so this is every call made while the run was going:
    -- exact for a run on its own, an overstatement when a draft was being written alongside it.
    (
        select coalesce(sum(c.cost_usd), 0)
        from public.llm_calls c
        where analytics.ts(c.called_at) between analytics.ts(r.started_at) and analytics.ts(r.finished_at)
    ) as model_usd_while_running
from public.intake_runs r
"""

SPEND_DAILY = """
create view analytics.spend_daily as
with model as (
    select analytics.ts(called_at)::date as day, sum(cost_usd) as model_usd, count(*) as model_calls,
           count(*) filter (where not ok) as model_failures
    from public.llm_calls group by 1
), search as (
    select analytics.ts(started_at)::date as day, sum(credits_used) as firecrawl_credits,
           sum(credits_saved) as firecrawl_credits_saved, sum(queries) as search_queries, count(*) as runs
    from public.intake_runs group by 1
)
select
    coalesce(m.day, s.day) as day,
    coalesce(m.model_usd, 0) as model_usd,
    coalesce(m.model_calls, 0) as model_calls,
    coalesce(m.model_failures, 0) as model_failures,
    coalesce(s.firecrawl_credits, 0) as firecrawl_credits,
    coalesce(s.firecrawl_credits_saved, 0) as firecrawl_credits_saved,
    coalesce(s.search_queries, 0) as search_queries,
    coalesce(s.runs, 0) as runs
from model m
full join search s on s.day = m.day
"""

MODEL_CALLS = """
create view analytics.model_calls as
select
    c.id,
    analytics.ts(c.called_at) as called_at,
    c.task,
    c.tier,
    c.model,
    c.attempt,
    c.ok,
    c.code,
    c.detail,
    c.input_tokens,
    c.output_tokens,
    c.cost_usd,
    c.latency_ms,
    c.trace_id
from public.llm_calls c
"""

FUNNEL = """
create view analytics.funnel as
with found as (
    select j.data -> 'foundBy' ->> 'runId' as run_id, j.id as job_id, f.job_id is not null as scored
    from public.jobs j
    left join public.fit_reports f on f.job_id = j.id
    where j.data ? 'foundBy'
), decided as (
    select job_id,
           bool_or(gate = 1 and decision = 'approve') as shortlisted,
           bool_or(gate = 2 and decision = 'approve') as approved
    from public.decisions group by job_id
)
select
    r.id as run_id,
    r.channel,
    r.status,
    analytics.ts(r.started_at) as started_at,
    r.queries,
    r.results,
    r.queued,
    r.local_pages + r.fallback_pages as read,
    r.new_jobs as accepted,
    -- Distinct, because a job can have more than one application (a discarded pack and its redo).
    count(distinct found.job_id) filter (where found.scored) as scored,
    count(distinct found.job_id) filter (where d.shortlisted) as shortlisted,
    count(distinct found.job_id) filter (where d.approved) as approved,
    count(distinct found.job_id) filter (where a.submitted_at is not null) as submitted
from public.intake_runs r
left join found on found.run_id = r.id
left join decided d on d.job_id = found.job_id
left join public.applications a on a.job_id = found.job_id
group by r.id
"""

VIEWS = {"jobs": JOBS, "runs": RUNS, "spend_daily": SPEND_DAILY, "model_calls": MODEL_CALLS, "funnel": FUNNEL}


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(length=32), nullable=True))
        batch_op.create_index("ix_llm_calls_trace_id", ["trace_id"])

    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("create schema if not exists telemetry")
    op.create_table(
        "records",
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("span_id", sa.String(16), nullable=False),
        sa.Column("parent_span_id", sa.String(16)),
        sa.Column("start_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_timestamp", sa.DateTime(timezone=True)),
        sa.Column("duration", sa.Float()),
        sa.Column("kind", sa.String(16)),
        sa.Column("level", sa.SmallInteger()),
        sa.Column("span_name", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("service_name", sa.String(128)),
        sa.Column("service_version", sa.String(64)),
        sa.Column("deployment_environment", sa.String(64)),
        sa.Column("otel_scope_name", sa.Text()),
        sa.Column("otel_status_code", sa.String(16)),
        sa.Column("otel_status_message", sa.Text()),
        sa.Column("is_exception", sa.Boolean()),
        sa.Column("exception_type", sa.Text()),
        sa.Column("exception_message", sa.Text()),
        sa.Column("exception_stacktrace", sa.Text()),
        sa.Column("attributes", postgresql.JSONB()),
        sa.Column("tags", postgresql.JSONB()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("trace_id", "span_id"),
        schema="telemetry",
        if_not_exists=True,
    )
    op.create_index("ix_records_start_timestamp", "records", ["start_timestamp"], schema="telemetry", if_not_exists=True)
    op.create_index(
        "ix_records_service_start", "records", ["service_name", "start_timestamp"], schema="telemetry", if_not_exists=True
    )
    op.create_index("ix_records_span_name", "records", ["span_name"], schema="telemetry", if_not_exists=True)
    op.create_table(
        "archive_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        # Everything that started before this is archived, apart from spans still being written.
        sa.Column("archived_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_added", sa.Integer(), nullable=False),
        schema="telemetry",
        if_not_exists=True,
    )

    op.execute("create schema if not exists analytics")
    op.execute(TS)
    for sql in VIEWS.values():
        op.execute(sql)
    op.create_table(
        "fit_totals",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("core", sa.Float(), nullable=False),
        sa.Column("adjacent", sa.Float(), nullable=False),
        sa.Column("experience", sa.Float(), nullable=False),
        sa.Column("eligibility", sa.Float(), nullable=False),
        sa.Column("freshness", sa.Float(), nullable=False),
        sa.Column("rough_only", sa.Boolean(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("cv_version", sa.Integer()),
        sa.Column("scored_at", sa.DateTime(timezone=True)),
        # A total depends on the date (freshness) and the weights, so it is true as of this.
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("drop schema if exists analytics cascade")
        op.execute("drop schema if exists telemetry cascade")
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_index("ix_llm_calls_trace_id")
        batch_op.drop_column("trace_id")
