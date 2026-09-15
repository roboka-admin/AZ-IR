"""Entity / article / source / search endpoints (docs/05 §7-§11)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...core.errors import NotFoundError
from ...domain.enums import EntityType
from ...domain.temporal import TimeWindow
from ..deps import ArticleDep, EntityDep, LocaleDep, RepositoryDep, SearchDep

router = APIRouter(tags=["catalog"])

_ENTITY_TYPES = tuple(e.value for e in EntityType)


@router.get("/entities/{entity_type}", summary="List entities of a type (rank ordered)")
def list_entities(
    service: EntityDep,
    locale: LocaleDep,
    entity_type: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    t: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    _guard_type(entity_type)
    window = TimeWindow.at(t) if t is not None else None
    return service.list_entities(entity_type, locale, limit, window)


@router.get("/entities/{entity_type}/{id_or_slug}", summary="Entity detail with graph + sources")
def get_entity(
    service: EntityDep, locale: LocaleDep, entity_type: str, id_or_slug: str
) -> dict[str, Any]:
    _guard_type(entity_type)
    return service.get(entity_type, id_or_slug, locale)


@router.get("/entities/{entity_type}/{id_or_slug}/related", summary="Graph neighbourhood")
def related_entities(
    service: EntityDep,
    locale: LocaleDep,
    entity_type: str,
    id_or_slug: str,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    _guard_type(entity_type)
    return service.related(entity_type, id_or_slug, locale, depth, limit)


@router.get("/articles", summary="List articles")
def list_articles(
    service: ArticleDep, locale: LocaleDep, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> dict[str, Any]:
    return service.list_articles(locale, limit)


@router.get("/articles/{id_or_slug}", summary="Article with entities and map_state")
def get_article(service: ArticleDep, locale: LocaleDep, id_or_slug: str) -> dict[str, Any]:
    return service.get(id_or_slug, locale)


@router.get("/sources", summary="List bibliographic sources")
def list_sources(
    repo: RepositoryDep, locale: LocaleDep, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> dict[str, Any]:
    from .entity_briefs import source_list

    return source_list(repo, locale, limit)


@router.get("/search", summary="Text search across entities and articles")
def search(
    service: SearchDep,
    locale: LocaleDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    types: Annotated[str | None, Query(description="Comma separated entity types")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    near: Annotated[str | None, Query(description="lon,lat")] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=500)] = None,
    t: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    from .atlas import _parse_point

    requested = tuple(t_.strip() for t_ in types.split(",") if t_.strip()) if types else _ENTITY_TYPES
    unknown = [item for item in requested if item not in _ENTITY_TYPES]
    if unknown:
        raise NotFoundError(f"unknown entity type(s): {', '.join(unknown)}", known=list(_ENTITY_TYPES))
    window = TimeWindow.at(t) if t is not None else None
    return service.search(
        q, types=requested, locale=locale, limit=limit, near=_parse_point(near),
        radius_km=radius_km, window=window,
    )


def _guard_type(entity_type: str) -> None:
    if entity_type not in _ENTITY_TYPES:
        raise NotFoundError(f"unknown entity type {entity_type!r}", known=list(_ENTITY_TYPES))
