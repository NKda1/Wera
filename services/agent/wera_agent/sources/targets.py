"""Where discovered postings wait for the Harvester (SRCH-20, between stages 2 and 3).

The Strategist finds more postings than one search should read — the 8–12 rule (SRCH-26) stops
reading at twelve — so the rest wait here, and "Find more" serves them without paying to search
again. Redis rather than SQLite because none of it is a record: a queue lasts a day, and a
posting the Harvester turned down is remembered for a week so discovery doesn't queue it again.

The kinds of key, all under `wera:`:

* `wera:pool:<pool>` — the queue: postings in a sorted set, scored by how well each matches the
  CV (17 Nov). The pool key is the CV version and what the person looks for — titles, level —
  but not *where*: a posting found for Zürich waits in the pool when Zürich is unticked, and is
  read again if it comes back, and with several places the best match is read first wherever it
  is (`take(accept=…)`). A new CV starts an empty pool (`retire_others`).
* `wera:queued:<pool>` — the same postings as a set of URL hashes, so a second discovery pass
  doesn't queue a posting twice.
* `wera:tiers:<plan>`, `wera:postings:<plan>` — which tiers of *this* plan were sent and what they
  found; the plan key includes where, since a new place is new searches.
* `wera:seen:<hash>` — a posting the Harvester rejected, and why, for seven days.

Demo mode never connects (DEMO-02), and an unreachable Redis stops a search before anything is
spent rather than partway through it.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
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
    # "open" — a Google search; "board" — a `site:` search of a board in sites.yaml; "feed" — a
    # full-text job API (SRCH-31), read from the API rather than its page.
    source: str
    query: str
    rank: int  # 0 is best: tier, then query, then the result's position
    title: str
    snippet: str
    date: str | None
    # The job API it came from ("reed", "adzuna"), also for a snippet API's lead whose original
    # posting was found (SRCH-32). Shown on the job as "via Reed". Defaulted, so postings queued
    # before these fields existed still load.
    via: str | None = None
    # What a feed's own reader needs: Reed's job id, USAJOBS' whole posting.
    payload: dict[str, Any] | None = None
    # The country it was searched for (a query's `gl`, lower case), so a pool kept across a change
    # of where can serve only the places now ticked. None when a search had no country.
    country: str | None = None


# How a pool scores postings: (title, snippet) pairs to scores, higher better. `prefilter.lead_ranker`.
type Ranker = Callable[[list[tuple[str, str]]], list[float]]


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


def source_of(target: Target) -> str:
    """Which source found a posting, for the share each may take of a search (SRCH-26): the job
    API that found it, else the search engine — Google through Serper, or Firecrawl's."""
    if target.via:
        return target.via
    return "firecrawl" if target.query.startswith("firecrawl:") else "google"


@dataclass
class Balance:
    """A search's reads so far, and the two soft limits on them (SRCH-26, 18 Nov).

    The best match is read first, within: a floor for each ticked country (about `per_run ÷
    countries` of the reads, so a place with weaker matches still gets its share) and a ceiling for
    each source (`ceiling` of the reads, so one API can't fill a search). Both give way rather than
    leave a slot empty: a country with nothing left gives its share up, and a source over its
    ceiling is read when nothing else is.
    """

    countries: list[str]
    per_run: int
    ceiling: float = 0.4
    by_country: dict[str, int] = field(default_factory=dict)
    by_source: dict[str, int] = field(default_factory=dict)
    # Ticked countries that were under their floor with nothing of theirs left to read (19 Nov).
    dry: set[str] = field(default_factory=set)

    @property
    def floor(self) -> int:
        return self.per_run // len(self.countries) if len(self.countries) > 1 else 0

    def note(self, target: Target) -> None:
        if target.country:
            self.by_country[target.country] = self.by_country.get(target.country, 0) + 1
        source = source_of(target)
        self.by_source[source] = self.by_source.get(source, 0) + 1

    def over_ceiling(self, target: Target, taking: int) -> bool:
        if len(self.by_source) == 0 and taking <= 1:
            return False
        limit = max(1, int(self.ceiling * max(self.per_run, sum(self.by_source.values()) + taking)))
        return self.by_source.get(source_of(target), 0) >= limit

    def choose(self, ordered: list[Target], n: int) -> list[Target]:
        """Up to `n` of `ordered` (best match first), honouring the floors, then the ceilings."""
        chosen: list[Target] = []
        left = list(ordered)
        while len(chosen) < n and left:
            pick = None
            # A ticked country under its floor goes first: its best posting, if it has one.
            behind = [c for c in self.countries if self.by_country.get(c, 0) < self.floor]
            for country in sorted(behind, key=lambda c: self.by_country.get(c, 0)):
                pick = next((t for t in left if t.country == country and not self.over_ceiling(t, 1)), None)
                if pick is not None:
                    break
                if not any(t.country == country for t in left):
                    self.dry.add(country)  # not just held back by a source's ceiling
            if pick is None:
                pick = next((t for t in left if not self.over_ceiling(t, 1)), None)
            if pick is None:
                # Every source at its ceiling (two sources can't both stay under 40% of a search):
                # the least-read source's best posting, so they take turns rather than one winning.
                pick = min(left, key=lambda t: self.by_source.get(source_of(t), 0))
            left.remove(pick)
            chosen.append(pick)
            self.note(pick)
        return chosen

    def short(self) -> list[tuple[str, int, int]]:
        """The countries that ended under their floor because they ran dry: (country, got, floor).

        The floor gives way quietly when a country has nothing left to read — on 18 Nov the US got
        2 of its 3 reads in two runs, and nothing said why. A country still under its floor that
        never ran dry (the search stopped first) isn't short of anything.
        """
        return [
            (c, self.by_country.get(c, 0), self.floor)
            for c in self.countries
            if c in self.dry and self.by_country.get(c, 0) < self.floor
        ]

    def split(self) -> str:
        """The reads so far as the run's log says them: "ch 4, gb 4 · google 5, reed 3", then any
        country that ran dry: "· us 2 of 3, none left to read"."""
        places = ", ".join(f"{c} {n}" for c, n in sorted(self.by_country.items(), key=lambda x: -x[1]))
        sources = ", ".join(f"{s} {n}" for s, n in sorted(self.by_source.items(), key=lambda x: -x[1]))
        dry = ", ".join(f"{c} {got} of {floor}" for c, got, floor in self.short())
        return " · ".join(part for part in (places, sources, dry and f"{dry}, none left to read") if part)


class TargetQueue:
    """A pool of postings, and one plan's record of what it sent. `client` is anything speaking
    redis-py (tests use fakeredis).

    Without a `rank`, postings are read in the order they came. With one, they are ranked when they
    are read, against the reader's CV — not when they arrive — so a pool shared by an industry holds
    nothing about any person and each person still reads their own best matches first (18 Nov).
    With a `reader`, what that person has read is kept apart from the pool (`wera:read:<reader>`)
    instead of being removed from it, so the pool stays whole for the next search, and the next
    person.
    """

    def __init__(
        self, client: Any, plan: str, *, pool: str | None = None, rank: Ranker | None = None, reader: str | None = None
    ) -> None:
        self.r = client
        self.plan = plan
        self.pool = pool or plan
        self.rank = rank
        self.reader = f"{PREFIX}read:{reader}" if reader else None
        self.queue = f"{PREFIX}pool:{self.pool}"
        self.queued = f"{PREFIX}queued:{self.pool}"
        self.order = f"{PREFIX}seq:{self.pool}"
        self.tiers = f"{PREFIX}tiers:{plan}"
        self._scores: dict[str, float] = {}

    def push(self, targets: list[Target]) -> int:
        """Add to the pool in arrival order, skipping postings already in it or recently rejected.
        Returns how many were added."""
        fresh: list[Target] = []
        for t in targets:
            key = url_key(t.url)
            if self.r.exists(f"{PREFIX}seen:{key}") or not self.r.sadd(self.queued, key):
                continue
            fresh.append(t)
        for t in fresh:
            # First come, first in the arrival order; a rank, when there is one, decides at read.
            self.r.zadd(self.queue, {json.dumps(asdict(t)): -float(self.r.incr(self.order))})
        if fresh:
            for key in (self.queue, self.queued, self.order):
                self.r.expire(key, QUEUE_TTL_SECONDS)
        return len(fresh)

    def _members(self, n: int = -1) -> list[str]:
        return list(self.r.zrevrange(self.queue, 0, n))

    def _waiting(self, accept: Callable[[Target], bool] | None) -> tuple[list[tuple[str, Target]], list[str]]:
        """The pool's postings this reader hasn't read and `accept` takes, best first; and the
        malformed entries to drop."""
        read = set(self.r.smembers(self.reader)) if self.reader else set()
        waiting: list[tuple[str, Target]] = []
        broken: list[str] = []
        for raw in self._members():
            try:
                target = Target(**json.loads(raw))
            except (TypeError, ValueError):
                broken.append(raw)  # a malformed entry is dropped, not retried for ever
                continue
            if url_key(target.url) in read or (accept is not None and not accept(target)):
                continue
            waiting.append((raw, target))
        if self.rank is not None and waiting:
            unscored = [(raw, t) for raw, t in waiting if raw not in self._scores]
            if unscored:
                scores = self.rank([(t.title, t.snippet) for _raw, t in unscored])
                self._scores.update({raw: score for (raw, _t), score in zip(unscored, scores, strict=True)})
            # Stable: equal matches keep the pool's own order (arrival, or put back to the front).
            waiting.sort(key=lambda item: -self._scores[item[0]])
        return waiting, broken

    def take(self, n: int, accept: Callable[[Target], bool] | None = None, balance: Balance | None = None) -> list[Target]:
        """The best `n` postings `accept` takes, within `balance`'s shares; the rest stay."""
        waiting, broken = self._waiting(accept)
        targets = [t for _raw, t in waiting]
        chosen = balance.choose(targets, max(n, 0)) if balance is not None else targets[: max(n, 0)]
        raws = {id(t): raw for raw, t in waiting}
        taken = [raws[id(t)] for t in chosen]
        if self.reader:
            if chosen:
                self.r.sadd(self.reader, *[url_key(t.url) for t in chosen])
                self.r.expire(self.reader, SEEN_TTL_SECONDS)
            if broken:
                self.r.zrem(self.queue, *broken)
        elif taken or broken:
            self.r.zrem(self.queue, *taken, *broken)
        return chosen

    def put_back(self, targets: list[Target]) -> int:
        """Return postings to the front of the pool, in order — read later rather than lost, when a
        cap stopped this run paying for them. Still counted as queued, so `push` won't add a copy."""
        if not targets:
            return 0
        if self.reader:
            self.r.srem(self.reader, *[url_key(t.url) for t in targets])
            return len(targets)
        top = self.r.zrevrange(self.queue, 0, 0, withscores=True)
        front = float(top[0][1]) if top else 0.0
        for i, t in enumerate(targets):
            self.r.zadd(self.queue, {json.dumps(asdict(t)): front + len(targets) - i})
        self.r.expire(self.queue, QUEUE_TTL_SECONDS)
        return len(targets)

    def peek(self, n: int = 50) -> list[Target]:
        """The next postings waiting for this reader, in the pool's order, without taking them."""
        if not self.reader:
            return [Target(**json.loads(raw)) for raw in self._members(max(n, 1) - 1)]
        rank, self.rank = self.rank, None
        try:
            return [t for _raw, t in self._waiting(None)[0][: max(n, 1)]]
        finally:
            self.rank = rank

    def size(self, accept: Callable[[Target], bool] | None = None) -> int:
        """Postings waiting for this reader, or only those `accept` takes."""
        if accept is None and not self.reader:
            return int(self.r.zcard(self.queue))
        rank, self.rank = self.rank, None  # a count needs no ranking
        try:
            return len(self._waiting(accept)[0])
        finally:
            self.rank = rank

    def note_postings(self, query: str, count: int) -> None:
        """How many postings a page-1 query returned today, known or new (SRCH-26, tier 4)."""
        key = f"{PREFIX}postings:{self.plan}"
        self.r.hset(key, query, count)
        self.r.expire(key, QUEUE_TTL_SECONDS)

    def postings_by_query(self) -> dict[str, int]:
        return {q: int(n) for q, n in (self.r.hgetall(f"{PREFIX}postings:{self.plan}") or {}).items()}

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
        """Delete every other pool and plan: a new CV or new titles mean their postings were found
        for a different question. Returns how many keys went. (`targets:*` is the list each plan
        kept before 17 Nov.)"""
        mine = {self.queue, self.queued, self.order, self.tiers, f"{PREFIX}postings:{self.plan}"}
        # A shared pool (one with readers) belongs to an industry, not a plan — later, to everyone
        # in it — so only this person's old plans go; pools expire on their own (`QUEUE_TTL_SECONDS`).
        pools = () if self.reader else (f"{PREFIX}pool:*", f"{PREFIX}queued:*", f"{PREFIX}seq:*")
        patterns = (*pools, f"{PREFIX}targets:*", f"{PREFIX}tiers:*", f"{PREFIX}postings:*")
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
