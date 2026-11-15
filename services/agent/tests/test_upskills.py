"""UPSK-01…04: the skills a person's jobs ask for that their CV doesn't show.

Arithmetic over the fit reports WERA already holds, so what matters is which jobs count, which
keywords count as one skill, and that nothing the CV already shows is called missing.
"""

from wera_agent.clock import iso, now_ms
from wera_agent.nodes import fit
from wera_agent.schemas.jobs import FitEvidence, Job, JobKeyword, JobSource, Location, SubScore
from wera_agent.services import upskills
from wera_agent.services.store import save_job

NOW = now_ms()
DAY = 24 * 60 * 60 * 1000


def job(i: int, *, seen: int = NOW, status: str = "new", hidden: str | None = None) -> Job:
    at = iso(seen)
    return Job.model_validate(
        {
            "id": f"job-up-{i}", "dedupe_key": f"url:https://up.example/{i}", "title": f"Graduate Engineer {i}",
            "company": f"Upco {i}", "location": Location(city="London", raw="London"), "remote": None,
            "description_md": "x" * 300, "description_status": "full", "apply_url": f"https://up.example/{i}",
            "sources": [JobSource(channel="board_search", detail="up.example", seen_at=at)], "language": "en",
            "first_seen": at, "last_seen": at, "status": status, "hidden_reason": hidden,
        }
    )  # fmt: skip


def scored(store, j: Job, *terms: tuple[str, str]) -> None:
    save_job(store.s, j, questions=[])
    sub = SubScore(score=5, job_quote="", cv_bullet_ids=[])
    evidence = FitEvidence(
        core=sub, adjacent=sub, experience=sub, eligibility=sub, reason="", job_quote="", cv_bullet_ids=[], model="demo",
        keywords=[JobKeyword(term=t, weight=w, category="tool") for t, w in terms],
    )  # fmt: skip
    fit.save_evidence(store.s, j, evidence, cv_version=store.cv().version, now=NOW)


def find(report, term):
    return next((u for u in report.missing if term.lower() in {s.lower() for s in u.spellings}), None)


class TestWhatCountsAsMissing:
    def test_spellings_of_one_skill_are_one_skill(self, store):
        scored(store, job(1), ("GCP", "required"))
        scored(store, job(2), ("Google Cloud Platform", "nice"))
        gcp = find(upskills.report(store.s, store.cv(), now=NOW), "GCP")
        assert gcp is not None and gcp.jobs == 2 and gcp.required_share == 0.5
        assert set(gcp.spellings) == {"GCP", "Google Cloud Platform"}

    def test_what_the_cv_shows_is_never_missing(self, store):
        scored(store, job(3), ("Python", "required"), ("Terraform", "required"))
        report = upskills.report(store.s, store.cv(), now=NOW)
        assert find(report, "Terraform") is not None and find(report, "Python") is None  # the demo CV has Python

    def test_traits_are_culture_not_skills(self, store):
        scored(store, job(4), ("Problem solving", "required"), ("Communication skills", "required"))
        report = upskills.report(store.s, store.cv(), now=NOW)
        assert find(report, "Problem solving") is None and find(report, "Communication skills") is None


class TestWhichJobsCount:
    def test_a_results_page_or_a_duplicate_doesnt_count(self, store):
        scored(store, job(5, status="hidden", hidden="Not a job posting: a page of results"), ("Kafka", "required"))
        scored(
            store, job(6, status="hidden", hidden="Duplicate: the same job as job-x, seen on another site"), ("Kafka", "required")
        )
        assert find(upskills.report(store.s, store.cv(), now=NOW), "Kafka") is None

    def test_jobs_outside_the_window_dont_count(self, store):
        scored(store, job(7, seen=NOW - 90 * DAY), ("Kafka", "required"))
        assert find(upskills.report(store.s, store.cv(), now=NOW, days=60), "Kafka") is None
        assert find(upskills.report(store.s, store.cv(), now=NOW, days=120), "Kafka") is not None

    def test_a_job_the_person_acted_on_counts_double(self, store):
        scored(store, job(8), ("Kafka", "required"))
        scored(store, job(9, status="shortlisted"), ("Snowflake", "required"))
        report = upskills.report(store.s, store.cv(), now=NOW)
        assert find(report, "Snowflake").score == 2 * find(report, "Kafka").score


class TestQuickWins:
    def test_a_skill_shown_in_a_line_but_not_listed_is_a_quick_win(self, store):
        cv = store.cv()
        line = cv.experience[0].bullets[0].text
        word = next(
            w.strip(".,")
            for w in line.split()
            if len(w) > 6 and w.isalpha() and not any(w.lower() in s.name.lower() for s in cv.skills)
        )
        scored(store, job(10), (word, "required"))
        report = upskills.report(store.s, cv, now=NOW)
        assert word in {w.term for w in report.quick_wins} and find(report, word) is None


def test_the_route_answers(api, store):
    scored(store, job(11), ("Kafka", "required"))
    store.s.commit()
    body = api.ok("GET", "/upskills?days=60")
    assert body["jobsConsidered"] >= 1 and any(u["term"] == "Kafka" for u in body["missing"])
