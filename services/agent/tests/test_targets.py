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
        assert new.retire_others() == 3  # the old pool, its set and its arrival counter
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
        assert targets.clear_all(r) == 4  # the pool, its set, its counter and the rejection
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


class TestThePool:
    """17 Nov: the queue is a pool scored by how well each posting matches the CV, kept across a
    change of where, and read best first among the places now ticked."""

    def test_the_best_match_is_read_first_wherever_it_was_found(self, r):
        scores = {"Graduate Software Engineer": 0.9, "Sales Manager": 0.1, "Junior Developer": 0.6}
        q = TargetQueue(r, "5-abc", rank=lambda items: [scores.get(title, 0.0) for title, _ in items])
        q.push([
            Target("https://a.example/1", "a.example", "open", "q", 0, "Sales Manager", "", None, country="gb"),
            Target("https://b.example/2", "b.example", "open", "q", 1, "Graduate Software Engineer", "", None, country="ch"),
            Target("https://c.example/3", "c.example", "open", "q", 2, "Junior Developer", "", None, country="de"),
        ])  # fmt: skip
        assert [t.title for t in q.take(3)] == ["Graduate Software Engineer", "Junior Developer", "Sales Manager"]

    def test_only_the_places_now_ticked_are_read_and_the_rest_wait(self, r):
        q = TargetQueue(r, "5-abc-where1", pool="5-abc")
        q.push([
            Target("https://a.example/1", "a.example", "open", "q", 0, "A", "", None, country="ch"),
            Target("https://b.example/2", "b.example", "open", "q", 1, "B", "", None, country="gb"),
        ])  # fmt: skip
        gb_only = lambda t: t.country == "gb"  # noqa: E731
        assert [t.title for t in q.take(5, accept=gb_only)] == ["B"]
        assert q.size() == 1 and q.size(accept=gb_only) == 0

    def test_a_change_of_where_keeps_the_pool_and_starts_a_new_plan(self, r):
        before = TargetQueue(r, "5-abc-gb", pool="5-abc")
        before.push([target(0)])
        before.mark_tier_sent(1)
        after = TargetQueue(r, "5-abc-gb-ch", pool="5-abc")
        after.retire_others()
        assert after.size() == 1  # what was found is still there
        assert after.tiers_sent() == set()  # but the new places' searches are still to send

    def test_put_back_goes_to_the_front(self, r):
        q = TargetQueue(r, "5-abc", rank=lambda items: [0.5 for _ in items])
        q.push([target(0), target(1)])
        first = q.take(1)
        q.put_back(first)
        assert q.take(1)[0].url == first[0].url


class TestCountryFloorsForLookups:
    """SRCH-32 (22 Nov): a search's lookups, and a short search's preview reads, give each ticked
    country its floor (`Balance`'s rule: n ÷ countries) first, then go best match first. With GB, CH
    and NL ticked, best match first alone gave every lookup to the British leads."""

    # Best match first: more British leads, and better, than Swiss or Dutch ones.
    LEADS = [("gb", 0.95), ("gb", 0.94), ("gb", 0.93), ("gb", 0.92), ("gb", 0.91), ("gb", 0.9), ("gb", 0.89),
             ("gb", 0.88), ("ch", 0.8), ("ch", 0.78), ("nl", 0.76), ("ch", 0.75), ("ch", 0.6), ("nl", 0.5)]  # fmt: skip

    def take(self, n: int, countries: list[str], leads=None) -> list[tuple[str, float]]:
        leads = self.LEADS if leads is None else leads
        chosen = targets.floors_first(leads, n, countries, lambda x: x[0], floor_ok=lambda x: x[1] >= 0.72)
        return [leads[i] for i in chosen]

    def test_each_country_above_the_bar_gets_its_floor_before_better_british_leads(self):
        got = self.take(6, ["gb", "ch", "nl"])  # a floor of 2 each
        assert len(got) == 6
        assert sum(c == "ch" for c, _ in got) == 2 and sum(c == "gb" for c, _ in got) == 3
        # The Netherlands has one lead above the bar: it takes that, and gives the other slot back,
        # to the best left whichever country.
        assert [x for x in got if x[0] == "nl"] == [("nl", 0.76)]
        assert ("gb", 0.93) in got

    def test_a_country_with_no_leads_gives_its_share_back(self):
        british = [x for x in self.LEADS if x[0] != "nl"]
        got = self.take(15, ["gb", "ch", "nl"], british)
        assert len(got) == len(british) == 12  # every lead, never more than there are
        got = self.take(9, ["gb", "ch", "nl"], british)
        assert len(got) == 9 and sum(c == "ch" for c, _ in got) == 3  # CH's 3 above the bar; NL's 3 to the rest

    def test_never_more_than_the_allowance_and_one_country_is_best_first(self):
        for n in range(0, 16):
            assert len(self.take(n, ["gb", "ch", "nl"])) == min(n, len(self.LEADS))
        assert self.take(3, ["gb"]) == self.LEADS[:3]

    def test_a_limit_orders_them_all_floors_first(self):
        order = targets.floors_first(self.LEADS, 6, ["gb", "ch", "nl"], lambda x: x[0], limit=len(self.LEADS))
        assert sorted(order) == list(range(len(self.LEADS)))
        assert {self.LEADS[i][0] for i in order[:3]} == {"gb", "ch", "nl"}  # one each, before a second British one
