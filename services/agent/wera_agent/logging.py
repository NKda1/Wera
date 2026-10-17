"""One logger with a redaction filter (ARCHITECTURE §7.2 #9, SPEC SEC-05).

Anything that looks like a key or a bearer token is masked before a log line is written,
whatever the call site formatted into it.
"""

import logging
import re

_KEYS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bfc-[A-Fa-f0-9]{16,}"),
]
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNED = re.compile(
    r"(?i)\b(authorization|api[_-]?key|[A-Z_]*_(?:KEY|TOKEN|PASSWORD|SECRET))(\s*[:=]\s*)(?!bearer\b)([^\s,;]+)"
)


def redact(text: str) -> str:
    for p in _KEYS:
        text = p.sub("[redacted]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", text)
    return _ASSIGNED.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    logging.getLogger("alembic").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(RedactingFilter())
