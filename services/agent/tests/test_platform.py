"""Contracts, migrations and logging: the plumbing other tests rely on."""

import logging

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from wera_agent.db.models import Base, MasterCvRow
from wera_agent.db.session import engine
from wera_agent.logging import RedactingFilter, redact
from wera_agent.migrate import upgrade
from wera_agent.schema_export import stale

from .conftest import TOKEN


def test_exported_schemas_match_the_pydantic_models():
    """SCH-03: packages/schemas/json is regenerated whenever a model changes."""
    assert stale() == [], "Run `uv run wera schemas` and `pnpm --filter web gen:contracts`."


def test_migrations_match_the_models():
    upgrade()
    with engine().connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_every_step_the_draft_graph_marks_is_a_step_the_contract_knows():
    """PERF-03: the progress indicator is only honest if these three lists agree.

    They didn't. `graphs/application.py` marked a fourth step, `answers`, that neither
    `DraftStep` nor `DRAFT_ORDER` listed — and `Store._draft` maps an unknown step back to the
    first one, so every pack's indicator jumped from "ATS check" to "Tailoring CV" on its last
    step. Read out of the source so adding a node to the graph fails here rather than in front
    of a person.
    """
    import inspect
    import re
    import typing

    from wera_agent.graphs import application
    from wera_agent.schemas.applications import DraftStep
    from wera_agent.services.store import DRAFT_ORDER

    marked = set(re.findall(r'_mark\(state,\s*"([a-z_]+)"\)', inspect.getsource(application)))
    assert marked, "no _mark calls found — has the graph been rewritten?"
    assert marked <= set(DRAFT_ORDER), f"steps the graph marks but the store can't order: {marked - set(DRAFT_ORDER)}"
    assert set(DRAFT_ORDER) == set(typing.get_args(DraftStep.__value__))


def test_the_supabase_schema_matches_the_models():
    """Hand-edited SQL would drift from the models the first time a column changed."""
    from wera_agent.supabase_export import stale as sql_stale

    assert not sql_stale(), "Run `uv run wera supabase`."


def test_every_supabase_table_has_row_level_security():
    """Supabase publishes `public` over PostgREST with a key that ships in the browser bundle,
    so a table without RLS is a public table — and these hold a CV, contact details and
    letters. A new model must not reach Supabase without a matching `enable row level
    security`."""
    from wera_agent.supabase_export import EMBEDDINGS_TABLE, build

    sql = build()
    expected = {t.name for t in Base.metadata.sorted_tables} | {EMBEDDINGS_TABLE}
    missing = [t for t in expected if f"alter table {t} enable row level security;" not in sql]
    assert missing == [], f"no row-level security for: {', '.join(sorted(missing))}"
    # And no policy hands that access back.
    assert "create policy" not in sql.lower()


def test_a_real_database_does_not_start_with_invented_jobs(monkeypatch, tmp_path):
    """DEMO-01's fixtures belong to the demo.

    A seeded job is indistinguishable from a found one on the shortlist — it has a fit score, an
    apply URL and a company — so a real install starts with an empty list and fills it on the
    first run. The CV and profile are seeded either way: those are a starting point to edit, not
    a claimed finding.
    """
    from starlette.testclient import TestClient

    from wera_agent.db.models import JobRow
    from wera_agent.db.session import transaction
    from wera_agent.main import create_app
    from wera_agent.services.store import get_kv
    from wera_agent.settings import get_settings

    monkeypatch.setenv("WERA_DEMO_MODE", "false")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    get_settings.cache_clear()

    with TestClient(create_app(), headers={"Authorization": f"Bearer {TOKEN}"}), transaction() as session:
        assert session.query(JobRow).count() == 0
        assert session.query(MasterCvRow).count() == 1  # the CV is still there to edit
        # And no invented figures on the channel strip either.
        stats = get_kv(session, "stats")
        assert (stats["board"]["results"], stats["board"]["ranAt"]) == (0, None)
        assert stats["inbox"]["checkedAt"] is None


def test_every_screen_loads_on_a_real_database(monkeypatch, tmp_path):
    """The whole suite runs in demo mode, which seeds figures a real install doesn't have —
    so a field that is only ever null outside demo mode went untyped and the Settings page
    answered 500 the first time anyone turned demo mode off. This walks the read routes on a
    freshly seeded real database, which is the state a person is in on their first live start.
    """
    from starlette.testclient import TestClient

    from wera_agent.main import create_app
    from wera_agent.settings import get_settings

    monkeypatch.setenv("WERA_DEMO_MODE", "false")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'real.db'}")
    get_settings.cache_clear()

    with TestClient(create_app(), headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        for path in (
            "/nav",
            "/sites",
            "/settings",
            "/cv",
            "/cv/contact",
            "/cv/lines",
            "/profile",
            "/jobs",
            "/jobs/meta",
            "/applications",
        ):
            response = client.get(path)
            assert response.status_code == 200, f"GET {path} → {response.status_code} {response.text[:200]}"
        settings = client.get("/settings").json()
        assert settings["demoMode"] is False
        # The field that caused it: "never checked" is a real state, not a missing value.
        assert settings["inbox"]["lastCheckedAt"] is None


def test_logs_never_contain_a_key(caplog):
    """SEC-05: a fake key passed to the logger comes out masked."""
    fake = "sk-or-v1-" + "0" * 64
    assert fake not in redact(f"calling OpenRouter with {fake}")
    assert "hunter2" not in redact("LLM_API_KEY=hunter2 failed")
    assert "abc123" not in redact("Authorization: Bearer abc123")

    log = logging.getLogger("wera.test")
    handler = logging.Handler()
    handler.addFilter(RedactingFilter())
    seen: list[str] = []
    handler.emit = lambda record: seen.append(record.getMessage())  # type: ignore[method-assign]
    log.addHandler(handler)
    try:
        log.error("request failed for key %s", fake)
    finally:
        log.removeHandler(handler)
    assert seen and fake not in seen[0]
