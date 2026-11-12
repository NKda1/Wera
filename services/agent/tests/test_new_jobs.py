"""SRCH-25: a newly added job is tagged New until someone opens it. SRCH-27: "Clear jobs".

The old marker was one list of the last run's job ids, which every run overwrote — an inbox run
that found nothing wiped the "new" off a search's twelve jobs before anyone had looked at them.
"""

import pytest
from sqlalchemy import select

from wera_agent.clock import iso, now_ms
from wera_agent.db.models import ApplicationRow, IntakeRunRow, JobRow, LlmCallRow
from wera_agent.db.session import transaction
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.services import store as store_module
from wera_agent.services.store import Store


def job(job_id: str = "job-new", url: str = "https://builtinlondon.uk/job/ai-engineer/11820879", **over) -> Job:
    at = iso(now_ms())
    base = {
        "id": job_id,
        "dedupe_key": f"key:{job_id}",
        "title": "AI Engineer",
        "company": "Loveholidays",
        "location": Location(city="London", country="GB", raw="London"),
        "remote": None,
        "description_md": "Build things. " * 30,
        "description_status": "full",
        "apply_url": url,
        "sources": [JobSource(channel="board_search", detail="builtinlondon.uk", seen_at=at)],
        "language": "en",
        "first_seen": at,
        "last_seen": at,
        "status": "new",
    }
    return Job.model_validate(base | over)


def stored(store: Store, job_id: str) -> Job:
    """The job as saved, scored or not — `get_job` is the shortlist's read and wants a score."""
    return store._job(store._job_row(job_id))


class TestTheNewTag:
    def test_a_new_job_names_the_run_that_found_it(self, store):
        store.upsert_jobs([job()], run_ids={"board_search": "run-7"})
        saved = stored(store, "job-new")
        assert saved.found_by is not None
        assert (saved.found_by.run_id, saved.found_by.channel) == ("run-7", "board_search")
        assert saved.seen_at is None

    def test_seeing_it_again_keeps_its_first_tag(self, store):
        store.upsert_jobs([job()], run_ids={"board_search": "run-7"})
        store.mark_seen("job-new")
        store.upsert_jobs([job(job_id="job-again", dedupe_key="key:job-new")], run_ids={"board_search": "run-8"})
        saved = stored(store, "job-new")
        assert saved.found_by.run_id == "run-7"
        assert saved.seen_at is not None  # a re-sighting doesn't make it new again

    def test_opening_it_ends_new(self, api, store):
        store.upsert_jobs([job()], run_ids={"board_search": "run-7"})
        store.s.commit()
        api.ok("POST", "/jobs/job-new/seen")
        with transaction() as session:
            assert stored(Store(session), "job-new").seen_at is not None

    def test_a_decision_ends_new_too(self, api):
        before = api.ok("GET", "/jobs/job-fernhill-mle")
        assert before.get("seenAt") is None
        after = api.ok("POST", "/jobs/job-fernhill-mle/decision", {"action": "snooze", "days": 1})
        assert after["seenAt"] is not None

    def test_mark_all_seen(self, api, store):
        store.upsert_jobs([job(), job("job-two", dedupe_key="key:two")], run_ids={"board_search": "run-7"})
        store.s.commit()
        assert api.ok("POST", "/jobs/seen") == {"marked": 2}
        assert api.ok("POST", "/jobs/seen") == {"marked": 0}

    def test_jobs_from_before_tagging_are_not_new(self, api):
        """The demo seed predates SRCH-25, like every job a real database already holds."""
        assert all(j.get("foundBy") is None for j in api.ok("GET", "/jobs")["items"])

    def test_the_meta_no_longer_carries_a_list_of_ids(self, api):
        assert "newJobIds" not in api.ok("GET", "/jobs/meta")


@pytest.fixture
def clearing(monkeypatch):
    """Pin `allow_clear_jobs` rather than inherit whatever the shipped config says today."""

    def pin(allowed: bool) -> None:
        pinned = store_module.wera().model_copy(update={"allow_clear_jobs": allowed})
        monkeypatch.setattr(store_module, "wera", lambda: pinned)

    return pin


class TestClearJobs:
    def test_the_preview_names_what_would_go(self, api, clearing):
        clearing(True)
        preview = api.ok("GET", "/jobs/clear")
        assert preview["jobs"] > 0 and preview["applications"] > 0 and preview["documents"] > 0

    def test_it_clears_jobs_applications_and_packs(self, api, clearing):
        clearing(True)
        preview = api.ok("GET", "/jobs/clear")
        assert api.ok("POST", "/jobs/clear") == preview
        assert api.ok("GET", "/jobs")["items"] == []
        assert api.ok("GET", "/applications")["items"] == []

    def test_it_keeps_both_ledgers(self, api, clearing):
        """Model spend and Firecrawl credits both stay, or clearing would hand back budget that
        was really spent — the weekly credit cap counts the runs (`credits_this_week`)."""
        clearing(True)
        with transaction() as session:
            session.add(LlmCallRow(task="fit.score", tier="scoring", model="qwen/test", cost_usd=0.02, called_at=iso(now_ms())))
            store = Store(session)
            run_ids = store.start_runs(["board_search"], scheduled=True)
            run = session.get(IntakeRunRow, run_ids["board_search"])
            run.status, run.credits_used = "succeeded", 39
        with transaction() as session:
            credits = Store(session).credits_this_week()
            calls = len(session.scalars(select(LlmCallRow)).all())
        api.ok("POST", "/jobs/clear")
        with transaction() as session:
            assert Store(session).credits_this_week() == credits
            assert len(session.scalars(select(LlmCallRow)).all()) == calls

    def test_it_is_refused_when_turned_off(self, api, clearing):
        clearing(False)
        assert api.code("GET", "/jobs/clear") == "CLEAR_JOBS_OFF"
        assert api.code("POST", "/jobs/clear") == "CLEAR_JOBS_OFF"
        assert api.ok("GET", "/jobs")["items"]  # nothing went

    def test_settings_says_whether_to_offer_it(self, api, clearing):
        clearing(True)
        assert api.ok("GET", "/settings")["allowClearJobs"] is True
        clearing(False)
        assert api.ok("GET", "/settings")["allowClearJobs"] is False


class TestAuditListings:
    """SRCH-21 over what is already saved: results pages saved as jobs are hidden, not deleted."""

    LISTING = "https://www.gradcracker.com/search/technology-it/graduate-jobs?page=2"

    def test_a_listing_is_found_and_hidden_on_apply(self, store):
        store.upsert_jobs([job(url=self.LISTING)])
        found = [f for f in store.audit_listings() if f["id"] == "job-new"]
        assert [(f["kind"], f["action"]) for f in found] == [("listing", "would hide")]
        assert stored(store, "job-new").status == "new"  # a dry run changes nothing

        store.audit_listings(apply=True)
        hidden = stored(store, "job-new")
        assert (hidden.status, hidden.hidden_reason) == ("hidden", "Not a job posting: a page of results")

    def test_a_posting_is_left_alone(self, store):
        store.upsert_jobs([job()])
        assert "job-new" not in {f["id"] for f in store.audit_listings(apply=True)}

    def test_one_with_an_application_is_flagged_not_hidden(self, store):
        store.upsert_jobs([job(url=self.LISTING)])
        store.s.add(ApplicationRow(id="app-x", job_id="job-new", status="ready_for_review", created_at=iso(now_ms())))
        store.s.flush()
        found = [f for f in store.audit_listings(apply=True) if f["id"] == "job-new"]
        assert found[0]["action"] == "flagged: has an application"
        assert store.s.get(JobRow, "job-new").status == "new"


class TestRefilter:
    def test_a_job_the_filters_no_longer_hide_comes_back_unscored(self, store):
        """The London bug: hidden for its location, kept by today's filter."""
        hidden = job(location=Location(city="London", country="GB", raw="London, England, GBR"),
                     status="hidden", hidden_reason="Location isn't in your search: London")  # fmt: skip
        store.upsert_jobs([hidden])
        assert [j.id for j in store.refilter_hidden()] == ["job-new"]
        assert stored(store, "job-new").status == "hidden"  # a dry run changes nothing
        store.refilter_hidden(apply=True)
        assert (stored(store, "job-new").status, stored(store, "job-new").hidden_reason) == ("new", None)

    def test_a_job_hidden_as_not_a_posting_stays_hidden(self, store):
        store.upsert_jobs([job(status="hidden", hidden_reason="Not a job posting: a page of results")])
        assert store.refilter_hidden(apply=True) == []
