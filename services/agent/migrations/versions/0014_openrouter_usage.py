"""openrouter usage

OpenRouter's own record of every call, in plain SQL (OBS-06, 15 Nov). OpenRouter's Broadcast sends
each request's trace to Logfire, and `wera telemetry archive` copies it into `telemetry.records`
with everything else (`service_name = 'openrouter'`). These views read the tokens and the cost out
of those records' attributes, and set them beside what `llm_calls` recorded.

Why: on 15 Nov `llm_calls` held $0.33 for the week and OpenRouter had billed $2.05. A live eval's
calls land in a database that is wiped at the start of the next eval, other uses of the key never
reach WERA at all, and a reply that failed to parse records no tokens. OpenRouter's record has all
of them.

The attribute names are the ones OpenRouter's own sample trace carried (`gen_ai.usage.*`,
`trace.metadata.*`). The sample is labelled `trace.metadata.source = 'observability-test-button'`
and is left out, so pressing "Send Trace" never shows up as spend.

Postgres only, like 0011; views are replaced by the migration that changes them.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15 23:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Attribute values arrive as JSON; read through this rather than a bare cast, so one odd value
# reads as null instead of failing every query over the view (as `analytics.ts` does for dates).
NUM = """
create or replace function analytics.num(value text) returns numeric
language sql immutable as $$
    select case when value ~ '^-?[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$' then value::numeric end
$$
"""

OPENROUTER_CALLS = """
create view analytics.openrouter_calls as
select
    r.start_timestamp as called_at,
    r.trace_id,
    r.span_id,
    -- WERA sends the task as the generation's name, so for WERA's calls this is the task.
    r.span_name,
    r.attributes ->> 'gen_ai.response.id' as generation_id,
    coalesce(r.attributes ->> 'gen_ai.response.model', r.attributes ->> 'gen_ai.request.model') as model,
    coalesce(r.attributes ->> 'trace.metadata.openrouter.provider_name', r.attributes ->> 'gen_ai.provider.name') as provider,
    -- `demo`, `real` or `eval` from WERA; null for anything else using the key.
    r.attributes ->> 'trace.metadata.environment' as environment,
    -- What joins `llm_calls.trace_id`. Sent as metadata as well as the trace id, because OpenRouter
    -- may keep a trace id of its own.
    r.attributes ->> 'trace.metadata.wera_trace_id' as wera_trace_id,
    r.attributes ->> 'trace.metadata.openrouter.api_key_name' as api_key_name,
    analytics.num(r.attributes ->> 'gen_ai.usage.input_tokens') as input_tokens,
    analytics.num(r.attributes ->> 'gen_ai.usage.input_tokens.cached') as cached_tokens,
    analytics.num(r.attributes ->> 'gen_ai.usage.output_tokens') as output_tokens,
    analytics.num(r.attributes ->> 'gen_ai.usage.output_tokens.reasoning') as reasoning_tokens,
    analytics.num(r.attributes ->> 'gen_ai.usage.total_cost') as cost_usd
from telemetry.records r
where r.service_name = 'openrouter'
  -- One generation per request; the provider attempts and timing around it are spans of their own.
  and r.attributes ->> 'span.type' = 'generation'
  and r.attributes ->> 'trace.metadata.source' is distinct from 'observability-test-button'
"""

# Days in UTC, as OpenRouter counts them, from the moment OpenRouter's record begins — not the
# day: on the day Broadcast was switched on (15 Nov, ~22:17 UTC), counting all of WERA's calls
# made the day read as WERA having recorded $0.0099 more than OpenRouter billed.
SPEND_RECONCILED = """
create view analytics.spend_reconciled as
with since as (
    select min(called_at) as at from analytics.openrouter_calls
), provider as (
    select (o.called_at at time zone 'UTC')::date as day,
           count(*) as calls,
           sum(o.cost_usd) as usd,
           sum(o.cost_usd) filter (where o.environment = 'real') as real_usd,
           sum(o.cost_usd) filter (where o.environment = 'eval') as eval_usd,
           sum(o.cost_usd) filter (where o.environment is null) as other_usd
    from analytics.openrouter_calls o
    group by 1
), wera as (
    -- `cost_usd` is a float in `llm_calls`; as numeric, like OpenRouter's, the difference reads
    -- 0.0000007 rather than 6.999999999999899e-07.
    select (analytics.ts(c.called_at) at time zone 'UTC')::date as day, count(*) as calls, sum(c.cost_usd::numeric) as usd
    from public.llm_calls c, since
    where analytics.ts(c.called_at) >= since.at
    group by 1
)
select
    coalesce(p.day, w.day) as day,
    coalesce(w.calls, 0) as wera_calls,
    coalesce(w.usd, 0) as wera_usd,
    coalesce(p.calls, 0) as openrouter_calls,
    coalesce(p.usd, 0) as openrouter_usd,
    -- Where OpenRouter's figure came from: the app, live evals, and anything else using the key.
    coalesce(p.real_usd, 0) as openrouter_real_usd,
    coalesce(p.eval_usd, 0) as openrouter_eval_usd,
    coalesce(p.other_usd, 0) as openrouter_other_usd,
    coalesce(p.usd, 0) - coalesce(w.usd, 0) as unrecorded_usd
from provider p
full join wera w on w.day = p.day
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(NUM)
    op.execute(OPENROUTER_CALLS)
    op.execute(SPEND_RECONCILED)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("drop view if exists analytics.spend_reconciled")
    op.execute("drop view if exists analytics.openrouter_calls")
    op.execute("drop function if exists analytics.num(text)")
