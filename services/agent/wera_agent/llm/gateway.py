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
from typing import Any

import logfire
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from ..clock import iso, now_ms
from ..config import Effort, wera
from ..db import state
from ..db.models import LlmCallRow
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
BAD_OUTPUT = "LLM_BAD_OUTPUT"
UPSTREAM = "LLM_UPSTREAM"
BUDGET = "BUDGET_REACHED"

MESSAGES = {
    AUTH: "The model provider rejected the API key. Check LLM_API_KEY in services/agent/.env.local.",
    QUOTA: "The model provider is out of credit. Top up the account, then try again.",
    RATE: "The model provider is rate-limiting WERA. Wait a moment and try again.",
    TIMEOUT: "The model took too long to answer.",
    BAD_OUTPUT: "The model didn't answer in the shape WERA needs, twice. Nothing was saved.",
    UPSTREAM: "The model provider couldn't be reached.",
    BUDGET: "This week's budget is used up. Raise it in Settings, or wait for the week to roll over.",
}

# Reasoning effort per tier, as OpenRouter expects it (SPEC §9.3 "Reasoning" column).
_REASONING: dict[Effort, dict[str, Any] | None] = {
    "off": None,
    "low": {"effort": "low"},
    "on": {"effort": "high"},
}


def _code_for(exc: BaseException) -> str:
    """Maps a provider exception to a WERA code by class name, so the same mapping works for
    the OpenRouter SDK, the OpenAI SDK and a proxy in between."""
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


def make_chat(model: str, *, base_url: str, max_tokens: int, reasoning: Effort) -> BaseChatModel:
    """One configured model. ARCHITECTURE §3.3: OpenRouter when that's the endpoint, any
    OpenAI-compatible endpoint otherwise — including a local PII gateway."""
    cfg = wera()
    env = get_settings()
    key = env.llm_api_key.get_secret_value() if env.llm_api_key else ""
    common: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,  # the same job should score the same way twice
        "timeout": cfg.llm_timeout_seconds,
        "max_retries": cfg.llm_max_retries,
    }
    provider = {"require_parameters": True, "data_collection": "deny"}  # PRIV: no provider training

    if env.llm_proxy_url:
        # A PII gateway in front of OpenRouter. Its options ride in extra_body so they pass
        # through unchanged (the proxy speaks the OpenAI protocol, not OpenRouter's).
        from langchain_openai import ChatOpenAI

        extra: dict[str, Any] = {"provider": provider}
        if _REASONING[reasoning]:
            extra["reasoning"] = _REASONING[reasoning]
        return ChatOpenAI(base_url=env.llm_proxy_url, api_key=key, extra_body=extra, **common)

    if "openrouter.ai" in base_url:
        from langchain_openrouter import ChatOpenRouter

        return ChatOpenRouter(
            openrouter_api_key=key,
            openrouter_api_base=base_url,
            app_title="WERA",
            openrouter_provider=provider,
            reasoning=_REASONING[reasoning],
            **common,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(base_url=base_url, api_key=key, **common)


def _chain[Out: BaseModel](
    models: list[str], Output: type[Out], *, base_url: str, max_tokens: int, reasoning: Effort
) -> Runnable:
    """The tier's model with the configured fallbacks behind it. Each one is asked for the same
    schema, so a fallback answer is as usable as the first."""

    def one(model: str) -> Runnable:
        chat = make_chat(model, base_url=base_url, max_tokens=max_tokens, reasoning=reasoning)
        # strict=True sends the schema to the provider; `include_raw` keeps the token counts and
        # turns a parse failure into a value we can retry on instead of an exception.
        return chat.with_structured_output(Output, method="json_schema", strict=True, include_raw=True)

    first, *rest = models
    chain = one(first)
    return chain.with_fallbacks([one(m) for m in rest]) if rest else chain


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
) -> float:
    """Writes the llm_calls row and returns what the call cost (PRIV-03: metadata only)."""
    tokens_in = int((usage or {}).get("input_tokens") or 0)
    tokens_out = int((usage or {}).get("output_tokens") or 0)
    cost = cost_usd(model, tokens_in, tokens_out)
    session.add(
        LlmCallRow(
            task=task,
            tier=tier,
            model=model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
            ok=ok,
            code=code,
            attempt=attempt,
            called_at=iso(now_ms()),
        )
    )
    session.flush()
    return cost


def _redactor(session: Session, redact: bool) -> Redactor:
    return ContactRedactor(state.contact(session)) if redact else NullRedactor()


def _validate[Out: BaseModel](Output: type[Out], data: Any, context: dict[str, Any] | None) -> Out:
    """Re-checks the reply with the master CV in context.

    LangChain parses into `Output` without context, which the context-aware validators treat as
    "nothing to check against". This second pass is where the CV rules actually bite.
    """
    raw = data.model_dump(by_alias=True) if isinstance(data, BaseModel) else data
    return Output.model_validate(raw, context=context)


def structured[Out: BaseModel](
    session: Session,
    task: str,
    Output: type[Out],
    variables: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
) -> Out:
    """Runs one §9.3 task and returns a validated object, or raises `ApiError` with a §9.4 code."""
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
    redactor = _redactor(session, bool(saved.get("redactContact", True)))
    prompt_vars = {k: redactor.redact(v)[0] if isinstance(v, str) else v for k, v in variables.items()}

    if get_settings().wera_demo_mode:
        # DEMO-02: no network. Still validated against the real schema, and still recorded, so
        # the demo exercises the contract and the spend figure has something to show.
        started = time.monotonic()
        model = f"demo:{saved.get('models', {}).get(spec.tier, spec.tier)}"
        try:
            out = _validate(Output, demo.respond(task, prompt_vars, context), context)
        except ValidationError as exc:
            _record(session, task=task, tier=spec.tier, model=model, started=started, attempt=1, ok=False, code=BAD_OUTPUT)
            raise ApiError(500, BAD_OUTPUT, f"The demo responder for {task} doesn't match its schema.") from exc
        _record(session, task=task, tier=spec.tier, model=model, started=started, attempt=1, ok=True)
        return out

    models = [saved["models"][spec.tier], *cfg.fallbacks.get(spec.tier, [])]
    chain = _chain(models, Output, base_url=saved["baseUrl"], max_tokens=spec.max_tokens, reasoning=spec.reasoning)
    messages = template(task).format_messages(**prompt_vars)
    errors: str | None = None

    for attempt in (1, 2):
        started = time.monotonic()
        turn = [*messages, ("human", RETRY_NOTE.format(errors=errors))] if errors else messages
        try:
            result: Any = chain.invoke(turn)
        except BaseException as exc:  # noqa: BLE001 — every provider failure becomes a WERA code
            code = _code_for(exc)
            _record(session, task=task, tier=spec.tier, model=models[0], started=started, attempt=attempt, ok=False, code=code)
            logfire.warn("{task} failed upstream: {code}", task=task, code=code)
            raise _fail(code) from None

        raw = result.get("raw") if isinstance(result, dict) else None
        usage = getattr(raw, "usage_metadata", None) or {}
        model = (getattr(raw, "response_metadata", {}) or {}).get("model_name") or models[0]
        parsed = result.get("parsed") if isinstance(result, dict) else result
        problem = result.get("parsing_error") if isinstance(result, dict) else None

        if problem is None and parsed is not None:
            try:
                out = _validate(Output, parsed, context)
            except ValidationError as exc:
                problem = exc
            else:
                _record(session, task=task, tier=spec.tier, model=model, started=started, attempt=attempt, ok=True, usage=usage)
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
        )
        errors = _problems(problem)
        logfire.warn("{task} returned an unusable shape on attempt {attempt}", task=task, attempt=attempt)

    raise _fail(BAD_OUTPUT)


def _problems(problem: Any) -> str:
    """The validation errors, as a list the model can act on: the field, the rule it broke, and
    where a validator names it, the offending value — which is always something the model just
    sent, never anything read from the CV. So the retry adds no new information about the person.
    """
    if isinstance(problem, ValidationError):
        return "\n".join(f"- {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in problem.errors())
    return "- The reply was not valid JSON for the required shape."
