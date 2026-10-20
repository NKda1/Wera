"""Manual import (ATS-04).

Three paths with three different levels of trust: an ATS link gives the employer's own fields, a
plain link is scraped once, and a paste is taken as given. What these assert most is what WERA
*doesn't* do — open LinkedIn, and invent the details it wasn't told.
"""

import httpx
import pytest
import respx

from wera_agent.clock import now_ms
from wera_agent.errors import ApiError
from wera_agent.sources import manual

NOW = now_ms()

GH_BODY = {
    "jobs": [
        {
            "id": 4461450008,
            "title": "ML Engineer, Graduate",
            "company_name": "Fernhill AI",
            "location": {"name": "London, UK"},
            "absolute_url": "https://job-boards.greenhouse.io/fernhillai/jobs/4461450008",
            "first_published": "2026-10-01T09:00:00-04:00",
            "content": "&lt;p&gt;Python and FastAPI, on the platform team. &lt;/p&gt;",
        }
    ]
}


class TestLinkedIn:
    """SPEC §11.2: refused before a request is built, not after one fails."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.linkedin.com/jobs/view/4321",
            "https://linkedin.com/jobs/view/4321",
            "https://uk.linkedin.com/jobs/view/4321",
        ],
    )
    def test_a_linkedin_link_is_never_fetched(self, url):
        with respx.mock(assert_all_called=False) as mock:
            everything = mock.route().mock(side_effect=AssertionError("WERA must not make this request"))
            with pytest.raises(ApiError) as raised:
                manual.imported(url=url, text=None, now=NOW)
            assert not everything.called

        assert raised.value.code == "LINKEDIN_NOT_FETCHED"
        assert "paste its description" in raised.value.message

    def test_the_refusal_says_what_to_do_instead(self):
        with pytest.raises(ApiError) as raised:
            manual.checked_url("https://www.linkedin.com/jobs/view/1")
        assert "Open the job on LinkedIn" in raised.value.message


class TestBadInput:
    @pytest.mark.parametrize("url", ["fernhill.example/jobs/1", "javascript:alert(1)", "ftp://x.example/job"])
    def test_something_that_is_not_a_link(self, url):
        with pytest.raises(ApiError) as raised:
            manual.checked_url(url)
        assert raised.value.code == "BAD_URL"

    def test_neither_a_link_nor_a_paste(self):
        with pytest.raises(ApiError) as raised:
            manual.imported(url=None, text="   ", now=NOW)
        assert raised.value.code == "NOTHING_TO_IMPORT"


class TestAtsLink:
    @respx.mock
    def test_a_greenhouse_link_gives_the_employers_own_fields(self, outbound):
        respx.get("https://boards-api.greenhouse.io/v1/boards/fernhillai/jobs", params={"content": "true"}).mock(
            return_value=httpx.Response(200, json=GH_BODY)
        )
        job = manual.imported(url="https://job-boards.greenhouse.io/fernhillai/jobs/4461450008", text=None, now=NOW)

        assert (job.title, job.company) == ("ML Engineer, Graduate", "Fernhill AI")
        assert job.location.city == "London"
        assert job.ats and job.ats.job_id == "4461450008"
        # Escaped vendor HTML never reaches a cover letter.
        assert "Python and FastAPI" in (job.description_md or "")
        assert "&lt;" not in (job.description_md or "")
        # However it was fetched, the person asked for this one by name.
        assert [s.channel for s in job.sources] == ["manual"]

    @respx.mock
    def test_a_posting_that_has_closed_says_so(self, outbound):
        respx.get("https://boards-api.greenhouse.io/v1/boards/fernhillai/jobs", params={"content": "true"}).mock(
            return_value=httpx.Response(200, json={"jobs": []})
        )
        with pytest.raises(ApiError) as raised:
            manual.imported(url="https://job-boards.greenhouse.io/fernhillai/jobs/999", text=None, now=NOW)
        assert raised.value.code == "IMPORT_NOT_FOUND"
        assert "may have closed" in raised.value.message

    @respx.mock
    def test_a_board_that_is_down_is_not_reported_as_a_missing_job(self, outbound):
        respx.get("https://boards-api.greenhouse.io/v1/boards/fernhillai/jobs", params={"content": "true"}).mock(
            return_value=httpx.Response(503)
        )
        with pytest.raises(ApiError) as raised:
            manual.imported(url="https://job-boards.greenhouse.io/fernhillai/jobs/1", text=None, now=NOW)
        assert raised.value.code == "IMPORT_FAILED"


class TestPlainLink:
    def test_any_other_link_is_scraped_once(self, monkeypatch):
        calls: list[str] = []

        def scrape(url, schema, outcome, caps):
            calls.append(url)
            assert caps.max_pages == 1  # one click never turns into a crawl
            return {
                "title": "Graduate Backend Engineer",
                "company": "Harbour Analytics",
                "location": "Bristol, UK",
                "description": "Python, Postgres and a small platform team. " * 6,
            }

        monkeypatch.setattr(manual.firecrawl_search, "scrape_card", scrape)
        job = manual.imported(url="https://careers.harbour.example/jobs/42", text=None, now=NOW)

        assert calls == ["https://careers.harbour.example/jobs/42"]
        assert (job.title, job.company) == ("Graduate Backend Engineer", "Harbour Analytics")
        assert job.location.city == "Bristol"
        assert job.apply_url == "https://careers.harbour.example/jobs/42"

    def test_a_page_with_no_job_on_it_asks_for_a_paste(self, monkeypatch):
        monkeypatch.setattr(manual.firecrawl_search, "scrape_card", lambda *a, **k: {"description": "About us"})
        with pytest.raises(ApiError) as raised:
            manual.imported(url="https://blog.example/post", text=None, now=NOW)
        assert raised.value.code == "IMPORT_NO_JOB"
        assert "Paste the description" in raised.value.message

    def test_a_page_that_cannot_be_read_asks_for_a_paste(self, monkeypatch):
        monkeypatch.setattr(manual.firecrawl_search, "scrape_card", lambda *a, **k: None)
        with pytest.raises(ApiError) as raised:
            manual.imported(url="https://careers.example/jobs/1", text=None, now=NOW)
        assert raised.value.code == "IMPORT_FAILED"


class TestPaste:
    def test_a_paste_is_taken_as_given(self):
        text = (
            "# Graduate Machine Learning Engineer\n"
            "We're hiring a graduate engineer at Kestrel Health to work on clinical NLP.\n"
            "Location: Manchester, UK\n\n"
            "## You have\n- Python\n- An interest in evaluation\n"
        )
        job = manual.imported(url=None, text=text, now=NOW)

        assert job.title == "Graduate Machine Learning Engineer"
        assert job.company == "Kestrel Health"
        assert job.location.city == "Manchester"
        assert job.description_md and "clinical NLP" in job.description_md
        assert job.apply_url is None  # a paste has no link to apply with
        assert [s.detail for s in job.sources] == ["pasted-text"]

    def test_a_paste_with_no_location_does_not_acquire_one(self):
        """The bug this guards: a pasted job quietly becoming a London job."""
        job = manual.imported(url=None, text="Junior Data Engineer\nat Lumen Payments\n\nSQL and dbt. " * 4, now=NOW)
        assert job.location.city is None
        assert job.location.raw == "Not stated"

    def test_a_company_that_cannot_be_read_is_not_guessed(self):
        job = manual.imported(url=None, text="Some role\n\n" + "Responsibilities and things. " * 10, now=NOW)
        assert job.company == "Unknown company"

    def test_an_empty_paste(self):
        with pytest.raises(ApiError) as raised:
            manual.from_text("\n\n", NOW)
        assert raised.value.code == "EMPTY_PASTE"


class TestThroughTheStore:
    """The live path replacing the demo stub: what the API returns is the posting that was
    fetched, scored like any other job."""

    @pytest.fixture
    def scored_by(self, monkeypatch):
        """Scoring, with the model replaced: what's under test is the wiring around it."""

        def install(result):
            from wera_agent.nodes import fit

            def scored(session, job, cv, *, model, now):
                # The real one saves what it scored; a stub that didn't would leave the store
                # with a job and no evidence, which is not a state the real path can reach.
                if result[0] is not None:
                    fit.save_evidence(session, job, result[0], cv_version=cv.version, now=now)
                return result

            monkeypatch.setattr(fit, "evidence_for", scored)

        return install

    def evidence(self, cv):
        from wera_agent.services.store import _quick_evidence

        return _quick_evidence(cv, "Python FastAPI platform", "Python and FastAPI, on the platform team.")

    @respx.mock
    def test_an_imported_job_is_the_one_that_was_fetched(self, client, api, outbound, scored_by, demo_cv):
        respx.get("https://boards-api.greenhouse.io/v1/boards/fernhillai/jobs", params={"content": "true"}).mock(
            return_value=httpx.Response(200, json=GH_BODY)
        )
        scored_by((self.evidence(demo_cv), True, None))

        job = api.ok("POST", "/jobs/import", {"url": "https://job-boards.greenhouse.io/fernhillai/jobs/4461450008"})

        # Not the stub's invented "Graduate Platform Engineer".
        assert (job["title"], job["company"]) == ("ML Engineer, Graduate", "Fernhill AI")
        assert job["fit"]["total"] > 0
        assert api.ok("GET", f"/jobs/{job['id']}")["title"] == "ML Engineer, Graduate"

    @respx.mock
    def test_a_job_that_could_not_be_scored_is_not_half_added(self, client, api, outbound, scored_by):
        """Either the import worked or it didn't: no job on the list with no fit."""
        respx.get("https://boards-api.greenhouse.io/v1/boards/fernhillai/jobs", params={"content": "true"}).mock(
            return_value=httpx.Response(200, json=GH_BODY)
        )
        scored_by((None, False, "LLM_QUOTA"))
        before = len(api.ok("GET", "/jobs")["items"])

        assert (
            api.code("POST", "/jobs/import", {"url": "https://job-boards.greenhouse.io/fernhillai/jobs/4461450008"})
            == "LLM_QUOTA"
        )
        assert len(api.ok("GET", "/jobs")["items"]) == before
