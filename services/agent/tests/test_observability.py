"""What a run looks like from the outside (ARCHITECTURE §7.2 #9, PRIV-03).

Telemetry is usually untested because nothing breaks when it is wrong — which is precisely how
it ends up wrong. Three things here are worth holding:

* **A model call is a span.** `instrument_fastapi` traced the request and nothing inside it, so
  a seven-minute run appeared as one flat span and the only way to ask where the time went was
  SQL against `llm_calls`. A trace that can't answer that is decoration.
* **The figures reach the span and the metrics.** Cost, tokens and latency are the numbers the
  dashboard exists for.
* **No text of any kind leaves.** PRIV-03 is the reason this file asserts on absence as hard as
  it asserts on presence: a span that carried a prompt would be a privacy failure that no test
  of behaviour would ever catch.
"""

import json

import logfire
import pytest
from logfire.testing import InMemoryMetricReader, SimpleSpanProcessor, TestExporter, get_collected_metrics

from wera_agent.schemas.llm_outputs import ProfileProposal


@pytest.fixture
def traced():
    """Capture spans and metrics for one test, then put Logfire back as it was."""
    exporter, reader = TestExporter(), InMemoryMetricReader()
    logfire.configure(
        send_to_logfire=False,
        console=False,
        inspect_arguments=False,
        additional_span_processors=[SimpleSpanProcessor(exporter)],
        metrics=logfire.MetricsOptions(additional_readers=[reader]),
    )
    yield exporter, reader
    exporter.clear()


def call_a_model(store, cv_text: str = "Jordan Ellery, backend engineer") -> None:
    """One real `structured()` call, on the demo responder (DEMO-02: no network)."""
    from wera_agent.llm.gateway import structured

    structured(store.s, "profile.generate", ProfileProposal, {"cv": cv_text}, context={"cv": store.cv()})


class TestAModelCallIsASpan:
    def test_the_task_gets_its_own_span(self, store, traced):
        exporter, _ = traced
        call_a_model(store)
        names = [s["name"] for s in exporter.exported_spans_as_dict()]
        assert "llm {task}" in names, names

    def test_the_span_names_the_task_and_tier(self, store, traced):
        exporter, _ = traced
        call_a_model(store)
        span = next(s for s in exporter.exported_spans_as_dict() if s["name"] == "llm {task}")
        assert span["attributes"]["task"] == "profile.generate"
        assert span["attributes"]["tier"] == "fast"

    def test_the_span_carries_the_figures_the_table_carries(self, store, traced):
        """Whatever is in `llm_calls` should be answerable from the trace, or the trace sends
        you back to SQL — which is the state this replaced."""
        exporter, _ = traced
        call_a_model(store)
        attrs = next(s["attributes"] for s in exporter.exported_spans_as_dict() if s["name"] == "llm {task}")
        for key in ("llm.model", "llm.ok", "llm.input_tokens", "llm.output_tokens", "llm.latency_ms", "llm.attempts"):
            assert key in attrs, f"{key} missing from {sorted(attrs)}"

    def test_a_refusal_puts_its_code_on_the_span(self, store, traced):
        """A budget refusal never reaches a model, so `_publish` never runs for it. Without the
        wrapper's own handler the span would say nothing about why the task produced nothing."""
        from wera_agent.errors import ApiError

        exporter, _ = traced
        # Spend is summed from `llm_calls`, and a fresh test database has none — so the only
        # way to be over budget here is to have no budget.
        data = store._settings()
        store._save_settings({**data, "weeklyBudgetUsd": 0})
        with pytest.raises(ApiError):
            call_a_model(store)
        attrs = next(s["attributes"] for s in exporter.exported_spans_as_dict() if s["name"] == "llm {task}")
        assert attrs["llm.code"] == "BUDGET_REACHED"


class TestTheMetrics:
    def test_cost_latency_and_tokens_are_all_published(self, store, traced):
        _, reader = traced
        call_a_model(store)
        names = {m["name"] for m in get_collected_metrics(reader)}
        assert {"wera.llm.calls", "wera.llm.cost_usd", "wera.llm.latency_ms", "wera.llm.tokens"} <= names, names

    def test_metrics_are_broken_down_by_task(self, store, traced):
        """A total with no breakdown answers "how much" and never "what for"."""
        _, reader = traced
        call_a_model(store)
        calls = next(m for m in get_collected_metrics(reader) if m["name"] == "wera.llm.calls")
        points = calls["data"]["data_points"]
        assert any(p["attributes"].get("task") == "profile.generate" for p in points), points


class TestNothingSensitiveLeaves:
    """PRIV-03: `llm_calls` holds metadata only, and so must the trace."""

    def test_no_prompt_or_reply_text_is_attached(self, store, traced):
        exporter, _ = traced
        secret = "Jordan Ellery, ada-the-unmistakable-token@example.com, +44 7700 900100"
        call_a_model(store, secret)
        dumped = json.dumps(exporter.exported_spans_as_dict())
        assert "ada-the-unmistakable-token" not in dumped
        assert "900100" not in dumped

    def test_the_span_carries_no_free_text_attributes(self, store, traced):
        """Every attribute on the span should be a number, a flag, or a closed vocabulary —
        a task name, a tier, a model id, an error code. Nothing a person typed."""
        exporter, _ = traced
        call_a_model(store)
        attrs = next(s["attributes"] for s in exporter.exported_spans_as_dict() if s["name"] == "llm {task}")
        allowed = {
            "task", "tier", "llm.model", "llm.ok", "llm.code", "llm.attempts",
            "llm.input_tokens", "llm.output_tokens", "llm.latency_ms",
        }  # fmt: skip
        extra = {k for k in attrs if not k.startswith("logfire.") and not k.startswith("code.") and k not in allowed}
        assert not extra, f"unvetted attribute(s) on the span: {extra}"


class TestTheCliConfiguresItsOwnTracing:
    def test_configure_works_without_a_fastapi_app(self):
        """`wera eval` and `wera discover` are the two commands that spend money, and both ran
        with no tracing at all because `configure` required an app to instrument."""
        from wera_agent.observability import configure

        configure()  # no app, no exception
