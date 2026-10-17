"""Live "Test connection" checks, one per provider (SET-02).

Each one makes the cheapest call that proves the credential works, and reports what the person
needs to know — for the model provider, that means the credit left on the key, since that is
the thing that actually stops WERA working.

Nothing here passes an upstream body, header or URL through (§9.4): a provider's reply becomes
one of WERA's codes and a sentence written here.
"""

import imaplib
from contextlib import suppress

import httpx

from ..errors import ApiError
from ..http import TIMEOUT
from ..schemas.api import TestResult
from ..settings import Settings, llm_provider_name


def _code(prefix: str, exc: BaseException) -> tuple[str, str]:
    """A provider failure as (code, message), with nothing quoted from upstream."""
    if isinstance(exc, httpx.TimeoutException):
        return f"{prefix}_TIMEOUT", "The provider didn't answer in time."
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return f"{prefix}_AUTH_FAILED", "The provider rejected the key."
        if status == 402:
            return f"{prefix}_QUOTA", "The account is out of credit."
        if status == 429:
            return f"{prefix}_RATE_LIMITED", "The provider is rate-limiting WERA. Try again shortly."
        if status >= 500:
            return f"{prefix}_UNREACHABLE", "The provider is having trouble. Try again shortly."
        return f"{prefix}_UNREACHABLE", "The provider refused the request."
    return f"{prefix}_UNREACHABLE", "WERA couldn't reach the provider."


def _money(value: object) -> str | None:
    return f"${float(value):.2f}" if isinstance(value, int | float) else None


def check_llm(env: Settings) -> TestResult:
    """OpenRouter exposes the key's own limit and usage; any other endpoint gets /models."""
    base = env.llm_base_url.rstrip("/")
    key = env.llm_api_key.get_secret_value() if env.llm_api_key else ""
    headers = {"authorization": f"Bearer {key}"}
    provider = llm_provider_name(env.llm_base_url)
    openrouter = "openrouter.ai" in base
    url = f"{base}/key" if openrouter else f"{base}/models"
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"user-agent": "WERA/0.1"}) as client:
            res = client.get(url, headers=headers)
            res.raise_for_status()
            body = res.json() if res.content else {}
    except Exception as exc:  # noqa: BLE001 — every failure becomes a WERA code
        code, message = _code("LLM", exc)
        return TestResult(ok=False, code=code, message=f"{provider}: {message}")

    if not openrouter:
        count = len(body.get("data") or []) if isinstance(body, dict) else 0
        return TestResult(ok=True, message=f"{provider} answered. {count} models available.")

    data = body.get("data") or {} if isinstance(body, dict) else {}
    limit, usage = _money(data.get("limit")), _money(data.get("usage"))
    if limit is None:
        return TestResult(ok=True, message=f"{provider} accepted the key. No spending limit set on it.")
    left = _money(max(0.0, float(data.get("limit") or 0) - float(data.get("usage") or 0)))
    return TestResult(ok=True, message=f"{provider} accepted the key. {left} left of {limit} (used {usage}).")


def check_firecrawl(env: Settings) -> TestResult:
    key = env.firecrawl_api_key.get_secret_value() if env.firecrawl_api_key else ""
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"user-agent": "WERA/0.1"}) as client:
            res = client.get("https://api.firecrawl.dev/v2/team/credit-usage", headers={"authorization": f"Bearer {key}"})
            res.raise_for_status()
            body = res.json() if res.content else {}
    except Exception as exc:  # noqa: BLE001
        code, message = _code("FIRECRAWL", exc)
        return TestResult(ok=False, code=code, message=f"Firecrawl: {message}")

    data = body.get("data") or {} if isinstance(body, dict) else {}
    remaining = data.get("remainingCredits")
    if isinstance(remaining, int | float):
        return TestResult(ok=True, message=f"Firecrawl accepted the key. {int(remaining):,} credits left.")
    return TestResult(ok=True, message="Firecrawl accepted the key.")


def check_inbox(env: Settings) -> TestResult:
    """Logs in, opens the alerts folder read-only, and logs out again (INBOX-*)."""
    host, user = env.alerts_imap_host, env.alerts_imap_user
    password = env.alerts_imap_app_password.get_secret_value() if env.alerts_imap_app_password else ""
    if not host or not user:
        return TestResult(ok=False, code="INBOX_NOT_CONFIGURED", message="Set ALERTS_IMAP_HOST and ALERTS_IMAP_USER first.")
    box: imaplib.IMAP4_SSL | None = None
    try:
        box = imaplib.IMAP4_SSL(host, timeout=20)
        box.login(user, password)
        status, detail = box.select(env.alerts_folder, readonly=True)
        if status != "OK":
            return TestResult(
                ok=False,
                code="INBOX_FOLDER_MISSING",
                message=f"Signed in, but the folder {env.alerts_folder!r} isn't there. Create it, or change ALERTS_FOLDER.",
            )
        count = int(detail[0] or 0) if detail and detail[0] else 0
        return TestResult(ok=True, message=f"Signed in and opened {env.alerts_folder}. {count} messages in it.")
    except imaplib.IMAP4.error:
        # The server's own text can name the account, so it isn't passed through.
        return TestResult(ok=False, code="INBOX_AUTH_FAILED", message="The mail server rejected the username or app password.")
    except (TimeoutError, OSError):
        return TestResult(ok=False, code="INBOX_UNREACHABLE", message=f"WERA couldn't reach {host}.")
    finally:
        if box is not None:
            # Closing a connection that already failed is not itself a failure.
            with suppress(Exception):
                box.logout()


CHECKS = {"llm": check_llm, "firecrawl": check_firecrawl, "inbox": check_inbox}


def check(provider: str, env: Settings) -> TestResult:
    try:
        run = CHECKS[provider]
    except KeyError:
        raise ApiError(404, "NOT_FOUND", "Unknown provider.") from None
    return run(env)
