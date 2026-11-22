"""SRCH-33: what WERA's Redis remembers in front of a paid or rate-limited provider.

Many searches at once must not mean many calls to the same provider. People's CVs differ but the
questions they ask don't much ("Graduate Software Engineer" in London), so four things are kept
per provider, in the Redis every process shares:

* **The answer to a query**, for `cache_minutes`, keyed on the query alone — no person, no plan.
  Two hundred people asking the same question make one call, and the next one hundred and
  ninety-nine are served from here at no cost.
* **Who is asking it right now** (a lock, `SET NX`). When twenty ask an unanswered question in the
  same moment, one fetches and the rest wait for its answer instead of each paying for it. A
  caller that waits `lock_seconds` without an answer asks itself: a lock only ever saves money,
  it never stops a search.
* **A breaker.** A provider that rate-limits WERA (429), or fails `failures` times inside a minute,
  is left alone by every caller for `open_seconds`. A search stops asking it at once and serves
  what it already has, rather than each caller learning the same thing at the cost of a timeout.
* **A meter of the day's use** (UTC days), every run and person counted. A search stops asking at
  `stop_at` of `daily`, leaving the rest for the scheduled runs.

The rules `crawl_cache` keeps hold here too. **An answer from here is never counted against a
cap** (SRCH-06): caps bound an invoice, and free work doesn't belong in them. **A Redis failure is
never a run failure:** every read and write swallows its own error, and without Redis the guard
does nothing, so the worst a broken Redis can do is make a search cost what it did before.

The answers and locks live under `wera:`, so "Clear jobs" (SRCH-27) clears them with the queues.
The breakers and meters live under `wera-meter:`, which it leaves alone: what bounds an invoice or
protects a provider mustn't reset because someone started their job hunt over.
"""

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import logfire

from ..config import GuardConfig, wera

ANSWERS = "wera:answer:"
LOCKS = "wera:lock:"
METER = "wera-meter:"
POLL_SECONDS = 0.1
# A day's count is kept a little past its day, so yesterday's can still be read in the morning.
METER_TTL_SECONDS = 3 * 24 * 60 * 60
# The providers a guard exists for, in the order `wera providers status` shows them.
PROVIDERS = ("serper", "reed", "usajobs", "adzuna", "careerjet", "reliefweb")


def answer_key(provider: str, *parts: Any) -> str:
    """One query, as the provider would be asked it. Equal queries get equal keys, whoever asks."""
    blob = json.dumps([provider, *parts], sort_keys=True, default=str, separators=(",", ":"))
    return f"{provider}:{hashlib.sha256(blob.encode()).hexdigest()[:24]}"


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass
class Guard:
    """One provider's answers, lock, breaker and meter. `redis` is anything speaking redis-py
    (tests use fakeredis); None makes every method a no-op that says "go ahead"."""

    redis: Any | None
    provider: str
    config: GuardConfig = field(default_factory=lambda: wera().guard)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    today: Callable[[], str] = _today

    # -- answers, and the caller fetching one ---------------------------------------------------

    def answer(self, key: str) -> Any | None:
        raw = self._call("get", f"{ANSWERS}{key}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def remember(self, key: str, value: Any) -> None:
        minutes = self.config.cache_minutes_by.get(self.provider, self.config.cache_minutes)
        self._call("set", f"{ANSWERS}{key}", json.dumps(value), ex=minutes * 60)

    def lead(self, key: str) -> str | None:
        """Take the right to fetch `key`. A token while it's held; None when another caller has it.
        Without Redis there is nobody else to wait for, so the caller always leads."""
        token = secrets.token_hex(8)
        if self.redis is None:
            return token
        taken = self._call("set", f"{LOCKS}{key}", token, nx=True, ex=self.config.lock_seconds, default=True)
        return token if taken else None

    def release(self, key: str, token: str) -> None:
        """Give it up, if it is still ours: a lock that expired may have passed to someone else."""
        if self._call("get", f"{LOCKS}{key}") == token:
            self._call("delete", f"{LOCKS}{key}")

    def wait_for(self, key: str) -> Any | None:
        """The answer another caller is fetching, once it lands. None if it lets go without one
        (its call failed) or holds on past `lock_seconds` — then the waiter asks for itself."""
        deadline = self.clock() + self.config.lock_seconds
        while self.clock() < deadline:
            if (found := self.answer(key)) is not None:
                return found
            if not self._call("exists", f"{LOCKS}{key}"):
                return self.answer(key)
            self.sleep(POLL_SECONDS)
        return None

    def shared(self, key: str, fetch: Callable[[], Any | None]) -> tuple[Any | None, str]:
        """The answer to `key`: remembered, waited for while another caller fetches it, or fetched.
        Returns it with how: "cached", "coalesced", "fetched", or "failed" when `fetch` gave None.
        Only an answer is remembered; a failure is never cached (SRCH-10's rule)."""
        if (known := self.answer(key)) is not None:
            return known, "cached"
        token = self.lead(key)
        if token is None:
            if (waited := self.wait_for(key)) is not None:
                return waited, "coalesced"
            token = self.lead(key)
        try:
            value = fetch()
        finally:
            if token is not None:
                self.release(key, token)
        if value is None:
            return None, "failed"
        self.remember(key, value)
        return value, "fetched"

    # -- the breaker ----------------------------------------------------------------------------

    def open_for(self) -> int:
        """Seconds until the breaker closes; 0 when it's closed."""
        ttl = self._call("ttl", f"{METER}breaker:{self.provider}", default=-2)
        return max(int(ttl), 0) if isinstance(ttl, int) else 0

    def why_open(self) -> str | None:
        return self._call("get", f"{METER}breaker:{self.provider}")

    def trip(self, why: str, seconds: int | None = None) -> None:
        seconds = max(int(seconds or self.config.breaker.open_seconds), 1)
        self._call("set", f"{METER}breaker:{self.provider}", why, ex=seconds)
        self._call("delete", f"{METER}fails:{self.provider}")
        logfire.warn("{provider} breaker open for {seconds}s: {why}", provider=self.provider, seconds=seconds, why=why)

    def failed(self, why: str) -> bool:
        """One more failure inside the window. True when it opened the breaker."""
        key = f"{METER}fails:{self.provider}"
        count = self._call("incr", key, default=0)
        if count == 1:
            self._call("expire", key, self.config.breaker.window_seconds)
        if isinstance(count, int) and count >= self.config.breaker.failures:
            self.trip(f"{count} failures in {self.config.breaker.window_seconds}s, the last {why}")
            return True
        return False

    def reset(self) -> None:
        self._call("delete", f"{METER}breaker:{self.provider}", f"{METER}fails:{self.provider}")

    # -- the day's meter ------------------------------------------------------------------------

    def count(self, n: int = 1) -> None:
        if n <= 0:
            return
        key = f"{METER}used:{self.provider}:{self.today()}"
        self._call("incrby", key, n)
        self._call("expire", key, METER_TTL_SECONDS)

    def used_today(self) -> int:
        raw = self._call("get", f"{METER}used:{self.provider}:{self.today()}")
        try:
            return int(raw or 0)
        except ValueError:
            return 0

    @property
    def daily(self) -> int | None:
        return self.config.daily.get(self.provider)

    def spent(self) -> bool:
        """The day's share for searches is used up: `stop_at` of `daily`."""
        return self.daily is not None and self.used_today() >= self.daily * self.config.stop_at

    def left_today(self) -> tuple[int, int] | None:
        """What a search may still use today, and of how much: (left, share), or None when the
        provider has no daily limit. The share is `stop_at` of `daily` — where `spent` stops a
        search — not the account's whole day, so "88 of 120 left" is what searches can really
        use (H4, 21 Nov). Read-only, like `used_today`; without Redis it reads as all of it."""
        if self.daily is None:
            return None
        share = int(self.daily * self.config.stop_at)
        return max(share - self.used_today(), 0), share

    # -------------------------------------------------------------------------------------------

    def _call(self, method: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
        if self.redis is None:
            return default
        try:
            return getattr(self.redis, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001 — a Redis that won't answer is a guard that does nothing
            return default


def status(redis: Any) -> list[dict[str, Any]]:
    """Each provider's breaker and today's use, for `wera providers status`."""
    rows = []
    for provider in PROVIDERS:
        guard = Guard(redis, provider)
        rows.append(
            {
                "provider": provider,
                "open_for": guard.open_for(),
                "why": guard.why_open(),
                "used_today": guard.used_today(),
                "daily": guard.daily,
                "answers": sum(1 for _ in redis.scan_iter(match=f"{ANSWERS}{provider}:*")),
            }
        )
    return rows
