"""Where discovered postings wait for the Harvester (SRCH-20, between stages 2 and 3).

The Strategist finds more postings than one search should read — the 8–12 rule (SRCH-26) stops
reading at twelve — so the rest wait here, and "Find more" serves them without paying to search
again. Redis rather than SQLite because none of it is a record: a queue lasts a day, and a
posting the Harvester turned down is remembered for a week so discovery doesn't queue it again.

Three kinds of key, all under `wera:`:

* `wera:targets:<plan>` — the queue, a list of postings best first, for one plan. The plan key is
  the CV version and the profile fingerprint, so saving a new CV starts an empty queue and the
  last CV's postings are never read (`retire_others` deletes them).
* `wera:queued:<plan>` — the same postings as a set of URL hashes, so a second discovery pass
  doesn't queue a posting twice.
* `wera:seen:<hash>` — a posting the Harvester rejected, and why, for seven days.

Demo mode never connects (DEMO-02), and an unreachable Redis stops a search before anything is
spent rather than partway through it.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

import logfire

from ..domain.dedupe import canonical_url
from ..errors import ApiError
from ..settings import get_settings

PREFIX = "wera:"
QUEUE_TTL_SECONDS = 24 * 60 * 60
SEEN_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class Target:
    """One posting, as discovery found it. Everything the Harvester needs, and nothing it reads."""

    url: str
    host: str
    source: str  # "open" — a Google search; "board" — a `site:` search of a board in sites.yaml
    query: str
    rank: int  # 0 is best: tier, then query, then the result's position
    title: str
    snippet: str
    date: str | None


def plan_key(cv_version: int, profile_key: str) -> str:
    return f"{cv_version}-{profile_key}"


def url_key(url: str) -> str:
    return hashlib.sha256((canonical_url(url) or url).encode()).hexdigest()[:24]


def connect() -> Any:
    """A Redis client, checked. Raises `REDIS_DOWN` before anything is spent if it won't answer."""
    if get_settings().wera_demo_mode:
        raise ApiError(409, "DEMO_MODE", "Demo mode makes no outbound requests, Redis included.")
    import redis

    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=5)
    try:
        client.ping()
    except redis.RedisError:
        raise ApiError(503, "REDIS_DOWN", "Start Redis to search: `brew services start redis` (or set REDIS_URL).") from None
    return client


class TargetQueue:
    """One plan's queue of postings. `client` is anything speaking redis-py (tests use fakeredis)."""

    def __init__(self, client: Any, plan: str) -> None:
        self.r = client
        self.plan = plan
        self.queue = f"{PREFIX}targets:{plan}"
        self.queued = f"{PREFIX}queued:{plan}"
        self.tiers = f"{PREFIX}tiers:{plan}"

    def push(self, targets: list[Target]) -> int:
        """Append in order, skipping postings already queued for this plan or recently rejected.
        Returns how many were added. Both keys' day starts again with each push."""
        added = 0
        for t in targets:
            key = url_key(t.url)
            if self.r.exists(f"{PREFIX}seen:{key}"):
                continue
            if not self.r.sadd(self.queued, key):
                continue
            self.r.rpush(self.queue, json.dumps(asdict(t)))
            added += 1
        if added:
            self.r.expire(self.queue, QUEUE_TTL_SECONDS)
            self.r.expire(self.queued, QUEUE_TTL_SECONDS)
        return added

    def take(self, n: int) -> list[Target]:
        """The next `n` postings, best first, removed from the queue."""
        out: list[Target] = []
        for _ in range(max(n, 0)):
            raw = self.r.lpop(self.queue)
            if raw is None:
                break
            try:
                out.append(Target(**json.loads(raw)))
            except (TypeError, ValueError):
                continue  # a malformed entry is dropped, not retried for ever
        return out

    def put_back(self, targets: list[Target]) -> int:
        """Return postings to the front of the queue, in order — read later rather than lost, when a
        cap stopped this run paying for them. Still counted as queued, so `push` won't add a copy."""
        for t in reversed(targets):
            self.r.lpush(self.queue, json.dumps(asdict(t)))
        if targets:
            self.r.expire(self.queue, QUEUE_TTL_SECONDS)
        return len(targets)

    def peek(self, n: int = 50) -> list[Target]:
        return [Target(**json.loads(raw)) for raw in self.r.lrange(self.queue, 0, max(n, 1) - 1)]

    def size(self) -> int:
        return int(self.r.llen(self.queue))

    def tiers_sent(self) -> set[int]:
        """The query tiers already sent for this plan today — what "Find more" moves past."""
        return {int(t) for t in self.r.smembers(self.tiers)}

    def mark_tier_sent(self, tier: int) -> None:
        self.r.sadd(self.tiers, str(tier))
        self.r.expire(self.tiers, QUEUE_TTL_SECONDS)

    def reject(self, url: str, reason: str) -> None:
        """The Harvester turned this posting down: don't queue it again for a week."""
        self.r.set(f"{PREFIX}seen:{url_key(url)}", reason, ex=SEEN_TTL_SECONDS)

    def is_rejected(self, url: str) -> bool:
        return bool(self.r.exists(f"{PREFIX}seen:{url_key(url)}"))

    def retire_others(self) -> int:
        """Delete every other plan's queue: a new CV or profile means their postings were found for
        a different question. Returns how many keys went."""
        mine = {self.queue, self.queued, self.tiers}
        patterns = (f"{PREFIX}targets:*", f"{PREFIX}queued:*", f"{PREFIX}tiers:*")
        stale = [k for pattern in patterns for k in self.r.scan_iter(match=pattern) if k not in mine]
        if stale:
            self.r.delete(*stale)
            logfire.info("Retired {count} queue key(s) from an earlier plan", count=len(stale))
        return len(stale)


def clear_all(client: Any) -> int:
    """Every `wera:` key — the queues and the rejections. For "Clear jobs" (SRCH-27)."""
    keys = list(client.scan_iter(match=f"{PREFIX}*"))
    if keys:
        client.delete(*keys)
    return len(keys)
