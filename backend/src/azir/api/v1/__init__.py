"""Version 1 API router. Routers hold no business logic (AGENTS.md rule 2)."""

from __future__ import annotations

from fastapi import APIRouter

from . import atlas, catalog, meta

api_v1 = APIRouter()
api_v1.include_router(meta.router)
api_v1.include_router(meta.atlas_layers_router)
api_v1.include_router(atlas.router)
api_v1.include_router(catalog.router)

__all__ = ["api_v1"]
