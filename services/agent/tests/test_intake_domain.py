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

    def test_no_remote_hides_a_remote_job_even_in_a_wanted_city(self):
        """The bug this closes: `no_remote` rejected nothing.

        A remote job fell past both remote branches to the location check, and a posting whose
        location reads "Remote (London)" contains "London" — so it was *kept* for someone who
        had said they don't want remote work.
        """
        remote_in_london = job(location=Location(city=None, raw="Remote (London)"), remote="remote")
        assert filters.wrong_location(remote_in_london, profile(remote="no_remote")) == "remote"
        assert filters.hidden_reason(remote_in_london, profile(remote="no_remote"))
        # And it is still kept for someone who doesn't mind.
        assert filters.hidden_reason(remote_in_london, profile(remote="any")) is None


class TestSeniorityActuallyGates:
    """`profile.seniority` had no effect on filtering at all.

    `too_senior` skipped any `ABOVE` word that appeared in `profile.seniority`, but `ABOVE` holds
    "senior"/"staff"/"lead" and the field only allowed "intern"…"mid" — so the two sets could
    never intersect and the skip was unreachable. Widening your search changed nothing.
    """

    def test_a_profile_that_asks_for_senior_is_shown_senior_roles(self):
        assert filters.too_senior("Senior ML Engineer", profile(seniority=["mid", "senior"])) is None

    def test_a_profile_that_does_not_still_hides_them(self):
        assert filters.too_senior("Senior ML Engineer", profile(seniority=["graduate"])) == "senior"

    def test_asking_for_lead_accepts_lead_and_staff_but_not_director(self):
        asks = profile(seniority=["lead"])
        assert filters.too_senior("Lead Engineer", asks) is None
        assert filters.too_senior("Staff Engineer", asks) is None
        assert filters.too_senior("Director of Engineering", asks) == "director"

    def test_an_empty_level_list_keeps_the_early_career_default(self):
        """No levels stated is a profile that hasn't been set up, not a request to see
        Director roles."""
        assert filters.too_senior("Principal Engineer", profile(seniority=[])) == "principal"


class TestRecencyIsEnforced:
    """`recency` was a Firecrawl `tbs` hint and nothing more — a suggestion about ranking, never
    a rule about what to keep. A 30-day-old posting could come back from a 24-hour search, and
    the watchlist and inbox never saw the setting at all."""

    def old(self, days: float, **over) -> Job:
        return job(posted_at=iso(NOW - int(days * DAY)), **over)

    def test_a_posting_outside_the_window_is_hidden(self):
        reason = filters.hidden_reason(self.old(20), profile(recency="7d"), NOW)
        assert reason and "outside your 7d window" in reason

    def test_a_posting_inside_the_window_is_kept(self):
        assert filters.hidden_reason(self.old(3), profile(recency="7d"), NOW) is None

    def test_a_job_with_no_date_is_never_hidden_for_its_age(self):
        """MATCH-08: `postedAt` is often missing. Hiding a job for having no date would throw
        away every posting from a board that doesn't publish one."""
        assert filters.hidden_reason(job(posted_at=None), profile(recency="24h"), NOW) is None

    def test_an_approximate_date_gets_a_day_of_slack(self):
        """MATCH-08: "about 7 days ago" shouldn't be hidden from a 7-day search by someone
        else's rounding."""
        edge = self.old(7.5, posted_precision="approximate")
        assert filters.hidden_reason(edge, profile(recency="7d"), NOW) is None
        exact = self.old(7.5, posted_precision="exact")
        assert filters.hidden_reason(exact, profile(recency="7d"), NOW) is not None


class TestCountryTargeting:
    """SRCH-11: `countries` is what "where" means when no city is named."""

    def test_a_job_in_a_target_country_is_kept_without_naming_the_city(self):
        berlin = job(location=Location(city="Berlin", country="Germany", raw="Berlin, Germany"), remote="onsite")
        assert filters.wrong_location(berlin, profile(locations=[], countries=["DE"])) is None

    def test_a_job_outside_every_target_country_is_hidden(self):
        berlin = job(location=Location(city="Berlin", country="Germany", raw="Berlin, Germany"), remote="onsite")
        assert filters.wrong_location(berlin, profile(locations=[], countries=["GB"])) == "Berlin"

    def test_a_countrys_other_names_count(self):
        """A posting says "London, UK", not "London, GB"."""
        uk = job(location=Location(city="Leeds", country="UK", raw="Leeds, UK"), remote="onsite")
        assert filters.wrong_location(uk, profile(locations=[], countries=["GB"])) is None

    def test_a_two_letter_code_does_not_match_inside_another_word(self):
        """Substring-matching "us" puts Austin in the United States, and "uk" puts Kyiv in the
        UK. Country names are matched whole-word for exactly this reason."""
        austin = job(location=Location(city="Austin", country=None, raw="Austin"), remote="onsite")
        assert filters.wrong_location(austin, profile(locations=[], countries=["US"])) == "Austin"
        kyiv = job(location=Location(city="Kyiv", country="Ukraine", raw="Kyiv, Ukraine"), remote="onsite")
        assert filters.wrong_location(kyiv, profile(locations=[], countries=["GB"])) == "Kyiv"

    def test_naming_neither_a_city_nor_a_country_hides_nothing_on_location(self):
        anywhere = job(location=Location(city="Lima", country="Peru", raw="Lima, Peru"), remote="onsite")
        assert filters.wrong_location(anywhere, profile(locations=[], countries=[])) is None


class TestHardFiltersContinued:
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
