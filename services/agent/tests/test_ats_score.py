"""ATSC-07 as amended on 20 Nov: parts that can't be measured, the graded title, the 15-skill cap,
and section headers the theme sets larger than the body."""

import pytest

from wera_agent.llm.demo import PackInput, generate_pack
from wera_agent.nodes.ats import MAX_SKILLS, PART_MAX, build_ats, reconcile_skills, title_grade
from wera_agent.schemas.jobs import JobKeyword
from wera_agent.services.demo_data import seed_to_job

FERNHILL = 0


@pytest.fixture
def drafted(demo_cv, demo_jobs):
    job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
    doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).cv
    return job, evidence, doc


class TestAPartThatCantBeMeasured:
    """A post with no keywords used to give the keywords part 45 of 45: full marks for a
    measurement nobody made. Now it is None, and the rest fill 100."""

    def test_no_keywords_means_two_parts_unmeasured_and_the_rest_rescaled(self, demo_cv, drafted):
        job, evidence, doc = drafted
        evidence.keywords = []
        report = build_ats(doc, cv=demo_cv, evidence=evidence, cv_pages=1, job=job)
        parts = {p.id: p for p in report.parts}
        assert parts["keywords"].points is None and parts["evidence"].points is None
        assert parts["keywords"].max == parts["evidence"].max == 0
        assert "Not measured" in parts["keywords"].detail
        # 15 + 10 + 10 of the old weights, scaled to 100.
        assert parts["alignment"].max == pytest.approx(15 * 100 / 35, abs=0.05)
        assert parts["structure"].max == pytest.approx(10 * 100 / 35, abs=0.05)
        assert sum(p.max for p in report.parts) == pytest.approx(100, abs=0.2)
        assert report.score == round(sum(p.points or 0 for p in report.parts))
        assert report.best_score == report.score  # nothing to place

    def test_every_part_measured_is_the_score_it_always_was(self, demo_cv, drafted):
        """No rescaling when nothing is missing: the weights are PART_MAX's, as before."""
        job, evidence, doc = drafted
        report = build_ats(doc, cv=demo_cv, evidence=evidence, cv_pages=1, job=job)
        assert {p.id: p.max for p in report.parts} == PART_MAX
        assert all(p.points is not None for p in report.parts)
        assert report.score == round(sum(p.points for p in report.parts))

    def test_rescaling_keeps_the_ratio_between_measured_parts(self, demo_cv, drafted):
        job, evidence, doc = drafted
        with_words = build_ats(doc, cv=demo_cv, evidence=evidence, cv_pages=1, job=job)
        evidence.keywords = []
        without = build_ats(doc, cv=demo_cv, evidence=evidence, cv_pages=1, job=job)
        a = {p.id: p for p in with_words.parts}
        b = {p.id: p for p in without.parts}
        # Structure measures the same thing either way; only its share of 100 changed.
        assert b["structure"].points / b["structure"].max == pytest.approx(a["structure"].points / a["structure"].max, abs=0.01)


class TestTheTitleGrade:
    """resume-matcher's grading: headline 100, summary 75, otherwise half the share of words."""

    def test_in_the_headline(self):
        assert title_grade("Senior Data Analyst (Hybrid)", "Data analyst, SQL and Python", "Anything.")[0] == 100

    def test_in_the_summary_only(self):
        grade, why = title_grade("Data Analyst", "Graduate, LSE", "A data analyst who reconciles ledgers.")
        assert grade == 75 and "summary" in why

    def test_word_overlap_otherwise(self):
        grade, why = title_grade("Data Analyst", "Economics graduate", "Built data pipelines.")
        assert grade == 25 and "1 of 2" in why

    def test_none_of_its_words(self):
        assert title_grade("Data Analyst", "Nurse", "Ward work.")[0] == 0

    def test_a_title_with_no_words_is_not_measured(self):
        assert title_grade("Senior (Remote)", "Anything", "Anything")[0] is None

    def test_the_alignment_part_grades_the_headline_above_the_summary(self, demo_cv, drafted):
        job, evidence, doc = drafted
        cv = demo_cv.model_copy(deep=True)
        cv.person.headline = job.title
        doc.summary.text = "Builds things."
        headline = next(p for p in build_ats(doc, cv=cv, evidence=evidence, cv_pages=1, job=job).parts if p.id == "alignment")
        cv.person.headline = "Engineer"
        doc.summary.text = f"A {job.title} who builds things."
        summary = next(p for p in build_ats(doc, cv=cv, evidence=evidence, cv_pages=1, job=job).parts if p.id == "alignment")
        assert headline.points == 15 and summary.points < headline.points


class TestFifteenSkills:
    """The JobLeads review (19 Nov): 8–15 skills, tailored per job. It was 25."""

    def test_the_line_never_holds_more_than_fifteen(self, demo_cv, drafted):
        job, evidence, doc = drafted
        cv = demo_cv.model_copy(deep=True)
        short = [f"S{i}" for i in range(30)]  # names short enough that the page would hold them all
        cv.skills = [cv.skills[0].model_copy(update={"name": n, "aliases": []}) for n in short]
        evidence.keywords = [JobKeyword(term=n, weight="required", category="tool") for n in short]
        doc.skills = []
        assert len(reconcile_skills(doc, cv=cv, evidence=evidence, cv_pages=2, threshold=1.0).skills) == MAX_SKILLS == 15

    def test_more_than_fifteen_costs_the_length_points(self, demo_cv, drafted):
        job, evidence, doc = drafted
        doc.skills = [f"Skill {i}" for i in range(16)]
        length = next(p for p in build_ats(doc, cv=demo_cv, evidence=evidence, cv_pages=1, job=job).parts if p.id == "length")
        assert "more than 15" in length.detail


class TestSectionHeaders:
    """The JobLeads review: section headers in a larger size than the body."""

    def test_every_theme_sets_them_larger_than_the_body(self):
        from wera_agent.export import cv_yaml

        sizes = cv_yaml.section_title_sizes()
        assert set(sizes) == set(cv_yaml.THEMES)
        for theme in cv_yaml.THEMES:
            design = cv_yaml.default_design(theme)
            floor = (design.get("typography") or {}).get("font_size", {}).get("section_titles")
            assert sizes[theme] >= cv_yaml.SECTION_TITLE_MIN_EM or floor == f"{cv_yaml.SECTION_TITLE_MIN_EM}em", theme
