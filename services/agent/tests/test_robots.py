"""robots.txt read as RFC 9309 says (§11.3), and the verdicts a search is planned around (SRCH-30)."""

import fakeredis

from wera_agent.sources.robots import Robots, Rules, refusing_hosts

# The shape of Indeed's file (13 Nov): `Allow: /` first, the posting path disallowed far below it.
# `urllib.robotparser` stops at the first match, so it read every Indeed posting as allowed.
INDEED = """
User-agent: *
Allow: /
Allow: /*&start=0&
Disallow: /*rt=nc
Disallow: /job/
Disallow: /viewjob
Disallow: /m/viewjob

User-agent: MyCentralAIScraperBot
Disallow: /
""".splitlines()


# job-room.ch, Switzerland's public job board (22 Nov): its adverts are under /job-search/, and
# its robots.txt asks that they are not crawled.
JOB_ROOM = """
User-agent: *
# Do not crawl Job Adverts
Disallow: /job-search/
""".splitlines()


class TestTheRules:
    def test_job_rooms_adverts_are_refused(self):
        rules = Rules.parse(JOB_ROOM)
        assert not rules.allows("https://www.job-room.ch/job-search/0f3c2a10-junior-financial-analyst")
        assert not rules.allows("https://www.job-room.ch/job-search/?query=analyst")
        assert rules.allows("https://www.job-room.ch/home")

    def test_the_longest_match_wins_not_the_first(self):
        rules = Rules.parse(INDEED)
        assert not rules.allows("https://www.indeed.com/viewjob?jk=abc123")
        assert not rules.allows("https://www.indeed.com/job/software-engineer-abc")
        assert rules.allows("https://www.indeed.com/q-software-engineer-jobs.html")

    def test_a_wildcard_matches_any_run_of_characters(self):
        rules = Rules.parse(["User-agent: *", "Disallow: /job/*/apply/*", "Disallow: /*rt=nc"])
        assert not rules.allows("https://www.totaljobs.com/job/software-engineer/apply/start")
        assert rules.allows("https://www.totaljobs.com/job/software-engineer/acme-job123")
        assert not rules.allows("https://www.indeed.com/jobs?q=x&rt=nc")

    def test_a_dollar_ends_the_path(self):
        rules = Rules.parse(["User-agent: *", "Disallow: /*.pdf$"])
        assert not rules.allows("https://example.com/brochure.pdf")
        assert rules.allows("https://example.com/brochure.pdf?page=2")

    def test_allow_wins_a_tie(self):
        rules = Rules.parse(["User-agent: *", "Disallow: /jobs", "Allow: /jobs"])
        assert rules.allows("https://example.com/jobs/1")

    def test_a_group_naming_wera_is_obeyed_over_the_star_group(self):
        rules = Rules.parse(["User-agent: *", "Allow: /", "", "User-agent: WERA", "Disallow: /"])
        assert not rules.allows("https://example.com/jobs/1")

    def test_another_bots_group_is_not_wera_s(self):
        rules = Rules.parse(INDEED)
        assert rules.allows("https://www.indeed.com/")  # `MyCentralAIScraperBot: Disallow: /` isn't for us

    def test_an_empty_disallow_disallows_nothing_and_comments_are_ignored(self):
        rules = Rules.parse(["User-agent: * # everyone", "Disallow:", "Disallow: /private # not this"])
        assert rules.allows("https://example.com/jobs/1")
        assert not rules.allows("https://example.com/private/1")

    def test_several_agents_can_share_one_group(self):
        rules = Rules.parse(["User-agent: googlebot", "User-agent: *", "Disallow: /search"])
        assert not rules.allows("https://example.com/search?q=x")


class AnswersFrom(Robots):
    """A reader whose files are given, and which counts how often it was asked."""

    def __init__(self, verdicts: dict[str, bool]):
        super().__init__()
        self.verdicts, self.asked = verdicts, []

    def allows(self, url: str) -> bool:
        self.asked.append(url)
        return next(v for host, v in self.verdicts.items() if host in url)


class TestWhatASearchLeavesOut:
    PROBES = {"brightnetwork.co.uk": "https://brightnetwork.co.uk/graduate-jobs/x/y", "gradcracker.com": "https://gradcracker.com/hub/1/x/graduate-job/12345/y"}  # fmt: skip

    def test_a_refusing_host_is_named_and_the_verdict_kept_for_a_week(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        robots = AnswersFrom({"brightnetwork": False, "gradcracker": True})
        assert refusing_hosts(self.PROBES, redis=redis, robots=robots) == {"brightnetwork.co.uk"}
        assert redis.get("wera:robots:brightnetwork.co.uk") == "refuse"
        assert 0 < redis.ttl("wera:robots:gradcracker.com") <= 7 * 24 * 3600

        again = AnswersFrom({"brightnetwork": True, "gradcracker": True})
        assert refusing_hosts(self.PROBES, redis=redis, robots=again) == {"brightnetwork.co.uk"}
        assert again.asked == []  # answered from the cache

    def test_a_read_that_mustnt_touch_the_network_uses_the_cache_alone(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        robots = AnswersFrom({"brightnetwork": False, "gradcracker": True})
        assert refusing_hosts(self.PROBES, redis=redis, robots=robots, probe=False) == set()
        assert robots.asked == []

    def test_a_file_that_cant_be_read_is_a_refusal(self):
        class Broken(Robots):
            def allows(self, url):
                raise RuntimeError("TLS")

        assert refusing_hosts(self.PROBES, redis=None, robots=Broken()) == set(self.PROBES)
