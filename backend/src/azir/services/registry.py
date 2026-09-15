"""Driver selection + service wiring (composition root).

FastAPI depends on this module only through ``Depends``; nothing else constructs repositories.
"""

from __future__ import annotations

from functools import lru_cache

from ..core.config import get_settings
from ..core.errors import UnavailableError
from ..repositories.fixtures import FixturesRepository
from ..repositories.ports import AtlasRepository, EditorialRepository


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


@lru_cache(maxsize=1)
def get_editorial_repository() -> EditorialRepository:
    """The write side. Both adapters implement it, so this mirrors :func:`get_repository`.

    The two caches return the *same object* for a given driver: reads and writes must share one
    connection pool (PostGIS) and one in-memory corpus (fixtures), otherwise a draft published
    through the panel would not appear on the map until the process restarted.
    """
    repository = get_repository()
    if not isinstance(repository, EditorialRepository):  # pragma: no cover - both adapters implement it
        raise UnavailableError(f"the {repository.driver_name} driver has no write side")
    return repository


def reset_repository_cache() -> None:
    get_repository.cache_clear()
    get_editorial_repository.cache_clear()
