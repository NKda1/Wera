"""UPSK-01…04: the skills a person's jobs ask for that their CV doesn't show.

Arithmetic over the fit reports WERA already holds, so what matters is which jobs count, which
keywords count as one skill, and that nothing the CV already shows is called missing.
"""

import pytest

from wera_agent.clock import iso, now_ms
from wera_agent.db.models import ApplicationRow, DocumentRow
from wera_agent.nodes import fit
from wera_agent.schemas.cv import Education
from wera_agent.schemas.jobs import EducationAsk, FitEvidence, Job, JobKeyword, JobSource, Location, SubScore
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


def scored(store, j: Job, *terms: tuple[str, str], education: EducationAsk | None = None, match: float = 5) -> None:
    """`match` is every sub-score (20 Nov): an undated job's total is 0.9 × fit + 0.1, so 10 is a
    strong match (9.1, lift 1) and 3 a poor one (2.8, lift 2)."""
    save_job(store.s, j, questions=[])
    sub = SubScore(score=match, job_quote="", cv_bullet_ids=[])
    evidence = FitEvidence(
        core=sub, adjacent=sub, experience=sub, eligibility=sub, reason="", job_quote="", cv_bullet_ids=[], model="demo",
        keywords=[JobKeyword(term=t, weight=w, category="tool") for t, w in terms], education=education,
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


class TestTheMarketAgainstTheCv:
    """17 Nov: the page charts what the posts ask for against the CV as a whole — covered, in a
    line but not the skills list, or missing — so it needs every skill asked, not only the gaps."""

    def test_every_asked_skill_is_counted_where_the_cv_stands_on_it(self, store):
        before = upskills.report(store.s, store.cv(), now=NOW)
        asked = {d.term: d.jobs for d in before.demand}
        scored(store, job(20), ("Python", "required"), ("Terraform", "required"))
        scored(store, job(21), ("Python", "required"), ("Kafka", "nice"))
        report = upskills.report(store.s, store.cv(), now=NOW)

        by_term = {d.term: d for d in report.demand}
        assert by_term["Python"].status == "covered" and by_term["Python"].jobs == asked.get("Python", 0) + 2
        # One job each: perhaps below the top of the demo seed's demand, but missing all the same.
        assert find(report, "Terraform") is not None and find(report, "Kafka") is not None
        assert all(d.status == "missing" for d in report.demand if d.term in ("Terraform", "Kafka"))
        assert [d.jobs for d in report.demand] == sorted((d.jobs for d in report.demand), reverse=True)  # most asked first
        grew = {
            field: getattr(report.coverage, field) - getattr(before.coverage, field)
            for field in ("covered", "quick_wins", "missing")
        }
        assert grew["covered"] + grew["quick_wins"] == 2 and grew["missing"] == 2

    def test_categories_add_up_to_the_whole(self, store):
        scored(store, job(22), ("Python", "required"), ("Terraform", "required"), ("Kafka", "nice"))
        report = upskills.report(store.s, store.cv(), now=NOW)
        total = report.coverage.covered + report.coverage.quick_wins + report.coverage.missing
        assert sum(c.covered + c.quick_wins + c.missing for c in report.by_category) == total
        assert "tool" in {c.category for c in report.by_category}


def bsc(store):
    """The 20 Nov finance CV's education, on the demo CV."""
    return store.cv().model_copy(
        update={"education": [Education(id="edu-1", degree="BSc (Hons) Economics and Finance", institution="LSE")]}
    )


class TestDegrees:
    """UPSK-05 (20 Nov): degrees are judged against the CV's education, never listed as skills."""

    def test_a_bsc_is_never_told_it_lacks_a_bachelors(self, store):
        """What the 20 Nov finance CV was shown, from a report scored before `education` existed."""
        scored(store, job(20), ("Bachelor\u2019s Degree", "required"), ("Business Administration", "required"), ("CFA", "nice"))
        report = upskills.report(store.s, bsc(store), now=NOW)
        assert find(report, "Bachelor\u2019s Degree") is None and find(report, "Business Administration") is None
        assert find(report, "CFA") is not None  # a qualification is still something to go and get

    def test_the_degree_a_job_asks_for_is_counted_against_the_cv(self, store):
        scored(store, job(21), education=EducationAsk(level="bachelor", subjects=["Business Administration"], required=True))
        scored(store, job(22), education=EducationAsk(level="master", subjects=["Finance"]))
        edu = upskills.report(store.s, bsc(store), now=NOW).education
        assert edu is not None and edu.cv_level == "bachelor"
        assert (edu.jobs_asking, edu.met, edu.level_gaps) == (2, 1, 1)
        assert {lvl.level: lvl.met for lvl in edu.levels} == {"bachelor": True, "master": False}

    def test_the_programmes_above_the_cvs_level_come_from_the_postings(self, store):
        for i in range(30, 36):
            scored(store, job(i), education=EducationAsk(level="master", subjects=["Finance"]))
        edu = upskills.report(store.s, bsc(store), now=NOW, field="investment banking").education
        assert edu.programmes[0].name == "Master's in Finance" and edu.programmes[0].mentions == 6
        assert all(p.source == "postings" for p in edu.programmes)  # enough postings: no starter list

    def test_the_starter_list_fills_in_while_postings_are_few(self, store):
        scored(store, job(40), education=EducationAsk(level="master", subjects=["Financial Economics"]))
        edu = upskills.report(store.s, bsc(store), now=NOW, field="investment banking and corporate finance").education
        assert edu.programmes[0].source == "postings"
        assert any(p.source == "starter" and p.name == "CFA Program" for p in edu.programmes)

    def test_no_degree_asked_means_no_education_block(self, store):
        scored(store, job(41), ("Kafka", "required"))
        assert upskills.report(store.s, bsc(store), now=NOW).education is None


def nearly(session, key: str, *terms: tuple[str, str], similarity: float = 0.8) -> None:
    """A near miss read into `job_signals` (UPSK-06), as `test_near_misses.signal` writes one."""
    from .test_near_misses import signal

    signal(session, key, *terms)
    from wera_agent.db.models import JobSignalRow

    session.get(JobSignalRow, key).similarity = similarity
    session.flush()


def asks(c) -> tuple[int, int]:
    """A coverage's asks: (on the list, nearly matched)."""
    return c.covered + c.quick_wins + c.missing, c.near_covered + c.near_quick_wins + c.near_missing


class TestTheChartsFollowTheView:
    """20 Nov, the user: "the bar charts and pie charts" didn't move with the near misses while the
    table's "nearly matched" counts did. Each view now carries its own chart data."""

    def test_the_near_miss_view_charts_the_near_misses_asks(self, store):
        scored(store, job(50), ("Kafka", "required"))
        for k in ("n1", "n2", "n3"):
            nearly(store.s, k, ("Kafka", "required"), ("Bloomberg Terminal", "nice"), ("Python", "nice"))
        report = upskills.report(store.s, store.cv(), now=NOW)
        view = report.with_near_misses
        assert view is not None and view.near_misses == 3 and view.jobs_considered == report.jobs_considered
        rows = {d.term: d for d in view.demand}
        assert (rows["Bloomberg Terminal"].jobs, rows["Bloomberg Terminal"].near_misses) == (0, 3)
        assert rows["Bloomberg Terminal"].status == "missing" and rows["Python"].status == "covered"
        assert rows["Kafka"].near_misses == 3 and rows["Kafka"].jobs == {d.term: d for d in report.demand}["Kafka"].jobs
        # Nine near-miss asks: Kafka and Bloomberg missing, Python on the CV — and none on the list's side.
        assert (view.coverage.near_missing, view.coverage.near_covered) == (6, 3)
        assert "Bloomberg Terminal" not in {d.term for d in report.demand}

    def test_the_split_adds_up(self, store):
        scored(store, job(51), ("Kafka", "required"), ("Python", "nice"))
        scored(store, job(52), ("Terraform", "required"))
        for k in ("n4", "n5"):
            nearly(store.s, k, ("Kafka", "required"), ("Terraform", "nice"), ("Snowflake", "nice"))
        report = upskills.report(store.s, store.cv(), now=NOW)
        view = report.with_near_misses
        # The list's side of every number is the scored view's; the near side is the near misses'.
        assert asks(view.coverage)[0] == asks(report.coverage)[0]
        assert asks(view.coverage)[1] == sum(d.near_misses for d in view.demand) == 6
        assert tuple(map(sum, zip(*(asks(c) for c in view.by_category), strict=True))) == asks(view.coverage)
        listed = {d.term: d.jobs for d in report.demand}
        assert all(d.jobs == listed.get(d.term, 0) and d.near_misses <= view.near_misses for d in view.demand)
        gaps = {u.term: u for u in view.missing}
        assert all(
            (gaps[d.term].jobs, gaps[d.term].near_misses) == (d.jobs, d.near_misses) for d in view.demand if d.status == "missing"
        )
        # A near miss counts half of what a scored job as close would (the same lift).
        snow = {d.term: d for d in view.demand}["Snowflake"]
        assert snow.importance == 0 and snow.near_importance == pytest.approx(2 * 0.5 * upskills.lift(0.8, strong=0.9, weak=0.7))

    def test_the_scored_only_view_is_unchanged_by_near_misses(self, store):
        scored(store, job(53), ("Kafka", "required"), ("Python", "nice"))
        scored(store, job(54), ("Terraform", "required"))
        before = upskills.report(store.s, store.cv(), now=NOW)
        for k in ("n6", "n7"):
            nearly(store.s, k, ("Kafka", "required"), ("Bloomberg Terminal", "nice"), ("Python", "nice"))
        after = upskills.report(store.s, store.cv(), now=NOW)
        assert after.with_near_misses is not None and before.with_near_misses is None
        for part in ("jobs_considered", "demand", "coverage", "by_category", "missing", "quick_wins"):
            assert getattr(after, part) == getattr(before, part), part
        assert asks(after.coverage)[1] == 0 and all(d.near_misses == 0 for d in after.demand)


class TestPoorMatchesTeachMost:
    """20 Nov, the user: "the lower a job's ATS or fit score, the more its keywords matter"."""

    def test_the_curve_is_one_at_a_strong_match_and_two_at_a_weak_one(self):
        assert upskills.lift(0.95) == upskills.lift(0.9) == 1.0
        assert upskills.lift(0.3) == upskills.lift(0.1) == 2.0
        assert upskills.lift(0.6) == 1.5 and upskills.lift(None) == 1.0
        steps = [upskills.lift(m / 10) for m in range(10, -1, -1)]
        assert steps == sorted(steps)  # never falls as the match falls
        assert 1.5 <= upskills.lift(0.4) <= 2.0  # a fit-hidden job under the bar of 4

    def test_a_gap_from_poor_matches_outranks_one_from_strong_matches(self, store):
        scored(store, job(60, status="hidden", hidden="Fit 2.8, below your bar of 4"), ("Kafka", "required"), match=3)
        scored(store, job(61), ("Kafka", "required"), match=3)
        scored(store, job(62), ("Snowflake", "required"), match=10)
        scored(store, job(63), ("Snowflake", "required"), match=10)
        report = upskills.report(store.s, store.cv(), now=NOW)
        kafka, snow = find(report, "Kafka"), find(report, "Snowflake")
        assert (kafka.jobs, snow.jobs) == (2, 2)  # the counts stay counts
        assert kafka.score == 2 * snow.score and kafka.poor == 2 and snow.poor == 0
        terms = [u.term for u in report.missing]
        assert terms.index("Kafka") < terms.index("Snowflake")
        rows = {d.term: d for d in report.demand}
        assert rows["Kafka"].importance == 4.0 and rows["Snowflake"].importance == 2.0

    def test_a_weak_ats_score_lifts_a_strong_fit(self, store):
        for i, term, ats in ((64, "Kafka", 30.0), (65, "Snowflake", 95.0)):
            scored(store, job(i), (term, "required"), match=10)
            store.s.add(ApplicationRow(id=f"app-{i}", job_id=f"job-up-{i}", status="ready", created_at=iso(NOW)))
            store.s.flush()
            store.s.add(
                DocumentRow(
                    application_id=f"app-{i}", kind="ats", version=1, content={"score": ats, "coverage": 0.5}, created_at=iso(NOW)
                )
            )
        store.s.flush()
        report = upskills.report(store.s, store.cv(), now=NOW)
        assert find(report, "Kafka").score == 2 * find(report, "Snowflake").score  # ATS 30 weighs 2, fit 9.1 weighs 1

    def test_a_covered_skill_weighs_its_plain_count(self, store):
        before = {d.term: d for d in upskills.report(store.s, store.cv(), now=NOW).demand}.get("Python")
        scored(store, job(66), ("Python", "required"), match=2)
        scored(store, job(67), ("Python", "required"), match=2)
        python = {d.term: d for d in upskills.report(store.s, store.cv(), now=NOW).demand}["Python"]
        assert python.status == "covered" and python.importance == python.jobs == (before.jobs if before else 0) + 2

    def test_a_less_similar_near_miss_counts_more_but_below_a_scored_job(self, store):
        nearly(store.s, "w", ("Kafka", "required"), similarity=0.70)
        nearly(store.s, "s", ("Snowflake", "required"), similarity=0.92)
        scored(store, job(68), ("Terraform", "required"), match=3)
        rows = {d.term: d for d in upskills.report(store.s, store.cv(), now=NOW).with_near_misses.demand}
        assert rows["Kafka"].near_importance == 1.0 and rows["Snowflake"].near_importance == 0.5
        assert rows["Kafka"].near_importance < rows["Terraform"].importance == 2.0


class TestWhoAsksForIt:
    """UPSK-07 (21 Nov): C++ was a gap to a finance CV, asked by quantitative roles. Each gap says
    which kinds of role ask for it, and one asked mostly outside the person's field is flagged."""

    def _titled(self, i: int, title: str):
        return job(i).model_copy(update={"title": title})

    def test_a_gap_from_another_field_is_flagged_and_the_report_filters_to_one(self, store):
        scored(store, self._titled(60, "Quantitative Developer"), ("C++", "required"))
        scored(store, self._titled(61, "Quant Analyst"), ("C++", "required"))
        scored(store, self._titled(62, "Investment Banking Analyst"), ("Pitchbooks", "required"))
        finance = ["Investment Banking Analyst", "Corporate Finance Analyst"]
        report = upskills.report(store.s, store.cv(), now=NOW, titles=finance)
        cpp, books = find(report, "C++"), find(report, "Pitchbooks")
        assert report.own_family == "finance"
        assert cpp.outside_field and [f.family for f in cpp.families] == ["quantitative"]
        assert books is not None and not books.outside_field
        quant = upskills.report(store.s, store.cv(), now=NOW, titles=finance, family="quantitative")
        assert find(quant, "C++") is not None and find(quant, "Pitchbooks") is None
        assert {f.family: f.jobs for f in quant.families}["finance"] >= 1  # the filter's chips still count every family
