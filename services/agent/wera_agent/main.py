"""The agent's FastAPI app. It listens on loopback and only answers the web app, which
proves itself with a shared bearer token (AGENT_TOKEN)."""

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import errors
from .api.routes import router
from .clock import now_ms
from .db.session import transaction
from .logging import configure as configure_logging
from .observability import configure as configure_observability
from .services.seed import is_seeded, seed
from .settings import get_settings


def ensure_database() -> None:
    """Migrate to the latest schema and seed an empty database."""
    from .migrate import upgrade

    upgrade()
    with transaction() as session:
        if not is_seeded(session):
            seed(session, now_ms())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_database()
    yield


def create_app(*, manage_database: bool = True) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="WERA agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan if manage_database else None
    )
    errors.install(app)
    configure_observability(app)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        expected = get_settings().agent_token
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if expected is None or not secrets.compare_digest(given.encode(), expected.get_secret_value().encode()):
            return JSONResponse(errors.error_body("UNAUTHORIZED", "This service only answers the WERA web app."), status_code=401)
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "demoMode": get_settings().wera_demo_mode}

    app.include_router(router)
    return app
