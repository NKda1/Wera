"""The lifecycle sweep (SRCH-13, ATS-06, NORM-07, MATCH-10).

Two invariants carry this file, and both are ways of being wrong that a person would experience
as WERA deleting their work:

* **only positive evidence closes a job** — a board that didn't answer closes nothing;
* **the archive clock is `max(lastSeen, lastConfirmedAt)`** — reading `lastSeen` alone archives
  every board-search job on day 30, because SRCH-05 means those jobs are never re-sighted.
"""

import pytest

from wera_agent.clock import iso, now_ms
from wera_agent.db.models import JobRow
from wera_agent.db.session import transaction
from wera_agent.graphs import recheck
from wera_agent.schemas.jobs import Ats, Job, JobSource, Location
from wera_agent.services.store import Store, save_job

# The frozen clock, not a literal: `recheck.run()` reads `now_ms()` itself, and a fixed
# timestamp from last year would make every job in here look 400 days stale to the sweep.
NOW = now_ms()
DAY = 24 * 60 * 60 * 1000


def job(**over) -> Job:
    base = {
        "id": over.pop("id", "job-1"),
        "dedupe_key": over.pop("dedupe_key", f"url:https://x.example/{over.get('id', 1)}"),
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


@pytest.fixture
def store(migrated):
    """A migrated database and a `Store` on the run's clock, without the app or the seed."""

    def put(*jobs: Job) -> None:
        with transaction() as session:
            for j in jobs:
                save_job(session, j, created_at=iso(NOW))

    return put


class TestTheArchiveClock:
    """NORM-07 × SRCH-05, the trap. `lastSeen` is about WERA's attention, not the vacancy."""

    def test_a_job_confirmed_today_is_not_archived_though_last_seen_is_ancient(self, store):
        """The regression that would have deleted live jobs wholesale.

        `firecrawl_search.search` drops a hit whose URL is already known *before* scraping it, so
        a stored board-search job is never re-sighted and its `lastSeen` is frozen at the day it
        was found. A plain `lastSeen` sweep archives all of them on day 30 — including the ones
        the morning check confirmed open an hour earlier.
        """
        store(job(id="j1", last_seen=iso(NOW - 40 * DAY), last_confirmed_at=iso(NOW)))
        with transaction() as session:
            assert Store(session, NOW).archive_stale(30) == 0
            assert session.get(JobRow, "j1").status == "new"

    def test_a_job_neither_seen_nor_confirmed_for_the_window_is_archived(self, store):
        store(job(id="j1", last_seen=iso(NOW - 40 * DAY)))
        with transaction() as session:
            assert Store(session, NOW).archive_stale(30) == 1
            assert session.get(JobRow, "j1").status == "archived"

    def test_a_job_inside_the_window_is_left_alone(self, store):
        store(job(id="j1", last_seen=iso(NOW - 10 * DAY)))
        with transaction() as session:
            assert Store(session, NOW).archive_stale(30) == 0

    def test_a_watchlist_job_is_never_archived_on_a_timer(self, store):
        """NORM-07 says non-watchlist: ATS-03 governs those from the employer's own feed, and a
        timer would fight it."""
        watched = job(
            id="j1",
            last_seen=iso(NOW - 40 * DAY),
            sources=[JobSource(channel="watchlist", detail="greenhouse:acme", seen_at=iso(NOW - 40 * DAY))],
        )
        store(watched)
        with transaction() as session:
            assert Store(session, NOW).archive_stale(30) == 0

    def test_an_application_in_flight_keeps_its_job(self, store):
        store(job(id="j1", last_seen=iso(NOW - 99 * DAY), status="in_application"))
        with transaction() as session:
            assert Store(session, NOW).archive_stale(30) == 0

    def test_an_unreadable_date_is_not_a_reason_to_archive(self, store):
        """The sweep acts on evidence that time has passed, and a bad date is not that."""
        store(job(id="j1", last_seen="not a date"))
        with transaction() as session:
            assert Store(session, NOW).archive_stale(30) == 0


class TestClosingNeedsAFeedThatAnswered:
    def test_a_job_gone_from_a_feed_that_answered_is_closed_with_a_date(self, store, monkeypatch):
        from wera_agent.sources import ats_feeds

        store(job(id="j1", ats=Ats(vendor="greenhouse", board_token="acme", job_id="9")))
        monkeypatch.setattr(ats_feeds, "fetch_boards", lambda boards, limit: ats_feeds.FeedResult(answered={"greenhouse:acme"}))
        assert recheck.run(force=True)["closed"] == 1
        with transaction() as session:
            row = session.get(JobRow, "j1")
            assert row.status == "closed"
            # LIFE-01: when, not just whether.
            assert (row.data or {}).get("closedAt")

    def test_a_board_that_did_not_answer_closes_nothing(self, store, monkeypatch):
        """ATS-03's rule, restated through the wider path: absence of evidence is not evidence."""
        from wera_agent.sources import ats_feeds

        store(job(id="j1", ats=Ats(vendor="greenhouse", board_token="acme", job_id="9")))
        monkeypatch.setattr(ats_feeds, "fetch_boards", lambda boards, limit: ats_feeds.FeedResult(errors=["acme: didn't answer"]))
        assert recheck.run(force=True)["closed"] == 0
        with transaction() as session:
            assert session.get(JobRow, "j1").status == "new"


class TestOneFetchPerBoard:
    def test_thirty_jobs_at_one_employer_is_one_request(self, store, monkeypatch):
        """The whole reason a daily sweep over hundreds of jobs is free rather than rude."""
        from wera_agent.sources import ats_feeds

        acme = [
            job(
                id=f"j{i}", dedupe_key=f"ats:greenhouse:acme:{i}", ats=Ats(vendor="greenhouse", board_token="acme", job_id=str(i))
            )
            for i in range(30)
        ]
        store(*acme)
        fetched: list[str] = []

        def spy(company):
            fetched.append(f"{company.vendor}:{company.token}")
            return [], None

        monkeypatch.setattr(ats_feeds, "fetch_company", spy)
        ats_feeds.fetch_boards(["greenhouse:acme" for _ in range(30)], limit=40)
        assert fetched == ["greenhouse:acme"]

    def test_the_board_limit_bounds_employers_not_jobs(self, monkeypatch):
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(ats_feeds, "fetch_company", lambda c: ([], None))
        boards = [f"greenhouse:co{i}" for i in range(10)]
        assert len(ats_feeds.fetch_boards(boards, limit=3).answered) == 3

    def test_an_unparseable_board_is_skipped_not_fetched(self, monkeypatch):
        from wera_agent.sources import ats_feeds

        monkeypatch.setattr(ats_feeds, "fetch_company", lambda c: ([], None))
        assert ats_feeds.fetch_boards(["nonsense", "unknownvendor:x", ""], limit=5).answered == set()


class TestFlaggedNotHidden:
    """MATCH-10: an unconfirmed job keeps its place. Inconclusive is not withdrawal."""

    def test_an_unreachable_job_is_recorded_but_not_touched_otherwise(self, store):
        store(job(id="j1"))
        with transaction() as session:
            Store(session, NOW).record_liveness("j1", confirmed=False)
        with transaction() as session:
            data = session.get(JobRow, "j1").data
            assert data["lastCheckedAt"]
            assert data.get("lastConfirmedAt") is None
            assert session.get(JobRow, "j1").status == "new"  # still ranked
            assert data.get("hiddenReason") is None

    def test_a_confirmed_job_records_both(self, store):
        store(job(id="j1"))
        with transaction() as session:
            Store(session, NOW).record_liveness("j1", confirmed=True)
        with transaction() as session:
            data = session.get(JobRow, "j1").data
            assert data["lastCheckedAt"] == data["lastConfirmedAt"]


class TestCandidateOrder:
    def test_never_asked_comes_before_asked_a_while_ago(self, store):
        store(
            job(id="asked", dedupe_key="a", last_checked_at=iso(NOW - DAY)),
            job(id="never", dedupe_key="b"),
        )
        with transaction() as session:
            assert [j.id for j in Store(session, NOW).recheck_candidates(10)][0] == "never"

    def test_terminal_jobs_are_not_candidates(self, store):
        store(
            job(id="closed", dedupe_key="a", status="closed"),
            job(id="archived", dedupe_key="b", status="archived"),
            job(id="applying", dedupe_key="c", status="in_application"),
            job(id="open", dedupe_key="d"),
        )
        with transaction() as session:
            assert [j.id for j in Store(session, NOW).recheck_candidates(10)] == ["open"]


class TestWhenItRuns:
    def test_demo_mode_never_sweeps(self):
        """DEMO-02: a demo makes no outbound request, so it has nothing to ask."""
        assert recheck.due(None, NOW) is False

    def test_it_runs_when_it_has_never_run(self, outbound):
        assert recheck.due(None, NOW) is True

    def test_it_refuses_a_second_run_inside_the_day(self, outbound):
        """SPEC §11.3: polling is at most daily, enforced here rather than by the cron being the
        only caller — so running it by hand is safe."""
        assert recheck.due(iso(NOW - 2 * 60 * 60 * 1000), NOW) is False
        assert recheck.due(iso(NOW - 21 * 60 * 60 * 1000), NOW) is True

    def test_an_unreadable_stamp_lets_it_run(self, outbound):
        assert recheck.due("not a date", NOW) is True


def test_the_summary_line_says_what_left(store):
    assert recheck.describe({}) == ""
    assert recheck.describe({"checked": 0}) == ""
    line = recheck.describe({"checked": 12, "closed": 2, "unconfirmed": 1, "archived": 3})
    assert line == "Checked 12 posting(s), 2 closed, 1 couldn't be confirmed, 3 archived."
