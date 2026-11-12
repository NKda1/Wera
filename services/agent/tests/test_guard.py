"""Citation guard (SPEC §8.5)."""

from wera_agent.domain.guard import check_cited
from wera_agent.schemas.pack import Cited


def cite(text: str, ids: list[str] | None = None, **kw):
    return [("b0", Cited(text=text, source_ids=["exp-idsu-01"] if ids is None else ids, **kw))]


def test_passes_a_line_that_stays_within_its_source(demo_cv):
    assert check_cited(
        cite("Built a FastAPI gateway that scans prompts for personal data before they reach LLMs."), demo_cv
    ).pass_


def test_fails_a_skill_the_cited_line_doesnt_mention(demo_cv):
    r = check_cited(cite("Built a FastAPI gateway in Docker that scans prompts for personal data."), demo_cv)
    assert not r.pass_
    assert r.violations[0].rule == "terms"


def test_fails_a_number_that_isnt_in_the_source(demo_cv):
    r = check_cited(cite("Cut prompt leaks by 40% with a FastAPI gateway."), demo_cv)
    assert any(v.rule == "numbers" for v in r.violations)


def test_fails_unknown_and_missing_source_ids(demo_cv):
    assert not check_cited(cite("Did things.", ["exp-nope-01"]), demo_cv).pass_
    assert not check_cited(cite("Did things.", []), demo_cv).pass_


def test_does_not_re_check_lines_the_user_edited(demo_cv):
    assert check_cited(cite("Ran Docker at 40% scale.", edited_by_user=True), demo_cv).pass_


def test_accepts_a_skill_from_the_master_skills_list(demo_cv):
    assert check_cited(cite("Built a Python service with FastAPI."), demo_cv).pass_


class TestASourcelessClose:
    """A letter's close asks for a conversation and cites nothing; it was dropped every time for
    "no master-CV source". Allowed to cite nothing now — and still checked for what it says."""

    def test_a_close_that_claims_nothing_passes(self, demo_cv):
        items = [("p3", Cited(text="I'd like to talk about the role. My CV is attached.", source_ids=[]))]
        assert check_cited(items, demo_cv, unsourced_ok={"p3"}).pass_

    def test_a_close_with_a_number_still_fails(self, demo_cv):
        items = [("p3", Cited(text="I cut costs by 40% and would like to talk.", source_ids=[]))]
        assert any(v.rule == "numbers" for v in check_cited(items, demo_cv, unsourced_ok={"p3"}).violations)

    def test_only_the_named_line_is_excused(self, demo_cv):
        items = [("p1", Cited(text="Did things.", source_ids=[]))]
        assert not check_cited(items, demo_cv, unsourced_ok={"p3"}).pass_


class TestTheClaimCheck:
    """`guard.claims`: what a paragraph says the applicant did that its lines don't. Measured
    live, it caught "the team adopted [it] for all outbound LLM traffic" — which the regex rules
    passed — and left an honest restatement alone."""

    TEXT = "Wrote prompt templates with the security team, which the team adopted for all traffic."

    def _respond(self, monkeypatch, findings, repaired=None):
        from wera_agent.llm import demo

        real = demo.respond
        seen: dict = {}

        def fake(task, variables, context, demo_input=None):
            if task == "guard.claims":
                return {"findings": findings}
            if task == "guard.repair":
                seen["violations"] = variables["violations"]
                return {"item": repaired}
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", fake)
        return seen

    def test_a_finding_must_quote_its_paragraph(self, store, monkeypatch):
        from wera_agent.nodes.guard import claim_violations

        self._respond(
            monkeypatch,
            [
                {"location": "p1", "claim": "the team adopted for all traffic"},
                {"location": "p1", "claim": "led a team of twelve"},
            ],
        )
        items = [("p1", Cited(text=self.TEXT, source_ids=["exp-idsu-03"]))]
        found = claim_violations(store.s, items, store.cv())
        assert [v.detail for v in found] == ['"the team adopted for all traffic" isn\'t in the lines it cites']

    def test_a_flagged_paragraph_is_repaired_with_the_claim_named(self, store, monkeypatch):
        from wera_agent.nodes.guard import guard_and_repair

        fixed = {"text": "Wrote prompt templates and evaluation notes with the security team.", "sourceIds": ["exp-idsu-03"]}
        seen = self._respond(monkeypatch, [{"location": "p1", "claim": "the team adopted for all traffic"}], repaired=fixed)
        items = [("p1", Cited(text=self.TEXT, source_ids=["exp-idsu-03"]))]
        result, decisions = guard_and_repair(store.s, items, store.cv(), [], claims=True)
        assert "the team adopted for all traffic" in seen["violations"]
        assert decisions["p1"] is not None and "adopted" not in decisions["p1"].text
        assert result.pass_ and result.repaired

    def test_an_unrepairable_claim_is_dropped_and_said_so(self, store, monkeypatch):
        from wera_agent.nodes.guard import guard_and_repair

        self._respond(monkeypatch, [{"location": "p1", "claim": "the team adopted for all traffic"}], repaired=None)
        items = [("p1", Cited(text=self.TEXT, source_ids=["exp-idsu-03"]))]
        result, decisions = guard_and_repair(store.s, items, store.cv(), [], claims=True)
        assert decisions["p1"] is None
        assert "a claim the lines it cites don't support" in result.violations[0].detail

    def test_a_check_that_cant_run_costs_the_letter_nothing(self, store, monkeypatch):
        """An outage must not read as a fabrication."""
        from wera_agent.errors import ApiError
        from wera_agent.llm import demo
        from wera_agent.nodes.guard import guard_and_repair

        real = demo.respond

        def down(task, variables, context, demo_input=None):
            if task == "guard.claims":
                raise ApiError(502, "LLM_UPSTREAM", "down")
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", down)
        items = [("p1", Cited(text="Wrote prompt templates with the security team.", source_ids=["exp-idsu-03"]))]
        result, decisions = guard_and_repair(store.s, items, store.cv(), [], claims=True)
        assert result.pass_ and decisions == {}
