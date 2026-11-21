"""M5 (20 Nov): postings read a few at once, to halve a search's 129–162 s reading step.

What must still hold: postings are checked in the order given whatever answers first, the run's
Firecrawl tally is the sum of each read's, and the browser — which belongs to one thread — is
shared through a lane of its own.
"""

import threading
import time
from contextlib import contextmanager

from wera_agent.clock import iso
from wera_agent.sources import extract, harvest
from wera_agent.sources.firecrawl_search import SearchCaps, SearchOutcome

from .test_harvest import BOARDS, DAY, NOW, AllowAll, posting_page, queue, target  # noqa: F401 — `queue` is a fixture


def urls(n: int) -> list[str]:
    return [f"https://builtinlondon.uk/job/graduate-software-engineer/1213805{i}" for i in range(n)]


class TestReadersAtOnce:
    def test_three_at_once_and_kept_in_the_order_given(self, queue, monkeypatch):  # noqa: F811
        busy, most = [0], [0]
        lock = threading.Lock()

        def get(url: str) -> str:
            with lock:
                busy[0] += 1
                most[0] = max(most[0], busy[0])
            time.sleep(0.05 if url.endswith("0") else 0.01)  # the first answers last
            with lock:
                busy[0] -= 1
            return posting_page(posted=iso(NOW - DAY)).replace("Graduate Software Engineer", url[-8:])

        monkeypatch.setattr(extract, "_get", get)
        targets = [target(u) for u in urls(6)]
        spend = SearchOutcome()
        out = harvest.harvest(
            targets, caps=SearchCaps(), spend=spend, now=NOW, sites=BOARDS, queue=queue, robots=AllowAll(), readers=3
        )
        assert most[0] == 3
        assert [j.title for j in out.jobs] == [u[-8:] for u in urls(6)]
        assert spend.local_pages == 6 and spend.credits_used == 0

    def test_the_tallies_of_each_read_add_up(self):
        spend = SearchOutcome(local_pages=2, credits_used=5)
        mine = SearchOutcome(local_pages=2, credits_used=5)
        before = SearchOutcome(local_pages=2, credits_used=5)
        mine.local_pages, mine.credits_used, mine.last_how = 3, 10, "firecrawl"
        harvest._add(spend, mine, before)
        assert (spend.local_pages, spend.credits_used, spend.last_how) == (3, 10, "firecrawl")


class TestTheBrowserLane:
    def test_every_render_runs_on_the_thread_that_opened_the_browser(self):
        opened_on, rendered_on, closed = [], [], []

        @contextmanager
        def session():
            opened_on.append(threading.get_ident())

            def fetch(url: str) -> str:
                rendered_on.append(threading.get_ident())
                return f"<html>{url}</html>"

            yield fetch
            closed.append(threading.get_ident())

        with harvest.BrowserLane(session) as fetch:
            threads = [threading.Thread(target=fetch, args=(f"https://x.example/{i}",)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert fetch("https://x.example/last") == "<html>https://x.example/last</html>"
        assert set(rendered_on) == set(opened_on) == set(closed) and len(rendered_on) == 5
