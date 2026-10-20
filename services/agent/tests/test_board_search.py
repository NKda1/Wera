"""Board search: what it asks for, what it refuses, and when it stops (SRCH-01…07, T-04).

The Firecrawl SDK builds its own HTTP client, so these tests replace the client itself and
inspect the calls. That is the right level anyway: what matters is the arguments WERA sends —
which domains, how many results, how recent — and the caps that stop a run.
"""

from dataclasses import dataclass

import pytest

from wera_agent.clock import iso
from wera_agent.config import Site
from wera_agent.schemas.profile import ProfileChannels, SearchProfile
from wera_agent.sources import firecrawl_search as board

NOW = 1_760_000_000_000

SITES = [
    Site(domain="gradcracker.com", name="Gradcracker", terms_reviewed_on="2026-01-01"),
    Site(domain="brightnetwork.co.uk", name="Bright Network", terms_reviewed_on="2026-01-01"),
    Site(domain="otta.com", name="Otta", terms_reviewed_on=None),  # SRCH-04: not reviewed
]


def profile(**over) -> SearchProfile:
    base = {
        "titles": ["ML Engineer"],
        "skills": ["Python"],
        "locations": ["London"],
        "remote": "any",
        "seniority": ["graduate"],
        "exclude": [],
        "sites": ["gradcracker.com", "brightnetwork.co.uk", "otta.com", "linkedin.com"],
        "recency": "7d",
        "channels": ProfileChannels(board_search=True, inbox=True, watchlist=True),
        "generated_from_cv_version": 1,
        "updated_at": iso(NOW),
    }
    return SearchProfile.model_validate(base | over)


@dataclass
class Hit:
    url: str
    title: str = "ML Engineer, Graduate"
    description: str = "We build guardrails."


class FakeResult:
    def __init__(self, urls):
        self.web = [Hit(url=u) for u in urls]


class FakeClient:
    """Records what WERA asked for, so the test can assert on the request, not the reply."""

    def __init__(self, results=None, raises=None):
        self.searches: list[dict] = []
        self.scrapes: list[str] = []
        self._results = results or {}
        self._raises = raises or {}

    def search(self, query, **kwargs):
        self.searches.append({"query": query, **kwargs})
        if query in self._raises:
            raise self._raises[query]
        return FakeResult(self._results.get(query, []))

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


class TestQueries:
    def test_one_query_per_title_times_location(self):
        """SRCH-01."""
        queries = board.queries_for(profile(titles=["A", "B"], locations=["X", "Y"]), SITES)
        assert queries == ['"A" "X"', '"A" "Y"', '"B" "X"', '"B" "Y"']

    def test_a_site_whose_terms_are_unreviewed_is_never_searched(self):
        """SRCH-04: `terms_reviewed_on` is the gate, not the checkbox."""
        assert "otta.com" not in board.enabled_domains(profile(), SITES)

    def test_linkedin_is_not_a_searchable_site_even_if_the_profile_lists_it(self):
        """SRCH-03."""
        assert "linkedin.com" not in board.enabled_domains(profile(), SITES)

    def test_no_reviewed_site_means_no_queries(self):
        assert board.queries_for(profile(sites=["otta.com"]), SITES) == []


class TestLinkedInIsNeverFetched:
    def test_every_search_excludes_linkedin_and_no_call_targets_it(self, fake):
        """T-04: whatever the profile or a result says, LinkedIn is not requested."""
        client = fake(
            FakeClient(
                results={
                    '"ML Engineer" "London"': [
                        "https://www.linkedin.com/jobs/view/123",
                        "https://uk.linkedin.com/jobs/view/456",
                        "https://www.gradcracker.com/jobs/7",
                    ]
                }
            )
        )
        outcome = board.search(profile(), SITES, board.SearchCaps(), known_urls=set())

        # The allow-list *is* the exclusion: Firecrawl refuses a request carrying both lists, so
        # SRCH-03 is enforced by never naming a denied domain and by dropping denied results.
        assert all("linkedin.com" not in call["include_domains"] for call in client.searches)
        # A LinkedIn result that comes back anyway is dropped locally too.
        assert [h.url for h in outcome.hits] == ["https://www.gradcracker.com/jobs/7"]
        assert not any("linkedin" in u for u in client.scrapes)

    def test_a_linkedin_url_is_refused_even_when_asked_for_directly(self, fake):
        client = fake(FakeClient())
        outcome = board.SearchOutcome()
        assert board.scrape_card("https://www.linkedin.com/jobs/view/1", {}, outcome, board.SearchCaps()) is None
        assert client.scrapes == []


class TestSearchArguments:
    def test_recency_and_limit_are_sent(self, fake):
        """SRCH-02."""
        client = fake(FakeClient())
        board.search(profile(recency="24h"), SITES, board.SearchCaps(), set())
        call = client.searches[0]
        assert call["tbs"] == "qdr:d"
        assert call["limit"] == board.LIMIT_PER_QUERY <= 10
        assert set(call["include_domains"]) == {"gradcracker.com", "brightnetwork.co.uk"}

    def test_the_request_never_carries_both_domain_lists(self, fake):
        """Firecrawl rejects a request with an include list *and* an exclude list. Sending both
        failed every query of every board search, and said only "ValidationError"."""
        client = fake(FakeClient())
        board.search(profile(), SITES, board.SearchCaps(), set())
        assert all(call["include_domains"] and not call.get("exclude_domains") for call in client.searches)


class TestErrorsSayWhatWentWrong:
    """A swallowed message is how a dead channel looks healthy."""

    def rejecting(self, message: str):
        class Rejected(FakeClient):
            def search(self, query, **kwargs):
                raise type("ValidationError", (Exception,), {})(message)

        return Rejected()

    def test_a_rejected_request_records_why_not_just_its_class(self, fake, monkeypatch):
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        fake(
            self.rejecting(
                "1 validation error for SearchRequest\n  include_domains and exclude_domains cannot both\n  be specified"
            )
        )

        outcome = board.search(profile(), SITES, board.SearchCaps(), set())

        assert outcome.errors
        # Collapsed onto one line, so a multi-line SDK error stays readable in a run's errors.
        assert "cannot both be specified" in outcome.errors[0]
        assert "\n" not in outcome.errors[0]

    def test_a_message_that_echoes_a_key_is_redacted(self, fake, monkeypatch):
        """SEC-05: the message is the provider's, and it quotes the request back."""
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        key = "fc-" + "9" * 40
        fake(self.rejecting(f"rejected request with Authorization: Bearer {key}"))

        outcome = board.search(profile(), SITES, board.SearchCaps(), set())
        assert outcome.errors and key not in outcome.errors[0]


class TestKnownUrls:
    def test_a_url_already_known_is_not_returned_again(self, fake):
        """SRCH-05, and what makes a second run nearly free."""
        url = "https://www.gradcracker.com/jobs/7"
        fake(FakeClient(results={'"ML Engineer" "London"': [url]}))
        outcome = board.search(profile(), SITES, board.SearchCaps(), known_urls={url})
        assert outcome.hits == []

    def test_the_same_url_twice_in_one_run_counts_once(self, fake):
        url = "https://www.gradcracker.com/jobs/7"
        fake(FakeClient(results={'"ML Engineer" "London"': [url, url]}))
        outcome = board.search(profile(), SITES, board.SearchCaps(), set())
        assert len(outcome.hits) == 1


class TestCaps:
    def test_a_query_cap_ends_the_run_as_partial(self, fake):
        """SRCH-06."""
        client = fake(FakeClient())
        outcome = board.search(profile(titles=["A", "B", "C"], locations=["X"]), SITES, board.SearchCaps(max_queries=2), set())
        assert len(client.searches) == 2
        assert outcome.capped == "queries" and outcome.partial

    def test_a_credit_cap_ends_the_run_as_partial(self, fake):
        fake(FakeClient())
        outcome = board.search(profile(titles=["A", "B", "C"], locations=["X"]), SITES, board.SearchCaps(max_credits=2), set())
        assert outcome.capped == "credits"
        assert outcome.credits_used <= 3  # the call that hit the cap is counted, then it stops

    def test_a_page_cap_stops_scraping(self, fake):
        client = fake(FakeClient())
        outcome = board.SearchOutcome()
        caps = board.SearchCaps(max_pages=1)
        assert board.scrape_card("https://www.gradcracker.com/jobs/1", {}, outcome, caps) is not None
        assert board.scrape_card("https://www.gradcracker.com/jobs/2", {}, outcome, caps) is None
        assert outcome.capped == "pages"
        assert client.scrapes == ["https://www.gradcracker.com/jobs/1"]


class TestRetries:
    def test_a_rate_limit_is_retried_then_recorded(self, fake, monkeypatch):
        """SRCH-07: three tries, then this query is given up on — not the run."""
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[int] = []

        class Limited(FakeClient):
            def search(self, query, **kwargs):
                tries.append(1)
                raise type("RateLimitError", (Exception,), {})()

        fake(Limited())
        outcome = board.search(profile(), SITES, board.SearchCaps(), set())
        assert len(tries) == board.MAX_TRIES
        assert outcome.errors and "RateLimitError" in outcome.errors[0]
        assert outcome.hits == []

    def test_an_auth_error_is_not_retried(self, fake, monkeypatch):
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[int] = []

        class Denied(FakeClient):
            def search(self, query, **kwargs):
                tries.append(1)
                raise type("UnauthorizedError", (Exception,), {})()

        fake(Denied())
        board.search(profile(), SITES, board.SearchCaps(), set())
        assert len(tries) == 1
