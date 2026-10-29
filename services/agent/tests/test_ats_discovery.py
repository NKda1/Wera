"""The watchlist that maintains itself (ATS-05) and the title filter in front of it (MATCH-01).

These two go together. Discovery makes the watchlist big — potentially sixty boards, every
vacancy each company has — and the title filter is what stops that volume reaching a paid
model. Either one without the other is a mistake: the filter alone leaves a list nobody
maintains, and discovery alone turns a good source into a bill.
"""

import shutil
from datetime import date

import pytest

from wera_agent.config import CompaniesFile, Company, companies, companies_file, write_companies
from wera_agent.domain import filters
from wera_agent.schemas.profile import SearchProfile
from wera_agent.sources import ats_discovery
from wera_agent.sources.ats_discovery import Candidate

from .test_intake_domain import job, profile


def company(token: str, vendor: str = "greenhouse", **kw) -> Company:
    return Company(name=kw.pop("name", token.title()), vendor=vendor, token=token, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MATCH-01: is this the kind of job I asked for?


class TestTitleRelevance:
    TARGETS = ["Software Engineer", "AI Engineer", "ML Engineer", "Backend Engineer (Python)"]

    @pytest.mark.parametrize(
        "title",
        [
            "Software Engineer",
            "Software Engineer II",  # a level marker is not a different job
            "Senior Software Engineer, Payments",  # more specific is fine
            "Graduate Software Engineer",
            "Software Developer",  # the same work under another name
            "Machine Learning Engineer",  # "ML Engineer" expanded
            "AI Engineer, Platform",
            "Backend Engineer",  # the target's bracketed note isn't a requirement
            "SWE III",
        ],
    )
    def test_a_posting_the_person_is_looking_for_is_kept(self, title):
        assert ats_discovery and filters.matches_a_target(title, self.TARGETS), title

    @pytest.mark.parametrize(
        "title",
        [
            "Account Executive",
            "Senior Recruiter",
            "Warehouse Operative",
            "Product Designer",
            "Sales Engineer",  # shares a word, not the job
            "Engineering Manager",
            "Data Scientist",
            "Customer Success Manager",
        ],
    )
    def test_a_posting_from_a_different_department_is_hidden(self, title):
        assert not filters.matches_a_target(title, self.TARGETS), title

    def test_a_posting_may_be_more_specific_than_the_target_but_not_less(self):
        """ "AI Engineer" should not be satisfied by a bare "Engineer" — that direction would
        let every vacancy at a watched company through, which is the problem, not the fix."""
        assert filters.matches_a_target("AI Engineer, Safety", ["AI Engineer"])
        assert not filters.matches_a_target("Engineer", ["AI Engineer"])

    def test_no_target_titles_hides_nothing(self):
        """An empty profile hasn't been set up; it is not a request to hide every job. The
        policy lives in `title_mismatch` — `matches_a_target` stays honest about the fact that
        nothing matches an empty list."""
        assert not filters.matches_a_target("Account Executive", [])
        # PRF-03 won't let a saved profile have no titles at all, so whitespace is the shape
        # this really arrives in; `model_construct` covers the defensive branch behind it.
        assert filters.title_mismatch("Account Executive", profile(titles=["  "])) is None
        assert filters.title_mismatch("Account Executive", SearchProfile.model_construct(titles=[])) is None

    def test_plurals_do_not_change_the_job(self):
        assert filters.matches_a_target("Software Engineers", ["Software Engineer"])
        assert filters.matches_a_target("Software Engineer", ["Software Engineers"])

    def test_the_reason_names_the_title_so_a_person_can_disagree(self):
        reason = filters.hidden_reason(job(title="Account Executive"), profile(titles=["ML Engineer"]))
        assert reason == "Not one of your target titles: Account Executive"

    def test_seniority_is_reported_before_being_off_target(self):
        """Both are true of "Head of Data"; "more senior" is the sharper thing to be told,
        because it means the work is right and the level isn't."""
        reason = filters.hidden_reason(job(title="Head of Data"), profile(titles=["ML Engineer"]))
        assert reason and "more senior" in reason

    def test_an_untitled_posting_is_left_for_the_scorer(self):
        assert filters.matches_a_target("", ["ML Engineer"])
        assert filters.matches_a_target("   ", ["ML Engineer"])


# ---------------------------------------------------------------------------
# ATS-05: finding the boards


class TestReadingBoardUrls:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://job-boards.greenhouse.io/monzo/jobs/4471023", ("greenhouse", "monzo")),
            ("https://boards.greenhouse.io/monzo/jobs/4471023", ("greenhouse", "monzo")),
            ("https://jobs.lever.co/monzo/8f3c1a2b-0000", ("lever", "monzo")),
            ("https://jobs.ashbyhq.com/monzo/8f3c1a2b", ("ashby", "monzo")),
            ("https://job-boards.greenhouse.io/monzo", ("greenhouse", "monzo")),
            ("https://job-boards.greenhouse.io/monzo/jobs/1?utm_source=x", ("greenhouse", "monzo")),
        ],
    )
    def test_the_board_token_is_the_first_path_segment(self, url, expected):
        assert ats_discovery.board_from_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/jobs/view/4100000001",  # never, under any circumstances
            "https://example.com/monzo/jobs/1",
            "https://job-boards.greenhouse.io/embed/job_board?for=monzo",  # the vendor's own page
            "https://jobs.lever.co/",
            "not a url",
            "",
        ],
    )
    def test_anything_that_is_not_an_employer_board_is_ignored(self, url):
        assert ats_discovery.board_from_url(url) is None

    def test_one_query_per_title_not_a_title_by_location_grid(self):
        """A board's URL is the same whichever office the vacancy is in, so multiplying by
        location would multiply the credits for nothing."""
        queries = ats_discovery.queries_for(["AI Engineer", "ML Engineer"], ["London", "Dublin", "Berlin", "Zurich"])
        assert len(queries) == 2
        assert queries[0].startswith('"AI Engineer"')
        assert "Zurich" not in queries[0]  # at most three locations as context


class TestMergingTheWatchlist:
    def test_a_hand_added_company_is_never_removed(self):
        """The single most important rule here: discovery rewrites the file, and a person's
        own entries have to survive that or nobody would ever trust it."""
        mine = [company("fernhillai"), company("saltmarsh", "lever")]
        found = [company(f"found{n}", discovered_on=date(2026, 10, 21)) for n in range(70)]
        merged = ats_discovery.merge(mine, found, max_companies=10)
        assert [c.token for c in merged[:2]] == ["fernhillai", "saltmarsh"]
        assert all(c.manual for c in merged[:2])

    def test_a_pinned_discovery_survives_too(self):
        pinned = company("monzo", discovered_on=date(2026, 1, 1), pinned=True)
        merged = ats_discovery.merge([pinned], [], max_companies=10)
        assert [c.token for c in merged] == ["monzo"]

    def test_the_cap_bounds_the_daily_run(self):
        """Every company here is an HTTP request every morning."""
        found = [company(f"c{n}", discovered_on=date(2026, 10, 21)) for n in range(100)]
        assert len(ats_discovery.merge([], found, max_companies=12)) == 12

    def test_a_board_already_known_keeps_the_date_it_was_first_found(self):
        """The list should read as a history, not as a timestamp of the last run."""
        old = company("monzo", discovered_on=date(2026, 1, 1), name="Monzo Bank")
        again = company("monzo", discovered_on=date(2026, 10, 21), name="Monzo")
        (merged,) = ats_discovery.merge([old], [again], max_companies=10)
        assert merged.discovered_on == date(2026, 1, 1)
        assert merged.name == "Monzo Bank"  # and a name a person corrected stays corrected

    def test_a_board_that_stopped_answering_drops_off(self):
        """Discovery confirms every candidate against the live feed, so a board that no longer
        answers simply isn't in `found` — and a file fetched every morning stays clean."""
        gone = company("closedco", discovered_on=date(2026, 1, 1))
        still = company("monzo", discovered_on=date(2026, 10, 21))
        merged = ats_discovery.merge([gone, still], [still], max_companies=10)
        assert [c.token for c in merged] == ["monzo"]

    def test_a_manual_entry_is_not_duplicated_when_discovery_finds_it_too(self):
        mine = company("monzo")
        merged = ats_discovery.merge([mine], [company("monzo", discovered_on=date(2026, 10, 21))], max_companies=10)
        assert len(merged) == 1
        assert merged[0].manual


class TestConfirming:
    def test_a_board_that_answers_with_jobs_is_kept_and_named_from_its_feed(self, monkeypatch):
        from wera_agent.schemas.jobs import Ats, Location
        from wera_agent.sources import ats_feeds

        def feed(company_in):
            return [
                ats_feeds.FeedJob(
                    ats=Ats(vendor="greenhouse", board_token=company_in.token, job_id="1"),
                    title="Software Engineer",
                    company="Monzo Bank",
                    location=Location(raw="London"),
                    apply_url="https://job-boards.greenhouse.io/monzo/jobs/1",
                    description="",
                    posted_at=None,
                )
            ], None

        monkeypatch.setattr(ats_feeds, "fetch_company", feed)
        found = ats_discovery.confirm(Candidate("greenhouse", "monzo", "Software Engineer"), date(2026, 10, 21))
        assert found and found.name == "Monzo Bank"
        assert found.discovered_on == date(2026, 10, 21)
        assert found.matched == "Software Engineer"

    @pytest.mark.parametrize(("jobs", "error"), [([], None), ([], "404")])
    def test_a_board_that_does_not_answer_is_not_added(self, monkeypatch, jobs, error):
        """A search result proves a URL was indexed once, not that the board exists today."""
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(ats_feeds, "fetch_company", lambda c: (jobs, error))
        assert ats_discovery.confirm(Candidate("greenhouse", "ghost", "X"), date(2026, 10, 21)) is None


class TestWritingTheFile:
    def test_the_file_round_trips_and_keeps_its_explanation(self, tmp_path, monkeypatch):
        from wera_agent import config

        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        data = CompaniesFile(
            watch_titles=["AI Engineer"],
            companies=[company("fernhillai"), company("monzo", discovered_on=date(2026, 10, 21), matched="AI Engineer")],
        )
        path = write_companies(data)
        text = path.read_text()
        assert "WERA MAINTAINS THIS LIST" in text
        assert "Your own entries are safe" in text

        config.companies_file.cache_clear()
        config.companies.cache_clear()
        reloaded = companies_file()
        assert reloaded.watch_titles == ["AI Engineer"]
        assert [c.token for c in reloaded.companies] == ["fernhillai", "monzo"]
        assert reloaded.companies[0].manual and not reloaded.companies[1].manual
        assert reloaded.companies[1].discovered_on == date(2026, 10, 21)
        assert [c.token for c in companies()] == ["fernhillai", "monzo"]

    def test_a_disabled_company_stays_in_the_file_but_out_of_the_run(self, tmp_path, monkeypatch):
        from wera_agent import config

        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        write_companies(CompaniesFile(companies=[company("off", enabled=False), company("on")]))
        config.companies_file.cache_clear()
        config.companies.cache_clear()
        assert [c.token for c in companies_file().companies] == ["off", "on"]
        assert [c.token for c in companies()] == ["on"]


class TestWhenDiscoveryRuns:
    def test_it_never_runs_in_demo_mode(self):
        """DEMO-02: a demo that quietly searched the web would be a lie."""
        from wera_agent.graphs import intake

        assert intake.discovery_due(None, 1_760_000_000_000) is False

    def test_it_runs_on_an_install_that_has_never_done_it(self, outbound):
        from wera_agent.graphs import intake

        assert intake.discovery_due(None, 1_760_000_000_000) is True

    def test_it_waits_the_configured_number_of_days(self, outbound):
        from wera_agent.clock import iso
        from wera_agent.graphs import intake

        now = 1_760_000_000_000
        day = 24 * 60 * 60 * 1000
        assert intake.discovery_due(iso(now - 2 * day), now) is False
        assert intake.discovery_due(iso(now - 8 * day), now) is True

    def test_an_unreadable_timestamp_runs_rather_than_never_running_again(self, outbound):
        from wera_agent.graphs import intake

        assert intake.discovery_due("not a date", 1_760_000_000_000) is True


class TestWatchTitles:
    def test_it_falls_back_to_the_search_profile(self):
        """Which is what ties the watchlist to the CV: a new CV, an approved profile, and the
        boards WERA watches follow."""
        from wera_agent.graphs import intake

        assert intake.watch_titles(profile(titles=["AI Engineer", "ML Engineer"])) == ["AI Engineer", "ML Engineer"]

    def test_the_file_wins_when_it_says_something(self, tmp_path, monkeypatch):
        from wera_agent import config
        from wera_agent.graphs import intake

        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        write_companies(CompaniesFile(watch_titles=["Research Engineer"], companies=[]))
        config.companies_file.cache_clear()
        assert intake.watch_titles(profile(titles=["AI Engineer"])) == ["Research Engineer"]


class TestInsideAnIntakeRun:
    """Discovery as the watchlist channel actually runs it."""

    @pytest.fixture
    def watchlist(self, tmp_path, monkeypatch, outbound):
        """A real (non-demo) install with its own companies.yaml and a stubbed Firecrawl."""
        from wera_agent import config
        from wera_agent.sources import ats_discovery as disc

        # A copy of the real config, so anything that reads it still works, and a companies.yaml
        # this test can rewrite without touching the repo's.
        for name in ("sites.yaml", "senders.yaml", "wera.yaml", "companies.yaml"):
            shutil.copy(config.CONFIG_DIR / name, tmp_path / name)
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        write_companies(CompaniesFile(watch_titles=["ML Engineer"], companies=[company("mine")]))
        config.companies_file.cache_clear()
        config.companies.cache_clear()

        found: list[str] = []

        class Result:
            web = [
                type("Item", (), {"url": "https://job-boards.greenhouse.io/monzo/jobs/1"})(),
                type("Item", (), {"url": "https://jobs.lever.co/wise/abc"})(),
                type("Item", (), {"url": "https://www.linkedin.com/jobs/view/1"})(),
            ]

        class Client:
            def search(self, query, **kw):
                found.append(query)
                return Result()

        monkeypatch.setattr(disc, "_client", lambda: Client())
        monkeypatch.setattr(
            disc,
            "confirm",
            lambda c, today: Company(
                name=c.token.title(), vendor=c.vendor, token=c.token, discovered_on=today, matched=c.matched
            ),
        )
        yield found
        config.companies_file.cache_clear()
        config.companies.cache_clear()

    def test_a_discovery_pass_writes_the_boards_it_found(self, watchlist, monkeypatch):
        from wera_agent.graphs import intake
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(ats_feeds, "fetch_all", lambda cs: ats_feeds.FeedResult())
        out = intake.from_watchlist(profile(titles=["ML Engineer"]), 1_760_000_000_000, discover=True)

        assert watchlist, "no search was run"
        assert out.queries == 1
        assert out.credits_used == 2  # one search, at Firecrawl's own rate
        assert out.discovered == 2  # monzo and wise; the LinkedIn result was never a candidate
        assert [c.token for c in companies()] == ["mine", "monzo", "wise"]

    def test_linkedin_can_never_become_a_watched_board(self, watchlist, monkeypatch):
        """SRCH-03/SEC-07: not a preference, and not something a search result can change."""
        from wera_agent.graphs import intake
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(ats_feeds, "fetch_all", lambda cs: ats_feeds.FeedResult())
        intake.from_watchlist(profile(), 1_760_000_000_000, discover=True)
        assert not any("linkedin" in c.token.lower() for c in companies())

    def test_the_watchlist_still_runs_when_discovery_fails(self, watchlist, monkeypatch):
        """A board search that fell over must not cost the feeds that were going to work."""
        from wera_agent.graphs import intake
        from wera_agent.sources import ats_discovery as disc
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(disc, "discover", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(ats_feeds, "fetch_all", lambda cs: ats_feeds.FeedResult())
        out = intake.from_watchlist(profile(), 1_760_000_000_000, discover=True)
        assert [code for code, _ in out.errors] == ["BOARD_DISCOVERY"]
        assert out.feed is not None  # the feeds were still fetched

    def test_not_discovering_costs_no_credits(self, watchlist, monkeypatch):
        from wera_agent.graphs import intake
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(ats_feeds, "fetch_all", lambda cs: ats_feeds.FeedResult())
        out = intake.from_watchlist(profile(), 1_760_000_000_000, discover=False)
        assert (out.queries, out.credits_used, out.discovered) == (0, 0, 0)
        assert not watchlist


def test_a_profile_fixture_is_a_real_search_profile():
    """Guards the shared fixture these tests lean on."""
    assert isinstance(profile(), SearchProfile)
