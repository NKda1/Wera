"""robots.txt, asked before any page WERA fetches for itself (§11.3).

Shared by the Strategist, which reads listing pages for their posting links, and the Harvester,
which reads postings. One reader per run, so a host's file is fetched once.

**Read as RFC 9309 says, which is how Google reads it.** The longest matching rule wins, `Allow`
wins a tie, `*` matches anything and `$` ends a path. This replaced `urllib.robotparser` (13 Nov),
which takes the *first* rule that matches and treats `*` literally: Indeed's file opens with
`Allow: /` and disallows `/viewjob` 170 lines later, so every Indeed posting read as allowed.

Before a search, the same verdicts say which boards to leave out of it (SRCH-30,
`refusing_hosts`): a `site:` query for a board WERA may not read buys results it must discard.
"""

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import logfire

from ..http import check

# A real browser's user agent, naming WERA too: honest about being automated without being the
# one string every bot rule matches on.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 WERA/0.1"
)
# The product token a robots.txt group would name WERA by. A group for it is obeyed over `*`.
PRODUCT = "wera"


@dataclass
class Rules:
    """One host's rules for WERA: the group naming it if there is one, else the `*` group."""

    rules: list[tuple[bool, re.Pattern[str], int]] = field(default_factory=list)

    @classmethod
    def parse(cls, lines: list[str]) -> "Rules":
        groups: list[tuple[set[str], list[tuple[str, str]]]] = []
        agents: set[str] = set()
        body: list[tuple[str, str]] = []
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if body:  # a user-agent after rules starts the next group
                    groups.append((agents, body))
                    agents, body = set(), []
                agents.add(value.lower())
            elif key in ("allow", "disallow") and agents:
                body.append((key, value))
        if agents:
            groups.append((agents, body))
        # RFC 9309 §2.2.1: every group naming the agent counts, combined; `*` only when none does.
        named = [b for a, b in groups if any(agent.split("/")[0] == PRODUCT for agent in a)]
        chosen = named or [b for a, b in groups if "*" in a]
        return cls([(key == "allow", _pattern(value), len(value)) for b in chosen for key, value in b if value])

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        if path == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None
        for allow, pattern, length in self.rules:
            if pattern.match(path) and (best is None or (length, allow) > best):
                best = (length, allow)
        return best is None or best[1]


def _pattern(value: str) -> re.Pattern[str]:
    """`*` is any run of characters and a trailing `$` the end of the path; the rest is literal."""
    end = value.endswith("$")
    body = ".*".join(re.escape(part) for part in (value[:-1] if end else value).split("*"))
    return re.compile(body + ("$" if end else ""))


class Robots:
    """robots.txt, read once per host per run. Unreadable is treated as "don't", which is the
    cautious failure: a page WERA can't ask about is a page it doesn't fetch."""

    def __init__(self) -> None:
        self._rules: dict[str, Rules | None] = {}

    def allows(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._rules:
            self._rules[host] = self._read(host)
        rules = self._rules[host]
        return rules is not None and rules.allows(url)

    @staticmethod
    def _read(host: str) -> Rules | None:
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
        if response.status_code == 404:
            return Rules()  # no robots.txt disallows nothing
        if response.status_code == 200:
            return Rules.parse(response.text.splitlines())
        return None


# ---------------------------------------------------------------------------
# SRCH-30: which hosts a search should leave out

# A verdict is a fact about a site's policy, which changes rarely: a week, like a rejection.
VERDICT_TTL_SECONDS = 7 * 24 * 60 * 60
VERDICT_KEY = "wera:robots:"


def refusing_hosts(probes: dict[str, str], *, redis: Any | None, robots: Robots | None = None, probe: bool = True) -> set[str]:
    """The hosts in `probes` (host → a posting URL there) whose robots.txt refuses that URL.

    Cached in Redis for a week. `probe=False` answers from the cache alone, for reads that must
    not touch the network (`GET /strategy`); a host never checked then counts as allowed, and the
    search that runs next checks it. Without Redis every host is asked again, which is correct
    and only slower.
    """
    rules = robots or Robots()
    refusing: set[str] = set()
    for host, url in probes.items():
        key = f"{VERDICT_KEY}{host}"
        cached = _get(redis, key)
        if cached is None:
            if not probe:
                continue
            try:
                allowed = rules.allows(url)
            except Exception:  # noqa: BLE001 — a robots file WERA can't read is a "don't"
                allowed = False
            cached = "allow" if allowed else "refuse"
            _set(redis, key, cached)
            if not allowed:
                logfire.info("{host}'s robots.txt refuses WERA; leaving it out of searches", host=host)
        if cached == "refuse":
            refusing.add(host)
    return refusing


def _get(redis: Any | None, key: str) -> str | None:
    if redis is None:
        return None
    try:
        return redis.get(key)
    except Exception:  # noqa: BLE001 — a cache that won't answer is a cache miss
        return None


def _set(redis: Any | None, key: str, value: str) -> None:
    if redis is None:
        return
    with contextlib.suppress(Exception):  # a cache that won't take a write costs a re-check
        redis.set(key, value, ex=VERDICT_TTL_SECONDS)


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
