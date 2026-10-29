"""Logfire tracing, next to the redacting logger it reuses (ARCHITECTURE §7.2 #9, SPEC SEC-05).

Nothing is sent without a Logfire project token, so WERA runs exactly as before for anyone
who hasn't connected one. What is sent stays metadata: request spans keep the route, the
status and how many fields failed validation, and drop the payload itself, so job text, CV
content and contact details never leave this machine. Log records forwarded to Logfire pass
through the same `RedactingFilter` as the console ones.

Queries aren't traced yet: opentelemetry-instrumentation-sqlalchemy supports SQLAlchemy < 2.1.0
and this service pins 2.1.4, so its instrumentor refuses to attach.
"""

import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import logfire
from fastapi import FastAPI, Request

from .logging import RedactingFilter
from .paths import ROOT
from .settings import get_settings

SERVICE_NAME = "wera-agent"

_configured = False


def _service_version() -> str | None:
    try:
        return version("wera-agent")
    except PackageNotFoundError:
        return None


def _route_only(_request: Request, attributes: dict[str, Any]) -> dict[str, Any]:
    """Keep the shape of the request, not its contents: a count replaces the failed fields."""
    kept = {k: v for k, v in attributes.items() if k not in ("values", "errors")}
    kept["error_count"] = len(attributes.get("errors") or [])
    return kept


def configure(app: FastAPI | None = None) -> None:
    """Set up tracing for this process, and instrument `app` if there is one.

    `app` is None for the CLI. It used to be required, which meant `wera eval`, `wera discover`
    and `wera serve`'s own startup produced no spans at all — the only commands that make model
    calls, and the only ones nobody could trace. A CLI run is a process like any other; the
    difference is that it has no routes to instrument, not that it has nothing to say.
    """
    global _configured
    s = get_settings()
    if not _configured:
        logfire.configure(
            service_name=SERVICE_NAME,
            service_version=_service_version(),
            # Demo data and real data are worth keeping apart; this is the same split as `wera reset`.
            environment="demo" if s.wera_demo_mode else "real",
            send_to_logfire="if-token-present" if s.logfire_enabled else False,
            data_dir=ROOT / ".logfire",
            # Warnings and errors print to the terminal; spans and info lines don't, so `pnpm dev`
            # stays readable. Without this a drafting failure was visible only in Logfire, which
            # is no help to someone running WERA with no token.
            console=logfire.ConsoleOptions(min_log_level="warn", span_style="simple"),
        )
        _configured = True
        if s.logfire_enabled:
            handler = logfire.LogfireLoggingHandler()
            handler.addFilter(RedactingFilter())
            logging.getLogger().addHandler(handler)
    if s.logfire_enabled and app is not None:
        logfire.instrument_fastapi(app, request_attributes_mapper=_route_only)
