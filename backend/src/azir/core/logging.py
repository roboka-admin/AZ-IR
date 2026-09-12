"""Structured JSON logging + request identity middleware.

Every log line and every response carries the same ``request_id``; the same id is written into
``audit_log`` for editorial writes, so a user-reported problem can be traced end to end.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
DRIVER_HEADER = "X-AZIR-Driver"

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    root.addHandler(handler)
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers = [handler]
    logging.getLogger("uvicorn.error").handlers = [handler]
    return logging.getLogger("azir")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Adds request_id/timing and the driver header that must never be silent (ADR-0014)."""

    def __init__(self, app: Any, *, driver_provider: Callable[[], str] | None = None) -> None:
        super().__init__(app)
        self._driver_provider = driver_provider
        self._log = logging.getLogger("azir.request")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            self._log.error(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        if self._driver_provider is not None:
            response.headers[DRIVER_HEADER] = self._driver_provider()
        self._log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "query": str(request.url.query)[:400],
            },
        )
        return response
