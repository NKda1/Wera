"""SRCH-05/06: what a page costs now, and the gate in front of a direct board.

The point of the ladder is the invoice, so these are about accounting: a page read locally must
not spend a credit, must not spend a cap, and must be *counted* — because "the local path works"
is an assumption until a run row says `18 free, 2 paid`.
"""

from wera_agent.config import Site
from wera_agent.schemas.profile import SearchProfile
from wera_agent.sources import firecrawl_search, google_careers
from wera_agent.sources.firecrawl_search import JSON_SCRAPE_CREDITS, SearchCaps, SearchOutcome

CARD = {"title": "Graduate Engineer", "company": "Fernhill AI", "location": "London, UK", "description": "x" * 400}


def _profile(**over) -> SearchProfile:
    base = {
        "titles": ["Graduate Software Engineer"],
        "skills": ["Python"],
        "locations": ["London"],
        "countries": ["GB"],
        "remote": "any",
        "seniority": ["graduate"],
        "exclude": [],
        "recency": "21d",
        "sites": ["careers.google.com", "gradcracker.com"],
        "channels": {"boardSearch": True, "inbox": False, "watchlist": True},
        "generatedFromCvVersion": 1,
        "updatedAt": "2026-10-31T09:00:00.000Z",
    }
    return SearchProfile.model_validate(base | over)


def _site(**over) -> Site:
    return Site.model_validate({"domain": "careers.google.com", "name": "Google Careers"} | over)


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


class TestADirectBoardIsNeverSearched:
    """`Site.direct` means WERA reads the board itself. If such a board also reached a search it
    would be both searched *and* fetched, and the free source would start costing."""

    def test_it_is_excluded_from_the_searches(self):
        from wera_agent.nodes import strategist

        google = _site(terms_reviewed_on="2026-10-31", direct=True, countries=["GB"])
        gradcracker = Site.model_validate(
            {"domain": "gradcracker.com", "name": "Gradcracker", "terms_reviewed_on": "2026-10-31", "countries": ["GB"]}
        )
        usable = firecrawl_search.usable_sites(_profile(), [google, gradcracker])
        assert [s.domain for s in usable] == ["gradcracker.com"]
        planned = strategist.queries(_profile(), None, [google, gradcracker])
        assert {q.domain for q in planned if q.source == "board"} == {"gradcracker.com"}

    def test_it_is_offered_to_the_reader_instead(self):
        google = _site(terms_reviewed_on="2026-10-31", direct=True, countries=["GB"])
        assert [s.domain for s in firecrawl_search.direct_sites(_profile(), [google])] == ["careers.google.com"]

    def test_an_unreviewed_direct_board_is_read_by_nobody(self):
        """The terms gate applies however a board is read. This is what makes shipping
        `careers.google.com` with `terms_reviewed_on: null` safe rather than sloppy."""
        unreviewed = _site(terms_reviewed_on=None, direct=True, countries=["GB"])
        assert firecrawl_search.direct_sites(_profile(), [unreviewed]) == []
        assert firecrawl_search.usable_sites(_profile(), [unreviewed]) == []

    def test_a_declined_direct_board_stays_declined(self):
        declined = _site(terms_reviewed_on="2026-10-31", declined_on="2026-10-31", direct=True)
        assert firecrawl_search.direct_sites(_profile(), [declined]) == []


class TestGoogleCareers:
    def test_a_results_page_yields_each_posting_once(self):
        html = """
          <a href="/about/careers/applications/jobs/results/123456-graduate-engineer">One</a>
          <a href="/about/careers/applications/jobs/results/123456-graduate-engineer">One again</a>
          <a href="/about/careers/applications/jobs/results/789012-data-scientist">Two</a>
          <a href="/about/careers/applications/jobs/results">The index, not a job</a>
        """
        assert google_careers.job_urls(html) == [
            f"{google_careers.RESULTS}/123456-graduate-engineer",
            f"{google_careers.RESULTS}/789012-data-scientist",
        ]

    def test_the_same_posting_under_two_slugs_is_read_once(self):
        """The id is the job's identity; the slug is decoration and is not always the same."""
        html = """
          <a href="/about/careers/applications/jobs/results/123456-graduate-engineer">One</a>
          <a href="/about/careers/applications/jobs/results/123456-grad-swe-london">Same job, other slug</a>
        """
        assert google_careers.job_urls(html) == [f"{google_careers.RESULTS}/123456-graduate-engineer"]

    def test_the_search_url_carries_the_title_and_place(self):
        url = google_careers.search_url("Graduate Software Engineer", "London")
        assert "q=Graduate+Software+Engineer" in url and "location=London" in url

    def test_no_browser_is_reported_rather_than_shown_as_no_jobs(self):
        """ "Google has no graduate roles" and "WERA has no browser" are opposite facts, and the
        channel strip must not show the first when it means the second."""
        jobs, errors = google_careers.list_jobs(["Graduate Engineer"], ["London"], fetch_html=lambda _u: None, now=0)
        assert jobs == []
        assert len(errors) == 1 and "browser" in errors[0]

    def test_no_titles_means_no_requests(self):
        asked: list[str] = []
        jobs, errors = google_careers.list_jobs([], [], fetch_html=lambda u: asked.append(u) or None, now=0)
        assert (jobs, errors, asked) == ([], [], [])
