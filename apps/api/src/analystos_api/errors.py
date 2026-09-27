"""API error types and the uniform error envelope.

Every error response has the shape ``{"detail": str, "code": str, "request_id": str | null,
"errors": list | null}`` (see ``schemas.common.ErrorResponse``).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logs import get_logger, request_id_var

log = get_logger("analystos.errors")


class ApiError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        extra: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        self.extra = extra
        self.headers = headers


class NotFound(ApiError):
    status_code = 404
    code = "not_found"


class Forbidden(ApiError):
    status_code = 403
    code = "forbidden"


class Unauthorized(ApiError):
    status_code = 401
    code = "unauthorized"


class Conflict(ApiError):
    status_code = 409
    code = "conflict"


class Unprocessable(ApiError):
    status_code = 422
    code = "unprocessable"


class PayloadTooLarge(ApiError):
    status_code = 413
    code = "payload_too_large"


class UnsafeQuery(ApiError):
    status_code = 400
    code = "unsafe_sql"


class TooManyRequests(ApiError):
    status_code = 429
    code = "too_many_attempts"


class ServiceUnavailable(ApiError):
    status_code = 503
    code = "unavailable"


def _body(detail: str, code: str, errors: Any = None) -> dict[str, Any]:
    return {"detail": detail, "code": code, "request_id": request_id_var.get(), "errors": errors}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.detail, exc.code, exc.extra), headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(str(exc.detail), code),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_body("Request validation failed", "validation_error", errors)
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(status_code=500, content=_body("Internal server error", "internal_error"))
