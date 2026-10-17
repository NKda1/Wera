"""The demo writer (ported from the web app's mock writer)."""

import pytest

from wera_agent.domain.vocab import cv_line_index, skill_text
from wera_agent.llm.demo import PackInput, generate_pack
from wera_agent.nodes.ats import tailored_text
from wera_agent.services.demo_data import seed_to_job

FERNHILL = 0  # Fernhill AI, Greenhouse


@pytest.fixture
def pack(demo_cv, demo_jobs):
    seed = demo_jobs["shortlist"][FERNHILL]
    job, evidence, questions = seed_to_job(seed, 0)
    return generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)), seed


def test_cites_a_real_master_cv_line_for_every_bullet_and_passes_the_guard(pack, demo_cv):
    p, _ = pack
    lines = cv_line_index(demo_cv)
    bullets = [b for s in p.cv.sections for i in s.items for b in i.bullets]
    assert bullets
    assert all(b.source_ids and all(i in lines for i in b.source_ids) for b in bullets)
    assert p.cv.guard.pass_ and p.letter.guard.pass_


def test_only_lists_skills_from_the_master_cv(pack, demo_cv):
    p, _ = pack
    assert set(p.cv.skills) <= {s.name for s in demo_cv.skills}


def test_reports_gaps_the_cv_cant_back_and_never_invents_them(pack):
    p, _ = pack
    assert [k.term for k in p.ats.keywords if not k.matched and not k.in_master_cv] == ["Docker", "Model evaluation"]
    assert "docker" not in tailored_text(p.cv).lower()
    assert round(p.ats.coverage * 100) == 86


def test_quotes_a_company_fact_verbatim(pack):
    p, seed = pack
    assert len(p.letter.company_fact.quote) > 20
    assert p.letter.company_fact.quote in seed["description"]
    assert p.letter.greeting == "Dear Hiring Team,"


def test_never_drafts_sensitive_answers(pack):
    p, _ = pack
    sensitive = [a for a in p.answers if a.sensitive]
    assert [a.question for a in sensitive] == [
        "Are you legally authorised to work in the United Kingdom?",
        "Will you now or in the future require visa sponsorship?",
        "What are your salary expectations?",
    ]
    assert all(a.answer is None for a in sensitive)


def test_records_omitted_lines_so_they_can_be_added_back(pack, demo_cv):
    p, _ = pack
    every = sorted(b.id for r in [*demo_cv.experience, *demo_cv.projects] for b in r.bullets)
    used = [sid for s in p.cv.sections for i in s.items for b in i.bullets for sid in b.source_ids]
    assert sorted(used + p.cv.omitted_bullet_ids) == every


def test_uses_skill_aliases_when_matching(demo_cv):
    assert "Postgres" in skill_text(next(s for s in demo_cv.skills if s.name == "PostgreSQL"))
