"""Reading a CV without a model (CV-03).

Two things are under test here, and the second matters more than the first.

The first is that a conventionally laid out CV comes out right: the roles, the dates, the
bullets, the skills. Those tests are ordinary.

The second is **declining**. This parser is allowed to be wrong in ways a person will notice
and correct — that is what the CV-03 review step is for — but it is not allowed to be
confidently wrong in ways they won't. A two-column PDF whose Experience section extracted as
interleaved nonsense must reach the model, not arrive as a CV with no jobs in it. The decline
tests are the ones to keep if the rest are ever rewritten.
"""

import pytest

from wera_agent.domain import cv_build, cv_extract

CLEAN = """\
Jordan Ellery
Computer science graduate interested in distributed systems
London, United Kingdom
jordan.ellery@example.com | +44 7700 900123
linkedin.com/in/jordan-ellery | github.com/jellery

Summary
Backend-leaning graduate with one internship and a side project in production.

Experience

Software Engineering Intern, Monzo Bank — London
Jun 2025 - Sep 2025
• Built an internal service for reconciling card settlements.
• Added integration tests to the payments pipeline.

Teaching Assistant, University of Leeds
Oct 2024 – May 2025
• Ran weekly lab sessions on algorithms for 30 first-year students.

Projects

Ferry — a real-time transit tracker
2024 - 2025
• Go backend with a Postgres read model.

Education

BSc Computer Science, University of Leeds
Sept 2022 — Jul 2025

Skills
Python, Go, TypeScript, PostgreSQL
Testing: pytest, Playwright

Languages
English (native)
German (B2)
"""


def read(text: str) -> cv_extract.Extraction:
    return cv_extract.extract(text)


class TestACleanCv:
    """The case that has to need no model at all, because it is most of them."""

    def test_is_read_without_declining(self):
        assert read(CLEAN).decline is None

    def test_splits_title_from_employer(self):
        roles = read(CLEAN).cv.experience
        assert [(r.title, r.org) for r in roles] == [
            ("Software Engineering Intern", "Monzo Bank"),
            ("Teaching Assistant", "University of Leeds"),
        ]

    def test_keeps_the_location_off_the_employer(self):
        assert read(CLEAN).cv.experience[0].location == "London"

    def test_reads_dates_through_cv_build(self):
        cv = cv_build.to_master_cv(read(CLEAN).cv, version=1)
        assert (cv.experience[0].start, cv.experience[0].end) == ("2025-06", "2025-09")

    def test_keeps_bullets_separate_and_in_order(self):
        bullets = read(CLEAN).cv.experience[0].bullets
        assert len(bullets) == 2
        assert bullets[0].startswith("Built an internal service")

    def test_a_project_name_is_the_org_so_the_id_reads(self):
        # `cv_build` builds the id from `org`, and `prj-ferry` is the readable id a guard
        # violation has to show a person. A project is written "Name — what it is", which is
        # the opposite order to a job.
        cv = cv_build.to_master_cv(read(CLEAN).cv, version=1)
        assert cv.projects[0].id == "prj-ferry"

    def test_reads_education_with_its_dates(self):
        cv = cv_build.to_master_cv(read(CLEAN).cv, version=1)
        assert (cv.education[0].degree, cv.education[0].institution) == (
            "BSc Computer Science",
            "University of Leeds",
        )

    def test_flattens_skills_across_layouts(self):
        skills = read(CLEAN).cv.skills
        assert "Python" in skills and "Playwright" in skills

    def test_drops_a_skills_label_rather_than_storing_it(self):
        # "Testing: pytest, Playwright" — the label is the CV's own category, not a skill.
        assert "Testing" not in read(CLEAN).cv.skills

    def test_reads_spoken_languages_with_levels(self):
        assert [(x.name, x.level) for x in read(CLEAN).cv.languages] == [
            ("English", "native"),
            ("German", "B2"),
        ]

    def test_takes_the_headline_from_under_the_name(self):
        assert read(CLEAN).cv.headline == "Computer science graduate interested in distributed systems"

    def test_keeps_links(self):
        labels = {x.label for x in read(CLEAN).cv.links}
        assert labels == {"LinkedIn", "GitHub"}


class TestContactDetails:
    """CV-06/PRIV-01: an email, phone or street number is stored separately and never becomes
    part of the CV — including by accident, as a stray fragment of a name or a city."""

    def test_the_email_never_reaches_a_field(self):
        assert "jordan.ellery@example.com" not in read(CLEAN).cv.model_dump_json()

    def test_the_phone_number_never_reaches_a_field(self):
        assert "900123" not in read(CLEAN).cv.model_dump_json()

    def test_the_name_is_not_contaminated_by_the_contact_line(self):
        assert read(CLEAN).cv.name == "Jordan Ellery"


class TestLayoutsItStillReads:
    """Variations that are common enough that falling back on them would waste the exercise."""

    def test_headings_in_capitals(self):
        assert read(CLEAN.replace("Experience", "EXPERIENCE")).decline is None

    def test_a_date_on_the_same_line_as_the_header(self):
        text = CLEAN.replace(
            "Software Engineering Intern, Monzo Bank — London\nJun 2025 - Sep 2025",
            "Software Engineering Intern, Monzo Bank — Jun 2025 - Sep 2025",
        )
        role = read(text).cv.experience[0]
        assert (role.title, role.org) == ("Software Engineering Intern", "Monzo Bank")

    def test_hyphen_bullets(self):
        assert len(read(CLEAN.replace("• ", "- ")).cv.experience[0].bullets) == 2

    def test_bullets_written_as_plain_lines(self):
        assert len(read(CLEAN.replace("• ", "")).cv.experience[0].bullets) == 2

    def test_a_following_header_is_not_swallowed_as_a_bullet(self):
        # The failure this guards: an unmarked line is ambiguous between "a bullet of the role
        # above" and "the title of the role below", and reading it the wrong way loses a whole
        # job. A date range arriving two lines later is what settles it.
        assert len(read(CLEAN.replace("• ", "")).cv.experience) == 2

    def test_education_with_no_dates_at_all(self):
        text = CLEAN.replace(
            "BSc Computer Science, University of Leeds\nSept 2022 — Jul 2025", "BSc Computer Science, University of Leeds"
        )
        cv = read(text).cv
        assert cv is not None, "a degree written without years is ordinary, not a reason for a model"
        assert cv.education[0].institution == "University of Leeds"

    def test_a_heading_with_a_year_in_it_is_an_entry_not_a_heading(self):
        # "Education 2021-2024" is a line in someone's CV, not a section break.
        assert cv_extract._heading("Education 2021-2024") is None


DENSE = """\
OLIVER TAYLOR
London, UK • +44 7700 900077 • oliver.taylor@email.com • linkedin.com/in/oliver-taylor
EDUCATION
University of Cambridge Oct 2023 - Jun 2026
BA (Hons) in Computer Science, Cambridge, UK
First Class Honours expected (Current average: 78%)
Relevant Modules: Advanced Algorithms, Optimising Compilers, Distributed Systems,
Machine Learning
TECHNICAL SKILLS
Languages: Python, Java, C++, TypeScript, Go
PROFESSIONAL EXPERIENCE
Software Engineering Intern - DeepMind Matrix Lab Jun 2025 - Sep 2025
• Refactored a high-throughput internal data pipeline using Go and gRPC, lowering overhead by
22%.
• Containerized legacy execution microservices with Docker.
PROJECTS
Type-Inferred Custom Programming Language Compiler - OCaml, LLVM Academic Project
• Designed a functional language compiler featuring a custom Hindley-Milner type inference
system.
High-Availability Distributed Key-Value Store - Python, Raft Consensus Personal Project
• Implemented the Raft consensus algorithm from scratch in Python.
HONOURS & AWARDS
• Trinity College Academic Scholarship, awarded for performance in the Tripos (2024)
"""


class TestADenseOnePageLayout:
    """The conventions a real graduate CV actually uses, each of which broke this once.

    Dates right-aligned onto the header line, the institution and the degree on separate lines,
    bullets wrapped by the PDF extractor, long project titles, and a section heading made of two
    words that appear nowhere as a phrase.
    """

    def test_it_is_read_without_a_model(self):
        assert read(DENSE).decline is None

    def test_an_all_caps_name_is_still_a_name(self):
        assert read(DENSE).cv.name == "OLIVER TAYLOR"

    def test_a_right_aligned_date_is_taken_off_the_header(self):
        role = read(DENSE).cv.experience[0]
        assert (role.title, role.org) == ("Software Engineering Intern", "DeepMind Matrix Lab")

    def test_a_degree_written_under_its_institution_is_one_entry(self):
        # "University of Cambridge <dates>" / "BA (Hons) in Computer Science" is two lines and
        # one qualification. Read as two entries it yields a degree called "University of
        # Cambridge", which is why an unsplittable education entry otherwise declines.
        education = read(DENSE).cv.education
        assert len(education) == 1
        assert education[0].institution == "University of Cambridge"
        assert education[0].degree.startswith("BA (Hons) in Computer Science")

    def test_a_note_under_a_degree_does_not_become_a_degree(self):
        # "Relevant Modules: Advanced Algorithms, Optimising Compilers, …" splits on its commas
        # convincingly enough to look like an entry, and "Systems" is a company word.
        assert [e.degree for e in read(DENSE).cv.education] == [read(DENSE).cv.education[0].degree]

    def test_a_wrapped_bullet_is_rejoined_rather_than_split_in_two(self):
        bullets = read(DENSE).cv.experience[0].bullets
        assert len(bullets) == 2
        assert bullets[0].endswith("lowering overhead by 22%.")

    def test_a_long_project_title_is_a_header_not_a_bullet(self):
        # 86 characters. A length cap tuned for job titles swallowed this into the project above.
        projects = read(DENSE).cv.projects
        assert len(projects) == 2
        assert projects[1].org == "High-Availability Distributed Key-Value Store"

    def test_a_heading_made_of_two_section_words_is_still_a_heading(self):
        # "HONOURS & AWARDS" matches no heading phrase, and missing it put an awards list into
        # Projects as three more projects.
        assert cv_extract._heading("HONOURS & AWARDS") == "interests"
        assert len(read(DENSE).cv.projects) == 2

    def test_a_labelled_skills_line_still_yields_the_skills(self):
        assert {"Python", "Java", "C++", "TypeScript", "Go"} <= set(read(DENSE).cv.skills)


class TestWhenItDeclines:
    """The important half. Each of these would otherwise produce a confident, wrong CV."""

    def test_a_two_column_scramble_reaches_the_model(self):
        # What a PDF extractor does to two columns: the Experience heading survives, its
        # content does not. Handing this back with no jobs in it is the worst outcome.
        scrambled = """\
Jordan Ellery
London, United Kingdom

Experience
Monzo Bank Python Go London Leeds
settlements reconciliation algorithms labs

Education
BSc Computer Science
"""
        assert "no datable entries" in read(scrambled).decline

    def test_a_cv_with_no_headings_reaches_the_model(self):
        assert read("Jordan Ellery\nI worked at Monzo from 2024 to 2025 doing backend work.").decline

    def test_a_cv_with_no_name_reaches_the_model(self):
        assert "no name" in read(CLEAN.replace("Jordan Ellery\n", "")).decline

    def test_an_empty_document_reaches_the_model(self):
        assert read("").decline is not None

    def test_a_declined_extraction_carries_no_cv(self):
        # The caller branches on `cv is not None`, so a decline that also returned a partial CV
        # would silently skip the fallback.
        assert read("").cv is None


class TestAmbiguity:
    """A guess is recorded as a guess. It is not a decline: the person confirms the proposal
    either way (CV-03), and one uncertain header is a poor reason to pay for the whole CV."""

    def test_an_unreadable_header_is_flagged(self):
        text = CLEAN.replace("Software Engineering Intern, Monzo Bank — London", "Vortex, Pinnacle")
        assert read(text).ambiguous

    def test_a_readable_header_is_not_flagged(self):
        assert read(CLEAN).ambiguous == []

    def test_flagging_does_not_prevent_a_result(self):
        text = CLEAN.replace("Software Engineering Intern, Monzo Bank — London", "Vortex, Pinnacle")
        assert read(text).cv is not None


class TestTheParsePath:
    """`store.parse_cv_text` has to actually use this — the saving is the point."""

    def test_a_clean_cv_costs_no_llm_call(self, store, monkeypatch):
        """The point of the whole module. An ordinary CV reaches no model at all."""
        from wera_agent.services import store as store_module

        def refuse(*a, **kw):
            raise AssertionError("an ordinary CV should not reach a model")

        monkeypatch.setattr(store_module, "structured", refuse)
        cv = store.parse_cv_text(CLEAN)
        assert cv.person.name == "Jordan Ellery"
        assert [r.org for r in cv.experience] == ["Monzo Bank", "University of Leeds"]

    def test_the_ids_still_come_from_cv_build(self, store):
        cv = store.parse_cv_text(CLEAN)
        ids = [b.id for r in cv.experience for b in r.bullets]
        assert len(ids) == len(set(ids))
        assert all(b.id.startswith(r.id) for r in cv.experience for b in r.bullets)

    def test_a_cv_it_cannot_read_is_handed_to_the_model(self, store, monkeypatch):
        """The fallback is the whole safety net: if declining stopped routing to `cv.parse`,
        every CV this parser can't read would fail instead of costing one call."""
        from wera_agent.services import store as store_module

        asked: list[str] = []
        real = store_module.structured

        def spy(session, task, Output, variables, **kw):
            asked.append(task)
            return real(session, task, Output, variables, **kw)

        monkeypatch.setattr(store_module, "structured", spy)
        prose = (
            "Jordan Ellery\n"
            "I worked at Monzo Bank from 2024 to 2025 on backend services, mostly Python and Go, "
            "after graduating from the University of Leeds with a degree in computer science. "
            "Before that I was a teaching assistant running algorithms labs for first-year students.\n"
        )
        from wera_agent.errors import ApiError

        # The demo responder can't read flowing prose either, so this still fails — but it
        # fails *after* reaching `cv.parse`, which is the routing this test is about.
        with pytest.raises(ApiError):
            store.parse_cv_text(prose)
        assert asked == ["cv.parse"]

    def test_a_document_with_no_text_is_still_refused(self, store):
        from wera_agent.errors import ApiError

        with pytest.raises(ApiError):
            store.parse_cv_text("   ")
