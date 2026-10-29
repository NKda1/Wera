"""Urgency grouping, and how much a posting date is actually worth (MATCH-07).

The grouping itself is small — the freshness ladder already existed and already bucketed
`postedAt`. What's new is that a bucket makes a *claim* a weighting doesn't: a header saying
"Today and yesterday" asserts WERA knows when the job was posted. Most of these tests are about
the cases where it doesn't.
"""

import pytest

from wera_agent.clock import iso, now_ms
from wera_agent.domain import dedupe
from wera_agent.domain.scoring import URGENCY, days_since, freshness_score, urgency
from wera_agent.sources import normalise

from .test_intake_domain import NOW, job

DAY = 24 * 60 * 60 * 1000


def ago(days: float) -> str:
    return iso(NOW - int(days * DAY))


class TestTheLadderIsOneTable:
    """Freshness and the groups are the same judgement asked twice, so they share a table.
    A score that called a job fresh while a header filed it under "Older" would be the kind of
    contradiction nobody reports and everybody stops trusting."""

    @pytest.mark.parametrize(
        ("days", "score", "group"),
        [
            (0, 10, "today"),
            (1.9, 10, "today"),
            (2, 10, "today"),  # the boundary is inclusive, on both
            (2.1, 7, "this_week"),
            (7, 7, "this_week"),
            (7.5, 4, "two_weeks"),
            (14, 4, "two_weeks"),
            (14.5, 1, "older"),
            (90, 1, "older"),
        ],
    )
    def test_every_bucket_agrees_with_its_score(self, days, score, group):
        assert freshness_score(ago(days), NOW) == score
        assert urgency(ago(days), NOW) == group

    def test_the_boundaries_come_from_one_place(self):
        assert [limit for limit, _, _ in URGENCY] == [2, 7, 14]
        assert [name for _, _, name in URGENCY] == ["today", "this_week", "two_weeks"]


class TestNoDateIsNotTheSameAsOld:
    """The crux. Both score 1 for freshness, and for a *weighting* that conflation is fine —
    it costs the job a tenth of its total and the person still sees it. For a group header it
    is a claim WERA can't support."""

    def test_an_unknown_date_scores_as_stale_but_is_not_filed_as_old(self):
        assert freshness_score(None, NOW) == 1
        assert urgency(None, NOW) == "unknown"
        assert urgency(ago(90), NOW) == "older"

    def test_an_unreadable_date_is_unknown_rather_than_a_crash(self):
        """Sources normalise their dates, so this means a row written before that existed."""
        assert days_since("not a date", NOW) is None
        assert urgency("not a date", NOW) == "unknown"
        assert freshness_score("not a date", NOW) == 1


class TestWhereTheDateCameFrom:
    """The three channels do not give dates of equal quality, and once a relative phrase has
    become an ISO string nothing downstream can tell it from a real one — so it is recorded."""

    def test_an_employers_own_feed_is_exact(self):
        from wera_agent.schemas.jobs import Ats, Location
        from wera_agent.sources.ats_feeds import FeedJob

        feed = FeedJob(
            ats=Ats(vendor="greenhouse", board_token="monzo", job_id="1"),
            title="Software Engineer",
            company="Monzo",
            location=Location(raw="London"),
            apply_url="https://job-boards.greenhouse.io/monzo/jobs/1",
            description="x" * 300,
            posted_at=ago(3),
        )
        assert normalise.from_feed(feed, NOW).posted_precision == "exact"

    @pytest.mark.parametrize("raw", ["3 days ago", "2 Days Ago", "yesterday", "Posted today", "just now"])
    def test_a_relative_phrase_is_approximate(self, raw):
        when, precision = normalise.dated(raw, NOW)
        assert when is not None
        assert precision == "approximate"

    def test_a_timestamp_from_a_board_is_still_only_approximate(self):
        """A scraped page's own date is better than "3 days ago" and still isn't the
        employer's; `from_feed` is the only caller that upgrades it."""
        assert normalise.dated(ago(3), NOW) == (ago(3), "approximate")

    def test_no_date_is_unknown(self):
        assert normalise.dated(None, NOW) == (None, "unknown")
        assert normalise.dated("  ", NOW) == (None, "unknown")
        assert normalise.dated("whenever", NOW) == (None, "unknown")

    def test_a_scraped_card_records_that_its_date_is_a_guess(self):
        found = normalise.from_scrape(
            {"title": "ML Engineer", "company": "Acme", "location": "London", "postedAt": "3 days ago"},
            url="https://www.gradcracker.com/jobs/1",
            site="gradcracker.com",
            markdown="x" * 300,
            now=NOW,
        )
        assert found is not None
        assert found.posted_precision == "approximate"

    def test_a_job_with_no_date_never_claims_a_precision(self):
        found = normalise.from_card(
            title="ML Engineer", company="Acme", location_raw="London", url=None, detail="linkedin-alert", now=NOW
        )
        assert (found.posted_at, found.posted_precision) == (None, "unknown")

    def test_an_old_row_without_the_field_loads_as_unknown(self):
        """The field is defaulted so rows written before it existed still load — and `unknown`
        is also the right answer for them, since nothing recorded where their date came from."""
        from wera_agent.schemas.jobs import Job

        data = job().model_dump(by_alias=True)
        del data["postedPrecision"]
        assert Job.model_validate(data).posted_precision == "unknown"


class TestMergingTwoSightings:
    """The same job arrives from an alert email saying "3 days ago" and from the employer's own
    feed with a real timestamp. Which order they arrive in is an accident of scheduling."""

    def test_an_exact_date_wins_even_when_the_fuzzy_one_arrived_first(self):
        """This was the bug: `posted_at or other.posted_at` kept whichever channel happened to
        see it first, so a job's urgency group depended on the scheduler."""
        fuzzy = job(id="a", posted_at=ago(5), posted_precision="approximate")
        exact = job(id="a", posted_at=ago(1), posted_precision="exact")
        merged = dedupe.merge_sources(fuzzy, exact)
        assert (merged.posted_at, merged.posted_precision) == (ago(1), "exact")
        assert urgency(merged.posted_at, NOW) == "today"

    def test_and_wins_when_it_arrived_first_too(self):
        exact = job(id="a", posted_at=ago(1), posted_precision="exact")
        fuzzy = job(id="a", posted_at=ago(5), posted_precision="approximate")
        merged = dedupe.merge_sources(exact, fuzzy)
        assert (merged.posted_at, merged.posted_precision) == (ago(1), "exact")

    def test_any_date_beats_no_date(self):
        none = job(id="a", posted_at=None, posted_precision="unknown")
        some = job(id="a", posted_at=ago(5), posted_precision="approximate")
        assert dedupe.merge_sources(none, some).posted_at == ago(5)

    def test_two_sightings_of_equal_quality_keep_the_first(self):
        """No reason to prefer one over the other, and churn would re-group jobs for nothing."""
        first = job(id="a", posted_at=ago(5), posted_precision="approximate")
        second = job(id="a", posted_at=ago(2), posted_precision="approximate")
        assert dedupe.merge_sources(first, second).posted_at == ago(5)


class TestOnTheApi:
    def test_every_listed_job_carries_its_group(self, api):
        jobs = api.ok("GET", "/jobs")["items"]
        assert jobs
        groups = {j["urgency"] for j in jobs}
        assert groups <= {"today", "this_week", "two_weeks", "older", "unknown"}
        # The demo spans 0.2 to 10 days, so it should land in at least two different groups.
        assert len(groups) >= 2

    def test_the_group_matches_the_freshness_the_score_used(self, api):
        """One table, two readers — if these ever disagree the table has been forked."""
        now = now_ms()  # the conftest freezes the clock, which is what the store read too
        for listed in api.ok("GET", "/jobs")["items"]:
            assert freshness_score(listed.get("postedAt"), now) == listed["fit"]["freshness"]
            assert listed["urgency"] == urgency(listed.get("postedAt"), now)

    def test_the_group_is_computed_on_read_not_stored(self):
        """A stored one would be wrong by morning."""
        from wera_agent.schemas.jobs import Job

        assert "urgency" not in Job.model_fields

    def test_precision_survives_a_round_trip_through_the_database(self, store, demo_jobs):
        """It lives in the row's JSON blob, so there is no column to forget to add — but there
        is also nothing that would fail loudly if it stopped being written."""
        from wera_agent.schemas.jobs import FitEvidence
        from wera_agent.services.store import save_job

        evidence = FitEvidence.model_validate(demo_jobs["shortlist"][0]["evidence"])
        # Against the store's clock, not the fixtures' fixed `NOW` — the store reads the real
        # (frozen) one, and a date two days before a constant from last year is not "today".
        saved = job(
            id="job-precision",
            dedupe_key="url:https://x.example/1",
            posted_at=iso(now_ms() - 2 * DAY),
            posted_precision="exact",
        )
        save_job(store.s, saved, evidence, questions=[])
        store.s.flush()
        read = store.get_job("job-precision")
        assert read.posted_precision == "exact"
        assert read.urgency == "today"
