"""SRCH-20 (21 Nov): which of a plan's searches a search sends, so the next one asks something new.

The plan is made once per CV, profile and model (`Store.search_queries`), and Google ranks a query
the same way all day. So every search sent the same queries in the same order, Serper's shared
answers (`provider_guard`, hours) handed back the same results, and on 20 Nov a second search the
same day found its postings "already saved" — the repetition the person saw.

Each planned search now carries other phrasings of itself (`PlannedQuery.variants`, written by
`strategist.queries`), and what a pool's searches sent is remembered here, in Redis:

* **`wera:asked:<pool>`**, a hash: one field per query as Google was asked it — country, language,
  words, page — holding when it was sent and how many postings it returned. The key lives
  `memory_days` (7) and every search refreshes it. The pool is `TargetQueue`'s: the industry and
  the level, so it is shared the way the postings found are, and nothing in it is about a person.
* **A search sent in the last `skip_days` (3) is not sent again.** If its page gave any posting,
  its next page goes instead, to `max_page` (3) — a query whose page 1 found jobs is the likeliest
  to find more on page 2; if it gave none, its next phrasing goes. A search with nothing left that
  wasn't asked lately is left out of this search, and comes back once its days have passed.
* **Tier order is kept.** One query per planned search, in the plan's order, so the Serper cap
  (`max_serper_queries`) cuts what it always cut, and a search spends no more than before.
* **Tier 4** (page 2 of tier 1 in the same search, for a page 1 rich in postings) follows what
  tier 1 actually sent: the next page of the same phrasing.

What this doesn't touch: the answer cache, which still makes the same query asked twice in an
afternoon free. Exclusions added after planning (`-site:` for a host whose robots.txt refuses,
`-intitle:` for a learned exclusion) are carried onto whichever phrasing is sent, and the
memory leaves them out, so a new refusing host doesn't make an old search look new.

A Redis failure is never a search failure: without a memory, the plan is sent as planned.
"""

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import logfire

from ..config import RotationConfig, wera
from ..schemas.strategy import PlannedQuery

ASKED = "wera:asked:"
DAY_MS = 24 * 60 * 60 * 1000
# The exclusions a search is given after planning (SRCH-30's `-site:`, PRF-06's `-intitle:`).
_LEFT_OUT = re.compile(r'(?<!\S)-(?:site|intitle|inurl):(?:"[^"]*"|\S+)')


def core(q: str) -> str:
    """A query as the memory knows it: its words and positive operators, without exclusions."""
    return " ".join(_LEFT_OUT.sub(" ", q).split())


def _with_exclusions(variant: str, planned: str) -> str:
    """A phrasing with the exclusions the planned search was given since it was planned."""
    has = set(_LEFT_OUT.findall(variant))
    missing = [t for t in dict.fromkeys(_LEFT_OUT.findall(planned)) if t not in has]
    return " ".join([variant, *missing])


def asked_field(gl: str, hl: str, q: str, page: int) -> str:
    return json.dumps([gl, hl, core(q), page], separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Record:
    at: int  # when it was sent, ms
    postings: int  # postings it returned, known or new


class Asked:
    """What one pool's searches asked Google lately. `redis` is anything speaking redis-py (tests
    use fakeredis); None, or a Redis that fails, remembers nothing and recalls nothing."""

    def __init__(self, redis: Any | None, pool: str, *, now: int, config: RotationConfig | None = None) -> None:
        self.r = redis
        self.key = f"{ASKED}{pool}"
        self.now = now
        self.config = config or wera().rotation

    def recall(self) -> dict[str, Record]:
        """Every search sent within `memory_days`; older ones are dropped from the hash on the way."""
        if self.r is None:
            return {}
        try:
            raw = self.r.hgetall(self.key) or {}
        except Exception:  # noqa: BLE001 — a memory that won't answer is a plan sent as planned
            return {}
        oldest = self.now - self.config.memory_days * DAY_MS
        out: dict[str, Record] = {}
        stale: list[str] = []
        for name, value in raw.items():
            try:
                data = json.loads(value)
                record = Record(at=int(data["at"]), postings=int(data.get("n", 0)))
            except (TypeError, ValueError, KeyError):
                stale.append(name)
                continue
            if record.at < oldest:
                stale.append(name)
            else:
                out[name] = record
        if stale:
            with contextlib.suppress(Exception):  # tidying only: a stale field is ignored anyway
                self.r.hdel(self.key, *stale)
        return out

    def note(self, query: PlannedQuery, postings: int) -> None:
        """A search was answered (by Serper, its shared answer, or Firecrawl standing in)."""
        if self.r is None:
            return
        try:
            self.r.hset(
                self.key, asked_field(query.gl, query.hl, query.q, query.page), json.dumps({"at": self.now, "n": postings})
            )
            self.r.expire(self.key, self.config.memory_days * 24 * 60 * 60)
        except Exception:  # noqa: BLE001 — forgetting a search costs one repeat, never the search
            logfire.warn("Could not remember a search sent", query=query.q)


@dataclass
class Rotated:
    """The searches this search sends, one per planned search that has something new, and how."""

    plan: list[PlannedQuery] = field(default_factory=list)
    as_planned: int = 0  # asked in the plan's own words, page 1
    rephrased: int = 0  # asked in another of its phrasings
    deeper: int = 0  # asked at a later page of a phrasing whose last page found postings
    rested: int = 0  # every phrasing asked lately: left out of this search


def rotate(planned: list[PlannedQuery], asked: dict[str, Record], *, now: int, config: RotationConfig | None = None) -> Rotated:
    """The plan as this search sends it: each planned search's first phrasing and page not asked
    in the last `skip_days`, in the plan's order (see the module's docstring)."""
    cfg = config or wera().rotation
    window = cfg.skip_days * DAY_MS
    out = Rotated()
    # A tier-1 search's planned words → the words and page it sends this search, for tier 4.
    sent_by: dict[tuple[str, str, str], tuple[str, int]] = {}

    def lately(q: PlannedQuery, text: str, page: int) -> Record | None:
        record = asked.get(asked_field(q.gl, q.hl, text, page))
        return record if record is not None and now - record.at < window else None

    for q in planned:
        if q.tier == 4:
            twin = sent_by.get((q.q, q.gl, q.hl))
            if twin is None:
                continue  # its tier-1 search is resting, so is its next page
            text, page = twin
            following = page + max(q.page - 1, 1)
            if following <= cfg.max_page and lately(q, text, following) is None:
                out.plan.append(q.model_copy(update={"q": text, "page": following}))
            continue
        options = [q.q, *(_with_exclusions(v, q.q) for v in q.variants)]
        pick: tuple[int, str, int] | None = None
        for i, text in enumerate(options):
            page = q.page
            while page <= cfg.max_page:
                record = lately(q, text, page)
                if record is None:
                    pick = (i, text, page)
                    break
                if record.postings <= 0:
                    break  # this phrasing's last page found nothing: the next phrasing
                page += 1
            if pick is not None:
                break
        if pick is None:
            out.rested += 1
            continue
        i, text, page = pick
        if page > q.page:
            out.deeper += 1
        elif i:
            out.rephrased += 1
        else:
            out.as_planned += 1
        if q.tier == 1:
            sent_by[(q.q, q.gl, q.hl)] = (text, page)
        out.plan.append(q if (i, page) == (0, q.page) else q.model_copy(update={"q": text, "page": page}))
    return out
