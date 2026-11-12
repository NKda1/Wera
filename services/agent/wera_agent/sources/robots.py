"""robots.txt, asked before any page WERA fetches for itself (§11.3).

Shared by the Strategist, which reads listing pages for their posting links, and the Harvester,
which reads postings. One reader per run, so a host's file is fetched once.
"""

import re
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from ..http import check

# A real browser's user agent, naming WERA too: honest about being automated without being the
# one string every bot rule matches on.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 WERA/0.1"
)


class Robots:
    """robots.txt, read once per host per run. Unreadable is treated as "don't", which is the
    cautious failure: a page WERA can't ask about is a page it doesn't fetch."""

    def __init__(self) -> None:
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allows(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._parsers:
            self._parsers[host] = self._read(host)
        parser = self._parsers[host]
        return parser is not None and parser.can_fetch("*", url)

    @staticmethod
    def _read(host: str) -> urllib.robotparser.RobotFileParser | None:
        """Redirects followed within the same site — `example.com/robots.txt` commonly sends you
        to `www.example.com/robots.txt`, and refusing to follow it would read as "don't"."""
        url = f"https://{host}/robots.txt"
        check(url)
        site = host.lower().removeprefix("www.")
        try:
            with httpx.Client(timeout=15, follow_redirects=True, headers={"user-agent": USER_AGENT}) as client:
                response = client.get(url)
        except Exception:  # noqa: BLE001 — unreachable is a "don't", not a crash
            return None
        final = (response.url.host or "").lower().removeprefix("www.")
        if final != site and not final.endswith(f".{site}"):
            return None
        parser = urllib.robotparser.RobotFileParser()
        if response.status_code == 404:
            parser.parse([])  # no robots.txt disallows nothing
        elif response.status_code == 200:
            parser.parse(response.text.splitlines())
        else:
            return None
        return parser


# Statuses that mean "the site refused", as opposed to "the network failed". Never retried.
REFUSED = {401, 403, 407, 429, 503}
# Titles of the interstitials a bot-protected board serves instead of its page.
CHALLENGE = re.compile(
    r"just a moment|attention required|access denied|request could not be satisfied|captcha|are you a robot", re.I
)


def refusal(status: int | None, title: str) -> str | None:
    if status in REFUSED:
        return f"HTTP {status}"
    if CHALLENGE.search(title or ""):
        return "a bot-check page"
    return None
