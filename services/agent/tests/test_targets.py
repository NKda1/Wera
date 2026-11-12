"""The discovery queue (SRCH-20, between the Strategist and the Harvester), on fakeredis.

What it must hold to: postings come out best first and once each; a posting the Harvester turned
down isn't queued again for a week; nothing lives for ever; and a new plan — a new CV or profile —
never inherits the last plan's postings.
"""

import fakeredis
import pytest

from wera_agent.errors import ApiError
from wera_agent.settings import get_settings
from wera_agent.sources import targets
from wera_agent.sources.targets import Target, TargetQueue


def target(n: int, url: str | None = None) -> Target:
    return Target(
        url=url or f"https://builtinlondon.uk/job/role-{n}/{1000000 + n}",
        host="builtinlondon.uk",
        source="board",
        query='site:builtinlondon.uk "Graduate Software Engineer" London',
        rank=n,
        title=f"Role {n}",
        snippet="",
        date=None,
    )


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


class TestTheQueue:
    def test_best_first_and_once_each(self, r):
        q = TargetQueue(r, "5-abc")
        assert q.push([target(0), target(1), target(0)]) == 2
        assert q.push([target(1), target(2)]) == 1  # a second pass doesn't queue a posting twice
        assert [t.rank for t in q.take(2)] == [0, 1]
        assert [t.rank for t in q.take(5)] == [2]
        assert q.take(1) == []

    def test_the_same_posting_by_another_address_is_still_one(self, r):
        q = TargetQueue(r, "5-abc")
        assert q.push([target(0), target(0, url="https://www.builtinlondon.uk/job/role-0/1000000?utm_source=google")]) == 1

    def test_a_rejected_posting_isnt_queued_again(self, r):
        q = TargetQueue(r, "5-abc")
        q.reject(target(3).url, "older than 21 days")
        assert q.push([target(3)]) == 0
        assert q.is_rejected(target(3).url)

    def test_nothing_lives_for_ever(self, r):
        q = TargetQueue(r, "5-abc")
        q.push([target(0)])
        q.reject(target(9).url, "closed")
        assert 0 < r.ttl(q.queue) <= targets.QUEUE_TTL_SECONDS
        assert 0 < r.ttl(q.queued) <= targets.QUEUE_TTL_SECONDS
        assert targets.QUEUE_TTL_SECONDS < r.ttl(f"wera:seen:{targets.url_key(target(9).url)}") <= targets.SEEN_TTL_SECONDS


class TestANewPlan:
    def test_the_last_plans_postings_are_never_read(self, r):
        """A new CV makes a new plan key. Its queue starts empty, and the old one is deleted."""
        old = TargetQueue(r, "4-abc")
        old.push([target(0), target(1)])
        new = TargetQueue(r, "5-abc")
        assert new.size() == 0
        new.push([target(2)])
        assert new.retire_others() == 2  # the old queue and its set
        assert old.size() == 0 and new.size() == 1

    def test_a_rejection_outlives_the_plan(self, r):
        TargetQueue(r, "4-abc").reject(target(0).url, "closed")
        new = TargetQueue(r, "5-abc")
        new.retire_others()
        assert new.push([target(0)]) == 0

    def test_clear_all_takes_everything(self, r):
        q = TargetQueue(r, "5-abc")
        q.push([target(0)])
        q.reject(target(1).url, "closed")
        r.set("someone-else:key", "kept")
        assert targets.clear_all(r) == 3
        assert r.get("someone-else:key") == "kept"  # only WERA's keys


class TestConnecting:
    def test_demo_mode_never_connects(self):
        with pytest.raises(ApiError) as exc:
            targets.connect()
        assert exc.value.code == "DEMO_MODE"

    def test_redis_down_stops_before_anything_is_spent(self, outbound):
        """The suite points REDIS_URL at a port nothing serves (conftest)."""
        assert "127.0.0.1:1/" in get_settings().redis_url
        with pytest.raises(ApiError) as exc:
            targets.connect()
        assert exc.value.code == "REDIS_DOWN"
