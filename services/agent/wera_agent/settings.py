"""Environment settings. The ONLY module that reads secrets on the Python side (SPEC SEC-04).

Secrets are SecretStr, so they never print, log or serialise by accident. Callers ask for
status (set / missing) through `secret_status()`, never for values, except the provider
clients that need a key to make a call.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import AGENT_DIR, ROOT

SecretStatus = Literal["set", "missing", "invalid"]


class Settings(BaseSettings):
    # One .env.local at the repo root holds the keys for both processes, and the last file wins,
    # so the root file is authoritative. Every path here is gitignored (`.env` and `.env.*`
    # match at any depth) and gitleaks blocks a key in a commit either way (SEC-01).
    model_config = SettingsConfigDict(
        env_file=(AGENT_DIR / ".env", AGENT_DIR / ".env.local", ROOT / ".env", ROOT / ".env.local"),
        extra="ignore",
    )

    wera_demo_mode: bool = True
    database_url: str | None = None
    data_dir: Path = ROOT / "data"

    # Tracing. Off in effect without a Logfire project token; see wera_agent/observability.py.
    logfire_enabled: bool = True

    # Service-to-service auth between the web app and this service (loopback only).
    agent_token: SecretStr | None = None
    agent_host: str = "127.0.0.1"
    agent_port: int = 8787

    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr | None = None
    # Optional OpenAI-compatible endpoint to call instead of the provider directly, so a PII
    # gateway can sit in front of it without any node changing (see wera_agent/pii.py).
    llm_proxy_url: str | None = None
    firecrawl_api_key: SecretStr | None = None
    alerts_imap_host: str | None = None
    alerts_imap_user: str | None = None
    alerts_imap_app_password: SecretStr | None = None
    alerts_folder: str = "WERA-alerts"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        name = "demo.db" if self.wera_demo_mode else "wera.db"
        return f"sqlite:///{self.data_dir / name}"

    def secret_status(self) -> dict[str, SecretStatus]:
        def status(v: SecretStr | None) -> SecretStatus:
            return "set" if v is not None and v.get_secret_value().strip() else "missing"

        return {
            "llm": status(self.llm_api_key),
            "firecrawl": status(self.firecrawl_api_key),
            "inbox": status(self.alerts_imap_app_password),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def llm_provider_name(base_url: str) -> str:
    from urllib.parse import urlparse

    try:
        host = urlparse(base_url).hostname or ""
    except ValueError:
        return "Custom endpoint"
    if not host:
        return "Custom endpoint"
    if host.endswith("openrouter.ai"):
        return "OpenRouter"
    if "dashscope" in host or "aliyuncs" in host:
        return "Alibaba Model Studio"
    return host
