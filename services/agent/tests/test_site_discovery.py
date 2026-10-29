"""Site discovery (SRCH-12) and the gate it must never open (SRCH-04).

The test that matters more than every other one here: **a discovered board is written unreviewed
and is not searched.** SRCH-04 is a MUST and SPEC §11.3 ties it to a person having read a site's
terms and robots.txt. If discovery could make a site searchable, this feature would be WERA
deciding on someone's behalf that a third party's terms are acceptable.
"""

from datetime import date

import pytest

from wera_agent import config
from wera_agent.clock import iso
from wera_agent.config import Site, SitesFile
from wera_agent.schemas.profile import ProfileChannels, SearchProfile
from wera_agent.sources import firecrawl_search as board
from wera_agent.sources import site_discovery as sd

NOW = 1_760_000_000_000
TODAY = date(2026, 10, 28)

EXISTING = [
    Site(domain="gradcracker.com", name="Gradcracker", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="declined.example", name="Declined", declined_on="2026-02-02", discovered_on="2026-02-01"),
]


def profile(**over) -> SearchProfile:
    base = {
        "titles": ["ML Engineer", "AI Engineer"],
        "skills": ["Python"],
        "locations": ["London"],
        "remote": "any",
        "seniority": ["graduate"],
        "exclude": [],
        "sites": ["gradcracker.com"],
        "recency": "7d",
        "channels": ProfileChannels(board_search=True, inbox=True, watchlist=True),
        "generated_from_cv_version": 1,
        "updated_at": iso(NOW),
    }
    return SearchProfile.model_validate(base | over)


class FakeResult:
    def __init__(self, urls):
        self.web = [type("I", (), {"url": u})() for u in urls]


class FakeClient:
    """Answers each query with a fixed list of result URLs, and records what was asked."""

    def __init__(self, per_query: dict[str, list[str]]):
        self.searches: list[dict] = []
        self._per_query = per_query

    def search(self, query, **kwargs):
        self.searches.append({"query": query, **kwargs})
        return FakeResult(self._per_query.get(query, []))


@pytest.fixture
def fake(monkeypatch):
    def install(per_query):
        client = FakeClient(per_query)
        monkeypatch.setattr(sd, "_client", lambda: client)
        # robots.txt always allows, unless a test overrides it.
        monkeypatch.setattr(sd, "confirm", lambda c, o: True)
        return client

    return install


def two_queries(urls_a: list[str], urls_b: list[str]) -> dict[str, list[str]]:
    return {'"ML Engineer" jobs "London"': urls_a, '"AI Engineer" jobs "London"': urls_b}


class TestWhatCountsAsABoard:
    def test_a_host_found_by_two_titles_with_two_postings_is_proposed(self, fake):
        fake(
            two_queries(
                ["https://niche.example/jobs/1", "https://niche.example/jobs/2"],
                ["https://niche.example/jobs/3"],
            )
        )
        found = sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome())
        assert [c.domain for c in found] == ["niche.example"]

    def test_one_posting_is_a_careers_page_not_a_board(self, fake):
        """A company's own careers page contributes exactly one path. That is the whole test."""
        fake(two_queries(["https://acme.example/careers/ml-engineer"], ["https://acme.example/careers/ml-engineer"]))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []

    def test_one_title_is_one_employer_not_a_board(self, fake):
        """A single employer's portal can usually answer one of your titles; a board answers
        several. This is why the two queries deliberately use different titles."""
        fake(two_queries(["https://acme.example/jobs/1", "https://acme.example/jobs/2"], []))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []

    def test_a_host_with_no_posting_shaped_path_is_not_a_board(self, fake):
        """An article about hiring trends matches the words and is not a job board."""
        fake(two_queries(["https://blog.example/2026/hiring"], ["https://blog.example/2026/salaries"]))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []


class TestWhatIsNeverProposed:
    @pytest.mark.parametrize(
        "host",
        ["www.linkedin.com", "uk.linkedin.com", "google.com", "news.google.com", "reddit.com", "indeed.com", "glassdoor.co.uk"],
    )
    def test_denied_and_noise_hosts(self, fake, host):
        urls = [f"https://{host}/jobs/1", f"https://{host}/jobs/2"]
        fake(two_queries(urls, urls))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []

    @pytest.mark.parametrize("host", ["job-boards.greenhouse.io", "jobs.lever.co", "acme.myworkdayjobs.com", "x.teamtailor.com"])
    def test_single_employer_ats_hosts_belong_to_ats_05(self, fake, host):
        """A Workday hit is a signal for employer discovery, not a job board to add."""
        urls = [f"https://{host}/jobs/1", f"https://{host}/jobs/2"]
        fake(two_queries(urls, urls))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []

    def test_a_board_already_in_the_file(self, fake):
        urls = ["https://gradcracker.com/jobs/1", "https://www.gradcracker.com/jobs/2"]
        fake(two_queries(urls, urls))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []

    def test_a_board_the_person_declined_is_never_offered_again(self, fake):
        """Otherwise a decline costs one click a week for ever instead of one click once."""
        urls = ["https://declined.example/jobs/1", "https://declined.example/jobs/2"]
        fake(two_queries(urls, urls))
        assert sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome()) == []


class TestTheRequest:
    def test_srch_03_is_sent_as_an_exclude_list(self, fake):
        """The one place WERA can enforce it in the request: discovery carries no include list,
        and Firecrawl only refuses a request carrying both."""
        client = fake(two_queries([], []))
        sd.search_hosts(profile(), EXISTING, sd.DiscoveryOutcome())
        assert all(call["exclude_domains"] == ["linkedin.com"] for call in client.searches)
        assert all("include_domains" not in call for call in client.searches)

    def test_it_asks_different_titles_and_stops_at_the_cap(self, fake):
        client = fake(two_queries([], []))
        sd.search_hosts(profile(titles=["A", "B", "C", "D"]), EXISTING, sd.DiscoveryOutcome())
        assert len(client.searches) == sd.MAX_QUERIES
        assert len({c["query"] for c in client.searches}) == sd.MAX_QUERIES

    def test_the_geography_clause_is_the_one_real_searches_use(self, fake):
        client = fake({})
        sd.search_hosts(profile(locations=["London", "Berlin"]), EXISTING, sd.DiscoveryOutcome())
        assert '("London" OR "Berlin")' in client.searches[0]["query"]

    def test_no_titles_means_no_queries(self, fake):
        client = fake({})
        assert sd.search_hosts(profile(titles=["  "]), EXISTING, sd.DiscoveryOutcome()) == []
        assert client.searches == []


class TestRobotsTxt:
    def test_a_blanket_disallow_is_rejected_outright(self):
        """Keeps the half of SRCH-04 a machine can judge honest, and keeps the queue short."""
        assert sd._DISALLOW_ALL.search("User-agent: *\nDisallow: /\n")

    def test_a_partial_disallow_is_fine(self):
        assert not sd._DISALLOW_ALL.search("User-agent: *\nDisallow: /admin\nAllow: /\n")

    def test_a_rule_for_another_crawler_is_not_about_us(self):
        assert not sd._DISALLOW_ALL.search("User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp\n")


class TestDiscoverProposesButNeverEnables:
    def one_board(self, fake):
        fake(
            two_queries(
                ["https://niche.example/jobs/1", "https://niche.example/jobs/2"],
                ["https://niche.example/jobs/3"],
            )
        )
        return SitesFile(sites=list(EXISTING))

    def test_every_proposal_is_written_unreviewed(self, fake):
        """SRCH-04, and the single most important assertion in this file."""
        merged, outcome = sd.discover(profile(), self.one_board(fake), today=TODAY)
        assert len(outcome.found) == 1
        proposed = next(s for s in merged.sites if s.domain == "niche.example")
        assert proposed.terms_reviewed_on is None
        assert proposed.reviewed is False
        assert proposed.proposed is True
        assert proposed.discovered_on == TODAY

    def test_a_proposed_board_is_not_searched(self, fake):
        """The gate end to end: it is in the file, and `usable_sites` still won't ask it."""
        merged, _ = sd.discover(profile(), self.one_board(fake), today=TODAY)
        enabled = profile(sites=[s.domain for s in merged.sites])  # even if the profile lists it
        assert "niche.example" not in board.enabled_domains(enabled, merged.sites)

    def test_a_proposal_claims_no_country(self, fake):
        """Tagging it from the profile's countries would look better-evidenced than it is: one
        GB-flavoured query answered by a global board would pin it to GB, and SRCH-11 would then
        skip it for a Berlin search — a false negative indistinguishable from a broken board."""
        merged, _ = sd.discover(profile(countries=["GB"]), self.one_board(fake), today=TODAY)
        assert next(s for s in merged.sites if s.domain == "niche.example").countries == []

    def test_it_records_what_matched_so_a_review_has_something_to_look_at(self, fake):
        _, outcome = sd.discover(profile(), self.one_board(fake), today=TODAY)
        found = outcome.found[0]
        assert found.matched in ("ML Engineer", "AI Engineer")
        assert found.examples and all(e.startswith("https://niche.example/jobs/") for e in found.examples)
        assert len(found.examples) <= 2

    def test_discovery_is_append_only(self, fake):
        """It can never cost someone a board they chose, which is why there is no `pinned`."""
        before = self.one_board(fake)
        merged, _ = sd.discover(profile(), before, today=TODAY)
        assert [s.domain for s in merged.sites][: len(before.sites)] == [s.domain for s in before.sites]

    def test_a_host_robots_turns_away_is_never_written(self, fake, monkeypatch):
        fake(
            two_queries(
                ["https://niche.example/jobs/1", "https://niche.example/jobs/2"],
                ["https://niche.example/jobs/3"],
            )
        )
        monkeypatch.setattr(sd, "confirm", lambda c, o: False)
        merged, outcome = sd.discover(profile(), SitesFile(sites=list(EXISTING)), today=TODAY)
        assert outcome.found == []
        assert [s.domain for s in merged.sites] == [s.domain for s in EXISTING]

    def test_a_full_file_proposes_nothing(self, fake):
        fake(two_queries([], []))
        full = SitesFile(max_sites=1, sites=[EXISTING[0]])
        _, outcome = sd.discover(profile(), full, today=TODAY)
        assert outcome.found == [] and outcome.queries == 0

    def test_a_declined_entry_does_not_count_against_the_ceiling(self, fake):
        """Declining a board should not permanently use up a slot."""
        fake(
            two_queries(
                ["https://niche.example/jobs/1", "https://niche.example/jobs/2"],
                ["https://niche.example/jobs/3"],
            )
        )
        file = SitesFile(max_sites=2, sites=list(EXISTING))  # one live, one declined
        _, outcome = sd.discover(profile(), file, today=TODAY)
        assert len(outcome.found) == 1


class TestWriteSites:
    def test_an_explicit_null_review_date_survives_the_round_trip(self, tmp_path, monkeypatch):
        """The line the whole file exists to be unambiguous about.

        `write_companies` dumps with `exclude_none=True`; copying that here would omit
        `terms_reviewed_on` entirely, which reads to a human as "no opinion" rather than "not
        reviewed, do not search".
        """
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.sites_file.cache_clear()
        config.sites.cache_clear()

        config.write_sites(SitesFile(sites=[Site(domain="niche.example", name="Niche", discovered_on=TODAY)]))
        text = (tmp_path / "sites.yaml").read_text()
        assert "terms_reviewed_on: null" in text
        assert config.sites_file().sites[0].terms_reviewed_on is None

    def test_both_caches_are_cleared(self, tmp_path, monkeypatch):
        """`sites()` reads through `sites_file()` and each is cached separately, so a write that
        cleared one would be invisible for the life of the process."""
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.write_sites(SitesFile(sites=[Site(domain="a.example", name="A", terms_reviewed_on=TODAY)]))
        assert [s.domain for s in config.sites()] == ["a.example"]

        config.write_sites(SitesFile(sites=[Site(domain="b.example", name="B", terms_reviewed_on=TODAY)]))
        assert [s.domain for s in config.sites()] == ["b.example"]

    def test_linkedin_can_never_be_written(self, tmp_path, monkeypatch):
        """SRCH-03, re-applied on the way out so a careless caller can't get it into the file."""
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.write_sites(SitesFile(sites=[Site(domain="linkedin.com", name="No"), Site(domain="jobs.linkedin.com", name="No")]))
        assert config.sites_file().sites == []

    def test_the_header_explains_the_gate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.write_sites(SitesFile(sites=[]))
        text = (tmp_path / "sites.yaml").read_text()
        assert "terms_reviewed_on` IS THE GATE" in text


class TestReviewingAProposal:
    """SRCH-04 through the API. Approving is the only thing that can make a board searchable."""

    @pytest.fixture
    def proposed(self, tmp_path, monkeypatch, api):
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.write_sites(
            SitesFile(
                sites=[
                    Site(domain="niche.example", name="Niche", discovered_on=TODAY, matched="ML Engineer"),
                    Site(domain="gradcracker.com", name="Gradcracker", terms_reviewed_on=TODAY),
                ]
            )
        )
        return api

    def test_approving_records_the_date_and_enables_the_board(self, proposed):
        """Both halves. `usable_sites` needs the review date *and* the profile to list it — a
        reviewed board the profile doesn't carry is still never searched, so an approval that set
        only the date would visibly do nothing."""
        sites = proposed.ok("POST", "/sites/niche.example/review", {"action": "approve"})
        entry = next(s for s in sites if s["domain"] == "niche.example")
        assert entry["reviewed"] is True and entry["proposed"] is False
        assert "niche.example" in proposed.ok("GET", "/profile")["sites"]

    def test_declining_remembers_the_no_and_does_not_enable_it(self, proposed):
        sites = proposed.ok("POST", "/sites/niche.example/review", {"action": "decline"})
        entry = next(s for s in sites if s["domain"] == "niche.example")
        assert entry["declined"] is True and entry["reviewed"] is False
        assert "niche.example" not in proposed.ok("GET", "/profile")["sites"]

    def test_a_second_decision_is_refused(self, proposed):
        proposed.ok("POST", "/sites/niche.example/review", {"action": "approve"})
        assert proposed.code("POST", "/sites/niche.example/review", {"action": "approve"}) == "INVALID_STATE"

    def test_an_unknown_board_cannot_be_created_by_the_route(self, proposed):
        """A route that could invent a site would be a route that could be talked into searching
        anything."""
        assert proposed.code("POST", "/sites/invented.example/review", {"action": "approve"}) == "NOT_FOUND"
        # And it is still not in the file afterwards.
        assert "invented.example" not in [s["domain"] for s in proposed.ok("GET", "/sites")]

    def test_linkedin_can_never_be_approved(self, proposed):
        assert proposed.code("POST", "/sites/linkedin.com/review", {"action": "approve"}) in ("NOT_FOUND", "LINKEDIN_BLOCKED")

    def test_a_bad_action_is_a_validation_error(self, proposed):
        assert proposed.code("POST", "/sites/niche.example/review", {"action": "maybe"}) == "VALIDATION"


@pytest.fixture(autouse=True)
def _forget_config_caches():
    """`write_sites` clears the real caches; make sure a test never leaks a tmp_path file."""
    yield
    config.sites_file.cache_clear()
    config.sites.cache_clear()
