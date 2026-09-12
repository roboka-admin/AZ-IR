"""Driver selection + service wiring (composition root).

FastAPI depends on this module only through ``Depends``; nothing else constructs repositories.
"""

from __future__ import annotations

from functools import lru_cache

from ..core.config import get_settings
from ..core.errors import UnavailableError
from ..repositories.fixtures import FixturesRepository
from ..repositories.ports import AtlasRepository


@lru_cache(maxsize=1)
def get_repository() -> AtlasRepository:
    settings = get_settings()
    if settings.db_driver == "fixtures":
        return FixturesRepository(settings.fixtures_dir)
    # PostGIS adapter (production truth). Imported lazily so the fixtures driver needs no DB libs.
    from ..repositories.postgis import PostgisRepository

    if not settings.db_url:
        raise UnavailableError("AZIR_DB_URL is not configured for the postgis driver")
    repository: AtlasRepository = PostgisRepository(settings.db_url, settings)
    return repository


def reset_repository_cache() -> None:
    get_repository.cache_clear()
