"""Version 1 API router. Routers hold no business logic (AGENTS.md rule 2)."""

from __future__ import annotations

from fastapi import APIRouter

from . import atlas, auth, catalog, editorial, meta

api_v1 = APIRouter()
# Public, anonymous, read-only: the published corpus is open data (ADR-0001).
api_v1.include_router(meta.router)
api_v1.include_router(meta.atlas_layers_router)
api_v1.include_router(atlas.router)
api_v1.include_router(catalog.router)
# Internal editorial panel (ADR-0010): session cookies, CSRF, role checks and an audit trail on
# every write. Switched off in production unless AZIR_EDITORIAL_ENABLED says otherwise.
api_v1.include_router(auth.router)
api_v1.include_router(editorial.router)

__all__ = ["api_v1"]
