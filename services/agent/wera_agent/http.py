"""The one outbound HTTP client (SEC-07).

Every request WERA makes to anything other than a model provider goes through here, so three
rules hold in one place:

* **LinkedIn is never fetched** (SRCH-03, MAIL-07). A LinkedIn URL is stored and shown to the
  person to open themselves; WERA does not scrape it, whatever config or an email says.
* **Links that came from email are checked against an allowlist**, because an alert email is
  untrusted input and a link in one can point anywhere (SEC-08).
* **Demo mode makes no outbound request at all** (DEMO-02), so the demo can't quietly depend
  on a network or a key.

Model calls don't come through here: LangChain owns that socket. The same denylist doesn't
apply there because a prompt is never a URL to fetch.
"""

from urllib.parse import urlparse

import httpx

from .errors import ApiError
from .settings import get_settings

DENY = ("linkedin.com",)  # SEC-07, non-negotiable

# Hosts an email-derived link may point at: the boards WERA knows and the ATS vendors.
EMAIL_ALLOW = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "brightnetwork.co.uk",
    "gradcracker.com",
    "welcometothejungle.com",
    "builtinlondon.uk",
    "indeed.com",
)

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith(f".{s}") for s in suffixes)


def is_denied(url: str) -> bool:
    return _matches(host_of(url), DENY)


def is_allowed_email_link(url: str) -> bool:
    return _matches(host_of(url), EMAIL_ALLOW)


def check(url: str, *, from_email: bool = False) -> None:
    """Raises before a request is made. Callers don't get to skip this."""
    host = host_of(url)
    if not host or urlparse(url).scheme not in ("http", "https"):
        raise ApiError(422, "VALIDATION", "That isn't a URL WERA can fetch.")
    if _matches(host, DENY):
        raise ApiError(422, "LINKEDIN_BLOCKED", "WERA never fetches LinkedIn. Open the link yourself and paste the description.")
    if from_email and not _matches(host, EMAIL_ALLOW):
        raise ApiError(422, "LINK_NOT_ALLOWED", f"Links from email are only followed to known job sites, not {host}.")
    if get_settings().wera_demo_mode:
        raise ApiError(409, "DEMO_MODE", "Demo mode makes no outbound requests. Turn it off in Settings to fetch live data.")


def request(method: str, url: str, *, from_email: bool = False, **kwargs) -> httpx.Response:
    """One checked request. The caller handles the status; nothing here retries."""
    check(url, from_email=from_email)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"user-agent": "WERA/0.1"}) as client:
        return client.request(method, url, **kwargs)
