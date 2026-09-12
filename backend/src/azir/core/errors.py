"""Error model: RFC 9457 Problem Details (ADR-0009).

Domain/service code raises these; a single exception handler turns them into responses, so no
router ever has to think about HTTP status codes.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

ERROR_BASE = "https://errors.azir.dev"


class AzirError(Exception):
    """Base class for every error the application raises on purpose."""

    status_code: int = 500
    error_type: str = "internal"
    title: str = "Internal error"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra

    def to_problem(self, request_id: str | None, instance: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": f"{ERROR_BASE}/{self.error_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "request_id": request_id,
        }
        if instance:
            body["instance"] = instance
        if self.extra:
            body.update(self.extra)
        return body


class ValidationError(AzirError):
    status_code = 422
    error_type = "validation"
    title = "Invalid request"


class NotFoundError(AzirError):
    status_code = 404
    error_type = "not-found"
    title = "Not found"


class GoneError(AzirError):
    status_code = 410
    error_type = "gone"
    title = "Archived"


class ConflictError(AzirError):
    status_code = 409
    error_type = "conflict"
    title = "Conflict"


class PermissionDeniedError(AzirError):
    status_code = 403
    error_type = "forbidden"
    title = "Permission denied"


class UnavailableError(AzirError):
    status_code = 503
    error_type = "unavailable"
    title = "Service unavailable"


class RepositoryError(AzirError):
    status_code = 503
    error_type = "repository"
    title = "Data access failed"


def _problem_response(
    request: Request, payload: dict[str, Any], status_code: int
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    if request_id and "request_id" not in payload:
        payload["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=payload, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AzirError)
    async def _azir_error(request: Request, exc: AzirError) -> JSONResponse:
        return _problem_response(
            request, exc.to_problem(getattr(request.state, "request_id", None), request.url.path),
            exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        payload = ValidationError(
            "One or more parameters are invalid",
            errors=[
                {"field": ".".join(str(p) for p in err.get("loc", [])), "message": err.get("msg", "")}
                for err in exc.errors()
            ],
        ).to_problem(getattr(request.state, "request_id", None), request.url.path)
        return _problem_response(request, payload, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        payload = {
            "type": f"{ERROR_BASE}/http",
            "title": exc.detail if isinstance(exc.detail, str) else "HTTP error",
            "status": exc.status_code,
            "detail": exc.detail,
            "request_id": getattr(request.state, "request_id", None),
            "instance": request.url.path,
        }
        return _problem_response(request, payload, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces to clients; the request_id is what support needs.
        request.app.state.logger.exception("unhandled_error", extra={"path": request.url.path})
        payload = {
            "type": f"{ERROR_BASE}/internal",
            "title": "Internal error",
            "status": 500,
            "detail": "An unexpected error occurred. Please quote the request id.",
            "request_id": getattr(request.state, "request_id", None),
            "instance": request.url.path,
        }
        return _problem_response(request, payload, 500)
