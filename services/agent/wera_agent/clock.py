"""One source of "now", in epoch milliseconds, so tests can move time (as Vitest's fake timers did)."""

import time
from datetime import UTC, datetime

_offset_ms = 0
_frozen_ms: int | None = None


def now_ms() -> int:
    if _frozen_ms is not None:
        return _frozen_ms + _offset_ms
    return int(time.time() * 1000) + _offset_ms


def iso(ms: float) -> str:
    """ISO 8601 in UTC with milliseconds and a Z, the same as JavaScript's toISOString()."""
    dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def freeze(at_ms: int | None = None) -> None:
    """Tests: stop the clock (at now, or at a given time)."""
    global _frozen_ms, _offset_ms
    _frozen_ms = at_ms if at_ms is not None else int(time.time() * 1000)
    _offset_ms = 0


def advance(ms: int) -> None:
    global _offset_ms
    _offset_ms += ms


def reset() -> None:
    global _frozen_ms, _offset_ms
    _frozen_ms = None
    _offset_ms = 0
