"""SRCH-05/06: what a page costs now.

The point of the ladder is the invoice, so these are about accounting: a page read locally must
not spend a credit, must not spend a cap, and must be *counted* — because "the local path works"
is an assumption until a run row says `18 free, 2 paid`.
"""

from wera_agent.sources import firecrawl_search
from wera_agent.sources.firecrawl_search import JSON_SCRAPE_CREDITS, SearchCaps, SearchOutcome

CARD = {"title": "Graduate Engineer", "company": "Fernhill AI", "location": "London, UK", "description": "x" * 400}


class TestAPageReadLocallyIsFree:
    def test_it_spends_no_credits_and_is_counted(self, monkeypatch):
        monkeypatch.setattr(firecrawl_search.extract, "card", lambda *_a, **_k: (CARD, "json-ld"))
        outcome = SearchOutcome()

        card = firecrawl_search.scrape_card("https://board.example/jobs/1", {}, outcome, SearchCaps())

        assert card == CARD
        assert outcome.credits_used == 0
        assert outcome.pages_scraped == 0
        assert (outcome.local_pages, outcome.fallback_pages) == (1, 0)
        # Recorded as a saving in the same units the invoice is in, like a cache hit (SRCH-10).
        assert outcome.credits_saved == JSON_SCRAPE_CREDITS

    def test_the_page_cap_no_longer_stops_a_free_read(self, monkeypatch):
        """`max_pages` bounds what a run may *pay* for. A run at its cap can still read pages
        for nothing, which is the whole point of the cap moving from "stop reading" to "stop
        paying" — and a run that stopped early used to be marked partial for it."""
        monkeypatch.setattr(firecrawl_search.extract, "card", lambda *_a, **_k: (CARD, "json-ld"))
        outcome = SearchOutcome(pages_scraped=99)

        assert firecrawl_search.scrape_card("https://board.example/jobs/1", {}, outcome, SearchCaps(max_pages=1)) == CARD
        assert outcome.capped is None
        assert outcome.credits_used == 0

    def test_a_page_no_rung_could_read_falls_through_to_firecrawl(self, monkeypatch):
        monkeypatch.setattr(firecrawl_search.extract, "card", lambda *_a, **_k: (None, "unreadable"))
        monkeypatch.setattr(firecrawl_search, "_client", lambda: _StubClient())
        outcome = SearchOutcome()

        firecrawl_search.scrape_card("https://board.example/jobs/1", {}, outcome, SearchCaps())

        assert outcome.credits_used == JSON_SCRAPE_CREDITS
        assert (outcome.local_pages, outcome.fallback_pages) == (0, 1)

    def test_linkedin_is_refused_before_any_rung_runs(self, monkeypatch):
        """SRCH-03 is not a preference, and it comes first — ahead of the free rungs as well as
        the paid one."""
        asked: list[str] = []
        monkeypatch.setattr(firecrawl_search.extract, "card", lambda url, **_k: asked.append(url) or (CARD, "json-ld"))
        outcome = SearchOutcome()

        assert firecrawl_search.scrape_card("https://www.linkedin.com/jobs/view/1", {}, outcome, SearchCaps()) is None
        assert asked == []


class _StubClient:
    def scrape(self, *_a, **_k):
        return type("Doc", (), {"json": CARD})()
