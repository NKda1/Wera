"""Run Alembic migrations from code (startup, `wera migrate`, tests)."""

from alembic import command
from alembic.config import Config

from .paths import AGENT_DIR
from .settings import get_settings


def _config(url: str | None = None) -> Config:
    cfg = Config(str(AGENT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(AGENT_DIR / "migrations"))
    cfg.attributes["url"] = url or get_settings().db_url
    return cfg


def upgrade(url: str | None = None) -> None:
    command.upgrade(_config(url), "head")
