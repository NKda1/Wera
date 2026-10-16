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
