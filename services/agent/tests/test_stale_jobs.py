"""LIFE-02…05 (14 Nov): getting rid of stale jobs in a market that moves in days.

Measured before: the median closed job lasted 12 days, only employer feeds ever closed anything
(1,252 jobs, none from a board), and 37 of 86 visible board jobs had never been confirmed open.
"""

import pytest

from wera_agent.clock import iso, now_ms
from wera_agent.domain.scoring import evergreen, possibly_closed
from wera_agent.errors import ApiError
from wera_agent.schemas.api import DecisionIn
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.services.store import save_job
from wera_agent.sources import normalise

NOW = now_ms()
DAY = 24 * 60 * 60 * 1000


def job(
    i: int = 1, *, seen_days: float = 0, confirmed_days: float | None = None, posted_days: float | None = None, **over
) -> Job:
    at = iso(NOW - int(seen_days * DAY))
    base = {
        "id": f"job-stale-{i}", "dedupe_key": f"url:https://stale.example/{i}", "title": "Graduate Engineer",
        "company": f"Staleco {i}", "location": Location(city="London", raw="London"), "remote": None,
        "description_md": "x" * 300, "description_status": "full", "apply_url": f"https://stale.example/{i}",
        "sources": [JobSource(channel="board_search", detail="stale.example", seen_at=at)], "language": "en",
        "first_seen": at, "last_seen": at, "status": "new",
        "last_confirmed_at": None if confirmed_days is None else iso(NOW - int(confirmed_days * DAY)),
        "posted_at": None if posted_days is None else iso(NOW - int(posted_days * DAY)),
    }  # fmt: skip
    return Job.model_validate(base | over)


class TestProofOfLife:
    def test_a_job_nothing_has_shown_open_for_a_week_is_possibly_closed(self):
        assert possibly_closed(job(seen_days=10), NOW)
        assert not possibly_closed(job(seen_days=3), NOW)  # found this week: that's evidence
        assert not possibly_closed(job(seen_days=30, confirmed_days=2), NOW)  # confirmed lately

    def test_only_jobs_on_the_list_can_be(self):
        assert not possibly_closed(job(seen_days=30, status="closed"), NOW)
        assert not possibly_closed(job(seen_days=30, status="in_application"), NOW)

    def test_an_employer_feed_listing_it_again_confirms_it(self, store):
        found = job(9, seen_days=10, sources=[JobSource(channel="watchlist", detail="staleco", seen_at=iso(NOW - 10 * DAY))])
        save_job(store.s, found, questions=[])
        again = found.model_copy(update={"sources": [JobSource(channel="watchlist", detail="staleco", seen_at=iso(NOW))]})
        [stored], _ = store.upsert_jobs([again])
        assert stored.last_confirmed_at is not None and not possibly_closed(stored, store.now)


class TestEvergreen:
    def test_open_two_months_is_flagged(self):
        assert evergreen(job(posted_days=75), NOW) and not evergreen(job(posted_days=20), NOW)
        assert evergreen(job(seen_days=61), NOW)  # no posting date: first seen is the floor


class TestClosingDates:
    def test_a_future_posted_date_is_read_as_the_deadline(self):
        ahead = iso(NOW + 10 * DAY)[:10]
        card = {"title": "Graduate Engineer", "company": "Acme", "postedAt": ahead, "description": "x" * 300}
        made = normalise.from_scrape(card, url="https://acme.example/1", site="acme.example", markdown="x" * 300, now=NOW)
        assert made.closes_at == ahead and made.posted_at is None

    def test_a_stated_closing_date_is_kept(self):
        card = {"title": "Graduate Engineer", "company": "Acme", "closesAt": "2026-12-01", "description": "x" * 300}
        made = normalise.from_scrape(card, url="https://acme.example/2", site="acme.example", markdown="x" * 300, now=NOW)
        assert made.closes_at == "2026-12-01"

    def test_a_job_past_its_closing_date_is_closed_and_wont_get_a_pack(self, store):
        save_job(store.s, job(2, closes_at=iso(NOW - 3 * DAY)[:10]), questions=[])
        store.settle()
        assert store._job(store._job_row("job-stale-2")).status == "closed"
        with pytest.raises(ApiError) as refused:
            store.decide("job-stale-2", DecisionIn(action="approve"))
        assert refused.value.code == "JOB_CLOSED"

    def test_a_date_still_ahead_leaves_it_open(self, store):
        save_job(store.s, job(3, closes_at=iso(NOW + 5 * DAY)[:10]), questions=[])
        store.settle()
        assert store._job(store._job_row("job-stale-3")).status == "new"


class TestCheckingBeforeActing:
    @pytest.fixture
    def checks(self, outbound, store, monkeypatch):
        from wera_agent.sources import liveness

        save_job(store.s, job(4, seen_days=2), questions=[])
        store.s.commit()
        answers = {"verdict": "gone", "asked": 0}

        def fake(url, **_kw):
            answers["asked"] += 1
            return answers["verdict"]

        monkeypatch.setattr(liveness, "check", fake)
        return answers

    def test_a_job_that_has_gone_is_closed(self, checks):
        from wera_agent.db.session import transaction
        from wera_agent.graphs import recheck
        from wera_agent.services.store import Store

        assert recheck.check_one("job-stale-4", force=True) == "gone"
        with transaction() as session:
            assert Store(session)._job(Store(session)._job_row("job-stale-4")).status == "closed"

    def test_a_job_checked_today_isnt_asked_again_unless_forced(self, checks):
        from wera_agent.graphs import recheck

        checks["verdict"] = "open"
        assert recheck.check_one("job-stale-4") == "open"
        assert recheck.check_one("job-stale-4") == "skipped" and checks["asked"] == 1
        assert recheck.check_one("job-stale-4", force=True) == "open" and checks["asked"] == 2

    def test_demo_mode_checks_nothing(self, store):
        from wera_agent.graphs import recheck

        save_job(store.s, job(5), questions=[])
        store.s.commit()
        assert recheck.check_one("job-stale-5", force=True) == "skipped"
