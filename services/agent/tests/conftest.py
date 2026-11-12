import json
import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from wera_agent import clock, tasks
from wera_agent.config import wera
from wera_agent.db.session import make_engine, reset_caches, transaction
from wera_agent.main import create_app
from wera_agent.migrate import wipe_postgres
from wera_agent.paths import FIXTURES_DIR
from wera_agent.schemas.cv import MasterCV
from wera_agent.services import store as store_module
from wera_agent.services.store import Store
from wera_agent.settings import Settings, get_settings

TOKEN = "test-agent-token"

# Set by the `agent-postgres` CI job to run this whole suite on Postgres instead of SQLite.
# SQLite is the default everywhere else, because it needs nothing installed and a developer
# shouldn't have to run a database to run the tests. One schema covers both (`db/models.py`),
# and this is what proves it: the same 290-odd assertions against the other dialect.
POSTGRES_URL = os.environ.get("WERA_TEST_DATABASE_URL")


def _wipe_postgres(url: str) -> None:
    """A clean database per test, the way a fresh SQLite file is.

    Dropping the schema takes the Alembic version table and LangGraph's checkpoint tables with
    it, which matches SQLite, where the checkpoints live in their own throwaway file. Migration
    0011's own schemas go too (`migrate.SCHEMAS`), or one test's archived records would be the
    next one's.
    """
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            wipe_postgres(conn)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch) -> Iterator[None]:
    """A fresh demo database per test, no .env.local, no keys, no tracing, and a frozen clock."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    if POSTGRES_URL:
        _wipe_postgres(POSTGRES_URL)
        monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    else:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'wera-test.db'}")
    monkeypatch.setenv("WERA_DEMO_MODE", "true")
    # DEMO-02: tests never send telemetry, even on a machine with a Logfire token.
    monkeypatch.setenv("LOGFIRE_ENABLED", "false")
    monkeypatch.setenv("LOGFIRE_IGNORE_NO_CONFIG", "1")
    monkeypatch.setenv("AGENT_TOKEN", TOKEN)
    # Loopback is open to the tests (the ASGI client needs it), and a developer's own Redis listens
    # there — so the suite points at a port nothing serves. Tests that want a queue get `fakeredis`.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    # A shell's DEMO_DATABASE_URL would win over the DATABASE_URL set above, since tests run in demo mode.
    for name in (
        "LLM_API_KEY",
        "FIRECRAWL_API_KEY",
        "SERPER_API_KEY",
        "ALERTS_IMAP_APP_PASSWORD",
        "ALERTS_IMAP_USER",
        "DEMO_DATABASE_URL",
        "LOGFIRE_READ_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    reset_caches()
    clock.freeze()
    yield
    clock.reset()
    reset_caches()
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        yield c


@pytest.fixture
def cv_upload_mode(monkeypatch):
    """Pin what saving a CV does, rather than inheriting `config/wera.yaml`'s answer.

    Both paths are real and the config only decides which is live, so a test that wants one has
    to say which: `cv_upload_mode("reset")` for the testing-phase wipe, `("rescore")` for CV-08's
    re-judge, which is what a multi-user install will run. Left to the shipped config, half these
    tests would pass or fail on a line someone flipped for an afternoon's testing.
    """

    def pin(mode: str) -> None:
        assert mode in ("reset", "rescore")
        pinned = wera().model_copy(update={"reset_on_cv_upload": mode == "reset"})
        monkeypatch.setattr(store_module, "wera", lambda: pinned)

    return pin


@pytest.fixture
def migrated() -> None:
    """The schema, and nothing else — no seed, no app, no scheduler.

    For code that opens its own transactions. The `store` fixture holds one open for the whole
    test, which on SQLite makes a second writer wait out `busy_timeout` and then fail; the crawl
    cache (SRCH-10) is exactly that shape, since it commits per read and write so a crashed run
    keeps what it already paid for.

    `isolated` has already pointed `DATABASE_URL` at a fresh file and cleared the engine cache,
    so applying the migrations is all that's left.
    """
    from wera_agent.migrate import upgrade

    upgrade()


class Api:
    """Calls the agent like the web app does, and returns (status, json)."""

    def __init__(self, c: TestClient):
        self.c = c

    def __call__(self, method: str, path: str, body=None):
        r = self.c.request(method, path, json=body)
        return r.status_code, (r.json() if r.content else None)

    def ok(self, method: str, path: str, body=None):
        status, data = self(method, path, body)
        assert status < 300, (status, data)
        return data

    def code(self, method: str, path: str, body=None) -> str:
        status, data = self(method, path, body)
        return "OK" if status < 300 else data["error"]["code"]


@pytest.fixture
def api(client) -> Api:
    return Api(client)


@pytest.fixture
def outbound(monkeypatch):
    """Demo mode off, for tests of code that makes real requests.

    The shared HTTP client refuses every outbound call in demo mode (DEMO-02), so a test of a
    live source has to turn it off. pytest-socket still fails the test if anything escapes the
    respx mock.
    """
    monkeypatch.setenv("WERA_DEMO_MODE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def drafted():
    """Waits for the application graph to finish what a request set off.

    A Gate 1 approval returns as soon as the decision is saved; the pack is drafted after the
    response. Tests that care about the pack wait here, which is also what the review screen
    does by polling.
    """

    def wait() -> None:
        tasks.drain(timeout=60)

    return wait


@pytest.fixture
def demo_cv() -> MasterCV:
    return MasterCV.model_validate(json.loads((FIXTURES_DIR / "demo" / "cv.json").read_text()))


@pytest.fixture
def demo_jobs() -> dict:
    return json.loads((FIXTURES_DIR / "demo" / "jobs.json").read_text())


@pytest.fixture
def store(client) -> Iterator[Store]:
    """Direct access to the store, after the app has migrated and seeded the database."""
    with transaction() as session:
        yield Store(session)
