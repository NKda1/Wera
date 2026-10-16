"""Contracts, migrations and logging: the plumbing other tests rely on."""

import logging

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from wera_agent.db.models import Base
from wera_agent.db.session import engine
from wera_agent.logging import RedactingFilter, redact
from wera_agent.migrate import upgrade
from wera_agent.schema_export import stale


def test_exported_schemas_match_the_pydantic_models():
    """SCH-03: packages/schemas/json is regenerated whenever a model changes."""
    assert stale() == [], "Run `uv run wera schemas` and `pnpm --filter web gen:contracts`."


def test_migrations_match_the_models():
    upgrade()
    with engine().connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


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
