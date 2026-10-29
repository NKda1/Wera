"""The web app's store tests (Phase 1), now against the agent's HTTP API and database."""

from fastapi.testclient import TestClient

from wera_agent import clock
from wera_agent.main import create_app

from .conftest import TOKEN


def ready_app(api) -> dict:
    return next(a for a in api.ok("GET", "/applications")["items"] if a["status"] == "ready_for_review")


def approve_and_draft(api, drafted, job_id: str) -> dict:
    """Gate 1 approval, then wait for the graph to draft the pack."""
    api.ok("POST", f"/jobs/{job_id}/decision", {"action": "approve"})
    drafted()
    return next(a for a in api.ok("GET", "/applications")["items"] if a["jobId"] == job_id)


class TestGate1:
    def test_lists_the_shortlist_best_first(self, api):
        jobs = api.ok("GET", "/jobs")["items"]
        assert len(jobs) == 12
        assert [j["fit"]["total"] for j in jobs[:5]] == [8.6, 8.1, 7.4, 7.0, 6.2]
        assert api.ok("GET", "/nav") == {"jobs": 12, "review": 2, "demoMode": True}

    def test_approving_drafts_a_pack_and_waits_at_gate_2(self, api, drafted):
        api.ok("POST", "/jobs/job-fernhill-mle/decision", {"action": "approve"})
        app = next(a for a in api.ok("GET", "/applications")["items"] if a["jobId"] == "job-fernhill-mle")
        # The decision returns before the pack exists: drafting happens after the response.
        assert app["status"] == "drafting"
        assert "pack" not in api.ok("GET", f"/applications/{app['id']}")

        drafted()
        done = api.ok("GET", f"/applications/{app['id']}")
        assert done["status"] == "ready_for_review"
        assert done["pack"]["cv"]["sections"]
        assert done["pack"]["letter"]["paragraphs"]
        assert done["pack"]["ats"]["coverage"] > 0
        assert api.ok("GET", "/nav")["jobs"] == 11
        assert api.ok("GET", "/nav")["review"] == 3

    def test_snoozes_rejects_and_refuses_a_second_decision(self, api, drafted):
        api.ok("POST", "/jobs/job-northwind-swe/decision", {"action": "snooze", "days": 3})
        api.ok("POST", "/jobs/job-kestrel-jaie/decision", {"action": "reject", "reason": "Wrong location"})
        assert "job-northwind-swe" not in [j["id"] for j in api.ok("GET", "/jobs")["items"]]
        assert api.code("POST", "/jobs/job-kestrel-jaie/decision", {"action": "approve"}) == "INVALID_STATE"
        drafted()

    def test_snoozed_jobs_come_back(self, api, drafted):
        api.ok("POST", "/jobs/job-northwind-swe/decision", {"action": "snooze", "days": 1})
        drafted()
        clock.advance(24 * 60 * 60 * 1000 + 1)
        assert "job-northwind-swe" in [j["id"] for j in api.ok("GET", "/jobs")["items"]]

    def test_warns_before_a_second_application_to_the_same_company_and_title(self, api, drafted):
        assert api.code("POST", "/jobs/job-lumen-be/decision", {"action": "approve"}) == "DUPLICATE_APPLICATION"
        assert api.code("POST", "/jobs/job-lumen-be/decision", {"action": "approve", "force": True}) == "OK"
        drafted()

    def test_refuses_model_work_once_the_weekly_budget_is_used(self, api):
        api.ok("PUT", "/settings", {"weeklyBudgetUsd": 0.01})
        status, body = api("POST", "/jobs/job-fernhill-mle/decision", {"action": "approve"})
        assert (status, body["error"]["code"]) == (402, "BUDGET_REACHED")
        assert api.code("POST", "/intake/run", {"channels": ["board_search"]}) == "BUDGET_REACHED"

    def test_re_ranks_when_weights_change_with_no_new_model_calls(self, api):
        before = [j["id"] for j in api.ok("GET", "/jobs")["items"]]
        api.ok("PUT", "/settings", {"weights": {"core": 0, "adjacent": 1, "experience": 0, "eligibility": 0, "freshness": 0}})
        after = api.ok("GET", "/jobs")["items"]
        assert after[0]["id"] == "job-northwind-swe"
        assert [j["id"] for j in after] != before

    def test_filters_like_the_jobs_screen(self, api):
        assert len(api.ok("GET", "/jobs?minScore=8")["items"]) == 2
        assert all(j["remote"] == "remote" for j in api.ok("GET", "/jobs?remote=remote")["items"])
        # Hidden jobs come from the meta endpoint, which is where the screen reads them: they
        # were filtered before any model call (MATCH-01), so they carry a reason and no score
        # and cannot appear in a list ranked by one.
        hidden = api.ok("GET", "/jobs/meta")["hidden"]
        assert {j["hiddenReason"] for j in hidden} >= {"Title contains an excluded term: Senior"}
        assert all("fit" not in j for j in hidden)
        assert api.code("GET", "/jobs?status=hidden") == "VALIDATION"


class TestIntake:
    def test_never_requests_a_linkedin_url_on_import(self, api):
        # pytest-socket fails the test if anything tries to open a connection (T-04).
        assert api.code("POST", "/jobs/import", {"url": "https://www.linkedin.com/jobs/view/123"}) == "LINKEDIN_NOT_FETCHED"

    def test_resolves_a_greenhouse_link_without_scraping(self, api):
        job = api.ok("POST", "/jobs/import", {"url": "https://job-boards.greenhouse.io/acmeai/jobs/123"})
        assert job["ats"] == {"vendor": "greenhouse", "boardToken": "acmeai", "jobId": "123"}
        assert api.code("POST", "/jobs/import", {"url": "https://job-boards.greenhouse.io/acmeai/jobs/123"}) == "ALREADY_KNOWN"

    def test_imports_pasted_text(self, api):
        text = "# Backend Engineer\nWe are hiring at Acme Robotics.\nYou will build Python services with FastAPI and PostgreSQL for our robots."
        job = api.ok("POST", "/jobs/import", {"text": text})
        assert (job["title"], job["company"], job["sources"][0]["detail"]) == ("Backend Engineer", "Acme Robotics", "pasted-text")
        assert api.code("POST", "/jobs/import", {"text": "too short"}) == "VALIDATION"

    def test_adds_new_jobs_on_the_first_search_only_and_one_run_at_a_time(self, api):
        run_ids = api.ok("POST", "/intake/run", {"channels": ["board_search", "email_alert", "watchlist"]})["runIds"]
        assert api.code("POST", "/intake/run", {"channels": ["board_search"]}) == "RUN_ACTIVE"
        clock.advance(7000)
        board = next(r for r in (api.ok("GET", f"/intake/runs/{i}") for i in run_ids) if r["channel"] == "board_search")
        assert (board["status"], board["newJobs"]) == ("succeeded", 2)
        assert len(api.ok("GET", "/jobs")["items"]) == 14
        again = api.ok("POST", "/intake/run", {"channels": ["board_search"]})["runIds"]
        clock.advance(7000)
        repeat = api.ok("GET", f"/intake/runs/{again[0]}")
        assert repeat["newJobs"] == 0
        # SRCH-05 saves the scrapes and SRCH-10 saves the searches, so a repeat run inside the
        # window is free. The demo is where most people will ever see that, so it shows it: the
        # 40 credits this used to spend are reported as saved rather than spent.
        assert (repeat["creditsUsed"], repeat["creditsSaved"]) == (0, 40)
        # The first run paid full price, and says so.
        assert board["creditsUsed"] > 0 and board["creditsSaved"] == 0

    def test_estimate_and_cancel(self, api):
        # SRCH-11: the grid is title × board, so the demo profile's 4 titles across its 4
        # enabled boards is 16 — and the dialog counts it with the same function the run uses
        # (COST-05), rather than re-deriving it from titles × locations and drifting.
        assert api.ok("GET", "/intake/estimate?channels=board_search")["queries"] == 16
        run = api.ok("POST", "/intake/run", {"channels": ["watchlist"]})["runIds"][0]
        assert api.ok("POST", f"/intake/runs/{run}/cancel")["status"] == "cancelled"


class TestGate2AndTracker:
    def test_only_a_person_approves_and_only_from_review(self, api):
        app = ready_app(api)
        assert api.code("PATCH", f"/applications/{app['id']}", {"status": "approved"}) == "INVALID_TRANSITION"
        api.ok("POST", f"/applications/{app['id']}/approve")
        assert api.code("POST", f"/applications/{app['id']}/approve") == "INVALID_STATE"
        submitted = api.ok("PATCH", f"/applications/{app['id']}", {"status": "submitted"})
        assert submitted["followUpAt"]

    def test_marks_in_place_edits_and_keeps_the_source_id(self, api):
        app = ready_app(api)
        nxt = api.ok("PATCH", f"/documents/{app['id']}.cv", {"target": "s0.i0.b0", "text": "Rewritten by me."})
        b = nxt["pack"]["cv"]["sections"][0]["items"][0]["bullets"][0]
        assert (b["text"], b["editedByUser"], len(b["sourceIds"])) == ("Rewritten by me.", True, 1)
        assert (
            api.ok("GET", f"/applications/{app['id']}")["pack"]["cv"]["sections"][0]["items"][0]["bullets"][0]["text"]
            == "Rewritten by me."
        )

    def test_removes_and_adds_back_a_line(self, api):
        app = ready_app(api)
        first = api.ok("GET", f"/applications/{app['id']}")["pack"]["cv"]["sections"][0]["items"][0]["bullets"][0]["sourceIds"][0]
        removed = api.ok("PATCH", f"/documents/{app['id']}.cv", {"target": "remove", "bulletId": first})
        assert first in removed["pack"]["cv"]["omittedBulletIds"]
        added = api.ok("PATCH", f"/documents/{app['id']}.cv", {"target": "add", "bulletId": first})
        assert first not in added["pack"]["cv"]["omittedBulletIds"]

    def test_request_changes_redrafts(self, api):
        app = ready_app(api)
        nxt = api.ok("POST", f"/applications/{app['id']}/regenerate", {"note": "Make it shorter"})
        assert (nxt["status"], nxt["changeNote"]) == ("drafting", "Make it shorter")

    def test_validates_cv_saves_and_bumps_the_version(self, api):
        cv = api.ok("GET", "/cv")
        assert api.ok("PUT", "/cv", cv)["version"] == cv["version"] + 1
        cv["experience"][0]["bullets"][1]["id"] = cv["experience"][0]["bullets"][0]["id"]
        assert api.code("PUT", "/cv", cv) == "VALIDATION"

    def test_never_returns_secret_values_from_settings(self, api, monkeypatch):
        from wera_agent.settings import get_settings

        monkeypatch.setenv("LLM_API_KEY", "sk-or-test-secret")
        get_settings.cache_clear()
        s = api.ok("GET", "/settings")
        assert s["secrets"]["llm"] == "set"
        assert "sk-or-test-secret" not in str(s)

    def test_cv_and_contact_are_kept_apart(self, api):
        assert "contact" not in api.ok("GET", "/cv")
        assert api.ok("GET", "/cv/contact")["email"] == "jordan.ellery@example.com"


class TestApi:
    def test_requires_the_shared_token(self, client):
        assert client.get("/nav", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.get("/nav", headers={"Authorization": ""}).status_code == 401
        assert client.get("/healthz", headers={"Authorization": ""}).json()["ok"] is True

    def test_validation_errors_name_the_field(self, api):
        profile = api.ok("GET", "/profile")
        status, body = api("PUT", "/profile", {**profile, "titles": []})
        assert status == 422
        assert body["error"]["fields"]["titles"] == "Add at least 1 target title"
        _, body = api("PUT", "/profile", {**profile, "channels": {"boardSearch": False, "inbox": False, "watchlist": False}})
        assert body["error"]["fields"]["channels"] == "Turn on at least one channel"

    def test_bad_json_is_a_400(self, client):
        r = client.put("/profile", content=b"{nope", headers={"content-type": "application/json"})
        assert (r.status_code, r.json()["error"]["code"]) == (400, "BAD_JSON")


def test_data_survives_a_restart(api):
    """Pasted gap: demo data used to reset when the server restarted."""
    api.ok("POST", "/jobs/job-kestrel-jaie/decision", {"action": "reject"})
    app = ready_app(api)
    api.ok("PATCH", f"/documents/{app['id']}.letter", {"target": "p0", "text": "My own opening."})
    with TestClient(create_app(), headers={"Authorization": f"Bearer {TOKEN}"}) as again:
        jobs = again.get("/jobs").json()["items"]
        assert "job-kestrel-jaie" not in [j["id"] for j in jobs]
        para = again.get(f"/applications/{app['id']}").json()["pack"]["letter"]["paragraphs"][0]
        assert (para["text"], para["editedByUser"]) == ("My own opening.", True)


class TestJobsWithNoFitReport:
    """What the Jobs screen does with a job that has never been scored.

    Not every stored job has a fit report, and two of the reasons are by design: a job hidden
    by the profile filters was filtered before any model call (MATCH-01) and will never have
    one, and a job past the day's scoring cap (MATCH-05) does not have one yet. A first real
    run left 3,223 hidden and 22 unscored jobs, and the shortlist answered 500 for all of them,
    because every read path assumed the row was there.
    """

    def unscored_job(self, store) -> str:
        """Strips the fit report off a real job, leaving it `new` — a job past the day's cap."""
        from wera_agent.db.models import FitReportRow

        job_id = store.list_jobs("new")[0].id
        store.s.delete(store.s.get(FitReportRow, job_id))
        store.s.flush()
        return job_id

    def test_the_shortlist_skips_it_rather_than_failing(self, store):
        job_id = self.unscored_job(store)
        assert job_id not in [j.id for j in store.list_jobs("new")]

    def test_it_is_counted_so_it_does_not_just_vanish(self, store):
        before = store.awaiting_score()
        self.unscored_job(store)
        assert store.awaiting_score() == before + 1

    def test_the_meta_endpoint_still_builds(self, store):
        self.unscored_job(store)
        meta = store.shortlist_meta()
        assert meta.awaiting_score >= 1
        assert meta.hidden_total >= 1

    def test_asking_for_it_directly_says_why_rather_than_500ing(self, store):
        from wera_agent.errors import ApiError

        job_id = self.unscored_job(store)
        try:
            store.get_job(job_id)
        except ApiError as exc:
            assert exc.code == "NOT_SCORED"
            assert exc.status == 409
        else:
            raise AssertionError("an unscored job should not come back as though it were scored")

    def test_a_hidden_job_carries_a_reason_and_no_score(self, store):
        """The demo fixture used to attach fit evidence to hidden jobs, which is the one thing
        a real run can never produce — so this passed everywhere except in production."""
        from wera_agent.db.models import FitReportRow

        hidden = store.list_unscored("hidden", limit=50)
        assert hidden, "the demo seed should still contain hidden jobs"
        assert all(j.hidden_reason for j in hidden)
        assert all(store.s.get(FitReportRow, j.id) is None for j in hidden)

    def test_the_hidden_sample_is_capped(self, store):
        assert len(store.list_unscored("hidden", limit=2)) <= 2


class TestAPartiallyDraftedPack:
    """A pack with some of its documents missing (REL, G2-02).

    The graph writes one document per step, so a draft that fails partway leaves the steps it
    finished behind and is parked as `changes_requested` with the reason. A dev-server restart
    mid-draft leaves the same thing. A real run did exactly this — `cv.tailor` returned the
    wrong shape twice after the ATS step sent it back for another pass — and the Review screen
    answered 500 for *every* application because `_load_pack` indexed a key that wasn't there.
    """

    def drop(self, store, app_id: str, kind: str) -> None:
        from sqlalchemy import select

        from wera_agent.db.models import DocumentRow

        for row in store.s.scalars(select(DocumentRow).where(DocumentRow.application_id == app_id, DocumentRow.kind == kind)):
            store.s.delete(row)
        store.s.flush()

    def ready(self, store) -> str:
        return next(a.id for a in store.list_applications() if a.status == "ready_for_review")

    def test_a_missing_answers_document_is_an_empty_list_not_a_crash(self, store):
        """A job with no application questions has no answers to draft, and a draft that failed
        before the answers step has none either. Neither is a reason to hide a built pack."""
        app_id = self.ready(store)
        self.drop(store, app_id, "answers")
        pack = store._load_pack(app_id)
        assert pack is not None
        assert pack.answers == []
        assert pack.letter.paragraphs

    def test_the_review_list_survives_a_partial_pack(self, store):
        app_id = self.ready(store)
        self.drop(store, app_id, "answers")
        assert any(a.id == app_id for a in store.list_applications())

    def test_a_pack_missing_its_cv_is_no_pack_at_all(self, store):
        """Unlike answers, the CV is what a pack *is*. Better to say the draft didn't finish
        than to show a pack with nothing in it."""
        app_id = self.ready(store)
        self.drop(store, app_id, "cv")
        assert store._load_pack(app_id) is None
        assert any(a.id == app_id for a in store.list_applications())

    def test_an_application_with_no_documents_at_all_still_lists(self, store):
        app_id = self.ready(store)
        for kind in ("cv", "letter", "ats", "answers"):
            self.drop(store, app_id, kind)
        assert store._load_pack(app_id) is None
        assert next(a.id for a in store.list_applications() if a.id == app_id) == app_id
