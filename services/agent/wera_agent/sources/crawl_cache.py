"""SRCH-10: a short-term cache in front of Firecrawl.

A board search asks the same twenty queries every run, and the answers barely move between one
sitting and the next. Re-asking them is 40 credits and about a minute before the first new job
appears — on Firecrawl's Hobby plan (roughly 160 credits a day) that is a quarter of the day's
allowance spent re-learning what the last run already knew.

SRCH-05 already skips a URL WERA has stored as a job. Two things it cannot do:

* **it doesn't stop the search** — `known_urls` is applied to the *results*, after the query has
  been paid for;
* **it doesn't cover the pages that didn't become a job.** When `normalise.from_scrape` can't read
  a title and a company off a page it returns `None`, so nothing is stored, so the URL never
  enters `known_urls` — and it was scraped again at 5 credits on every later run, for ever. That
  set only grows, and those re-scrapes ate the credit cap ahead of genuinely new jobs. It is the
  bigger of the two leaks and the main reason this module exists.

So two things are remembered:

* **a query's results**, for six hours, because a search filtered to "posted this week" shifts
  through the day and the whole point of running one is to catch what appeared since;
* **a scraped page's card**, for two days, because a job ad's text does not change after it is
  posted — and this is the expensive one.

Including the pages that had no job on them, on the same two days. A board that starts working
again produces *new* URLs, which have no entry here, so remembering that an old URL is
unparseable costs nothing in missed jobs.

**A transport failure is never cached.** A rate limit or a timeout says nothing about the page,
and caching one would turn a bad minute into two bad days.

What this does *not* claim: leak #2 is closed for two days at a time, not permanently. A URL that
is still unparseable in three days' time will be paid for once more. Closing it for good needs a
durable "scraped, yielded no job" marker feeding `known_urls`, which is a different change.

Four rules hold everywhere in here:

1. **A cache hit is never counted against a cap** (SRCH-06). The caps exist to bound an invoice;
   free work doesn't belong in them. What the cache changes is how far a run's credits reach.
2. **A cache failure is never a run failure.** Every read, write and prune swallows its own
   error. The worst a broken cache may do is make a run cost what it used to.
3. **Reads check expiry themselves.** `prune` swallows its own failures, so a prune that didn't
   happen must not quietly turn into stale answers being served.
4. **The run's own `now` is the only clock**, so an entry written early in a long run cannot
   expire mid-flight, and a test moves time by building a second cache at a later `now`.

One deliberate import style: `wera` is imported **by name** so a test can monkeypatch
`crawl_cache.wera`. `config.wera()` is `lru_cache`d and never cleared, so that module attribute is
the only seam there is — don't "tidy" it into `from .. import config`.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import logfire

from ..clock import iso, parse_ms
from ..config import wera

SEARCH, CARD = "search", "card"

MINUTE_MS = 60 * 1000

# Hashed into every key, so the payload shape can change in a later commit without anyone having
# to write a data migration for a table that is by definition disposable: bump this and every old
# entry becomes unreachable and is pruned on its own expiry.
# Bumped to 2 when cards began storing an absolute `postedAt` instead of the page's own relative
# wording (`firecrawl_search._dated_card`). Every card written under format 1 carries a phrase
# like "2 days ago" that would be re-resolved against the reading run's clock, so they are
# stranded rather than migrated — which is exactly what this constant is for.
CACHE_FORMAT = 2


def _digest(kind: str, *parts: Any) -> str:
    """A stable key for one request, with the kind inside the hash so namespaces can't collide."""
    material = json.dumps([CACHE_FORMAT, kind, *parts], sort_keys=True, separators=(",", ":"), default=str)
    return f"{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


@dataclass
class CrawlCache:
    """One run's view of the cache. Opens its own transactions; holds its own counters.

    Built per run rather than per process: the TTLs come from config once, `now` is the run's, and
    the counters are what that run reports it saved. The TTLs are plain fields rather than
    `wera()` reads so a test can ask for a ten-minute window without touching global config.
    """

    now: int
    search_ttl_ms: int
    card_ttl_ms: int
    unreadable_ttl_ms: int
    max_entries: int = 500
    max_payload_bytes: int = 200_000

    # What this run got for free, for the Logfire line and the run's `credits_saved`.
    searches_served: int = 0
    cards_served: int = 0
    credits_saved: int = 0

    @classmethod
    def from_config(cls, now: int) -> "CrawlCache | None":
        """The cache this install wants, or `None` for "don't cache".

        `None` rather than a disabled instance, so every call site reads as "cache or no cache"
        and the no-cache path is the same one the manual import (ATS-04) and the unit tests take.

        Demo mode never searches (DEMO-02), so a cache would never be consulted — returning
        `None` also keeps a demo database free of rows describing crawls that never happened.
        """
        from ..settings import get_settings

        cfg = wera().crawl_cache
        if not cfg.enabled or get_settings().wera_demo_mode:
            return None
        return cls(
            now=now,
            search_ttl_ms=cfg.search_minutes * MINUTE_MS,
            card_ttl_ms=cfg.card_minutes * MINUTE_MS,
            unreadable_ttl_ms=cfg.unreadable_minutes * MINUTE_MS,
            max_entries=cfg.max_entries,
            max_payload_bytes=cfg.max_payload_bytes,
        )

    # -- searches ------------------------------------------------------------

    def hits(self, query: str, domains: list[str], tbs: str | None, limit: int) -> list[dict[str, Any]] | None:
        """A query's results, or `None` when they aren't cached.

        `None`, not `[]`: a query that legitimately matched nothing still cost 2 credits, so an
        empty result is worth remembering and has to be distinguishable from a miss. Callers must
        test `is None`.
        """
        payload = self._read(self._search_key(query, domains, tbs, limit))
        if payload is None:
            return None
        found = payload.get("hits")
        if not isinstance(found, list):
            return None  # written by some other shape; treat as a miss and let it be overwritten
        self.searches_served += 1
        return found

    def put_hits(self, query: str, domains: list[str], tbs: str | None, limit: int, hits: list[dict[str, Any]]) -> None:
        self._write(self._search_key(query, domains, tbs, limit), SEARCH, {"hits": hits}, self.search_ttl_ms)

    def _search_key(self, query: str, domains: list[str], tbs: str | None, limit: int) -> str:
        # Sorted domains: the same set in a different order is the same search, and
        # `enabled_domains` returns them in sites.yaml order — so without the sort, editing that
        # file silently invalidates every entry. Everything else that changes the answer is here
        # too, so a profile edit can never be served someone else's results.
        return _digest(SEARCH, query, sorted(domains), tbs, limit)

    # -- scraped pages -------------------------------------------------------

    def card(self, url: str, schema: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        """`(cached, card)`. A remembered page with no job on it is `(True, None)`.

        The pair exists because three outcomes are genuinely distinct — not cached, cached and
        readable, cached and unreadable — and collapsing the last two onto `None` is exactly how
        the 5 credits for an unreadable page get spent a second time.
        """
        payload = self._read(self._card_key(url, schema))
        if payload is None:
            return False, None
        self.cards_served += 1
        card = payload.get("card")
        return True, card if isinstance(card, dict) else None

    def put_card(self, url: str, schema: dict[str, Any], card: dict[str, Any] | None) -> None:
        """Remember a scraped page, including one that had no job on it."""
        ttl = self.card_ttl_ms if card is not None else self.unreadable_ttl_ms
        self._write(self._card_key(url, schema), CARD, {"card": card}, ttl)

    def _card_key(self, url: str, schema: dict[str, Any]) -> str:
        # The schema is part of the request. `CARD_SCHEMA` will gain a field eventually, and
        # without this a card extracted under the old one would be served missing the new field,
        # invisibly, until it expired.
        return _digest(CARD, url, schema)

    # -- storage -------------------------------------------------------------

    def _read(self, key: str) -> dict[str, Any] | None:
        """An unexpired payload, or `None`. Read-only — nothing is written on the read path.

        Deliberately no hit counter on the row. It would be useful, and it would make every
        lookup take SQLite's write lock: thirty of those in the middle of a crawl, to collect a
        statistic the counters on this object already carry for the length of a run.
        """
        from ..db.models import CrawlCacheRow
        from ..db.session import transaction

        # The `try` is outside the `with`: a context manager rolls an exception back and *re-raises*
        # it, so an `except` inside the block would catch nothing (rule 2).
        try:
            with transaction() as session:
                row = session.get(CrawlCacheRow, key)
                if row is None:
                    return None
                payload, expires = row.payload, row.expires_at
        except Exception:  # noqa: BLE001 — a broken cache costs credits, never the run
            logfire.warn("Crawl cache unreadable; carrying on without it")
            return None
        try:
            if parse_ms(expires) <= self.now:
                return None  # rule 3: expiry is decided here, not by whether `prune` ran
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write(self, key: str, kind: str, payload: dict[str, Any], ttl_ms: int) -> None:
        from ..db.models import CrawlCacheRow
        from ..db.session import transaction

        # One pathological page shouldn't be allowed to push out the entries that earned their
        # place. Measured on the serialised form, because that is what the column holds.
        try:
            size = len(json.dumps(payload, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return
        if size > self.max_payload_bytes:
            logfire.info("Crawl cache: not storing a {size}-byte payload", size=size)
            return

        expires = iso(self.now + ttl_ms)
        try:
            with transaction() as session:
                row = session.get(CrawlCacheRow, key)
                if row is None:
                    session.add(CrawlCacheRow(key=key, kind=kind, payload=payload, stored_at=iso(self.now), expires_at=expires))
                else:
                    # A re-write refreshes the expiry, which is intended: the entry was just
                    # confirmed against the live site, and that is what the TTL measures.
                    row.kind, row.payload = kind, payload
                    row.stored_at, row.expires_at = iso(self.now), expires
        except Exception:  # noqa: BLE001 — rule 2 again
            logfire.warn("Crawl cache not written; the next run will pay for this crawl again")

    def prune(self) -> int:
        """Drop what has expired, then trim to `max_entries`. Returns how many rows went.

        Called at the start of a run rather than on a timer: a run is the only thing that puts
        rows here, so it is the only thing that needs to bound them, and someone who never starts
        a search shouldn't have a background job tidying a cache nobody is reading.

        The trim takes the soonest-expiring rows first, which on equal TTLs is the oldest. A cache
        over its ceiling grew faster than it was consulted, and the entry about to expire anyway
        is the cheapest one to lose.
        """
        from sqlalchemy import delete, func, select

        from ..db.models import CrawlCacheRow
        from ..db.session import transaction

        removed = 0
        try:
            with transaction() as session:
                stale = session.execute(delete(CrawlCacheRow).where(CrawlCacheRow.expires_at <= iso(self.now)))
                removed += stale.rowcount or 0
                total = int(session.scalar(select(func.count(CrawlCacheRow.key))) or 0)
                excess = total - self.max_entries
                if excess > 0:
                    doomed = list(
                        session.scalars(select(CrawlCacheRow.key).order_by(CrawlCacheRow.expires_at).limit(excess)).all()
                    )
                    # `in_` on a literal list rather than a correlated subquery: SQLite and
                    # Postgres disagree about `delete ... limit`, and the list is at most `excess`.
                    session.execute(delete(CrawlCacheRow).where(CrawlCacheRow.key.in_(doomed)))
                    removed += len(doomed)
        except Exception:  # noqa: BLE001 — tidying is not worth failing a run over
            logfire.warn("Crawl cache not pruned")
            return 0
        if removed:
            logfire.info("Crawl cache: dropped {removed} expired or excess entry/entries", removed=removed)
        return removed

    def summary(self) -> None:
        """One line per run, so the saving is visible without querying the table.

        Named `summary` rather than `report` because `graphs/intake.py` already has a `report` —
        the progress heartbeat — called a few lines from where this is.
        """
        if not (self.searches_served or self.cards_served):
            return
        logfire.info(
            "Crawl cache served {searches} search(es) and {cards} page(s), saving {credits} credit(s)",
            searches=self.searches_served,
            cards=self.cards_served,
            credits=self.credits_saved,
        )
