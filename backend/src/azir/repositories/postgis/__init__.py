"""PostGIS driver: the production truth for the atlas read model.

Importing this package must never require a database (the API boots on the fixtures driver without
one), so engine creation is a function call, not an import side effect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .editorial import EDITABLE_TYPES, PostgisEditorialMixin
from .repository import NARRATIVE_TYPES, PostgisRepository
from .schema import METADATA
from .seeder import SeedReport, seed_database

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine

    from ...core.config import Settings


def build_engine(db_url: str, settings: Settings | None = None) -> Engine:
    """Create an engine without importing psycopg2 at module scope."""
    from sqlalchemy import create_engine

    from ...core.config import get_settings

    resolved = settings or get_settings()
    return create_engine(
        db_url,
        future=True,
        pool_pre_ping=True,
        pool_size=resolved.db_pool_size,
        max_overflow=resolved.db_max_overflow,
        connect_args={"options": f"-c statement_timeout={resolved.db_statement_timeout_ms}"},
    )


__all__ = [
    "EDITABLE_TYPES",
    "METADATA",
    "NARRATIVE_TYPES",
    "PostgisEditorialMixin",
    "PostgisRepository",
    "SeedReport",
    "build_engine",
    "seed_database",
]
