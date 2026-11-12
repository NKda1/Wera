"""The paid read: Firecrawl as the last rung for a posting no free rung could read (SRCH-05…07, SRCH-10).

Board search no longer starts with Firecrawl — discovery is Serper's (`test_serper.py`,
`test_discovery.py`) and reading is the Harvester's (`test_harvest.py`). What is left is
`scrape_card`: the caps that stop it paying, the deny list in front of it, the cache that stops it
paying twice, and the retries that keep one bad minute from costing a page.

The Firecrawl SDK builds its own HTTP client, so these tests replace the client itself and
inspect the calls.
"""

import pytest

from wera_agent.sources import firecrawl_search as board

NOW = 1_760_000_000_000


class FakeClient:
    """Records what WERA asked for, so the test can assert on the request, not the reply."""

    def __init__(self):
        self.scrapes: list[str] = []

    def scrape(self, url, **kwargs):
        self.scrapes.append(url)

        class Doc:
            json = {"title": "ML Engineer, Graduate", "company": "Fernhill AI"}

        return Doc()


@pytest.fixture
def fake(monkeypatch):
    def install(client):
        monkeypatch.setattr(board, "_client", lambda: client)
        return client

    return install


class TestTheCaps:
    def test_a_linkedin_url_is_refused_even_when_asked_for_directly(self, fake):
        client = fake(FakeClient())
        outcome = board.SearchOutcome()
        assert board.scrape_card("https://www.linkedin.com/jobs/view/1", {}, outcome, board.SearchCaps()) is None
        assert client.scrapes == []

    def test_a_page_cap_stops_scraping(self, fake):
        client = fake(FakeClient())
        outcome = board.SearchOutcome()
        caps = board.SearchCaps(max_pages=1)
        assert board.scrape_card("https://www.gradcracker.com/jobs/1", {}, outcome, caps) is not None
        assert board.scrape_card("https://www.gradcracker.com/jobs/2", {}, outcome, caps) is None
        assert outcome.capped == "pages"
        assert client.scrapes == ["https://www.gradcracker.com/jobs/1"]


class TestTheCrawlCache:
    """SRCH-10, measured in credits through the real paid read.

    The cache's own behaviour lives in `test_crawl_cache.py`; what matters here is that a hit
    costs nothing and that a page which was paid for once is not paid for again.
    """

    def cache(self, now: int = NOW, **over):
        from wera_agent.sources.crawl_cache import MINUTE_MS, CrawlCache

        base = {
            "search_ttl_ms": 360 * MINUTE_MS,
            "card_ttl_ms": 2880 * MINUTE_MS,
            "unreadable_ttl_ms": 2880 * MINUTE_MS,
        }
        return CrawlCache(now=now, **(base | over))

    def test_an_unreadable_page_is_not_scraped_twice(self, fake, migrated):
        """Leak #2, the expensive one.

        A page WERA can read but can't pull a title off never becomes a job, so it never enters
        `known_urls` — and before SRCH-10 it was re-scraped at 5 credits on every later run, for
        ever, eating the credit cap ahead of jobs that were actually new.
        """

        class Unreadable(FakeClient):
            def scrape(self, url, **kwargs):
                self.scrapes.append(url)

                class Doc:
                    json = None  # read fine; nothing a job could be built from

                return Doc()

        client = fake(Unreadable())
        cache = self.cache()
        url = "https://www.gradcracker.com/jobs/7"

        first = board.SearchOutcome()
        assert board.scrape_card(url, board.CARD_SCHEMA, first, board.SearchCaps(), cache=cache) is None
        second = board.SearchOutcome()
        assert board.scrape_card(url, board.CARD_SCHEMA, second, board.SearchCaps(), cache=cache) is None

        assert client.scrapes == [url], "the second run paid to fail on the same page again"
        assert first.credits_used == board.JSON_SCRAPE_CREDITS
        assert second.credits_used == 0
        assert (second.cached_cards, second.credits_saved) == (1, board.JSON_SCRAPE_CREDITS)

    def test_a_cached_card_does_not_make_a_posting_younger(self, fake, migrated):
        """MATCH-08. The bug this closes compounded with every cache hit.

        A card saying "2 days ago" was stored verbatim and re-resolved against each later run's
        clock, so a withdrawn posting got two days younger every time the cache served it — and
        its freshness score and urgency group moved with it. It also meant NORM-07's archive
        sweep could never fire for anything the cache still held.
        """
        from wera_agent.clock import parse_ms
        from wera_agent.sources.crawl_cache import MINUTE_MS

        class Relative(FakeClient):
            def scrape(self, url, **kwargs):
                self.scrapes.append(url)
                return type("Doc", (), {"json": {"title": "ML Engineer", "company": "Co", "postedAt": "2 days ago"}})()

        fake(Relative())
        url = "https://www.gradcracker.com/jobs/7"
        written = self.cache()
        first = board.scrape_card(url, board.CARD_SCHEMA, board.SearchOutcome(), board.SearchCaps(), cache=written)

        # 24 hours later the same card is served from cache, not re-scraped.
        later = self.cache(now=NOW + 1440 * MINUTE_MS)
        served = board.scrape_card(url, board.CARD_SCHEMA, board.SearchOutcome(), board.SearchCaps(), cache=later)
        assert later.cards_served == 1

        # The live scrape returns the page's own wording; the cache returns the resolved date.
        assert first["postedAt"] == "2 days ago"
        assert parse_ms(served["postedAt"]) == NOW - 2 * 1440 * MINUTE_MS

    def test_a_card_with_no_date_is_cached_unchanged(self, fake, migrated):
        fake(FakeClient())
        cache = self.cache()
        card = board.scrape_card(
            "https://www.gradcracker.com/jobs/7", board.CARD_SCHEMA, board.SearchOutcome(), board.SearchCaps(), cache=cache
        )
        assert "postedAt" not in card
        assert cache.card("https://www.gradcracker.com/jobs/7", board.CARD_SCHEMA)[1] == card

    def test_a_good_card_is_not_scraped_twice_either(self, fake, migrated):
        client = fake(FakeClient())
        cache = self.cache()
        url = "https://www.gradcracker.com/jobs/7"

        outcome = board.SearchOutcome()
        first = board.scrape_card(url, board.CARD_SCHEMA, outcome, board.SearchCaps(), cache=cache)
        second = board.scrape_card(url, board.CARD_SCHEMA, outcome, board.SearchCaps(), cache=cache)

        assert first == second
        assert client.scrapes == [url]

    def test_a_transport_failure_is_never_cached(self, fake, migrated, monkeypatch):
        """A rate limit says nothing about the page. Remembering one would turn a bad minute
        into two bad days."""
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[str] = []

        class Limited(FakeClient):
            def scrape(self, url, **kwargs):
                tries.append(url)
                raise type("RateLimitError", (Exception,), {})()

        fake(Limited())
        cache = self.cache()
        url = "https://www.gradcracker.com/jobs/7"

        for _ in range(2):
            board.scrape_card(url, board.CARD_SCHEMA, board.SearchOutcome(), board.SearchCaps(), cache=cache)

        # Three tries each time (SRCH-07), not three then a cached failure.
        assert len(tries) == 2 * board.MAX_TRIES
        assert cache.card(url, board.CARD_SCHEMA) == (False, None)

    def test_linkedin_is_not_served_from_the_cache_either(self, fake, migrated):
        """SRCH-03 sits in front of the cache, not behind it: a denied page is read from
        nowhere, whatever is remembered about it."""
        client = fake(FakeClient())
        cache = self.cache()
        url = "https://www.linkedin.com/jobs/view/1"
        cache.put_card(url, board.CARD_SCHEMA, {"title": "ML Engineer", "company": "Fernhill AI"})

        outcome = board.SearchOutcome()
        assert board.scrape_card(url, board.CARD_SCHEMA, outcome, board.SearchCaps(), cache=cache) is None
        assert client.scrapes == []
        assert outcome.cached_cards == 0

    def test_a_denied_url_at_the_page_cap_does_not_mark_the_run_partial(self, fake):
        """DENY moved ahead of the caps. A page that was never going to be fetched must not
        spend a cap or make a healthy run look capped."""
        fake(FakeClient())
        outcome = board.SearchOutcome(pages_scraped=30)
        assert board.scrape_card("https://www.linkedin.com/jobs/view/1", {}, outcome, board.SearchCaps(max_pages=30)) is None
        assert outcome.capped is None

    def test_no_cache_means_no_behaviour_change(self, fake, migrated):
        """The manual import (ATS-04) passes no cache, and must keep paying for what it asks."""
        client = fake(FakeClient())
        url = "https://www.gradcracker.com/jobs/7"
        for _ in range(2):
            board.scrape_card(url, board.CARD_SCHEMA, board.SearchOutcome(), board.SearchCaps())
        assert client.scrapes == [url, url]


class TestRetries:
    def test_a_rate_limit_is_retried_then_recorded(self, fake, monkeypatch):
        """SRCH-07: three tries, then this page is given up on — not the run."""
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[str] = []

        class Limited(FakeClient):
            def scrape(self, url, **kwargs):
                tries.append(url)
                raise type("RateLimitError", (Exception,), {})()

        fake(Limited())
        outcome = board.SearchOutcome()
        assert board.scrape_card("https://www.gradcracker.com/jobs/7", {}, outcome, board.SearchCaps()) is None
        assert len(tries) == board.MAX_TRIES
        assert outcome.errors and "RateLimitError" in outcome.errors[0]

    def test_an_auth_error_is_not_retried(self, fake, monkeypatch):
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[str] = []

        class Denied(FakeClient):
            def scrape(self, url, **kwargs):
                tries.append(url)
                raise type("UnauthorizedError", (Exception,), {})()

        fake(Denied())
        board.scrape_card("https://www.gradcracker.com/jobs/7", {}, board.SearchOutcome(), board.SearchCaps())
        assert len(tries) == 1
