"""The keyword-aware cover letter (LTR-01…06 with the ATS letter template).

The template places seven keywords and draws on the post's strategic context. What these hold it
to: the letter is only ever offered keywords the CV can back, each with the line that backs it;
the rest are named as never-claim; a context field the post doesn't state leaves its clause out;
and whether the letter actually carried the keywords is measured, not assumed.
"""

from wera_agent.db.session import transaction
from wera_agent.llm import demo
from wera_agent.nodes.letter_plan import context_text, keywords_text, plan
from wera_agent.nodes.tailor import _repaired
from wera_agent.schemas.jobs import FitEvidence, JobKeyword, SubScore
from wera_agent.schemas.pack import Cited, JobAnalysis, KeywordMatrix, Paragraph, RoleProfile, StrategicContext
from wera_agent.services.store import Store


def _sub() -> SubScore:
    return SubScore(score=5, job_quote="", cv_bullet_ids=[])


def _evidence(keywords: list[JobKeyword], culture: list[str] | None = None) -> FitEvidence:
    return FitEvidence(
        core=_sub(),
        adjacent=_sub(),
        experience=_sub(),
        eligibility=_sub(),
        reason="",
        job_quote="",
        cv_bullet_ids=[],
        keywords=keywords,
        culture=culture or [],
        model="m",
    )


def kw(term: str, category: str, weight: str = "required") -> JobKeyword:
    return JobKeyword(term=term, weight=weight, category=category)  # type: ignore[arg-type]


class TestThePlan:
    def test_only_keywords_the_cv_backs_are_offered(self, demo_cv):
        p = plan(_evidence([kw("Python", "hard_skill"), kw("Kubernetes", "tool"), kw("Rust", "hard_skill")]), demo_cv)
        assert [s.term for s in p.keywords] == ["Python"]
        assert p.never_claim == ["Kubernetes", "Rust"]

    def test_a_skill_needs_no_citation_and_a_line_only_term_names_its_line(self, demo_cv):
        """Mirrors `check_cited`: the skills list is cite-free, anything else needs its line."""
        p = plan(_evidence([kw("FastAPI", "tool"), kw("Azure AI Foundry", "tool")]), demo_cv)
        by_term = {s.term: s for s in p.keywords}
        assert by_term["FastAPI"].cite == ()
        assert by_term["Azure AI Foundry"].cite == ("exp-idsu-02",)

    def test_required_keywords_take_the_opening_slots(self, demo_cv):
        p = plan(_evidence([kw("TypeScript", "hard_skill", "nice"), kw("Python", "hard_skill")]), demo_cv)
        assert [(s.slot, s.term) for s in p.keywords] == [(1, "Python"), (2, "TypeScript")]

    def test_a_skill_shown_only_in_projects_is_the_adjacent_slot(self, demo_cv):
        """ATS_KEYWORD_7 is "expanding my capabilities in" — true only for work outside a paid role."""
        p = plan(_evidence([kw("schema validation", "methodology"), kw("Python", "hard_skill")]), demo_cv)
        assert {s.term: s.slot for s in p.keywords} == {"schema validation": 7, "Python": 1}

    def test_a_slot_with_nothing_honest_for_it_stays_empty(self, demo_cv):
        p = plan(_evidence([kw("Python", "hard_skill")]), demo_cv)
        assert [s.slot for s in p.keywords] == [1]

    def test_the_prompt_text_names_every_never_claim_and_offers_none_of_them(self, demo_cv):
        text = keywords_text(plan(_evidence([kw("Python", "hard_skill"), kw("Kubernetes", "tool")], ["ownership"]), demo_cv))
        offered, _, never = text.partition("Never claim")
        assert "Kubernetes" not in offered and "Kubernetes" in never
        # Grouped by paragraph, not by slot number: measured live, numbered slots made the writing
        # tier think for 10k tokens over a 264-word letter.
        assert "- Opening: Python" in offered
        assert "- Why this company: ownership" in offered
        assert "ATS_KEYWORD" not in text


class TestContext:
    def _analysis(self, **fields) -> JobAnalysis:
        context = {k: None for k in StrategicContext.model_fields} | fields
        return JobAnalysis(
            role_profile=RoleProfile(
                target_role_title="t", department=None, company_name="c", industry_name=None, experience_level=None
            ),
            ats_keyword_matrix=KeywordMatrix(
                hard_skills=[], methodologies_and_frameworks=[], tools_and_software=[], soft_skills_and_culture=[]
            ),
            strategic_context=StrategicContext(**context),
        )

    def test_only_stated_fields_reach_the_letter(self):
        text = context_text(self._analysis(company_problem_to_solve="claims take a week to decide"))
        assert 'company_problem_to_solve: "claims take a week to decide"' in text
        assert "company_value_or_pillar" not in text

    def test_no_analysis_says_so_rather_than_guessing(self):
        assert "`companyQuote` alone" in context_text(None)
        assert "`companyQuote` alone" in context_text(self._analysis())


def test_a_repaired_paragraph_keeps_its_place_in_the_template():
    original = Paragraph(text="old", source_ids=["a"], section="opening_hook", theme="t")
    fixed = _repaired(original, Cited(text="new", source_ids=["a"]))
    assert (fixed.text, fixed.section, fixed.theme) == ("new", "opening_hook", "t")
    assert _repaired(original, None) is None


class TestTheDraftedLetter:
    def _draft(self, monkeypatch, job_id: str = "job-fernhill-mle") -> tuple[str, dict]:
        from wera_agent.schemas.api import DecisionIn
        from wera_agent.services import drafting

        seen: dict = {}
        real = demo.respond

        def spy(task, variables, context, demo_input=None):
            if task == "letter.write":
                seen.update(variables)
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", spy)
        with transaction() as session:
            store = Store(session)
            # A seeded application may already hold this job; a fresh one keeps the test about
            # what *this* draft was given.
            for app in store.list_applications():
                if app.job_id == job_id:
                    store._app_row(app.id).status = "discarded"
            store.decide(job_id, DecisionIn(action="approve"))
            app_id = store.started_application_id
        with transaction() as session:
            drafting.draft_pack(session, app_id, ready=True)
        return app_id, seen

    def test_the_letter_is_given_the_plan_and_the_posts_context(self, client, monkeypatch):
        app_id, seen = self._draft(monkeypatch)
        assert "- Opening:" in seen["keywords"]
        with transaction() as session:
            pack = Store(session)._load_pack(app_id)
        assert pack is not None and pack.analysis is not None
        stated = [v for v in pack.analysis.strategic_context.model_dump(by_alias=False).values() if v]
        assert stated and all(f'"{q}"' in seen["context"] for q in stated)

    def test_the_stored_letter_records_its_template_and_is_measured(self, client, monkeypatch):
        app_id, _ = self._draft(monkeypatch)
        with transaction() as session:
            pack = Store(session)._load_pack(app_id)
        assert pack is not None
        sections = [p.section for p in pack.letter.paragraphs]
        assert sections[0] == "opening_hook" and sections[-1] == "call_to_action_and_close"
        assert pack.letter.hook_type is not None
        assert pack.ats.letter_coverage is not None
        assert all(k.in_letter is not None for k in pack.ats.keywords)

    def test_editing_the_letter_remeasures_it(self, client, monkeypatch):
        """The ATS report reads the letter now, so a letter edit has to reach it — a keyword typed
        into the letter by hand counts the moment it is saved."""
        app_id, _ = self._draft(monkeypatch)
        with transaction() as session:
            pack = Store(session)._load_pack(app_id)
        assert pack is not None
        backed = [k for k in pack.ats.keywords if k.in_master_cv]
        target = next((k for k in backed if not k.in_letter), backed[0])
        last = len(pack.letter.paragraphs) - 1
        text = pack.letter.paragraphs[last].text + f" I'd bring {target.term} to it."
        res = client.patch(f"/documents/{app_id}.letter", json={"target": f"p{last}", "text": text})
        assert res.status_code == 200, res.text
        after = {k["term"]: k for k in res.json()["pack"]["ats"]["keywords"]}
        assert after[target.term]["inLetter"] is True
