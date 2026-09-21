"""Application factory + entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.v1 import api_v1
from .api.v1.health import router as health_router
from .core.config import get_settings
from .core.errors import install_error_handlers
from .core.logging import RequestContextMiddleware, configure_logging
from .services.registry import get_repository


def create_app(settings: Any | None = None) -> FastAPI:
    settings = settings or get_settings()
    logger = configure_logging(
        "DEBUG" if settings.debug else settings.log_level,
        json_output=not settings.debug,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        repository = get_repository()
        logger.info(
            "app_started",
            extra={
                "driver": repository.driver_name,
                "env": settings.env,
                "version": __version__,
                "locales": settings.supported_locales,
            },
        )
        yield

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Interactive Historical Atlas of Iranian Azerbaijan. "
            "Read-only public API v1: map features, timeline, entities, articles, sources, search. "
            "See docs/05-api-contract.md."
        ),
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.logger = logger

    app.add_middleware(
        RequestContextMiddleware, driver_provider=lambda: get_repository().driver_name
    )
    # Credentialed on purpose: the editorial panel authenticates with an HttpOnly session cookie
    # (ADR-0010), which a wildcard origin could never carry. Origins therefore come from settings,
    # and the write verbs are allowed only for those origins. The public corpus stays open data.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "OPTIONS", "POST", "PATCH", "DELETE"],
        allow_headers=["*", "X-CSRF-Token", "Content-Type"],
        expose_headers=["X-Request-Id", "X-AZIR-Driver", "ETag", "Retry-After"],
        max_age=600,
    )
    install_error_handlers(app)

    app.include_router(health_router)
    app.include_router(api_v1, prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "driver": get_repository().driver_name,
            "docs": "/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    settings = get_settings()
    uvicorn.run("azir.main:app", host="0.0.0.0", port=8000, reload=settings.debug)


if __name__ == "__main__":  # pragma: no cover
    main()
