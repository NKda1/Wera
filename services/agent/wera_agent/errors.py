"""API errors leave as `{ error: { code, message, fields? } }` with a stable code (SPEC §9.4).
Unexpected errors never echo internals."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("wera")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields


def error_body(code: str, message: str, fields: dict[str, str] | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if fields:
        err["fields"] = fields
    return {"error": err}


def validation_fields(errors: list[dict]) -> dict[str, str]:
    """Pydantic errors → {"titles": "Add at least 1 target title"}, first message per field."""
    fields: dict[str, str] = {}
    for e in errors:
        loc = [str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path")]
        key = ".".join(loc) or "_"
        msg = str(e.get("msg", "Invalid value")).removeprefix("Value error, ")
        fields.setdefault(key, msg)
    return fields


def validation_error(errors: list[dict]) -> ApiError:
    fields = validation_fields(errors)
    first = next(iter(fields.items()), None)
    message = f"{first[0]}: {first[1]}" if first else "Invalid input"
    return ApiError(422, "VALIDATION", message, fields)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, err: ApiError):
        return JSONResponse(error_body(err.code, err.message, err.fields), status_code=err.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, err: RequestValidationError):
        e = validation_error(list(err.errors()))
        if any(x.get("type") == "json_invalid" for x in err.errors()):
            return JSONResponse(error_body("BAD_JSON", "The request body isn't valid JSON."), status_code=400)
        return JSONResponse(error_body(e.code, e.message, e.fields), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, err: Exception):
        log.error("unhandled error: %s", type(err).__name__)
        return JSONResponse(error_body("INTERNAL", "Something went wrong on the server."), status_code=500)
