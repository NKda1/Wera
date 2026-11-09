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


class TestWholeWordsOnly:
    def test_a_substring_is_not_a_match(self):
        assert not text_has_term("Recovered the archive", "R")

    def test_a_term_inside_a_longer_word_is_not_a_match(self):
        assert not text_has_term("Reactive streams with RxJava", "React")
