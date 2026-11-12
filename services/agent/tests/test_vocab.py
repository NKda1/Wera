"""Keyword matching (ATSC-01).

These are the tests behind "a job with a fit score of 8 reports 19% ATS coverage". The fit score
was right and the coverage was wrong: the post asked for Version control, CI/CD pipelines,
Testing, Debugging, Build tools, Memory management, Multithreading and Relational databases, the
CV listed Git, SQL and C++, and every one of the eight came back both unmatched *and* unbacked —
so they were shown to the applicant as skills to go and acquire.
"""

from wera_agent.domain.vocab import aliases_for, text_has_term


class TestSynonymsResolveFromAnySpelling:
    """The map is written canonical-first and was read that way too, which was the wrong way
    round: the keyword side of every comparison is the *post's* wording."""

    def test_the_canonical_name_finds_an_alias_in_the_text(self):
        assert text_has_term("Tuned Postgres query plans", "PostgreSQL")

    def test_an_alias_finds_the_canonical_name_in_the_text(self):
        """The direction that was broken. `keywords.extract` returns the post's own wording, so
        this is the direction that actually happens in production."""
        assert text_has_term("Ran PostgreSQL in production", "Postgres")

    def test_two_aliases_of_one_group_find_each_other(self):
        assert text_has_term("Set up continuous delivery for the monorepo", "Continuous integration")


class TestQualifierTails:
    """A post rarely names a bare skill. "CI/CD pipelines" is the same requirement as "CI/CD" and
    matched nothing but itself."""

    def test_a_tail_still_reaches_the_synonym_group(self):
        assert text_has_term("Owned the Jenkins setup", "CI/CD pipelines")

    def test_a_tail_on_an_unmapped_term_falls_back_to_the_bare_word(self):
        assert text_has_term("Wrote the Kafka consumers", "Kafka experience")

    def test_stacked_tails_reduce(self):
        assert aliases_for("build tools experience") == aliases_for("build tools")

    def test_a_term_with_no_tail_is_unchanged(self):
        assert aliases_for("Rust") == ["rust"]


class TestTheGroupsThatWereMissing:
    """Each of these was a measured false gap. Naming the tool is how a CV claims the practice —
    a CV does not write "version control", it writes "Git"."""

    def test_git_counts_as_version_control(self):
        assert text_has_term("Git", "Version control")

    def test_sql_counts_as_relational_databases(self):
        assert text_has_term("Python · SQL · Docker", "Relational databases")

    def test_pytest_counts_as_testing(self):
        assert text_has_term("Covered the parser with pytest", "Testing")

    def test_concurrency_counts_as_multithreading(self):
        assert text_has_term("Wrote the concurrency layer", "Multithreading")

    def test_a_degree_subject_matches_how_a_cv_writes_it(self):
        assert text_has_term("BA (Hons) in Computer Science (Tripos)", "Computer Science")


class TestGroupsStayHonest:
    """A synonym group says "a recruiter would accept these as the same requirement". Terms that
    are merely adjacent must not merge, or coverage becomes a number that flatters."""

    def test_docker_is_not_kubernetes(self):
        assert not text_has_term("Shipped the service in Docker", "Kubernetes")

    def test_a_skill_the_cv_lacks_is_still_missing(self):
        assert not text_has_term("Python · TypeScript · React", "Rust")

    def test_sql_is_not_a_specific_engine_the_cv_never_named(self):
        """ "Relational databases" is a family and SQL answers it. MongoDB does not, and neither
        does SQL answer a post that specifically wants Oracle."""
        assert not text_has_term("Python · SQL", "MongoDB")


class TestWordsThatAreAlsoOrdinaryEnglish:
    """`Go` is why `CASE_SENSITIVE_ALIASES` exists. Lower-cased it matches "go to the shop", and
    it appears in a CV bullet about as often as the language does."""

    def test_the_language_matches_capitalised(self):
        assert text_has_term("Wrote the ingest service in Go", "Go")

    def test_the_ordinary_word_does_not(self):
        assert not text_has_term("Had to go through the backlog", "Go")

    def test_golang_matches_in_any_case(self):
        """The group used to omit "go" from its own alias list so the bare word could only be
        reached through a separate map — correct by accident, and unextendable. Case sensitivity
        is per alias now, so the group can hold both spellings."""
        assert text_has_term("golang", "Go")
        assert text_has_term("Built it in Golang", "Go")


class TestOrdinaryWordsAreNotSkills:
    """Each of these matched before, and each inflated coverage and let the guard's terms rule
    pass a line it should have caught. WERA reads CVs from every trade, so a word that is a skill
    in software is often just a word in someone else's."""

    def test_the_rest_of_something_is_not_rest(self):
        assert not text_has_term("Handled the rest of the migration", "REST APIs")
        assert not text_has_term("Rest of the team was remote", "REST APIs")

    def test_rest_written_as_the_architecture_still_counts(self):
        assert text_has_term("Designed REST endpoints for billing", "REST APIs")
        assert text_has_term("Built a RESTful service", "REST APIs")

    def test_shipping_containers_are_not_docker(self):
        assert not text_has_term("Moved shipping containers through the port", "Docker")

    def test_blood_tests_are_not_testing(self):
        assert not text_has_term("Ran blood tests on the ward", "Testing")

    def test_a_post_that_literally_asks_for_the_word_still_matches_it(self):
        """The word only stops counting as a *synonym*. A post asking for "tests" is answered by
        a CV saying "tests", however that CV meant it."""
        assert text_has_term("Wrote tests for the parser", "tests")

    def test_said_in_jest_is_not_jest(self):
        assert not text_has_term("Said in jest at the offsite", "Testing")
        assert text_has_term("Covered the UI with Jest", "Testing")

    def test_rag_needs_its_capitals(self):
        assert not text_has_term("Wrote for the student rag", "RAG")
        assert text_has_term("Shipped a RAG pipeline", "RAG")


class TestKeywordsMustBeInThePost:
    """`in_post` is what makes coverage a measurement of *this* post. A keyword it didn't ask
    for becomes a gap the applicant is told to go and fill."""

    def test_a_term_the_post_never_mentions_is_dropped(self):
        from wera_agent.domain.vocab import in_post
        from wera_agent.schemas.jobs import JobKeyword

        post = "Integrate third-party APIs (Stripe, email, model providers). TypeScript and React."
        kept = in_post([JobKeyword(term="TypeScript", weight="required"), JobKeyword(term="REST APIs", weight="required")], post)
        assert [k.term for k in kept] == ["TypeScript"]

    def test_a_different_spelling_of_what_the_post_says_is_kept(self):
        from wera_agent.domain.vocab import in_post
        from wera_agent.schemas.jobs import JobKeyword

        assert in_post([JobKeyword(term="PostgreSQL", weight="nice")], "Postgres in production")

    def test_no_post_means_nothing_to_cover(self):
        from wera_agent.domain.vocab import in_post
        from wera_agent.schemas.jobs import JobKeyword

        assert in_post([JobKeyword(term="Python", weight="required")], None) == []

    def test_the_extractor_drops_what_the_post_doesnt_say(self):
        from wera_agent.schemas.llm_outputs import KeywordList

        raw = {
            "keywords": [
                {"term": "Docker", "weight": "required", "category": "tool"},
                {"term": "Kubernetes", "weight": "nice", "category": "tool"},
            ]
        }
        out = KeywordList.model_validate(raw, context={"post": "Package services with Docker."})
        assert [k.term for k in out.keywords] == ["Docker"]


class TestWholeWordsOnly:
    def test_a_substring_is_not_a_match(self):
        assert not text_has_term("Recovered the archive", "R")

    def test_a_term_inside_a_longer_word_is_not_a_match(self):
        assert not text_has_term("Reactive streams with RxJava", "React")
