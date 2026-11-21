"""UPSK-05 (20 Nov): a post's degree is judged against the CV's education, never listed as a skill.

On 20 Nov Upskills showed "Bachelor’s Degree" and "Business Administration" as gaps to a CV whose
education reads "BSc (Hons) Economics and Finance". These pin what a recruiter would say instead.
"""

import pytest

from wera_agent.domain import degrees
from wera_agent.schemas.cv import Education
from wera_agent.schemas.jobs import EducationAsk


def cv_with(store, *lines: str):
    return store.cv().model_copy(
        update={"education": [Education(id=f"edu-{i}", degree=d, institution="A University") for i, d in enumerate(lines)]}
    )


class TestTheLevelACvWrites:
    @pytest.mark.parametrize(
        ("degree", "level"),
        [
            ("BSc (Hons) Economics and Finance", "bachelor"),
            ("BA (Hons) in Computer Science", "bachelor"),
            ("LLB Law", "bachelor"),
            ("MSc Financial Engineering", "master"),
            ("Integrated Masters (MEng) Hons, Mechanical Engineering", "master"),
            ("MBA", "master"),
            ("PhD in Physics", "phd"),
            ("DPhil Economics", "phd"),
            ("A-Levels & GCSEs", "none"),
        ],
    )
    def test_each_way_of_writing_it(self, degree, level):
        assert degrees.level_of(degree) == level

    def test_the_cv_is_its_highest_degree(self, store):
        assert degrees.cv_level(cv_with(store, "A-Levels", "BSc Mathematics", "MSc Statistics")) == "master"


class TestMeetingTheAsk:
    def test_a_bsc_meets_a_bachelors_in_business_administration(self, store):
        """Economics and Finance is a business degree for this purpose, which is how a recruiter
        reads "a Bachelor's in Business Administration or similar"."""
        cv = cv_with(store, "BSc (Hons) Economics and Finance")
        ask = EducationAsk(level="bachelor", subjects=["Business Administration"], required=True)
        assert degrees.meets(ask, cv) == "met"

    def test_a_masters_ask_is_a_level_gap_to_a_bachelors(self, store):
        ask = EducationAsk(level="master", subjects=["Finance"])
        assert degrees.meets(ask, cv_with(store, "BSc Economics")) == "level_gap"

    def test_a_subject_in_another_family_is_a_subject_gap(self, store):
        ask = EducationAsk(level="bachelor", subjects=["Law"])
        assert degrees.meets(ask, cv_with(store, "BSc Computer Science")) == "subject_gap"

    def test_a_related_field_is_the_cvs_own(self, store):
        ask = EducationAsk(level="bachelor", subjects=["a related field"])
        assert degrees.meets(ask, cv_with(store, "BA History")) == "met"

    def test_no_level_and_no_subject_asks_nothing(self, store):
        assert degrees.meets(EducationAsk(), cv_with(store, "BSc Physics")) is None
        assert degrees.meets(None, cv_with(store, "BSc Physics")) is None


class TestADegreeIsNotASkill:
    @pytest.mark.parametrize("term", ["Bachelor’s Degree", "Bachelor's degree", "MSc", "Business Administration", "Economics"])
    def test_degree_terms(self, term):
        assert degrees.looks_like_degree(term)

    @pytest.mark.parametrize(
        "term", ["Financial modelling", "Machine learning", "Data science", "Finance", "Accounting", "Python"]
    )
    def test_skills_that_share_a_word_with_a_subject_stay(self, term):
        assert not degrees.looks_like_degree(term)
