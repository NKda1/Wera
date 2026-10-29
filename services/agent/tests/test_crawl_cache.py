"""The crawl cache on its own (SRCH-10).

Every test builds a `CrawlCache` directly rather than through `from_config`. Two reasons, and
both are deliberate properties of the design: `config.wera()` is `lru_cache`d and never cleared,
so a test can't change the TTLs globally; and `conftest` forces demo mode on, where `from_config`
correctly returns `None`. The TTLs being plain dataclass fields is what makes this possible — a
test that wants a ten-minute window just asks for one.

Expiry is driven by building a *second* cache at a later `now`, never by sleeping: the run's own
clock is the only one the cache consults (rule 4).
"""

import json

import pytest

from wera_agent.clock import iso
from wera_agent.config import CrawlCacheConfig, wera
from wera_agent.db.models import CrawlCacheRow
from wera_agent.db.session import transaction
from wera_agent.sources import crawl_cache
from wera_agent.sources.crawl_cache import MINUTE_MS, CrawlCache

NOW = 1_760_000_000_000

DOMAINS = ["gradcracker.com", "brightnetwork.co.uk"]
SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}}
QUERY = '"ML Engineer" "London"'

HITS = [
    {
        "url": "https://www.gradcracker.com/jobs/7",
        "title": "ML Engineer",
        "snippet": "We build guardrails.",
        "site": "gradcracker.com",
    }
]


def cache(now: int = NOW, **over) -> CrawlCache:
    base = {
        "search_ttl_ms": 360 * MINUTE_MS,
        "card_ttl_ms": 2880 * MINUTE_MS,
        "unreadable_ttl_ms": 2880 * MINUTE_MS,
    }
    return CrawlCache(now=now, **(base | over))


class TestSearches:
    def test_a_search_is_remembered_and_a_different_one_is_not(self, migrated):
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)

        assert c.hits(QUERY, DOMAINS, "qdr:w", 10) == HITS
        assert c.hits('"Data Scientist" "London"', DOMAINS, "qdr:w", 10) is None

    def test_an_empty_answer_is_an_answer_not_a_miss(self, migrated):
        """The assertion the whole module hangs on.

        A query that legitimately matched nothing still cost 2 credits, so `[]` is worth
        remembering — and it has to be distinguishable from "never asked", or every empty query
        is re-paid for on every run. Callers test `is None`, never truthiness.
        """
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, [])

        assert c.hits(QUERY, DOMAINS, "qdr:w", 10) == []
        assert c.hits(QUERY, DOMAINS, "qdr:w", 10) is not None

    @pytest.mark.parametrize(
        "domains,tbs,limit",
        [
            (["gradcracker.com"], "qdr:w", 10),  # a site turned off
            (DOMAINS, "qdr:d", 10),  # a different recency
            (DOMAINS, "qdr:w", 5),  # a different limit
        ],
    )
    def test_everything_that_changes_the_answer_is_in_the_key(self, migrated, domains, tbs, limit):
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
        assert c.hits(QUERY, domains, tbs, limit) is None

    def test_the_same_sites_in_a_different_order_are_the_same_search(self, migrated):
        """`enabled_domains` returns sites.yaml order, so without the sort an edit to that file
        would silently invalidate every entry in the cache."""
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
        assert c.hits(QUERY, list(reversed(DOMAINS)), "qdr:w", 10) == HITS

    def test_an_expired_search_is_a_miss_and_a_live_one_is_not(self, migrated):
        cache().put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)

        assert cache(NOW + 359 * MINUTE_MS).hits(QUERY, DOMAINS, "qdr:w", 10) == HITS
        assert cache(NOW + 361 * MINUTE_MS).hits(QUERY, DOMAINS, "qdr:w", 10) is None

    def test_a_payload_written_by_another_build_is_a_miss_not_a_crash(self, migrated):
        """A cache is not a contract."""
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
        with transaction() as session:
            row = session.get(CrawlCacheRow, c._search_key(QUERY, DOMAINS, "qdr:w", 10))
            row.payload = {"something": "else"}

        assert c.hits(QUERY, DOMAINS, "qdr:w", 10) is None


class TestCards:
    def test_a_card_round_trips_and_an_unseen_url_does_not(self, migrated):
        c = cache()
        card = {"title": "ML Engineer, Graduate", "company": "Fernhill AI"}
        c.put_card("https://x.example/1", SCHEMA, card)

        assert c.card("https://x.example/1", SCHEMA) == (True, card)
        assert c.card("https://x.example/2", SCHEMA) == (False, None)

    def test_a_page_with_no_job_on_it_is_remembered_as_such(self, migrated):
        """Leak #2, at the storage layer.

        `(True, None)` and `(False, None)` have to be different answers, and the row has to hold
        a JSON object containing null — not SQL NULL — or a dialect that collapses the two turns
        "WERA read this and found nothing" back into "WERA has never read this", and the 5
        credits get spent again on every run for ever.
        """
        c = cache()
        c.put_card("https://x.example/dead", SCHEMA, None)

        assert c.card("https://x.example/dead", SCHEMA) == (True, None)
        with transaction() as session:
            row = session.get(CrawlCacheRow, c._card_key("https://x.example/dead", SCHEMA))
        assert row.payload == {"card": None}
        assert row.payload is not None

    def test_the_card_key_covers_the_extraction_schema(self, migrated):
        """`CARD_SCHEMA` will gain a field. Without the schema in the key, every cached card
        would be served missing it — invisibly — until it expired."""
        c = cache()
        c.put_card("https://x.example/1", SCHEMA, {"title": "ML Engineer"})

        widened = {**SCHEMA, "properties": {**SCHEMA["properties"], "salary": {"type": "string"}}}
        assert c.card("https://x.example/1", widened) == (False, None)

    def test_the_search_window_is_shorter_than_the_card_window(self, migrated):
        """The two TTLs have to be behaviourally distinct, not just distinct in config.

        A stale search hides jobs that exist; a stale card can only describe one that closed. So
        at 12 hours the search is gone and the card is still good.
        """
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
        c.put_card("https://x.example/1", SCHEMA, {"title": "ML Engineer"})

        later = cache(NOW + 720 * MINUTE_MS)
        assert later.hits(QUERY, DOMAINS, "qdr:w", 10) is None
        assert later.card("https://x.example/1", SCHEMA)[0] is True

    def test_a_payload_over_the_ceiling_is_not_stored(self, migrated):
        """One pathological page shouldn't push out the entries that earned their place."""
        c = cache(max_payload_bytes=500)
        c.put_card("https://x.example/huge", SCHEMA, {"description": "x" * 2000})
        assert c.card("https://x.example/huge", SCHEMA) == (False, None)


class TestCounters:
    def test_what_it_served_is_counted_for_the_run_to_report(self, migrated):
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
        c.put_card("https://x.example/1", SCHEMA, {"title": "ML Engineer"})

        c.hits(QUERY, DOMAINS, "qdr:w", 10)
        c.card("https://x.example/1", SCHEMA)
        assert (c.searches_served, c.cards_served) == (1, 1)

    def test_a_miss_counts_nothing(self, migrated):
        c = cache()
        c.hits(QUERY, DOMAINS, "qdr:w", 10)
        c.card("https://x.example/1", SCHEMA)
        assert (c.searches_served, c.cards_served) == (0, 0)


class TestPrune:
    def test_it_drops_what_expired_and_keeps_what_did_not(self, migrated):
        cache().put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
        cache().put_card("https://x.example/1", SCHEMA, {"title": "ML Engineer"})

        # Twelve hours on: the search has expired, the card has not.
        later = cache(NOW + 720 * MINUTE_MS)
        assert later.prune() == 1
        assert later.card("https://x.example/1", SCHEMA)[0] is True

    def test_it_enforces_the_entry_ceiling_soonest_expiry_first(self, migrated):
        """A count ceiling that only ran on expiry would be decorative: these entries are all
        live, and the table still has to be bounded."""
        c = cache(max_entries=3)
        for i in range(5):
            # Staggered expiry, so "soonest first" has something to order by.
            cache(NOW + i * MINUTE_MS).put_card(f"https://x.example/{i}", SCHEMA, {"title": str(i)})

        assert c.prune() == 2
        with transaction() as session:
            assert session.query(CrawlCacheRow).count() == 3
        # The two oldest went; the three most recently stored survived.
        assert c.card("https://x.example/0", SCHEMA) == (False, None)
        assert c.card("https://x.example/1", SCHEMA) == (False, None)
        assert c.card("https://x.example/4", SCHEMA)[0] is True


class TestABrokenCacheIsNeverARunFailure:
    """Rule 2. The worst a broken cache may do is make a run cost what it used to."""

    @pytest.fixture
    def exploding(self, monkeypatch):
        # Patched at `db.session`, not on `crawl_cache`: the module imports `transaction` inside
        # each method, so the function object is looked up fresh on the session module every call.
        def boom(*_a, **_k):
            raise RuntimeError("no database today")

        monkeypatch.setattr("wera_agent.db.session.transaction", boom)

    def test_every_operation_degrades_to_a_permanent_miss(self, migrated, exploding):
        c = cache()
        c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)  # must not raise
        c.put_card("https://x.example/1", SCHEMA, None)  # must not raise

        assert c.hits(QUERY, DOMAINS, "qdr:w", 10) is None
        assert c.card("https://x.example/1", SCHEMA) == (False, None)
        assert c.prune() == 0

    def test_it_works_the_same_with_no_tables_at_all(self):
        """The real shape of the failure: a database that was never migrated. Deliberately
        without the `migrated` fixture."""
        c = cache()
        c.put_card("https://x.example/1", SCHEMA, {"title": "ML Engineer"})
        assert c.card("https://x.example/1", SCHEMA) == (False, None)
        assert c.prune() == 0


class TestFromConfig:
    def test_demo_mode_has_no_cache(self):
        """DEMO-02: the demo never searches, so there is nothing to remember — and a demo
        database shouldn't collect rows describing crawls that never happened."""
        assert CrawlCache.from_config(NOW) is None

    def test_a_live_install_has_one_with_the_configured_windows(self, outbound):
        c = CrawlCache.from_config(NOW)
        cfg = wera().crawl_cache
        assert c is not None
        assert c.search_ttl_ms == cfg.search_minutes * MINUTE_MS
        assert c.card_ttl_ms == cfg.card_minutes * MINUTE_MS
        # The search window has to be the shorter one; a stale search hides jobs that exist.
        assert c.search_ttl_ms < c.card_ttl_ms

    def test_disabling_it_in_config_means_no_cache(self, outbound, monkeypatch):
        """`None` rather than a disabled instance, so every call site reads as "cache or no
        cache" and the off path is the one the manual import already takes."""
        off = wera().model_copy(update={"crawl_cache": CrawlCacheConfig(enabled=False)})
        monkeypatch.setattr(crawl_cache, "wera", lambda: off)
        assert CrawlCache.from_config(NOW) is None


class TestKeys:
    def test_the_two_kinds_cannot_collide(self):
        c = cache()
        assert c._search_key(QUERY, DOMAINS, "qdr:w", 10) != c._card_key(QUERY, SCHEMA)
        assert c._search_key(QUERY, DOMAINS, "qdr:w", 10).startswith("search:")
        assert c._card_key("https://x.example/1", SCHEMA).startswith("card:")

    def test_a_key_fits_the_column(self):
        c = cache()
        for key in (c._search_key(QUERY, DOMAINS, "qdr:w", 10), c._card_key("https://x.example/1" * 20, SCHEMA)):
            assert len(key) <= 80

    def test_the_format_version_is_in_the_key(self, migrated, monkeypatch):
        """Bumping `CACHE_FORMAT` has to strand old entries rather than mis-read them, so the
        payload shape can change without a data migration for a disposable table."""
        c = cache()
        c.put_card("https://x.example/1", SCHEMA, {"title": "ML Engineer"})
        monkeypatch.setattr(crawl_cache, "CACHE_FORMAT", crawl_cache.CACHE_FORMAT + 1)
        assert cache().card("https://x.example/1", SCHEMA) == (False, None)


def test_a_stored_payload_is_json_serialisable(migrated):
    """The column is JSON on SQLite and JSONB on Postgres; anything that can't round-trip
    through `json` would fail on one and not the other."""
    c = cache()
    c.put_hits(QUERY, DOMAINS, "qdr:w", 10, HITS)
    with transaction() as session:
        row = session.get(CrawlCacheRow, c._search_key(QUERY, DOMAINS, "qdr:w", 10))
        assert json.loads(json.dumps(row.payload)) == row.payload
        # And the bookkeeping columns are the ISO strings the rest of the schema uses.
        assert row.stored_at == iso(NOW)
        assert row.kind == "search"
