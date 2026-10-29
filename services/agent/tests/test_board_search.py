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
    Site(domain="gradcracker.com", name="Gradcracker", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="brightnetwork.co.uk", name="Bright Network", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="otta.com", name="Otta", terms_reviewed_on=None, countries=["GB"]),  # SRCH-04: not reviewed
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


# One title against one board, so a test that counts calls counts one per query rather than one
# per query × board. The grid itself is covered by `TestQueries`.
ONE_BOARD = profile(titles=["ML Engineer"], locations=["London"], sites=["gradcracker.com"])


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
    def test_one_query_per_title_times_board(self):
        """SRCH-01/11: a board per query, so none can crowd the others out."""
        queries = board.queries_for(profile(titles=["A", "B"], locations=["X"]), SITES)
        assert [(q.text, q.domain) for q in queries] == [
            ('"A" "X"', "gradcracker.com"),
            ('"A" "X"', "brightnetwork.co.uk"),
            ('"B" "X"', "gradcracker.com"),
            ('"B" "X"', "brightnetwork.co.uk"),
        ]

    def test_several_places_become_one_or_group_not_one_query_each(self):
        """The grid is title × board. Locations go in the text, because a board search is a
        keyword match and `filters.wrong_location` is what actually decides."""
        queries = board.queries_for(profile(titles=["A"], locations=["X", "Y"]), SITES)
        assert {q.text for q in queries} == {'"A" ("X" OR "Y")'}
        assert len(queries) == 2  # one per board, not one per location

    def test_a_country_only_profile_still_names_a_place(self):
        """A profile that says "the UK" rather than naming cities should not ask a question with
        no geography in it at all."""
        queries = board.queries_for(profile(titles=["A"], locations=[], countries=["GB"]), SITES)
        assert all('"United Kingdom"' in q.text for q in queries)

    def test_a_site_whose_terms_are_unreviewed_is_never_searched(self):
        """SRCH-04: `terms_reviewed_on` is the gate, not the checkbox."""
        assert "otta.com" not in board.enabled_domains(profile(), SITES)

    def test_linkedin_is_not_a_searchable_site_even_if_the_profile_lists_it(self):
        """SRCH-03."""
        assert "linkedin.com" not in board.enabled_domains(profile(), SITES)

    def test_no_reviewed_site_means_no_queries(self):
        assert board.queries_for(profile(sites=["otta.com"]), SITES) == []

    def test_no_title_means_no_queries(self):
        assert board.queries_for(profile(titles=["  "]), SITES) == []


class TestCountryTargeting:
    """SRCH-11: most boards are national, so a search for Berlin shouldn't pay to ask a UK
    graduate board about it."""

    def test_a_board_that_does_not_serve_the_country_is_not_queried(self):
        queries = board.queries_for(profile(countries=["NL"]), SITES)
        # Both SITES entries that are reviewed are GB-only.
        assert queries == []

    def test_a_board_serving_one_of_several_countries_is_queried(self):
        assert board.enabled_domains(profile(countries=["NL", "GB"]), SITES)

    def test_no_country_named_rules_nothing_out(self):
        """A profile written before SRCH-11 has no countries, and must keep working — failing
        open, not closed. A profile that searched no boards at all would look like an outage."""
        assert set(board.enabled_domains(profile(countries=[]), SITES)) == {"gradcracker.com", "brightnetwork.co.uk"}

    def test_an_untagged_board_serves_everywhere(self):
        """A site nobody has tagged yet shouldn't silently vanish from every search — that
        failure is indistinguishable from a board that stopped answering."""
        untagged = Site(domain="jobs.example", name="Example", terms_reviewed_on="2026-01-01")
        assert untagged.serves(["NL"]) is True

    def test_country_codes_are_compared_case_insensitively(self):
        gb = Site(domain="x.example", name="X", terms_reviewed_on="2026-01-01", countries=["gb"])
        assert gb.serves(["GB"]) and gb.serves(["gb"])


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

    def test_each_query_names_exactly_one_board(self, fake):
        """SRCH-11, and the reason this changed.

        One request carrying four `include_domains` and `limit: 10` lets the search engine's
        ranking decide the split — and it doesn't split. A real run came back almost entirely
        from the one board with the best SEO while three reviewed boards went unread and still
        cost their share of the credits. Ten slots each, or it isn't coverage.
        """
        client = fake(FakeClient())
        board.search(profile(), SITES, board.SearchCaps(), set())

        assert all(len(call["include_domains"]) == 1 for call in client.searches)
        asked = [call["include_domains"][0] for call in client.searches]
        assert sorted(asked) == ["brightnetwork.co.uk", "gradcracker.com"]

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

    def test_a_tracking_tag_does_not_make_a_known_job_new_again(self, fake):
        """SRCH-05 compares the posting, not the string.

        `known_apply_urls` compared raw strings while `dedupe.dedupe_key` compares
        `canonical_url`, so the same vacancy arriving with a `utm_` tag or a `www.` read as
        unseen: 5 credits to re-scrape it, and a fresh sighting that pushed the job's `last_seen`
        forward — the clock NORM-07 archives on.
        """
        stored = "https://gradcracker.com/jobs/7"
        tagged = "https://www.gradcracker.com/jobs/7/?utm_source=newsletter&utm_campaign=x"
        fake(FakeClient(results={'"ML Engineer" "London"': [tagged]}))
        assert board.search(profile(), SITES, board.SearchCaps(), known_urls={stored}).hits == []

    def test_two_spellings_of_one_url_in_a_run_count_once(self, fake):
        a = "https://www.gradcracker.com/jobs/7"
        b = "https://gradcracker.com/jobs/7?utm_medium=email"
        fake(FakeClient(results={'"ML Engineer" "London"': [a, b]}))
        assert len(board.search(profile(), SITES, board.SearchCaps(), set()).hits) == 1

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
        caps = board.SearchCaps(max_credits=board.SEARCH_CREDITS)
        outcome = board.search(profile(titles=["A", "B", "C"], locations=["X"]), SITES, caps, set())
        assert outcome.capped == "credits"
        # The call that hit the cap is counted, then it stops: one search, not three.
        assert outcome.credits_used == board.SEARCH_CREDITS
        assert outcome.queries_run == 1

    def test_credits_are_counted_in_the_units_firecrawl_bills_in(self, fake):
        """SRCH-06 only protects anyone if a "credit" here is a credit on the invoice: a search
        is 2 and a JSON-format scrape is 5, so one query plus one page is 7, not 2."""
        fake(FakeClient())
        # One title against one board, so this measures a credit rather than the grid.
        one_board = profile(titles=["A"], locations=["X"], sites=["gradcracker.com"])
        outcome = board.search(one_board, SITES, board.SearchCaps(), set())
        assert outcome.credits_used == 2
        board.scrape_card("https://www.gradcracker.com/jobs/1", {}, outcome, board.SearchCaps())
        assert outcome.credits_used == 7

    def test_a_page_cap_stops_scraping(self, fake):
        client = fake(FakeClient())
        outcome = board.SearchOutcome()
        caps = board.SearchCaps(max_pages=1)
        assert board.scrape_card("https://www.gradcracker.com/jobs/1", {}, outcome, caps) is not None
        assert board.scrape_card("https://www.gradcracker.com/jobs/2", {}, outcome, caps) is None
        assert outcome.capped == "pages"
        assert client.scrapes == ["https://www.gradcracker.com/jobs/1"]

    def test_the_dialog_estimate_and_the_cap_use_the_same_arithmetic(self):
        """A dialog that promises 7 credits while the cap counts 2 makes the cap meaningless."""
        queries, credits = board.estimate(profile(titles=["A", "B"], locations=["X", "Y"]), SITES)
        assert queries == 4
        assert credits == queries * (board.SEARCH_CREDITS + board.JSON_SCRAPE_CREDITS)


class TestTheCrawlCache:
    """SRCH-10, measured in credits through the real search path.

    The cache's own behaviour lives in `test_crawl_cache.py`; what matters here is that a hit
    costs nothing, that it doesn't consume a cap, and that the two things which used to be paid
    for on every run are not.
    """

    def cache(self, now: int = NOW, **over):
        from wera_agent.sources.crawl_cache import MINUTE_MS, CrawlCache

        base = {
            "search_ttl_ms": 360 * MINUTE_MS,
            "card_ttl_ms": 2880 * MINUTE_MS,
            "unreadable_ttl_ms": 2880 * MINUTE_MS,
        }
        return CrawlCache(now=now, **(base | over))

    def test_a_repeat_search_in_the_window_issues_no_query(self, fake, migrated):
        url = "https://www.gradcracker.com/jobs/7"
        client = fake(FakeClient(results={'"ML Engineer" "London"': [url]}))
        cache = self.cache()

        first = board.search(ONE_BOARD, SITES, board.SearchCaps(), set(), cache=cache)
        second = board.search(ONE_BOARD, SITES, board.SearchCaps(), set(), cache=cache)

        assert len(client.searches) == 1, "the second run re-asked a question it had the answer to"
        assert [h.url for h in second.hits] == [h.url for h in first.hits]
        assert (second.queries_run, second.credits_used) == (0, 0)
        assert (second.cached_queries, second.credits_saved) == (1, board.SEARCH_CREDITS)
        # What the Jobs screen shows: one query answered, not zero queries run.
        assert second.queries_answered == 1

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

    def test_a_failed_search_is_never_cached(self, fake, migrated, monkeypatch):
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[str] = []

        class Limited(FakeClient):
            def search(self, query, **kwargs):
                tries.append(query)
                raise type("RateLimitError", (Exception,), {})()

        fake(Limited())
        cache = self.cache()

        board.search(ONE_BOARD, SITES, board.SearchCaps(), set(), cache=cache)
        second = board.search(ONE_BOARD, SITES, board.SearchCaps(), set(), cache=cache)

        assert len(tries) == 2 * board.MAX_TRIES
        assert second.cached_queries == 0

    def test_a_cached_answer_is_exempt_from_every_cap(self, fake, migrated):
        """SRCH-06 bounds an invoice. A cached answer costs nothing, so it cannot be the thing
        that stops a run — and a run that spent nothing must not be reported as partial."""
        three = profile(titles=["A", "B", "C"], locations=["X"], sites=["gradcracker.com"])
        fake(FakeClient())
        cache = self.cache()

        board.search(three, SITES, board.SearchCaps(), set(), cache=cache)  # warm all three
        nothing_allowed = board.SearchCaps(max_queries=0, max_pages=0, max_credits=0)
        outcome = board.search(three, SITES, nothing_allowed, set(), cache=cache)

        assert outcome.cached_queries == 3
        assert outcome.queries_run == 0 and outcome.credits_used == 0
        assert outcome.capped is None and not outcome.partial

    def test_a_cap_no_longer_hides_a_free_answer(self, fake, migrated):
        """The reason a cap `continue`s instead of `break`ing: the queries after the one that hit
        the cap may be cached, and declining a free answer buys nobody anything."""
        third = '"C" "X"'
        url = "https://www.gradcracker.com/jobs/3"
        client = fake(FakeClient(results={third: [url]}))
        cache = self.cache()

        # Warm only the third query, by running it alone.
        board.search(
            profile(titles=["C"], locations=["X"], sites=["gradcracker.com"]), SITES, board.SearchCaps(), set(), cache=cache
        )
        client.searches.clear()

        three = profile(titles=["A", "B", "C"], locations=["X"], sites=["gradcracker.com"])
        outcome = board.search(three, SITES, board.SearchCaps(max_queries=1), set(), cache=cache)

        assert len(client.searches) == 1, "only the first query should have been sent"
        assert outcome.capped == "queries"  # the second one was genuinely refused
        assert outcome.cached_queries == 1  # but the third was still answered
        assert url in [h.url for h in outcome.hits]

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
        """SRCH-07: three tries, then this query is given up on — not the run."""
        monkeypatch.setattr(board.time, "sleep", lambda _s: None)
        tries: list[int] = []

        class Limited(FakeClient):
            def search(self, query, **kwargs):
                tries.append(1)
                raise type("RateLimitError", (Exception,), {})()

        fake(Limited())
        outcome = board.search(ONE_BOARD, SITES, board.SearchCaps(), set())
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
        board.search(ONE_BOARD, SITES, board.SearchCaps(), set())
        assert len(tries) == 1
