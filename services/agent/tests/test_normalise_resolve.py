"""Turning a source's output into a `Job`, and finding the original posting (NORM-01…04)."""

import pytest

from wera_agent.schemas.jobs import Ats, Location
from wera_agent.sources import normalise, resolve
from wera_agent.sources.ats_feeds import FeedJob

NOW = 1_760_000_000_000


class TestSalary:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Salary: £30,000 - £35,000 per annum", ("GBP", 30000, 35000, "year")),
            ("up to £45k a year", ("GBP", 45000, None, "year")),
            # A European ad writes thirty thousand as 30.000, which must not become 30.
            ("€55.000 per year", ("EUR", 55000, None, "year")),
            ("€48.500 - €55.000 annually", ("EUR", 48500, 55000, "year")),
            ("$120,000-$150,000", ("USD", 120000, 150000, "year")),
            ("£350 per day", ("GBP", 350, None, "day")),
            ("£18.50 an hour", ("GBP", 18.5, None, "hour")),
        ],
    )
    def test_real_formats_parse(self, text, expected):
        salary = normalise.parse_salary(text)
        assert salary is not None
        assert (salary.currency, salary.min, salary.max, salary.period) == expected

    @pytest.mark.parametrize("text", ["competitive salary", "see page 30 a year", "", None])
    def test_nothing_stated_means_no_salary(self, text):
        """An invented salary is worse than a missing one."""
        assert normalise.parse_salary(text) is None

    def test_a_reversed_range_is_put_the_right_way_round(self):
        salary = normalise.parse_salary("£35,000 - £30,000")
        assert salary and (salary.min, salary.max) == (30000, 35000)


class TestInference:
    @pytest.mark.parametrize(
        ("title", "level"),
        [
            ("Graduate ML Engineer", "graduate"),
            ("Junior Developer", "junior"),
            ("Software Engineer", None),
            ("Summer Internship, Data", "intern"),
        ],
    )
    def test_seniority_comes_from_the_ads_own_words(self, title, level):
        assert normalise.infer_seniority(title) == level

    def test_a_german_ad_is_detected_so_it_can_be_skipped(self):
        german = "Wir suchen einen Mitarbeiter und die Aufgaben sind vielfältig. Kenntnisse in Python. " * 3
        assert normalise.detect_language(german) == "de"

    def test_an_english_ad_is_english(self):
        english = "We are looking for a graduate engineer to join our team and build things. " * 3
        assert normalise.detect_language(english) == "en"

    def test_a_short_snippet_is_not_guessed_at(self):
        assert normalise.detect_language("Python") == "en"


class TestFromFeed:
    def test_a_watchlist_job_keeps_its_ats_identity(self):
        feed = FeedJob(
            ats=Ats(vendor="greenhouse", board_token="fernhillai", job_id="42"),
            title="ML Engineer, Graduate",
            company="Fernhill AI",
            location=Location(city="London", country="UK", raw="London, UK"),
            apply_url="https://job-boards.greenhouse.io/fernhillai/jobs/42",
            description="We build guardrails. " * 20,
            posted_at="2026-10-01T09:00:00Z",
            remote="hybrid",
        )
        job = normalise.from_feed(feed, NOW)
        assert job.dedupe_key == "ats:greenhouse:fernhillai:42"
        assert job.description_status == "full"
        assert job.sources[0].channel == "watchlist"
        assert job.seniority == "graduate"
        assert job.id == normalise.job_id("ats:greenhouse:fernhillai:42")


class TestFromCard:
    def test_an_unresolved_card_still_becomes_a_job(self):
        """NORM-03: a job with no description is still a job the person can look at."""
        job = normalise.from_card(
            title="ML Engineer",
            company="Fernhill AI",
            location_raw="London, UK",
            url="https://job-boards.greenhouse.io/fernhillai/jobs/42",
            detail="linkedin-alert",
            now=NOW,
        )
        assert job.description_status == "missing"
        assert job.description_md is None
        assert job.apply_url == "https://job-boards.greenhouse.io/fernhillai/jobs/42"

    def test_a_linkedin_link_is_kept_apart_from_the_apply_url(self):
        """MAIL-07: stored so the person can open it, never where WERA fetches from."""
        job = normalise.from_card(
            title="ML Engineer",
            company="Fernhill AI",
            location_raw="London",
            url="https://www.linkedin.com/jobs/view/123",
            detail="linkedin-alert",
            now=NOW,
        )
        assert job.apply_url is None
        assert job.linkedin_url == "https://www.linkedin.com/jobs/view/123"


class TestFromScrape:
    def test_a_page_without_a_company_is_not_a_job(self):
        assert normalise.from_scrape({"title": "ML Engineer"}, url="https://x/1", site="x", markdown="…", now=NOW) is None

    def test_a_scraped_page_becomes_a_board_search_job(self):
        job = normalise.from_scrape(
            {"title": "Graduate AI Engineer", "company": "Saltmarsh", "location": "Bristol, UK"},
            url="https://www.gradcracker.com/jobs/7",
            site="gradcracker.com",
            markdown="Python and FastAPI. " * 20,
            now=NOW,
        )
        assert job is not None
        assert job.sources[0].detail == "gradcracker.com"
        assert job.location.city == "Bristol"
        assert job.dedupe_key.startswith("url:")


class TestResolve:
    @pytest.mark.parametrize(
        ("url", "vendor", "token"),
        [
            ("https://job-boards.greenhouse.io/fernhillai/jobs/4461450008", "greenhouse", "fernhillai"),
            ("https://jobs.lever.co/saltmarsh/ad208490-4052-4f91-a57e-433f2d1e484b", "lever", "saltmarsh"),
            ("https://jobs.ashbyhq.com/veridian/7458d4e9-da2e-47bd-98cb-adfda43d42b2", "ashby", "veridian"),
        ],
    )
    def test_a_board_url_is_already_the_original(self, url, vendor, token):
        """NORM-01 step 1, and it costs nothing."""
        found = resolve.resolve(title="ML Engineer", company="Fernhill AI", url=url)
        assert found.how == "ats-url"
        assert found.ats and (found.ats.vendor, found.ats.board_token) == (vendor, token)

    def test_a_search_result_on_the_employers_board_is_accepted(self):
        found = resolve.resolve(
            title="ML Engineer, Graduate",
            company="Fernhill AI",
            url="https://www.linkedin.com/jobs/view/9",
            search_one=lambda _q: [("https://job-boards.greenhouse.io/fernhillai/jobs/42", "Graduate ML Engineer")],
        )
        assert found.how == "search"
        assert found.url.endswith("/42")

    @pytest.mark.parametrize(
        ("url", "result_title"),
        [
            # Right title, but the board token is a different employer.
            ("https://job-boards.greenhouse.io/someoneelse/jobs/1", "Graduate ML Engineer"),
            # Right employer, but a different job.
            ("https://job-boards.greenhouse.io/fernhillai/jobs/1", "Senior Data Scientist"),
            # An aggregator that is neither an ATS nor the employer.
            ("https://jobsaggregator.example/1", "ML Engineer, Graduate"),
            # LinkedIn is never a resolution target.
            ("https://www.linkedin.com/jobs/view/1", "ML Engineer, Graduate"),
        ],
    )
    def test_anything_less_than_confident_is_left_unresolved(self, url, result_title):
        """NORM-02: a CV tailored against the wrong ad is a confident wasted application."""
        found = resolve.resolve(
            title="ML Engineer, Graduate",
            company="Fernhill AI",
            url=None,
            search_one=lambda _q: [(url, result_title)],
        )
        assert found.how == "no confident match"
        assert found.ats is None

    def test_the_employers_own_careers_page_is_accepted(self):
        found = resolve.resolve(
            title="ML Engineer, Graduate",
            company="Fernhill AI",
            url=None,
            search_one=lambda _q: [("https://fernhillai.com/careers/ml-grad", "ML Engineer, Graduate")],
        )
        assert found.how == "search"

    def test_a_failed_search_leaves_the_card_unresolved(self):
        def boom(_q):
            raise RuntimeError("firecrawl down")

        found = resolve.resolve(title="ML Engineer", company="Fernhill AI", url=None, search_one=boom)
        assert found.how == "search failed"
        assert found.ats is None
