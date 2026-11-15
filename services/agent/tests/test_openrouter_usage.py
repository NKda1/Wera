"""OBS-06: OpenRouter's own record of each call, read out of the Logfire archive.

The records go in the way OpenRouter's spans really arrive — through `telemetry_archive.archive`,
from a fake Logfire — with the attributes OpenRouter's own sample trace carried on 15 Nov. The
views exist only on Postgres (migration 0014), so most of this runs in the Postgres lane.
"""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from wera_agent.clock import iso
from wera_agent.db.models import LlmCallRow
from wera_agent.db.session import engine, reset_caches, transaction
from wera_agent.services import analytics
from wera_agent.services import telemetry_archive as ta

from .test_telemetry_archive import NOW, FakeLogfire, real, span  # noqa: F401 — `real` is a fixture

ON_POSTGRES = bool(os.environ.get("WERA_TEST_DATABASE_URL"))
needs_postgres = pytest.mark.skipif(not ON_POSTGRES, reason="needs WERA_TEST_DATABASE_URL (the Postgres lane)")

WERA_TRACE = "a" * 32


def generation(at: datetime, n: int, *, cost: float = 0.002, span_type: str = "generation", **metadata: str) -> dict:
    """One OpenRouter record as Logfire returns it. `metadata` lands under `trace.metadata.*`."""
    attributes = {
        "span.type": span_type,
        "gen_ai.request.model": "qwen/qwen3.7-flash",
        "gen_ai.response.model": "qwen/qwen3.7-flash",
        "gen_ai.provider.name": "alibaba",
        "gen_ai.response.id": f"gen-{n}",
        "gen_ai.usage.input_tokens": 1000,
        "gen_ai.usage.input_tokens.cached": 200,
        "gen_ai.usage.output_tokens": 300,
        "gen_ai.usage.output_tokens.reasoning": 0,
        "gen_ai.usage.total_cost": cost,
        "trace.metadata.openrouter.provider_name": "Alibaba",
        **{f"trace.metadata.{k}": v for k, v in metadata.items()},
    }
    return span(
        at, n, service="openrouter", span_name="profile.generate", message="profile.generate", attributes=json.dumps(attributes)
    )


# A WERA call, a live eval's, another app's on the same key — and two that aren't spend: the
# sample "Send Trace" produces, and a provider attempt inside a request.
RECORDS = [
    generation(NOW - timedelta(hours=3), 1, environment="real", wera_trace_id=WERA_TRACE),
    generation(NOW - timedelta(hours=2), 2, cost=0.01, environment="eval"),
    generation(NOW - timedelta(hours=2), 3),
    generation(NOW - timedelta(hours=1), 4, cost=0.02, source="observability-test-button"),
    generation(NOW - timedelta(hours=1), 5, span_type="span"),
]


def query(sql: str) -> list[dict]:
    with engine().connect() as conn:
        return [dict(r) for r in conn.execute(text(sql)).mappings()]


@needs_postgres
@pytest.mark.usefixtures("real")
class TestOpenRoutersRecord:
    def test_each_generation_is_one_row_and_the_sample_trace_is_not_spend(self):
        ta.archive(FakeLogfire(RECORDS), now=NOW)
        rows = query("select * from analytics.openrouter_calls order by called_at, span_id")
        assert [r["environment"] for r in rows] == ["real", "eval", None]
        wera = rows[0]
        assert (wera["input_tokens"], wera["cached_tokens"], wera["output_tokens"], wera["reasoning_tokens"]) == (
            1000,
            200,
            300,
            0,
        )
        assert float(wera["cost_usd"]) == pytest.approx(0.002)
        assert (wera["model"], wera["provider"], wera["span_name"]) == ("qwen/qwen3.7-flash", "Alibaba", "profile.generate")
        assert (wera["wera_trace_id"], wera["generation_id"]) == (WERA_TRACE, "gen-1")

    def test_the_days_bill_sits_beside_what_wera_recorded(self):
        ta.archive(FakeLogfire(RECORDS), now=NOW)
        at = int((NOW - timedelta(hours=3)).timestamp() * 1000)
        before = int((NOW - timedelta(hours=5)).timestamp() * 1000)
        with transaction() as s:
            # Made before OpenRouter's record begins, so there's nothing to compare it with.
            s.add(
                LlmCallRow(
                    task="fit.score",
                    tier="fast",
                    model="qwen/qwen3.7-flash",
                    cost_usd=0.5,
                    ok=True,
                    attempt=1,
                    called_at=iso(before),
                )
            )
            s.add(
                LlmCallRow(
                    task="profile.generate",
                    tier="fast",
                    model="qwen/qwen3.7-flash",
                    cost_usd=0.002,
                    ok=True,
                    attempt=1,
                    called_at=iso(at),
                    trace_id=WERA_TRACE,
                )
            )
        (day,) = query("select * from analytics.spend_reconciled")
        assert day["day"] == NOW.date()
        assert (day["wera_calls"], day["openrouter_calls"]) == (1, 3)
        assert float(day["wera_usd"]) == pytest.approx(0.002)
        assert float(day["openrouter_usd"]) == pytest.approx(0.014)
        assert (
            float(day["openrouter_real_usd"]),
            float(day["openrouter_eval_usd"]),
            float(day["openrouter_other_usd"]),
        ) == pytest.approx((0.002, 0.01, 0.002))
        assert float(day["unrecorded_usd"]) == pytest.approx(0.012)
        # And the call joins its own row in `llm_calls`.
        assert (
            query(
                "select count(*) as n from analytics.openrouter_calls o join public.llm_calls c on c.trace_id = o.wera_trace_id"
            )[0]["n"]
            == 1
        )

    def test_the_figures_wera_telemetry_status_prints(self):
        ta.archive(FakeLogfire(RECORDS), now=NOW)
        usage = analytics.openrouter_usage(NOW - timedelta(days=1))
        assert usage is not None and usage.calls == 3
        assert usage.by_environment == pytest.approx({"real": 0.002, "eval": 0.01, "other": 0.002})
        assert usage.last == NOW - timedelta(hours=2)
        assert analytics.openrouter_usage(NOW).calls == 0  # nothing since

    def test_a_reset_keeps_the_record_and_rebuilds_the_views(self):
        from wera_agent.cli import main

        ta.archive(FakeLogfire(RECORDS), now=NOW)
        for _ in range(2):  # and over a database the first reset rebuilt
            assert main(["reset", "--yes"]) == 0
            reset_caches()
            assert len(query("select * from analytics.openrouter_calls")) == 3
            assert query("select count(*) as n from analytics.spend_reconciled")[0]["n"] == 1


@pytest.mark.skipif(ON_POSTGRES, reason="the SQLite lane's half")
def test_there_is_no_record_to_read_on_sqlite():
    assert analytics.openrouter_usage(datetime.now(UTC)) is None
