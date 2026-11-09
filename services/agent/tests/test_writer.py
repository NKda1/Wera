"""The demo writer (ported from the web app's mock writer)."""

import pytest

from wera_agent.domain.vocab import cv_line_index, skill_text
from wera_agent.llm.demo import PackInput, generate_pack
from wera_agent.nodes.ats import tailored_text
from wera_agent.services.demo_data import seed_to_job

FERNHILL = 0  # Fernhill AI, Greenhouse


def _letter_reply(**over) -> dict:
    """A minimal valid `letter.write` reply, for testing one field at a time. Long enough to clear
    `LetterOutput._length`, which counts the paragraphs."""
    body = "I built the ingest path and cut its cost. " * 10
    return {
        "greeting": "Dear Hiring Team,",
        "paragraphs": [{"text": body, "sourceIds": []} for _ in range(3)],
        "signoff": "Sincerely,\nJordan Ellery",
        "companyQuote": "",
        "policyQuote": "",
        **over,
    }


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


class TestTheProfessionalProfileSurvives:
    """TAIL-07. The summary is the line most likely to fail a strict citation check, because
    summarising *is* paraphrasing across the whole document — and it was being dropped, leaving
    a tailored CV with no profile at the top."""

    def test_the_tailored_cv_carries_a_profile(self, pack):
        p, _ = pack
        assert p.cv.summary.text.strip()
        assert p.cv.summary.source_ids

    def test_a_rejected_summary_falls_back_to_the_applicants_own(self, demo_cv):
        """Not to nothing. The master summary is their own words, already in the CV, and cites
        itself — it cannot fail the guard and cannot claim anything they haven't written."""
        from wera_agent.nodes.tailor import master_summary

        fallback = master_summary(demo_cv)
        assert fallback.text == demo_cv.summary.text
        assert fallback.source_ids == [demo_cv.summary.id]

    def test_master_summary_alone_invents_nothing(self, demo_cv):
        """`master_summary` is selection only: no summary in, nothing out. Composing one is a
        separate call, so that this function can never be the thing that writes a claim."""
        from wera_agent.nodes.tailor import master_summary

        assert master_summary(demo_cv.model_copy(update={"summary": None})).text == ""

    def test_a_cv_with_no_summary_still_gets_a_profile(self, demo_cv, demo_jobs):
        """The bug this exists for: the CV went out with no professional profile at all.

        The old chain was "the model's selection, or the master summary, or nothing", and for a CV
        whose master has no summary every branch was nothing — so the export dropped the section
        and the top of the page was a name and a contact line.
        """
        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        bare = demo_cv.model_copy(update={"summary": None})
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=bare, cv_pages=1)).cv

        assert doc.summary.text.strip(), "a CV with no master summary still has no profile"
        assert 20 <= len(doc.summary.text.split()) <= 90

    def test_a_composed_profile_cites_the_lines_it_was_built_from(self, demo_cv, demo_jobs):
        """It is the one piece of CV prose a model writes, so it is held to the same citation rule
        as a letter paragraph — and the guard has to actually pass, not merely run."""
        from wera_agent.domain.vocab import cv_line_index

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        bare = demo_cv.model_copy(update={"summary": None})
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=bare, cv_pages=1)).cv

        lines = cv_line_index(bare)
        assert doc.summary.source_ids
        assert all(i in lines for i in doc.summary.source_ids)
        assert doc.guard.pass_, doc.guard.violations

    def test_a_composed_profile_survives_the_verbatim_check(self, demo_cv, demo_jobs, store):
        """The collision `wera eval --packs` found: `Store._assert_verbatim` requires every cited
        line to equal the master line it names, and a composed profile never can.

        It cost 2 of 3 packs to `CV_DRIFTED`, and the third only "passed" because `summary.write`
        had been truncated into the empty-profile fallback — so the feature either failed the save
        or silently didn't happen. Asserted against the real save path, not against the flag.
        """
        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        bare = demo_cv.model_copy(update={"summary": None})
        store.put_cv(bare, rescore=False)
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=bare, cv_pages=1)).cv

        assert doc.summary.composed, "a composed profile has to say so, or the save rejects it"
        store._assert_verbatim(doc)  # raises CV_DRIFTED if the exemption is gone

    def test_a_selected_bullet_that_drifts_is_still_caught(self, demo_cv, demo_jobs, store):
        """Guards the exemption above: `composed` must buy the profile a pass and nothing else.
        A tailored bullet that doesn't match its master line is still a refused document."""
        from wera_agent.errors import ApiError

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).cv
        doc.sections[0].items[0].bullets[0].text = "Something the master CV never said."

        with pytest.raises(ApiError) as caught:
            store._assert_verbatim(doc)
        assert caught.value.code == "CV_DRIFTED"

    def test_a_composed_profile_does_not_name_the_company(self, demo_cv, demo_jobs):
        """A profile is CV furniture, not a cover letter. One naming the employer is unusable the
        moment the recruiter forwards the CV internally."""
        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        bare = demo_cv.model_copy(update={"summary": None})
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=bare, cv_pages=1)).cv

        assert job.company.lower() not in doc.summary.text.lower()


def test_only_lists_skills_from_the_master_cv(pack, demo_cv):
    p, _ = pack
    assert set(p.cv.skills) <= {s.name for s in demo_cv.skills}


def test_reports_gaps_the_cv_cant_back_and_never_invents_them(pack, demo_cv):
    p, _ = pack
    assert [k.term for k in p.ats.keywords if not k.matched and not k.in_master_cv] == ["Docker", "Model evaluation"]
    assert "docker" not in tailored_text(p.cv, demo_cv).lower()
    assert round(p.ats.coverage * 100) == 86


def test_coverage_is_measured_over_the_page_the_parser_actually_reads(pack, demo_cv):
    """`tailored_text` has to agree with `export/content.ts`, which renders role headers and the
    education section from the master CV (TAIL-02).

    It used to be summary + bullets + skills, so a degree subject the post asked for was reported
    as a gap against a CV that prints it two inches above — and the headline percentage was
    measuring a narrower document than the PDF it was describing.
    """
    p, _ = pack
    text = tailored_text(p.cv, demo_cv).lower()
    assert demo_cv.education[0].degree.lower() in text
    assert demo_cv.education[0].institution.lower() in text
    role = next(r for r in demo_cv.experience if any(i.role_id == r.id for s in p.cv.sections for i in s.items))
    assert role.title.lower() in text and role.org.lower() in text


class TestTheSameInputsGiveTheSameCv:
    """The acceptance test for "CV generation is inconsistent every time".

    The old `cv.tailor` returned each bullet's text, so the model retyped every line; altered
    wording then failed the citation guard, a `guard.repair` call fired per failing line, and
    whatever still failed was dropped — a different set each run. Selection by id removes every
    step of that chain, so the output is a pure function of the inputs.
    """

    def test_two_runs_of_the_same_job_produce_identical_documents(self, demo_cv, demo_jobs):
        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        inp = PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)
        first, second = generate_pack(inp), generate_pack(inp)
        assert first.cv.model_dump(mode="json") == second.cv.model_dump(mode="json")

    def test_every_line_is_its_cited_master_cv_line(self, pack, demo_cv):
        """What "verbatim" means, checked character for character rather than by eye."""
        p, _ = pack
        lines = cv_line_index(demo_cv)
        for bullet in [b for s in p.cv.sections for i in s.items for b in i.bullets]:
            assert bullet.text == lines[bullet.source_ids[0]]["text"]


class TestSkillsReconciliation:
    """ATSC-02/03 in code. What replaced the re-tailor loop — see `nodes.ats.reconcile_skills`."""

    def test_covers_a_required_keyword_the_cv_lists_as_a_skill(self, demo_cv, demo_jobs):
        """The coverage win, and the whole reason the loop existed: a keyword the CV already
        backs just has to appear, which needs no model."""
        from wera_agent.nodes.ats import reconcile_skills

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        inp = PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)
        p = generate_pack(inp)

        backed = [k for k in p.ats.keywords if k.in_master_cv]
        assert backed, "fixture no longer has a keyword the CV can back"
        # Strip the skills line and let reconciliation rebuild it from nothing.
        stripped = p.cv.model_copy(deep=True)
        stripped.skills = []
        rebuilt = reconcile_skills(stripped, cv=demo_cv, evidence=evidence, cv_pages=1, threshold=0.75)
        assert rebuilt.skills

    def test_never_adds_a_keyword_the_cv_does_not_list_as_a_skill(self, demo_cv, demo_jobs):
        """ATSC-04: a term that appears only inside a bullet is a gap for the person to decide
        about, not a skill for WERA to claim on their behalf."""
        from wera_agent.nodes.ats import reconcile_skills

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).cv
        rebuilt = reconcile_skills(doc, cv=demo_cv, evidence=evidence, cv_pages=1, threshold=1.0)
        assert set(rebuilt.skills) <= {s.name for s in demo_cv.skills}
        assert "Docker" not in rebuilt.skills  # the fixture's unbacked keyword

    def test_stops_at_the_threshold_rather_than_stuffing_the_line(self, demo_cv, demo_jobs):
        """A skills line carrying every term the post mentioned clears the robot and reads as
        keyword spam to the person who sees it next."""
        from wera_agent.nodes.ats import reconcile_skills

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).cv
        lenient = reconcile_skills(doc.model_copy(deep=True), cv=demo_cv, evidence=evidence, cv_pages=1, threshold=0.1)
        strict = reconcile_skills(doc.model_copy(deep=True), cv=demo_cv, evidence=evidence, cv_pages=1, threshold=1.0)
        assert len(lenient.skills) <= len(strict.skills)

    def test_keeps_the_skills_line_inside_the_page_budget(self, demo_cv, demo_jobs):
        from wera_agent.nodes.ats import SKILLS_LINE_CHARS, reconcile_skills

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).cv
        rebuilt = reconcile_skills(doc, cv=demo_cv, evidence=evidence, cv_pages=1, threshold=1.0)
        assert len(" · ".join(rebuilt.skills)) <= SKILLS_LINE_CHARS

    def test_the_threshold_is_met_by_the_line_that_actually_ships(self, demo_cv, demo_jobs):
        """The loop stops at the threshold and the line is then capped to the page, so the two
        have to be measured on the same thing.

        They weren't: coverage was checked against every skill chosen so far, and `_fit_line` then
        dropped whatever didn't fit — so a job with more backable keywords than fit on one page
        stopped early against a line it had not produced, and the stored report came in under the
        threshold the function had just declared met.
        """
        from wera_agent.nodes.ats import build_ats, reconcile_skills

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][FERNHILL], 0)
        doc = generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).cv
        doc.skills = []
        rebuilt = reconcile_skills(doc, cv=demo_cv, evidence=evidence, cv_pages=1, threshold=0.75)
        report = build_ats(rebuilt, cv=demo_cv, evidence=evidence, cv_pages=1, job=job, attempt=1)

        backable = [k for k in report.keywords if k.in_master_cv]
        uncovered = [k.term for k in backable if not k.matched]
        # Either the threshold is genuinely met, or every keyword the CV can back is on the page
        # and the shortfall is the applicant's gap rather than a premature stop.
        assert report.coverage >= 0.75 or not uncovered, uncovered


def test_quotes_a_company_fact_verbatim(pack):
    p, seed = pack
    assert len(p.letter.company_fact.quote) > 20
    assert p.letter.company_fact.quote in seed["description"]
    assert p.letter.greeting == "Dear Hiring Team,"


class TestTheLetterReadsLikeAPerson:
    """LTR-04. A letter can be perfectly honest, perfectly cited, and still read like a form —
    which is the one thing a cover letter cannot afford."""

    def test_it_contains_none_of_the_banned_phrases(self, pack):
        from wera_agent.nodes.tailor import banned_phrases

        p, _ = pack
        assert banned_phrases(p.letter) == []

    def test_it_does_not_open_by_announcing_the_application(self, pack):
        """ "I am writing to apply for…" tells the reader what they already know, and spends the
        one sentence most likely to be read on nothing."""
        p, _ = pack
        opening = p.letter.paragraphs[0].text.lower()
        assert not opening.startswith("i'm applying")
        assert "i am writing" not in opening

    def test_every_demo_letter_is_in_the_ltr_01_band(self, api):
        """LTR-01: 250–350 words. Every demo letter was 229–236 — short because the writer used
        two bullets per role and stopped, not because there was nothing left to say.

        Asserted over the packs the demo actually produces, not over `generate_pack` on arbitrary
        jobs: a job the CV barely matches has little to say, and padding it to a word count is the
        exact filler LTR-04 exists to remove.
        """
        for item in api.ok("GET", "/applications")["items"]:
            app = api.ok("GET", f"/applications/{item['id']}")
            letter = (app.get("pack") or {}).get("letter")
            if letter:
                assert 250 <= letter["wordCount"] <= 350, f"{app['job']['company']}: {letter['wordCount']} words"
                assert letter["guard"]["pass"], f"{app['job']['company']}: lengthening broke the guard"

    def test_the_salutation_is_never_a_dead_one(self):
        """ "To Whom It May Concern" dates a letter by forty years and "Dear Sir or Madam" guesses
        at something it has no business guessing at. Corrected rather than rejected: the greeting
        is one line of furniture, and spending one of two attempts on it would be a poor trade."""
        from wera_agent.schemas.llm_outputs import LetterOutput

        for dead in ("To Whom It May Concern,", "Dear Sir or Madam", "dear sirs,"):
            out = LetterOutput.model_validate(_letter_reply(greeting=dead))
            assert out.greeting == "Dear Hiring Team,"

    def test_a_named_salutation_is_left_alone(self):
        from wera_agent.schemas.llm_outputs import LetterOutput

        assert LetterOutput.model_validate(_letter_reply(greeting="Dear Priya Raman,")).greeting == "Dear Priya Raman,"

    def test_the_signoff_is_one_of_three_valedictions(self):
        """A closed set, because there are three forms a cover letter ends with and everything
        else a model reaches for is wrong for the register or wrong for the century."""
        from wera_agent.schemas.llm_outputs import LetterOutput

        assert LetterOutput.model_validate(_letter_reply(signoff="Warmest regards,\nJordan")).signoff == "Sincerely,\nJordan"
        assert LetterOutput.model_validate(_letter_reply(signoff="Best regards\nJordan")).signoff == "Best regards,\nJordan"
        assert LetterOutput.model_validate(_letter_reply(signoff="Respectfully,")).signoff == "Respectfully,"

    def test_the_ban_list_actually_catches_filler(self, pack):
        """Guards the guard: a check that never fires is indistinguishable from no check. The
        demo letter used to end with one of these, and this is what found it."""
        from wera_agent.nodes.tailor import banned_phrases
        from wera_agent.schemas.pack import Cited

        p, _ = pack
        spoiled = p.letter.model_copy(
            update={"paragraphs": [*p.letter.paragraphs, Cited(text="I am passionate about this perfect fit.", source_ids=[])]}
        )
        assert sorted(banned_phrases(spoiled)) == ["I am passionate about", "perfect fit"]


class TestWhyThisCompany:
    """LTR-06: the paragraph that shows the applicant read the post, not the job title."""

    def test_a_policy_fact_is_quoted_verbatim_from_the_post(self, pack):
        p, seed = pack
        policy = p.letter.company_fact.policy_quote
        if policy:  # the demo post may or may not state one
            assert policy in seed["description"]

    def test_a_post_that_states_no_policy_gets_no_invented_one(self):
        """An absent paragraph beats a fabricated policy every time — this is the half of a
        letter with no CV behind it, so the post is the only thing holding it to the truth."""
        from wera_agent.llm.demo import policy_fact

        assert policy_fact("We build things.\n\n## About\nA company.") == ""
        assert policy_fact(None) == ""

    def test_a_policy_sentence_is_found_and_is_quotable(self):
        from wera_agent.llm.demo import policy_fact

        post = "## About us\nWe make tools.\n\n- We are remote-first across the EU. Everyone gets a learning budget.\n"
        found = policy_fact(post)
        assert found == "We are remote-first across the EU."
        assert found in post  # LTR-02's rule applies here too: verbatim or it doesn't count


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
