"""Filters and de-duplication — the rules that run before any model call.

Both matter for money as much as for correctness: a job filtered here is never scored, and a
duplicate collapsed here is never scored twice (MATCH-01, NORM-05/06, COST-04).
"""

import pytest

from wera_agent.clock import iso
from wera_agent.domain import dedupe, filters
from wera_agent.schemas.jobs import Ats, Job, JobSource, Location
from wera_agent.schemas.profile import ProfileChannels, SearchProfile

NOW = 1_760_000_000_000
DAY = 24 * 60 * 60 * 1000


def profile(**over) -> SearchProfile:
    base = {
        "titles": ["ML Engineer"],
        "skills": ["Python"],
        "locations": ["London"],
        "remote": "any",
        "seniority": ["graduate", "junior"],
        "exclude": ["Senior", "10+ years"],
        "sites": [],
        "recency": "7d",
        "channels": ProfileChannels(board_search=True, inbox=True, watchlist=True),
        "generated_from_cv_version": 1,
        "updated_at": iso(NOW),
    }
    return SearchProfile.model_validate(base | over)


def job(**over) -> Job:
    base = {
        "id": over.pop("id", "job-1"),
        "dedupe_key": "",
        "title": "ML Engineer, Graduate",
        "company": "Fernhill AI",
        "location": Location(city="London", country="UK", raw="London, UK"),
        "remote": "hybrid",
        "description_md": "We build guardrails.",
        "description_status": "full",
        "sources": [JobSource(channel="board_search", detail="gradcracker.com", seen_at=iso(NOW))],
        "language": "en",
        "first_seen": iso(NOW),
        "last_seen": iso(NOW),
        "status": "new",
    }
    return Job.model_validate(base | over)


class TestHardFilters:
    def test_a_job_the_profile_wants_is_kept(self):
        assert filters.hidden_reason(job(), profile()) is None

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Senior ML Engineer", "excluded term"),
            ("Staff Machine Learning Engineer", "more senior"),
            ("Head of Data", "more senior"),
            ("Principal Engineer", "more senior"),
        ],
    )
    def test_titles_above_the_search_are_hidden(self, title, expected):
        reason = filters.hidden_reason(job(title=title), profile())
        assert reason and expected in reason

    def test_a_seniority_the_profile_asks_for_is_not_filtered(self):
        """Someone searching for mid-level roles should see them."""
        assert filters.too_senior("Mid-level Engineer", profile(seniority=["mid"])) is None

    def test_an_excluded_term_matches_whole_words_only(self):
        """ "Lead" shouldn't hide "Leadership Development Programme"."""
        assert filters.excluded_term("Graduate Leadership Programme", profile(exclude=["Lead"])) is None
        assert filters.excluded_term("Lead Engineer", profile(exclude=["Lead"])) == "Lead"

    def test_the_wrong_city_is_hidden(self):
        reason = filters.hidden_reason(job(location=Location(city="Munich", raw="Munich"), remote="onsite"), profile())
        assert reason == "Location isn't in your search: Munich"

    def test_remote_anywhere_is_kept_when_remote_is_welcome(self):
        far = job(location=Location(city="Munich", raw="Munich"), remote="remote")
        assert filters.hidden_reason(far, profile()) is None
        # …but not for someone who only wants to be in the office.
        assert filters.hidden_reason(far, profile(remote="no_remote"))

    def test_remote_only_hides_an_onsite_job_in_the_right_city(self):
        assert filters.wrong_location(job(remote="onsite"), profile(remote="remote_only")) == "not remote"

    def test_filtered_jobs_are_hidden_with_a_reason_not_dropped(self):
        """MATCH-01: a filter is a guess, so the person can see what it hid."""
        kept, hidden = filters.apply([job(), job(id="job-2", title="Senior ML Engineer")], profile())
        assert [j.id for j in kept] == ["job-1"]
        assert [j.id for j in hidden] == ["job-2"]
        assert hidden[0].status == "hidden"
        assert "Senior" in (hidden[0].hidden_reason or "")


class TestDedupeKey:
    def test_the_ats_identity_wins(self):
        """NORM-05: the employer's own id beats anything WERA could derive."""
        key = dedupe.key_for(job(ats=Ats(vendor="greenhouse", board_token="fernhillai", job_id="42"), apply_url="https://x/y"))
        assert key == "ats:greenhouse:fernhillai:42"

    def test_tracking_parameters_do_not_make_a_new_job(self):
        plain = dedupe.key_for(job(apply_url="https://boards.example.com/jobs/7"))
        tagged = dedupe.key_for(job(apply_url="https://www.boards.example.com/jobs/7/?utm_source=alert&gh_src=x"))
        assert plain == tagged

    def test_a_card_with_no_url_still_gets_a_stable_key(self):
        first = dedupe.key_for(job(apply_url=None))
        again = dedupe.key_for(job(apply_url=None, title="ML Engineer, Graduate "))
        assert first == again and first.startswith("hash:")


class TestCollapse:
    def test_one_job_from_three_channels_keeps_every_source(self):
        """T-06: found by search, by an alert and on the watchlist — one job, three sources."""
        url = "https://job-boards.greenhouse.io/fernhillai/jobs/42"
        search = job(id="a", apply_url=url)
        alert = job(
            id="b",
            apply_url=f"{url}?utm_source=linkedin",
            description_md=None,
            description_status="missing",
            sources=[JobSource(channel="email_alert", detail="linkedin-alert", seen_at=iso(NOW))],
        )
        watch = job(
            id="c",
            apply_url=url,
            sources=[JobSource(channel="watchlist", detail="fernhillai", seen_at=iso(NOW))],
        )
        out = dedupe.collapse([search, alert, watch])
        assert len(out) == 1
        assert {s.channel for s in out[0].sources} == {"board_search", "email_alert", "watchlist"}
        # The fullest description survives the merge.
        assert out[0].description_status == "full"

    def test_a_near_duplicate_title_is_merged(self):
        """NORM-06: the same role, worded differently by two boards."""
        left = job(id="a", title="ML Engineer, Graduate", apply_url="https://a.example/1")
        right = job(
            id="b",
            title="Graduate ML Engineer",
            apply_url="https://b.example/2",
            sources=[JobSource(channel="watchlist", detail="fernhillai", seen_at=iso(NOW))],
        )
        out = dedupe.collapse([left, right])
        assert len(out) == 1
        assert {s.channel for s in out[0].sources} == {"board_search", "watchlist"}

    def test_a_different_role_at_the_same_company_is_not_merged(self):
        out = dedupe.collapse(
            [
                job(id="a", title="ML Engineer, Graduate", apply_url="https://a.example/1"),
                job(id="b", title="Data Scientist", apply_url="https://b.example/2"),
            ]
        )
        assert len(out) == 2

    def test_the_same_title_a_year_apart_is_a_new_posting(self):
        """NORM-06's 30-day window: last year's vacancy isn't this year's."""
        old = job(id="a", apply_url="https://a.example/1", first_seen=iso(NOW - 400 * DAY))
        new = job(id="b", apply_url="https://b.example/2")
        assert len(dedupe.collapse([old, new])) == 2

    def test_collapse_sets_the_key_it_matched_on(self):
        out = dedupe.collapse([job(ats=Ats(vendor="lever", board_token="saltmarsh", job_id="9"))])
        assert out[0].dedupe_key == "ats:lever:saltmarsh:9"
