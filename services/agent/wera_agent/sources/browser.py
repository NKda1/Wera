"""A headless browser, for the pages plain HTTP can't read (SRCH-05).

**Why this exists at all.** Firecrawl's JSON-format scrape costs 5 credits a page against a
search's 2, and page scrapes were 5 of every 7 credits a run spent — so with Firecrawl at ~85% of
the bill, this one call was most of the invoice. What those 5 credits buy is an LLM reading a
rendered page into fields, and both halves can be done here: a browser renders, and
`sources.extract` reads. See that module for the ladder; this is only the renderer.

**Scoped to a session, not the process.** Playwright's sync API is bound to the thread that
created it, and WERA scrapes from a worker thread (`asyncio.to_thread`) whose identity is a
thread-pool detail. A session opened around the scrape loop sidesteps that entirely: one browser
for the burst of pages a run reads, closed when the burst ends, nothing left running afterwards.

**Absent is a normal state.** Chromium is a ~150 MB download, so `pnpm install` does not fetch it
and `fetch_html` returns `None` when it isn't there. The ladder simply skips this rung — steps 1,
2 and 4 are plain HTTP and parsing — so a person who never runs `playwright install chromium`
gets a working, cheaper-than-before scraper, and one who does gets the JS-rendered boards too.
That is also why nothing here raises: a renderer that isn't installed is not an error.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Any

import logfire

from ..http import check
from ..settings import get_settings

# A job page is text. Blocking the rest cuts a render from seconds to under one on a board that
# loads a tracker, three fonts and a hero image — and WERA has no use for any of them.
BLOCKED_RESOURCES = ("image", "media", "font", "stylesheet")

# Per page. Generous enough for a slow ATS behind a cold CDN, short enough that thirty of them
# can't become a quarter of an hour.
PAGE_TIMEOUT_MS = 20_000

# What counts as rendered. `domcontentloaded` fires before client-side job content exists on a
# React board; `networkidle` waits for analytics beacons that never stop. `load` plus a short
# settle is the compromise that reads both kinds of page.
SETTLE_MS = 600

INSTALL_HINT = "Run `uv run playwright install chromium` in services/agent to read JavaScript-rendered boards."

# The signature `extract` depends on: a URL in, HTML or None out. Keeps that module testable
# without a browser, and keeps this one free of any opinion about job postings.
type Fetcher = Callable[[str], str | None]


def _unavailable(_url: str) -> None:
    return None


# Set once a session has established that there is no usable browser, so later sessions don't
# pay to find out again. Starting Playwright's driver is ~half a second whether or not Chromium
# is there, and that cost lands on a single-job import (ATS-04) as well as on a run — it added
# twenty seconds to the test suite before this existed. Reset by `forget_unavailable`, which is
# what makes "install Chromium and try again" work without a restart.
_no_browser: str | None = None


def forget_unavailable() -> None:
    """Try the browser again on the next session, after an install."""
    global _no_browser
    _no_browser = None


@contextmanager
def session() -> Iterator[Fetcher]:
    """One browser for a burst of pages. Yields a fetcher that returns `None` if it can't render.

    **Started on first use, not on entry.** Playwright's driver takes about 3.3 seconds to come
    up, and the browser is the *third* rung of the ladder — JSON-LD and the ATS feeds answer most
    pages without it. Paying that on entry meant a run where every page parsed cleanly still spent
    three seconds launching a browser it never asked a question of, and it put twenty seconds on
    the test suite.

    Never raises: every caller treats "couldn't render" the same as "nothing useful on the page",
    and a missing browser must not be able to fail a run.
    """
    if get_settings().wera_demo_mode:
        # DEMO-02: the demo makes no outbound request. `http.check` enforces this per URL too,
        # but starting a browser at all would already be wrong.
        yield _unavailable
        return

    lazy = _LazyBrowser()
    try:
        yield lazy.fetch_html
    finally:
        lazy.close()


class _LazyBrowser:
    """A browser that exists only once something asks it to render something."""

    def __init__(self) -> None:
        self._play: Any = None
        self._browser: Any = None
        self._tried = False

    def fetch_html(self, url: str) -> str | None:
        # The same gate every other outbound request passes (SEC-07): LinkedIn is never fetched,
        # whatever asked for it. Checked before starting anything — a refused URL must not be
        # able to bring a browser up.
        try:
            check(url)
        except Exception:  # noqa: BLE001 — a refusal is a decision, not a page
            return None

        browser = self._ensure()
        if browser is None:
            return None

        page = None
        try:
            page = browser.new_page(user_agent="WERA/0.1")
            page.route("**/*", _skip_heavy_assets)
            page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="load")
            page.wait_for_timeout(SETTLE_MS)
            return page.content()
        except Exception:  # noqa: BLE001 — a page that won't render is an answer, not an error
            logfire.info("Couldn't render a page in the browser")
            return None
        finally:
            if page is not None:
                _quietly(page.close)

    def _ensure(self) -> Any:
        """The browser, starting it the first time. One attempt per session, whatever happens."""
        global _no_browser

        if self._tried:
            return self._browser
        self._tried = True
        if _no_browser is not None:
            return None

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _no_browser = "playwright isn't installed"
            logfire.info("Playwright isn't installed; reading pages without a browser")
            return None

        # `.start()` rather than the context manager, because the driver's lifetime is the
        # session's and the session is a context manager of its own.
        try:
            self._play = sync_playwright().start()
        except Exception:  # noqa: BLE001 — a driver that won't start is not a failed run
            _no_browser = "couldn't start the driver"
            logfire.warn("Couldn't start Playwright; reading pages without a browser")
            return None

        try:
            self._browser = self._play.chromium.launch(args=["--disable-dev-shm-usage"])
        except Exception:  # noqa: BLE001 — overwhelmingly "the binary isn't installed"
            _no_browser = "no Chromium"
            logfire.info("No Chromium to launch. {hint}", hint=INSTALL_HINT)
            self.close()
        return self._browser

    def close(self) -> None:
        if self._browser is not None:
            _quietly(self._browser.close)
            self._browser = None
        if self._play is not None:
            _quietly(self._play.stop)
            self._play = None


def _skip_heavy_assets(route: Any) -> None:
    try:
        if route.request.resource_type in BLOCKED_RESOURCES:
            route.abort()
        else:
            route.continue_()
    except Exception:  # noqa: BLE001 — a routing decision is never worth failing a page over
        _quietly(route.continue_)


def _quietly(fn: Callable[[], Any]) -> None:
    """Closing a page or a browser that has already died is not news."""
    with suppress(Exception):
        fn()
