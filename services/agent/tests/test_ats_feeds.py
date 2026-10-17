"""The watchlist readers (ATS-01, ATS-03).

The payloads below are trimmed from live boards — Greenhouse (Anthropic), Lever (leverdemo) and
Ashby (ashby) — so these tests fail if a vendor changes shape, instead of a cover letter quietly
coming out with `<div>` in it.
"""

import httpx
import pytest
import respx

from wera_agent.config import Company
from wera_agent.schemas.jobs import Ats
from wera_agent.sources import ats_feeds

GH = Company(name="Fernhill AI", vendor="greenhouse", token="fernhillai")
LEVER = Company(name="Saltmarsh", vendor="lever", token="saltmarsh")
ASHBY = Company(name="Veridian", vendor="ashby", token="veridian")

# Greenhouse sends its HTML *escaped*, which is the detail that matters here.
GH_BODY = {
    "jobs": [
        {
            "id": 4461450008,
            "title": "ML Engineer, Graduate",
            "company_name": "Fernhill AI",
            "location": {"name": "London, UK; Remote | London, UK"},
            "absolute_url": "https://job-boards.greenhouse.io/fernhillai/jobs/4461450008",
            "first_published": "2026-10-01T09:00:00-04:00",
            "updated_at": "2026-10-12T09:00:00-04:00",
            "content": '&lt;div class="content-intro"&gt;&lt;h2&gt;&lt;strong&gt;About us&lt;/strong&gt;&lt;/h2&gt;\n&lt;p&gt;We build guardrails &amp;amp; evals.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;&lt;/div&gt;',
        }
    ]
}

LEVER_BODY = [
    {
        "id": "ad208490-4052",
        "text": "Graduate AI Engineer",
        "categories": {"location": "Atlanta, Georgia", "department": "Engineering"},
        "hostedUrl": "https://jobs.lever.co/saltmarsh/ad208490-4052",
        "applyUrl": "https://jobs.lever.co/saltmarsh/ad208490-4052/apply",
        "createdAt": 1788199939703,
        "descriptionPlain": "Join a scaling team. Python and FastAPI.",
        "workplaceType": "hybrid",
    }
]

ASHBY_BODY = {
    "jobs": [
        {
            "id": "7458d4e9",
            "title": "Software Engineer, Applied AI",
            "location": "Remote - European Union",
            "jobUrl": "https://jobs.ashbyhq.com/veridian/7458d4e9",
            "publishedAt": "2026-03-04T14:29:08.532+00:00",
            "descriptionPlain": "We ship evaluation tooling.",
            "isRemote": True,
            "isListed": True,
        },
        {"id": "hidden-1", "title": "Unlisted role", "isListed": False},
    ]
}


class TestGreenhouse:
    @respx.mock
    def test_it_reads_a_board_and_unescapes_the_description(self, outbound):
        respx.get(ats_feeds.GREENHOUSE.format(token="fernhillai")).mock(return_value=httpx.Response(200, json=GH_BODY))
        jobs, error = ats_feeds.fetch_company(GH)
        assert error is None
        (job,) = jobs
        assert job.title == "ML Engineer, Graduate"
        assert job.ats == Ats(vendor="greenhouse", board_token="fernhillai", job_id="4461450008")
        assert job.location.city == "London"
        # The whole point: readable text, no markup and no entities left behind.
        assert "<" not in job.description and "&amp;" not in job.description
        assert "About us" in job.description
        assert "We build guardrails & evals." in job.description
        assert "- Python" in job.description
        assert job.posted_at == "2026-10-01T09:00:00-04:00"  # first published, not last touched


class TestLever:
    @respx.mock
    def test_it_reads_a_bare_list_and_a_millisecond_timestamp(self, outbound):
        respx.get(ats_feeds.LEVER.format(token="saltmarsh")).mock(return_value=httpx.Response(200, json=LEVER_BODY))
        jobs, error = ats_feeds.fetch_company(LEVER)
        assert error is None
        (job,) = jobs
        assert job.ats.vendor == "lever"
        assert job.remote == "hybrid"
        assert job.posted_at and job.posted_at.startswith("20")
        assert " " not in job.description  # non-breaking spaces become ordinary ones


class TestAshby:
    @respx.mock
    def test_it_skips_an_unlisted_posting(self, outbound):
        respx.get(ats_feeds.ASHBY.format(token="veridian")).mock(return_value=httpx.Response(200, json=ASHBY_BODY))
        jobs, error = ats_feeds.fetch_company(ASHBY)
        assert error is None
        assert [j.title for j in jobs] == ["Software Engineer, Applied AI"]
        assert jobs[0].remote == "remote"


class TestFailures:
    @respx.mock
    def test_a_board_that_is_down_costs_only_its_own_jobs(self, outbound):
        respx.get(ats_feeds.GREENHOUSE.format(token="fernhillai")).mock(return_value=httpx.Response(503))
        respx.get(ats_feeds.LEVER.format(token="saltmarsh")).mock(return_value=httpx.Response(200, json=LEVER_BODY))
        result = ats_feeds.fetch_all([GH, LEVER])
        assert len(result.jobs) == 1
        assert len(result.errors) == 1 and "Fernhill AI" in result.errors[0]
        # Only the board that answered is eligible to close anything.
        assert result.answered == {"lever:saltmarsh"}

    @respx.mock
    def test_an_unreadable_shape_is_not_a_crash(self, outbound):
        respx.get(ats_feeds.GREENHOUSE.format(token="fernhillai")).mock(return_value=httpx.Response(200, json={"jobs": "nope"}))
        jobs, error = ats_feeds.fetch_company(GH)
        assert jobs == []
        assert error and "couldn't read" in error

    def test_demo_mode_makes_no_request(self, client):
        """DEMO-02: the shared HTTP client refuses before a socket is opened."""
        from wera_agent.errors import ApiError

        with pytest.raises(ApiError) as caught:
            ats_feeds.request("GET", ats_feeds.GREENHOUSE.format(token="fernhillai"))
        assert caught.value.code == "DEMO_MODE"


class TestClosedDetection:
    @respx.mock
    def test_a_job_gone_from_a_feed_that_answered_is_closed(self, outbound):
        """ATS-03."""
        respx.get(ats_feeds.LEVER.format(token="saltmarsh")).mock(return_value=httpx.Response(200, json=LEVER_BODY))
        result = ats_feeds.fetch_all([LEVER])
        known = {
            "job-still-open": Ats(vendor="lever", board_token="saltmarsh", job_id="ad208490-4052"),
            "job-gone": Ats(vendor="lever", board_token="saltmarsh", job_id="vanished"),
        }
        assert ats_feeds.closed_tokens(result, known) == ["job-gone"]

    @respx.mock
    def test_a_board_outage_never_closes_a_job(self, outbound):
        """Absence of evidence isn't evidence the vacancy went away."""
        respx.get(ats_feeds.LEVER.format(token="saltmarsh")).mock(return_value=httpx.Response(500))
        result = ats_feeds.fetch_all([LEVER])
        known = {"job-gone": Ats(vendor="lever", board_token="saltmarsh", job_id="vanished")}
        assert ats_feeds.closed_tokens(result, known) == []
