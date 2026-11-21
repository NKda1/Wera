"""ATSC-08 (20 Nov): the job ad beside the CV, and the term boundary it highlights by."""

import pytest

from wera_agent.domain.vocab import master_cv_text, text_has_term
from wera_agent.llm.demo import PackInput, generate_pack
from wera_agent.nodes.ats import ad_text, build_ats, compare, display_text, tailored_text, term_spans
from wera_agent.services.demo_data import seed_to_job


class TestTheBoundary:
    """resume-matcher's `keyword_in_text`: no word character touching the term on a side where the
    term itself ends in one."""

    @pytest.mark.parametrize(
        ("text", "term", "found"),
        [
            ("Built services in C# and SQL", "C#", True),
            ("C#, F# and VB", "C#", True),
            ("ABC# notation", "C#", False),
            ("Modern C++17 and Rust", "C++", True),
            ("C++/CUDA kernels", "C++", True),
            ("ASP.NET Core APIs", ".NET", True),
            ("Shipped on .NET 8", ".NET", True),
            ("Ran the CI/CD pipeline", "CI/CD", True),
            ("ci/cd, monitoring", "CI/CD", True),
            ("JavaScript and TypeScript", "Java", False),
            ("Java, Kotlin", "Java", True),
            ("python_scripts folder", "Python", False),
            ("SQL SQL", "SQL", True),
        ],
    )
    def test_cases(self, text, term, found):
        assert text_has_term(text, term) is found

    def test_two_occurrences_one_space_apart_are_both_found(self):
        """The old pattern consumed the space between them, so the second was invisible."""
        assert [(s.start, s.end) for s in term_spans("SQL SQL", "SQL")] == [(0, 3), (4, 7)]


TEXTS = [
    "Built ETL in Python and PostgreSQL; ran CI/CD on GitHub Actions.",
    "Led financial modelling and DCF valuations for a £2m fund.\nComps and LBO models.",
    "Ward nurse — patient care, IV therapy.\nRota planning in Excel.",
    "Candidates who excel under pressure. Go to the shop. REST of the team.",
    "Machine\nLearning on GCP; C++17 and ASP.NET; containers shipped by sea.",
]
TERMS = ["Python", "Postgres", "Continuous integration", "Financial Modeling", "Discounted cash flow", "comps",
         "Excel", "Go", "REST APIs", "Machine learning", "Google Cloud", "C++", ".NET", "Docker", "Testing",
         "patient care", "LBO", "Leveraged buyout"]  # fmt: skip


class TestSpansAreTheMatchersAnswers:
    """A span exactly when `text_has_term` says the term is there, so the view and the card agree."""

    @pytest.mark.parametrize("raw", TEXTS)
    def test_agrees_with_text_has_term(self, raw):
        text = display_text(raw)
        assert text_has_term(text, "x") == text_has_term(raw, "x")
        for term in TERMS:
            assert bool(term_spans(text, term)) == text_has_term(raw, term), term

    def test_a_span_is_where_the_word_is(self):
        text = display_text("Led financial modelling and DCF valuations.")
        spans = term_spans(text, "Financial Modeling")
        assert [text[s.start : s.end] for s in spans] == ["financial modelling"]  # the British spelling, as written
        assert [text[s.start : s.end] for s in term_spans(text, "Discounted cash flow")] == ["DCF valuations"]

    def test_a_proper_noun_span_is_the_word_only(self):
        text = display_text("Wrote Go services. Go to the shop? no: go home.")
        assert [text[s.start : s.end] for s in term_spans(text, "Go")] == ["Go", "Go"]

    def test_overlapping_aliases_give_one_span(self):
        text = display_text("Designed REST APIs")
        assert [text[s.start : s.end] for s in term_spans(text, "REST APIs")] == ["REST APIs"]

    def test_the_ad_loses_its_markdown_and_keeps_its_words(self):
        md = "## What you'll do\n\n* **Python** and [SQL](https://x.example/sql)\n- C# too"
        text = ad_text(md)
        assert text == "What you'll do\n• Python and SQL\n• C# too"
        for term in ["Python", "SQL", "C#"]:
            assert text_has_term(text, term) == text_has_term(md, term)


class TestTheComparison:
    def _pack(self, demo_cv, demo_jobs, i=0):
        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][i], 0)
        return job, evidence, generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1))

    @pytest.mark.parametrize("i", [0, 1, 2])
    def test_counts_what_the_ats_report_counts(self, demo_cv, demo_jobs, i):
        job, evidence, pack = self._pack(demo_cv, demo_jobs, i)
        report = build_ats(pack.cv, cv=demo_cv, evidence=evidence, cv_pages=1, job=job)
        view = compare(job, report.keywords, tailored_text(pack.cv, demo_cv), master=master_cv_text(demo_cv))
        assert not view.stale
        assert view.coverage == report.coverage
        status = {k.term: ("matched" if k.matched else "in_cv" if k.in_master_cv else "missing") for k in report.keywords}
        assert {k.term: k.status for k in view.keywords} == status
        for k in view.keywords:
            assert bool(k.cv_spans) == (k.status == "matched"), k.term
            assert k.job_spans, f"{k.term} is in the post (vocab.in_post), so it is on the ad"
        assert view.potential_coverage >= view.coverage

    def test_a_report_from_an_older_matcher_is_flagged_and_measured_again(self, demo_cv, demo_jobs):
        job, evidence, pack = self._pack(demo_cv, demo_jobs)
        report = build_ats(pack.cv, cv=demo_cv, evidence=evidence, cv_pages=1, job=job)
        hit = next(k for k in report.keywords if k.matched)
        old = [k.model_copy(update={"matched": False}) if k.term == hit.term else k for k in report.keywords]
        view = compare(job, old, tailored_text(pack.cv, demo_cv), master=master_cv_text(demo_cv))
        assert view.stale and next(k for k in view.keywords if k.term == hit.term).status == "matched"


def test_the_route_answers_for_a_drafted_pack(api):
    app = next(a for a in api.ok("GET", "/applications")["items"] if a["status"] == "ready_for_review")
    view = api.ok("GET", f"/applications/{app['id']}/compare")
    ats = api.ok("GET", f"/applications/{app['id']}")["pack"]["ats"]
    assert {k["term"] for k in view["keywords"]} == {k["term"] for k in ats["keywords"]}
    assert view["jobText"] and view["cvText"] and view["stale"] is False
    for k in view["keywords"]:
        for s in k["cvSpans"]:
            assert 0 <= s["start"] < s["end"] <= len(view["cvText"])
    assert api.code("GET", "/applications/nope/compare") == "NOT_FOUND"


class TestReportsMeasuredAgain:
    """20 Nov: reports stored before the matcher learned a spelling counted less than the Job vs CV
    tab showed. `wera jobs ats` measures them again; an unchanged pack doesn't move."""

    def test_once_measured_again_nothing_moves(self, store):
        store.remeasure_ats(apply=True)
        assert store.remeasure_ats(apply=False) == []

    def test_a_stale_report_is_measured_again(self, store):
        app_id = next(a.id for a in store.list_applications() if a.status == "ready_for_review")
        pack = store._load_pack(app_id)
        pack.ats = pack.ats.model_copy(update={"score": 1.0})
        store._save_pack(app_id, pack, ("ats",))
        moved = store.remeasure_ats(apply=True)
        assert any(a == app_id and old == 1.0 for a, _c, old, _new in moved)
        assert store._load_pack(app_id).ats.score != 1.0
