"""16 Nov: the scorer marked jobs down for not being where the CV says the applicant lives, and
saving a new "where" left the list as it was. Both are fixed here (MATCH-04, SRCH-11).
"""

from sqlalchemy import select

from wera_agent.clock import iso
from wera_agent.db.models import DecisionRow, FitReportRow, JobRow
from wera_agent.domain.cv_extract import _person
from wera_agent.llm import prompts
from wera_agent.nodes import fit
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.services.store import Store, save_job


class TestTheScorerDoesNotJudgeTheHomeTown:
    def test_the_cv_it_is_sent_says_nowhere_the_applicant_lives(self, store):
        cv = store.cv()
        assert "Based in:" in prompts.cv_text(cv)
        assert "Based in:" not in prompts.cv_text(cv, home=False)

    def test_the_rubric_takes_location_out_of_eligibility(self):
        system = prompts.FIT_SCORE.messages[0].prompt.template
        assert "`eligibility`: work pattern and seniority" in system
        assert "Never mark a job down for its location" in system

    def test_a_score_from_the_old_rubric_is_stale_and_rescored(self, store):
        row = store.s.scalars(select(FitReportRow)).first()
        job = store._job(store.s.get(JobRow, row.job_id))
        assert fit.cached_evidence(store.s, job, cv_version=row.cv_version, model=row.model) is not None
        row.evidence = {**row.evidence, "rubric": None}
        store.s.flush()
        assert fit.cached_evidence(store.s, job, cv_version=row.cv_version, model=row.model) is None
        assert store._stale_score(job)


class TestALabelledContactLine:
    def test_the_location_is_the_labelled_value(self):
        """Stored on 16 Nov as "Email: | Phone: | Location: Birmingham, UK"."""
        line = "Email: jane@example.com | Phone: 0121 496 0000 | Location: Birmingham, UK"
        assert _person(["Jane Doe", line])[1] == "Birmingham, UK"
        assert _person(["Jane Doe", "London, United Kingdom"])[1] == "London, United Kingdom"


def a_job(store: Store, job_id: str, raw: str, status: str = "new", hidden_reason: str | None = None) -> Job:
    job = Job(
        id=job_id,
        dedupe_key=job_id,
        title="Graduate Software Engineer",
        company="Acme",
        location=Location(city=raw.split(",")[0], raw=raw),
        remote="onsite",
        description_md="We build things. " * 30,
        description_status="full",
        apply_url=f"https://boards.greenhouse.io/acme/jobs/{abs(hash(job_id)) % 10**8:08d}",
        sources=[JobSource(channel="board_search", detail="test", seen_at=iso(store.now))],
        language="en",
        status=status,
        hidden_reason=hidden_reason,
        first_seen=iso(store.now),
        last_seen=iso(store.now),
    )
    save_job(store.s, job)
    return job


class TestTheListFollowsWhere:
    def test_changing_where_hides_and_brings_back_at_once(self, store):
        base = store.get_profile().model_copy(
            update={
                "titles": ["Graduate Software Engineer"],
                "exclude": [],
                "countries": ["GB"],
                "locations": ["London, UK"],
                "city_scope": "only",
            }  # fmt: skip
        )
        store.put_profile(base)
        a_job(store, "job-ldn", "London, UK")
        a_job(store, "job-zrh", "Zürich", status="hidden", hidden_reason="Location isn't in your search: Zürich")
        a_job(store, "job-kept", "Leeds, UK")  # brought back by the person: stays put
        store.s.add(DecisionRow(job_id="job-kept", gate=1, decision="unhide", decided_at=iso(store.now)))
        store.s.flush()

        store.put_profile(base.model_copy(update={"countries": ["CH"], "locations": []}))
        change = store.where_change
        # The seeded demo jobs move too; these three are the ones this test made.
        assert change is not None and change.shown >= 1 and change.hidden >= 1
        status = {i: store.s.get(JobRow, i).status for i in ("job-ldn", "job-zrh", "job-kept")}
        assert status == {"job-ldn": "hidden", "job-zrh": "new", "job-kept": "new"}

    def test_a_save_that_doesnt_touch_where_doesnt_refilter(self, store):
        profile = store.get_profile()
        store.put_profile(profile.model_copy(update={"recency": "7d"}))
        assert store.where_change is None

    def test_the_api_says_what_moved(self, client):
        body = client.get("/profile").json()
        body["countries"] = ["CH"]
        res = client.put("/profile", json=body)
        assert res.status_code == 200, res.text
        assert set(res.json()["refiltered"]) == {"shown", "hidden"}
