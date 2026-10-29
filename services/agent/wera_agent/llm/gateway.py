"""The one way WERA talks to a model: LangChain → OpenRouter → Pydantic.

`structured()` is the single choke point. Everything that must happen on every call happens
here, so no node can forget it:

* the weekly budget is checked first (COST-01) and refused at 100%;
* contact details are redacted before the prompt is built (PRIV-01), and this is where a
  Phase 3 PII gateway plugs in — see `wera_agent.pii`;
* external text is wrapped in untrusted-data blocks by the templates (§9.3);
* the reply is validated against a Pydantic schema, with the master CV as validation context,
  and one retry carries the validation errors back (§8.5 step 1);
* an `llm_calls` row records the metadata — never prompt or reply text (PRIV-03);
* provider failures become WERA's own codes, with no upstream body passed through (§9.4).

Demo mode never reaches the network: it answers from `llm.demo` responders and still validates
the result with the same schema, so the demo exercises the real contract.
"""

import time
from functools import cache
from json import JSONDecodeError
from typing import Any

import logfire
import openai
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from opentelemetry import trace
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import iso, now_ms
from ..config import Effort, wera
from ..db import state
from ..db.models import LlmCallRow
from ..db.session import transaction
from ..errors import ApiError
from ..pii import ContactRedactor, NullRedactor, Redactor
from ..settings import get_settings
from . import demo
from .pricing import budget, cost_usd
from .prompts import RETRY_NOTE, template

# §9.4: stable codes. The provider's own message never reaches the caller.
AUTH = "LLM_AUTH_FAILED"
QUOTA = "LLM_QUOTA"
RATE = "LLM_RATE_LIMITED"
TIMEOUT = "LLM_TIMEOUT"
TRUNCATED = "LLM_TRUNCATED"
BAD_OUTPUT = "LLM_BAD_OUTPUT"
UPSTREAM = "LLM_UPSTREAM"
BUDGET = "BUDGET_REACHED"

MESSAGES = {
    AUTH: "The model provider rejected the API key. Check LLM_API_KEY in .env.local.",
    QUOTA: (
        "The model provider wouldn't accept the request on the credit left — it reserves the "
        "whole token budget up front, so a task can be refused while credit remains. Top up the "
        "account, pick a cheaper model for this tier in Settings, or lower the task's max_tokens "
        "in config/wera.yaml."
    ),
    RATE: "The model provider is rate-limiting WERA. Wait a moment and try again.",
    TIMEOUT: "The model took too long to answer.",
    TRUNCATED: (
        "The model ran out of room before it finished — its thinking used the whole token "
        "budget. Give this task more tokens in config/wera.yaml, or choose a model that thinks "
        "less for this tier in Settings."
    ),
    BAD_OUTPUT: "The model didn't answer in the shape WERA needs, twice. Nothing was saved.",
    UPSTREAM: "The model provider couldn't be reached.",
    BUDGET: "This week's budget is used up. Raise it in Settings, or wait for the week to roll over.",
}

# Provider failures another model might survive: a connection that dropped, a timeout, a
# provider overloaded or rate-limiting. An auth or credit failure is not here — the next model
# would fail the same way, so WERA says so straight away.
RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    openai.RateLimitError,
)

# Set by the tests to an httpx client on respx's transport. The provider SDK builds its own
# client otherwise, and that one can't be intercepted — so without this seam a test of the live
# path would have to reach the real network.
http_client: Any = None

# SPEC §9.3's "Reasoning" column, as OpenRouter expects it. "off" has to be said out loud:
# today's models think by default, and sending nothing is not the same as sending no.
_REASONING: dict[Effort, dict[str, Any]] = {
    "off": {"enabled": False},
    "low": {"effort": "low"},
    "on": {"effort": "high"},
}

# §9.3's token budgets are for the answer, but a model's thinking is charged against the same
# max_tokens. Without an allowance the thinking eats the whole budget and the reply comes back
# empty or cut off mid-sentence — which reads as a broken model rather than a budget that was
# too small.
#
# These were 600/1200 and measurably too small. `wera eval --packs` caught `cv.tailor` cut off
# at 4,200 tokens and `letter.write` at 2,700 — both their ceiling plus the old `on` allowance —
# so the thinking was consuming more than the answer it was meant to be preparing. Only tokens
# actually generated are billed; a larger allowance costs nothing unless it is used, and what it
# does raise is what the provider reserves against the balance for that one call.
_HEADROOM: dict[Effort, int] = {"off": 0, "low": 1200, "on": 3000}

# What a cut-off reply's second attempt gets. How long a model thinks varies per call, so the
# retry buys room instead of carrying feedback. Only the tokens actually generated are billed,
# so the larger ceiling costs nothing unless it is used — it only raises what the provider
# reserves against the balance for that one call.
#
# It scales the *allowance* as well as the ceiling. It used to scale only the ceiling, which
# made the retry useless in the one case it exists for: when it is the thinking that overran,
# doubling the room for the answer changes nothing, and the second attempt overran at exactly
# the same point as the first. That is how `cv.parse` failed twice at 4,600 and then 8,600.
TRUNCATION_RETRY_FACTOR = 2


def _code_for(exc: BaseException) -> str:
    """Maps a provider exception to a WERA code by class name and status, so the same mapping
    works for the OpenAI SDK, a proxy in between, and anything OpenAI-compatible behind them."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if "Unauthorized" in name or "Authentication" in name or "Forbidden" in name or status in (401, 403):
        return AUTH
    if "PaymentRequired" in name or "Quota" in name or status == 402:
        return QUOTA
    if "TooManyRequests" in name or "RateLimit" in name or status == 429:
        return RATE
    if "Timeout" in name or status == 408:
        return TIMEOUT
    return UPSTREAM


def _fail(code: str) -> ApiError:
    status = 429 if code == RATE else 402 if code in (QUOTA, BUDGET) else 502 if code == UPSTREAM else 424
    return ApiError(status, code, MESSAGES[code])


def make_chat(model: str, *, base_url: str, max_tokens: int, reasoning: Effort, headroom: int | None = None) -> BaseChatModel:
    """One configured model, through `ChatOpenAI` (ARCHITECTURE §3.3).

    OpenRouter speaks the OpenAI protocol, so one client covers it, any other OpenAI-compatible
    endpoint, and a local PII gateway — and OpenRouter's own options travel in `extra_body`,
    which passes through a proxy unchanged.

    `langchain-openrouter` was tried here first and taken out again: its invoke ignored both
    `timeout` and `max_retries` and blocked for sixteen minutes on a request plain httpx
    answered in five seconds, which would strand a draft with no way to give up.
    """
    cfg = wera()
    env = get_settings()
    key = env.llm_api_key.get_secret_value() if env.llm_api_key else ""

    options: dict[str, Any] = {
        # PRIV: never routed to a provider that trains on prompts. `require_parameters` is
        # deliberately not set — with it, OpenRouter finds no endpoint for these models at all
        # (404) because no provider advertises support for every option WERA sends.
        "provider": {"data_collection": "deny"},
        "reasoning": _REASONING[reasoning],
    }

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        base_url=env.llm_proxy_url or base_url,
        api_key=key,
        max_tokens=max_tokens + (_HEADROOM[reasoning] if headroom is None else headroom),
        temperature=0,  # the same job should score the same way twice
        timeout=cfg.llm_timeout_seconds,
        max_retries=cfg.llm_max_retries,
        extra_body=options,
        default_headers={"X-Title": "WERA"},  # what the OpenRouter dashboard lists the spend under
        **({"http_client": http_client} if http_client is not None else {}),
    )


def _chain[Out: BaseModel](
    models: list[str], Output: type[Out], *, base_url: str, max_tokens: int, reasoning: Effort, headroom: int | None = None
) -> Runnable:
    """The tier's model with the configured fallbacks behind it. Each one is asked for the same
    schema, so a fallback answer is as usable as the first."""

    def one(model: str) -> Runnable:
        chat = make_chat(model, base_url=base_url, max_tokens=max_tokens, reasoning=reasoning, headroom=headroom)
        # strict=True sends the schema to the provider; `include_raw` keeps the token counts and
        # turns a parse failure into a value we can retry on instead of an exception.
        return chat.with_structured_output(Output, method="json_schema", strict=True, include_raw=True)

    first, *rest = models
    chain = one(first)
    if not rest:
        return chain
    # Only a provider that can't answer is worth asking a different model about. A reply in the
    # wrong *shape* is handled by `structured()`, which retries the same model with the
    # validation errors (§8.5 step 1) — falling back there would spend three models on one bad
    # answer and throw away the feedback that usually fixes it.
    return chain.with_fallbacks([one(m) for m in rest], exceptions_to_handle=RETRYABLE)


def _reported_cost(metadata: dict[str, Any]) -> float | None:
    """What the provider says the call cost, if it says. OpenRouter returns this on every reply."""
    cost = (metadata.get("token_usage") or {}).get("cost")
    return float(cost) if isinstance(cost, int | float) else None


def _usage_from(exc: BaseException) -> dict[str, Any] | None:
    """The tokens a *failed* call still consumed.

    A reply cut off at the token ceiling was generated and billed in full; recording it as free
    made WERA's spend figure — and therefore the weekly cap (COST-01) — read an order of
    magnitude low. The provider attaches the usage to the exception, so it is read from there.
    """
    completion = getattr(exc, "completion", None) or getattr(exc, "response", None)
    usage = getattr(completion, "usage", None)
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    if prompt is None and completion_tokens is None:
        return None
    return {"input_tokens": prompt or 0, "output_tokens": completion_tokens or 0}


@cache
def _metrics() -> tuple[Any, Any, Any, Any]:
    """The four figures worth charting, created on first use.

    Built lazily because this module is imported long before `logfire.configure()` runs, and a
    metric bound to a provider that doesn't exist yet reports to nowhere for the life of the
    process.
    """
    return (
        logfire.metric_counter("wera.llm.calls", unit="1", description="Model calls, by task and outcome"),
        logfire.metric_counter("wera.llm.cost_usd", unit="USD", description="Spend, by task and model"),
        logfire.metric_histogram("wera.llm.latency_ms", unit="ms", description="Call latency, by task and tier"),
        logfire.metric_counter("wera.llm.tokens", unit="1", description="Tokens, by task and direction"),
    )


def _publish(row: dict[str, Any]) -> None:
    """Send one call's figures to the trace and to the metrics pipeline (PRIV-03: no text).

    Both, deliberately. The span attributes are the reliable half — traces export cleanly and
    can be read per run, which is the breakdown that was missing: Logfire showed the HTTP
    request and nothing of the calls inside it, so the only way to ask "where did the time go"
    was SQL against `llm_calls`. The metrics are the half that charts without a query, and they
    had nothing to carry until now, which is part of why nobody noticed the exporter failing.
    """
    tags = {"task": row["task"], "tier": row["tier"], "model": row["model"], "ok": row["ok"]}
    try:
        calls, cost, latency, tokens = _metrics()
        calls.add(1, {**tags, "code": row["code"] or ""})
        cost.add(row["cost_usd"], tags)
        latency.record(row["latency_ms"], tags)
        tokens.add(row["input_tokens"], {**tags, "direction": "input"})
        tokens.add(row["output_tokens"], {**tags, "direction": "output"})
    except Exception:  # noqa: BLE001 — never let accounting break the work it is accounting for
        logfire.debug("Could not publish metrics for {task}", task=row["task"])

    span = trace.get_current_span()
    if span.is_recording():
        # The last attempt wins on the shared keys; `attempts` is what says there was more
        # than one, and the per-attempt detail stays in `llm_calls`.
        span.set_attributes(
            {
                "llm.model": row["model"],
                "llm.attempts": row["attempt"],
                "llm.ok": row["ok"],
                "llm.input_tokens": row["input_tokens"],
                "llm.output_tokens": row["output_tokens"],
                "llm.latency_ms": row["latency_ms"],
            }
        )
        if row["code"]:
            span.set_attribute("llm.code", row["code"])


def _record(
    session: Session,
    *,
    task: str,
    tier: str,
    model: str,
    started: float,
    attempt: int,
    ok: bool,
    code: str | None = None,
    usage: dict[str, Any] | None = None,
    reported_cost: float | None = None,
) -> float:
    """Records the call and returns what it cost (PRIV-03: metadata only).

    The row goes in the caller's session, so it commits with the work it paid for. A *failed*
    call is also buffered: the failure raises, the caller's unit of work rolls back, and the row
    would go with it — losing the record of tokens the provider has already charged for. SQLite
    takes one writer at a time, so it can't be written here either; `flush_unrecorded` writes it
    once the caller's transaction has closed. Spend is recorded whether or not the work it was
    for succeeded (COST-01).

    `reported_cost` is the provider's own figure, preferred over the local price table whenever
    it is sent: the table is a copy of a published price and goes stale silently, and measured
    against a real OpenRouter reply it read about 12% low. The table stays for the pre-flight
    budget estimate, where there is no reply to read a price from yet.
    """
    tokens_in = int((usage or {}).get("input_tokens") or 0)
    tokens_out = int((usage or {}).get("output_tokens") or 0)
    cost = reported_cost if reported_cost is not None else cost_usd(model, tokens_in, tokens_out)
    row = {
        "task": task,
        "tier": tier,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "ok": ok,
        "code": code,
        "attempt": attempt,
        "called_at": iso(now_ms()),
    }
    _publish(row)
    session.add(LlmCallRow(**row))
    session.flush()
    if not ok:
        _unrecorded.append(row)
    return cost


# Failed calls whose row may be rolled back with the work they were for. Written by
# `flush_unrecorded` after the transaction closes; single process, so a plain list is enough.
_unrecorded: list[dict[str, Any]] = []


def flush_unrecorded() -> int:
    """Writes buffered failed-call rows. Called once a request or a graph run has finished, by
    which time the write lock is free. A row already committed is skipped."""
    if not _unrecorded:
        return 0
    rows, _unrecorded[:] = list(_unrecorded), []
    written = 0
    try:
        with transaction() as session:
            for row in rows:
                exists = session.scalar(
                    select(LlmCallRow.id).where(LlmCallRow.task == row["task"], LlmCallRow.called_at == row["called_at"])
                )
                if exists is None:
                    session.add(LlmCallRow(**row))
                    written += 1
    except Exception:  # noqa: BLE001 — accounting must not break the request that paid for it
        logfire.warn("Couldn't record {count} failed model call(s)", count=len(rows))
    return written


def _redactor(session: Session, redact: bool) -> Redactor:
    return ContactRedactor(state.contact(session)) if redact else NullRedactor()


def _restore(value: Any, redactor: Redactor, mapping: dict[str, str]) -> Any:
    """Puts redacted values back into a reply, wherever they appear.

    A model answers with the text it was given, so a masked value comes back masked: ask about a
    CV that says `[[ADDRESS]]` and the reply offers `[[ADDRESS]]` as a location. Restoring here
    means nothing downstream ever stores or searches for a placeholder (PRIV-01).
    """
    if not mapping:
        return value
    if isinstance(value, str):
        return redactor.restore(value, mapping)
    if isinstance(value, list):
        return [_restore(v, redactor, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _restore(v, redactor, mapping) for k, v in value.items()}
    return value


def _validate[Out: BaseModel](
    Output: type[Out],
    data: Any,
    context: dict[str, Any] | None,
    redactor: Redactor | None = None,
    mapping: dict[str, str] | None = None,
) -> Out:
    """Re-checks the reply with the master CV in context.

    LangChain parses into `Output` without context, which the context-aware validators treat as
    "nothing to check against". This second pass is where the CV rules actually bite — and it
    runs on restored text, so a validator comparing against the CV sees the real values.
    """
    raw = data.model_dump(by_alias=True) if isinstance(data, BaseModel) else data
    if redactor is not None and mapping:
        raw = _restore(raw, redactor, mapping)
    return Output.model_validate(raw, context=context)


def structured[Out: BaseModel](
    session: Session,
    task: str,
    Output: type[Out],
    variables: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    demo_input: Any = None,
    redact: bool = True,
) -> Out:
    """One §9.3 task, as one span.

    The span is the point. `instrument_fastapi` traced the request and `instrument_openai` was
    never added, so a run that took seven minutes appeared in Logfire as a single flat span with
    nothing inside it — the per-task breakdown existed only in the `llm_calls` table, reachable
    by SQL and nowhere near the trace that would have explained it.

    Retries live inside this span rather than beside it: one task is one unit of work to the
    caller, however many attempts it took, and `llm.attempts` says when it took more than one.
    """
    with logfire.span("llm {task}", task=task, tier=wera().task(task).tier) as span:
        try:
            return _structured(session, task, Output, variables, context=context, demo_input=demo_input, redact=redact)
        except ApiError as exc:
            # The code is already on the span from `_publish` for a call that reached a model;
            # this also catches the ones that never did, like a refused budget (COST-01).
            span.set_attribute("llm.code", exc.code)
            raise


def _structured[Out: BaseModel](
    session: Session,
    task: str,
    Output: type[Out],
    variables: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    demo_input: Any = None,
    redact: bool = True,
) -> Out:
    """Runs one §9.3 task and returns a validated object, or raises `ApiError` with a §9.4 code.

    `context` is the Pydantic validation context — pass `{"cv": cv}` so the CV-aware checks run.
    `demo_input` is for the demo responders only: the real path never reads it.

    `redact=False` turns off the *stored-contact* masking for one call, and is only correct when
    the caller has already removed contact details itself. `cv.parse` is the one such case: its
    input is the document that replaces the stored contact, so masking the old values against it
    both misses the new ones and corrupts unrelated text (see `pii.scrub_contacts`). It is not a
    way to opt out of PRIV-01 — it is a way to satisfy it with the right instrument.
    """
    cfg = wera()
    spec = cfg.task(task)
    saved = state.settings_data(session)
    limit = float(saved.get("weeklyBudgetUsd", cfg.weekly_budget_usd))

    # COST-01, before anything is built or sent.
    spend = budget(session, limit)
    if spend.reached:
        raise _fail(BUDGET)
    if spend.warn:  # COST-02
        logfire.warn("Weekly LLM budget at {percent}%", percent=round(spend.fraction * 100), task=task)

    # PRIV-01. Demo mode redacts too, so the demo path exercises the same code.
    redactor = _redactor(session, redact and bool(saved.get("redactContact", True)))
    prompt_vars: dict[str, Any] = {}
    mapping: dict[str, str] = {}
    for key, value in variables.items():
        if isinstance(value, str):
            masked, found = redactor.redact(value)
            prompt_vars[key], mapping = masked, {**mapping, **found}
        else:
            prompt_vars[key] = value

    if get_settings().wera_demo_mode:
        # DEMO-02: no network. Still validated against the real schema, and still recorded, so
        # the demo exercises the contract and the spend figure has something to show.
        started = time.monotonic()
        model = f"demo:{saved.get('models', {}).get(spec.tier, spec.tier)}"
        try:
            out = _validate(Output, demo.respond(task, prompt_vars, context, demo_input), context, redactor, mapping)
        except ValidationError as exc:
            _record(session, task=task, tier=spec.tier, model=model, started=started, attempt=1, ok=False, code=BAD_OUTPUT)
            raise ApiError(500, BAD_OUTPUT, f"The demo responder for {task} doesn't match its schema.") from exc
        _record(session, task=task, tier=spec.tier, model=model, started=started, attempt=1, ok=True)
        return out

    models = [saved["models"][spec.tier], *cfg.fallbacks.get(spec.tier, [])]
    ceiling = spec.max_tokens
    headroom = _HEADROOM[spec.reasoning]
    chain = _chain(models, Output, base_url=saved["baseUrl"], max_tokens=ceiling, reasoning=spec.reasoning, headroom=headroom)
    messages = template(task).format_messages(**prompt_vars)
    errors: str | None = None

    def cut_off(*, model: str, usage: Any, reported: float | None, started: float, attempt: int) -> bool:
        """Records a cut-off reply and says whether to try again with more room.

        Both the SDK's `LengthFinishReasonError` and a `finish_reason` of "length" mean the same
        thing, and which one arrives depends on the provider and the structured-output method,
        so they share this.
        """
        nonlocal ceiling, headroom, chain, errors
        _record(
            session,
            task=task,
            tier=spec.tier,
            model=model,
            started=started,
            attempt=attempt,
            ok=False,
            code=TRUNCATED,
            usage=usage,
            reported_cost=reported,
        )
        logfire.warn("{task} was cut off at {tokens} tokens", task=task, tokens=ceiling + headroom)
        if attempt > 1:
            return False
        # Both, not just the ceiling: a reply cut off because the *thinking* ran long is not
        # helped by more room for the answer.
        ceiling *= TRUNCATION_RETRY_FACTOR
        headroom *= TRUNCATION_RETRY_FACTOR
        chain = _chain(models, Output, base_url=saved["baseUrl"], max_tokens=ceiling, reasoning=spec.reasoning, headroom=headroom)
        errors = None  # nothing was wrong with the shape; the reply never got as far as a shape
        return True

    for attempt in (1, 2):
        started = time.monotonic()
        turn = [*messages, ("human", RETRY_NOTE.format(errors=errors))] if errors else messages
        try:
            result: Any = chain.invoke(turn)
        except openai.LengthFinishReasonError as exc:
            # Cut off mid-answer, but generated and billed in full — so its tokens are recorded,
            # and it gets one more pass with a bigger ceiling.
            if cut_off(model=models[0], usage=_usage_from(exc), reported=None, started=started, attempt=attempt):
                continue
            raise _fail(TRUNCATED) from None
        except (OutputParserException, ValidationError, JSONDecodeError) as exc:
            # The provider answered; it just didn't answer in the required shape. That is a bad
            # reply to retry, not a provider WERA couldn't reach.
            result, problem = None, exc
        except BaseException as exc:  # noqa: BLE001 — every provider failure becomes a WERA code
            code = _code_for(exc)
            _record(session, task=task, tier=spec.tier, model=models[0], started=started, attempt=attempt, ok=False, code=code)
            logfire.warn("{task} failed upstream: {code}", task=task, code=code)
            raise _fail(code) from None

        if result is None:  # the parse failed above; skip straight to the retry
            # This reply was generated and billed, but when the provider SDK raises during its
            # own parse the response body is gone with it, so there is usually no usage to read
            # and the row records zero. That is a known undercount in the spend figure, kept
            # small by prompting so the shape is right first time (see `REASON_LIMIT`): when
            # every fit.score reply overran its reason limit, this path alone made WERA's
            # recorded spend read 2.2x low against the provider's own figure.
            _record(
                session,
                task=task,
                tier=spec.tier,
                model=models[0],
                started=started,
                attempt=attempt,
                ok=False,
                code=BAD_OUTPUT,
                usage=_usage_from(problem),
            )
            errors = _problems(problem)
            logfire.warn(
                "{task} returned an unusable shape on attempt {attempt}: {fields}",
                task=task,
                attempt=attempt,
                fields=_problem_fields(problem),
            )
            continue

        raw = result.get("raw") if isinstance(result, dict) else None
        usage = getattr(raw, "usage_metadata", None) or {}
        metadata = getattr(raw, "response_metadata", {}) or {}
        model = metadata.get("model_name") or models[0]

        if metadata.get("finish_reason") == "length":
            # Cut off, reported in the reply rather than raised. Same handling.
            if cut_off(model=model, usage=usage, reported=_reported_cost(metadata), started=started, attempt=attempt):
                continue
            raise _fail(TRUNCATED)

        parsed = result.get("parsed") if isinstance(result, dict) else result
        problem = result.get("parsing_error") if isinstance(result, dict) else None

        if problem is None and parsed is not None:
            try:
                out = _validate(Output, parsed, context, redactor, mapping)
            except ValidationError as exc:
                problem = exc
            else:
                _record(
                    session,
                    task=task,
                    tier=spec.tier,
                    model=model,
                    started=started,
                    attempt=attempt,
                    ok=True,
                    usage=usage,
                    reported_cost=_reported_cost(metadata),
                )
                return out

        # §8.5 step 1: one retry carrying the problems, then give up.
        _record(
            session,
            task=task,
            tier=spec.tier,
            model=model,
            started=started,
            attempt=attempt,
            ok=False,
            code=BAD_OUTPUT,
            usage=usage,
            reported_cost=_reported_cost(metadata),
        )
        errors = _problems(problem)
        logfire.warn(
            "{task} returned an unusable shape on attempt {attempt}: {fields}",
            task=task,
            attempt=attempt,
            fields=_problem_fields(problem),
        )

    raise _fail(BAD_OUTPUT)


def _problem_fields(problem: Any) -> str:
    """Which fields a model got wrong, for the log. Field paths and rule names only — no values,
    so a log line can't carry anything about the person (PRIV-03)."""
    if isinstance(problem, ValidationError):
        return ", ".join(sorted({f"{'.'.join(str(p) for p in e['loc'])} ({e['type']})" for e in problem.errors()})) or "unknown"
    return "reply was not valid JSON"


def _problems(problem: Any) -> str:
    """The validation errors, as a list the model can act on: the field, the rule it broke, and
    where a validator names it, the offending value — which is always something the model just
    sent, never anything read from the CV. So the retry adds no new information about the person.
    """
    if isinstance(problem, ValidationError):
        return "\n".join(f"- {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in problem.errors())
    return "- The reply was not valid JSON for the required shape."
