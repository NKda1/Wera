"""The free liveness check (SRCH-13) and the lifecycle flag it feeds (MATCH-10).

`classify` is pure, so the whole truth table is asserted directly rather than through a fake
server. The invariant worth more than any single row: **nothing but positive evidence returns
`gone`.** A board that blocks a bot, rate-limits, or times out must never be able to empty
someone's shortlist, and that is one careless `else` away at all times.
"""

import pytest

from wera_agent.clock import iso
from wera_agent.domain.scoring import liveness
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.sources.liveness import CLOSED_PHRASES, classify

NOW = 1_760_000_000_000
DAY = 24 * 60 * 60 * 1000


def job(**over) -> Job:
    base = {
        "id": "job-1",
        "dedupe_key": "url:https://x.example/1",
        "title": "ML Engineer",
        "company": "Acme",
        "location": Location(city="London", raw="London"),
        "remote": "hybrid",
        "description_md": "We build things.",
        "description_status": "full",
        "apply_url": "https://x.example/1",
        "sources": [JobSource(channel="board_search", detail="gradcracker.com", seen_at=iso(NOW))],
        "language": "en",
        "first_seen": iso(NOW),
        "last_seen": iso(NOW),
        "status": "new",
    }
    return Job.model_validate(base | over)


class TestGoneNeedsPositiveEvidence:
    @pytest.mark.parametrize("status", [404, 410])
    def test_the_two_codes_that_mean_not_here(self, status):
        assert classify(status, None, "") == "gone"

    @pytest.mark.parametrize("status", [401, 403, 405, 418, 429, 500, 502, 503, 504])
    def test_nothing_else_can_ever_close_a_job(self, status):
        """The branch that stops a blocked crawler pruning a live shortlist.

        A board refusing a bot is not a closed vacancy. This is ATS-03's `answered` discipline
        restated for HTTP, and it is the single most important row in this file.
        """
        assert classify(status, None, "") == "unknown"


class TestRedirects:
    @pytest.mark.parametrize("target", ["/", "/jobs", "/jobs/", "/job", "/search", "/careers/", "/vacancies"])
    def test_collapsing_to_the_root_or_an_index_is_gone(self, target):
        assert classify(301, target, "") == "gone"

    def test_a_redirect_naming_the_closure_is_gone(self):
        assert classify(302, "/jobs/expired", "") == "gone"
        assert classify(301, "https://x.example/job-removed", "") == "gone"

    def test_a_redirect_to_another_job_is_not_evidence(self):
        """A canonical redirect and a "this job moved" redirect look identical."""
        assert classify(301, "/jobs/7-ml-engineer", "") == "unknown"

    def test_a_redirect_offsite_is_not_evidence(self):
        """Could be a CDN, a login wall or a rebrand. None of those say the job is gone."""
        assert classify(302, "https://other.example/jobs/7", "") == "unknown"

    def test_a_redirect_with_no_target_is_not_evidence(self):
        assert classify(301, None, "") == "unknown"


class TestTwoHundred:
    def test_a_normal_page_is_open(self):
        assert classify(200, None, "<h1>ML Engineer</h1> Apply now") == "open"

    @pytest.mark.parametrize("phrase", CLOSED_PHRASES)
    def test_each_closure_phrase_is_likely_gone_not_gone(self, phrase):
        """`likely_gone` never closes anything — the phrase list is a hint, not a signal."""
        assert classify(200, None, f"<div>{phrase}</div>") == "likely_gone"

    def test_a_closure_phrase_broken_across_whitespace_still_matches(self):
        assert classify(200, None, "no longer\n   accepting\tapplications") == "likely_gone"

    def test_the_word_closed_alone_does_not_trip_it(self):
        """ "Recently closed roles" is a sidebar on half the boards there are. A list loose enough
        to catch it would flag live jobs for ever."""
        assert classify(200, None, "<aside>Recently closed roles</aside> Apply now") == "open"

    def test_company_boilerplate_does_not_trip_it(self):
        assert classify(200, None, "We closed our Series B. This position is open.") == "open"


class TestTheFlagOnRead:
    """MATCH-10: four states from three fields, computed on read like `urgency`."""

    def test_never_asked_is_unverified_not_doubtful(self):
        """No apply URL, LinkedIn-only, or past the cap. An absence of a question, not a doubt
        about the job — telling someone WERA checked a job it never looked at would be a lie."""
        assert liveness(job(), NOW) == "unverified"

    def test_confirmed_recently_is_open(self):
        assert liveness(job(last_checked_at=iso(NOW), last_confirmed_at=iso(NOW)), NOW) == "open"

    def test_a_confirmation_survives_one_missed_sweep(self):
        day_old = job(last_checked_at=iso(NOW - DAY), last_confirmed_at=iso(NOW - DAY))
        assert liveness(day_old, NOW) == "open"

    def test_a_stale_confirmation_stops_counting(self):
        old = job(last_checked_at=iso(NOW - 5 * DAY), last_confirmed_at=iso(NOW - 5 * DAY))
        assert liveness(old, NOW) == "unreachable"

    def test_asked_but_not_confirmed_is_unreachable(self):
        assert liveness(job(last_checked_at=iso(NOW)), NOW) == "unreachable"

    def test_a_closed_job_is_gone_whatever_the_timestamps_say(self):
        """`status` is the decision; these fields are only the evidence behind it."""
        closed = job(status="closed", last_checked_at=iso(NOW), last_confirmed_at=iso(NOW))
        assert liveness(closed, NOW) == "gone"

    def test_an_unreadable_timestamp_does_not_raise(self):
        assert liveness(job(last_checked_at="not a date", last_confirmed_at="nonsense"), NOW) == "unreachable"


class TestCheckRefusesWhatItMustNotFetch:
    def test_linkedin_is_never_asked_and_so_never_confirmed(self):
        """SRCH-03. A job WERA may not fetch stays `unverified` for ever, which is honest."""
        from wera_agent.sources.liveness import check

        assert check("https://www.linkedin.com/jobs/view/1") == "unknown"

    def test_no_url_is_unknown(self):
        from wera_agent.sources.liveness import check

        assert check(None) == "unknown"
        assert check("") == "unknown"

    def test_demo_mode_makes_no_request(self, monkeypatch):
        """DEMO-02: `http.check` refuses in demo mode, and that refusal must read as "couldn't
        tell", never as "gone"."""
        from wera_agent.sources import liveness as mod

        def boom(*_a, **_k):
            raise AssertionError("demo mode must not reach the network")

        monkeypatch.setattr(mod, "request", boom)
        # The conftest default is demo mode on, so `http.check` raises before `request` is used.
        assert mod.check("https://x.example/1") == "unknown"
