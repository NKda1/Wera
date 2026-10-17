import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from wera_agent import clock, tasks
from wera_agent.db.session import reset_caches, transaction
from wera_agent.main import create_app
from wera_agent.paths import FIXTURES_DIR
from wera_agent.schemas.cv import MasterCV
from wera_agent.services.store import Store
from wera_agent.settings import Settings, get_settings

TOKEN = "test-agent-token"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch) -> Iterator[None]:
    """A fresh demo database per test, no .env.local, no keys, no tracing, and a frozen clock."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'wera-test.db'}")
    monkeypatch.setenv("WERA_DEMO_MODE", "true")
    # DEMO-02: tests never send telemetry, even on a machine with a Logfire token.
    monkeypatch.setenv("LOGFIRE_ENABLED", "false")
    monkeypatch.setenv("LOGFIRE_IGNORE_NO_CONFIG", "1")
    monkeypatch.setenv("AGENT_TOKEN", TOKEN)
    for name in ("LLM_API_KEY", "FIRECRAWL_API_KEY", "ALERTS_IMAP_APP_PASSWORD", "ALERTS_IMAP_USER"):
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
