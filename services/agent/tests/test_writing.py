"""ATSC-09 (20 Nov): the Writing card — action verbs, numbers, summary length, skills count."""

import pytest

from wera_agent.domain.writing import check_bullet, opens_with_action, weak_opening, writing_report
from wera_agent.llm.demo import PackInput, generate_pack
from wera_agent.services.demo_data import seed_to_job

JOBLEADS = "Collaborated in an Agile team of 6 engineers, participating in daily standups and sprint planning."


class TestALine:
    def test_the_line_the_jobleads_review_flagged(self):
        issues, weak, suggestions, _ = check_bullet(JOBLEADS)
        assert "weak_verb" in issues and weak == "Collaborated in"
        assert suggestions and all(s[:1].isupper() for s in suggestions)

    @pytest.mark.parametrize(
        ("line", "weak"),
        [
            ("Helped launch the spring campaign", "helped"),
            ("Worked on the payments API", "worked on"),
            ("Assisted the ward sister with handovers", "assisted"),
            ("Was responsible for month-end close", "was responsible for"),
            ("Participated in the audit of 40 accounts", "participated in"),
            ("Ran the till, helping new staff settle in", "helping"),
        ],
    )
    def test_weak_openings(self, line, weak):
        assert weak_opening(line) == weak

    @pytest.mark.parametrize(
        "line",
        [
            "Reconciled 40 supplier accounts each month",
            "Led a team of 4 baristas",
            "Taught Year 9 maths to 30 pupils",
            "Onboarded 12 new clients",
            "Built a churn model in Python",
            "• Negotiated renewals worth £1.2m",
        ],
    )
    def test_action_openings_for_any_kind_of_work(self, line):
        assert opens_with_action(line) and weak_opening(line) is None

    def test_no_verb(self):
        assert check_bullet("Python scripts for 3 reports")[0] == ["no_verb"]

    def test_no_number_and_the_figure_to_ask_for(self):
        issues, _, _, figure = check_bullet("Cut onboarding time for new hires", figure="Cut onboarding time")
        assert issues == ["no_number"] and figure == "Cut onboarding time"

    def test_a_line_with_a_number_is_not_asked_for_one(self):
        """A version number is not a result (`metrics_in`), but a count of accounts is."""
        assert check_bullet("Reconciled 40 accounts", figure="Reconciled accounts")[3] is None
        assert "no_number" in check_bullet("Upgraded the service to Python 3.11")[0]


class TestTheReport:
    @pytest.fixture
    def pack(self, demo_cv, demo_jobs):
        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][0], 0)
        return generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1))

    def test_targets_and_shares(self, pack, demo_cv):
        report = writing_report(pack.cv, demo_cv)
        lines = [b for s in pack.cv.sections for i in s.items for b in i.bullets]
        assert report.bullet_count == len(lines)
        assert 0 <= report.action_share <= 1 and 0 <= report.number_share <= 1
        assert (report.summary.low, report.summary.high) == (50, 100)
        assert (report.skills.low, report.skills.high, report.skills.count) == (8, 15, len(pack.cv.skills))
        assert report.skills.ok == (8 <= len(pack.cv.skills) <= 15)

    def test_suggests_and_never_writes(self, pack, demo_cv):
        """Every flagged line is the pack's own line, word for word, and no figure is a number."""
        doc = pack.cv.model_copy(deep=True)
        first = doc.sections[0].items[0].bullets[0]
        first.text = "Helped the team ship a release"
        implied = {first.source_ids[0]: "ship a release"}
        report = writing_report(doc, demo_cv, implied=implied)
        top = report.bullets[0]
        assert top.text == first.text and top.weak_phrase == "Helped" and top.figure_prompt == "ship a release"
        shown = {b.text for s in doc.sections for i in s.items for b in i.bullets}
        assert all(b.text in shown for b in report.bullets)
        assert all(not any(c.isdigit() for c in b.figure_prompt or "") for b in report.bullets)

    def test_a_long_summary_and_too_many_skills(self, pack, demo_cv):
        doc = pack.cv.model_copy(deep=True)
        doc.summary.text = "word " * 120
        doc.skills = [f"S{i}" for i in range(20)]
        report = writing_report(doc, demo_cv)
        assert not report.summary.ok and "aim for 50–100" in report.summary.message
        assert not report.skills.ok and report.skills.count == 20


def test_the_route(api):
    app = next(a for a in api.ok("GET", "/applications")["items"] if a["status"] == "ready_for_review")
    report = api.ok("GET", f"/applications/{app['id']}/writing")
    assert report["bulletCount"] > 0 and set(report["summary"]) >= {"count", "low", "high", "ok", "message"}
