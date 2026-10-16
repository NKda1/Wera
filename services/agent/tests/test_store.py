"""The web app's store tests (Phase 1), now against the agent's HTTP API and database."""

from fastapi.testclient import TestClient

from wera_agent import clock
from wera_agent.main import create_app

from .conftest import TOKEN


def ready_app(api) -> dict:
    return next(a for a in api.ok("GET", "/applications")["items"] if a["status"] == "ready_for_review")


class TestGate1:
    def test_lists_the_shortlist_best_first(self, api):
        jobs = api.ok("GET", "/jobs")["items"]
        assert len(jobs) == 12
        assert [j["fit"]["total"] for j in jobs[:5]] == [8.6, 8.1, 7.4, 7.0, 6.2]
        assert api.ok("GET", "/nav") == {"jobs": 12, "review": 2, "demoMode": True}

    def test_approving_creates_a_drafting_application_that_becomes_ready(self, api):
        api.ok("POST", "/jobs/job-fernhill-mle/decision", {"action": "approve"})
        app = next(a for a in api.ok("GET", "/applications")["items"] if a["jobId"] == "job-fernhill-mle")
        assert app["status"] == "drafting"
        assert "pack" not in api.ok("GET", f"/applications/{app['id']}")
        clock.advance(7000)
        assert api.ok("GET", f"/applications/{app['id']}")["status"] == "ready_for_review"
        assert api.ok("GET", "/nav")["jobs"] == 11
        assert api.ok("GET", "/nav")["review"] == 3

    def test_snoozes_rejects_and_refuses_a_second_decision(self, api):
        api.ok("POST", "/jobs/job-northwind-swe/decision", {"action": "snooze", "days": 3})
        api.ok("POST", "/jobs/job-kestrel-jaie/decision", {"action": "reject", "reason": "Wrong location"})
        assert "job-northwind-swe" not in [j["id"] for j in api.ok("GET", "/jobs")["items"]]
        assert api.code("POST", "/jobs/job-kestrel-jaie/decision", {"action": "approve"}) == "INVALID_STATE"

    def test_snoozed_jobs_come_back(self, api):
        api.ok("POST", "/jobs/job-northwind-swe/decision", {"action": "snooze", "days": 1})
        clock.advance(24 * 60 * 60 * 1000 + 1)
        assert "job-northwind-swe" in [j["id"] for j in api.ok("GET", "/jobs")["items"]]

    def test_warns_before_a_second_application_to_the_same_company_and_title(self, api):
        assert api.code("POST", "/jobs/job-lumen-be/decision", {"action": "approve"}) == "DUPLICATE_APPLICATION"
        assert api.code("POST", "/jobs/job-lumen-be/decision", {"action": "approve", "force": True}) == "OK"

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
        hidden = api.ok("GET", "/jobs?status=hidden")["items"]
        assert {j["hiddenReason"] for j in hidden} >= {"Title contains an excluded term: Senior"}


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
        assert api.ok("GET", f"/intake/runs/{again[0]}")["newJobs"] == 0

    def test_estimate_and_cancel(self, api):
        assert api.ok("GET", "/intake/estimate?channels=board_search")["queries"] == 20
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

    def test_downloads_unlock_only_after_approval(self, api):
        app = ready_app(api)
        assert api.code("GET", f"/documents/{app['id']}.cv/export?format=pdf") == "PACK_NOT_APPROVED"

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
