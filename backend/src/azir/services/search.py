"""SearchService + AtlasQueryService (structured queries, not text search)."""

from __future__ import annotations

from typing import Any

from ..core.config import Settings
from ..domain.geo import BBox
from ..domain.temporal import TimeWindow
from ..repositories.ports import AtlasRepository
from .atlas import AtlasService, FeatureQuery
from .entity import _brief


class SearchService:
    def __init__(self, repository: AtlasRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings

    def search(
        self,
        term: str,
        *,
        types: tuple[str, ...],
        locale: str,
        limit: int,
        near: tuple[float, float] | None = None,
        radius_km: float | None = None,
        window: TimeWindow | None = None,
    ) -> dict[str, Any]:
        from ..domain.text import normalize_fa

        hits = self._repo.search(
            term, types=types, locale=locale, limit=limit, near=near, radius_km=radius_km, window=window
        )
        return {
            "data": [
                {
                    **_brief(hit.entity, locale),
                    "score": hit.score,
                    "matched_on": hit.matched_on,
                    "snippet": hit.snippet,
                }
                for hit in hits
            ],
            "page": {"limit": limit, "next_cursor": None, "total_estimate": len(hits)},
            "meta": {
                "normalized_query": normalize_fa(term),
                "driver": self._repo.driver_name,
                "locale": locale,
            },
        }

    def atlas_query(
        self,
        atlas: AtlasService,
        *,
        kinds: tuple[str, ...],
        bbox: BBox,
        zoom: float,
        window: TimeWindow,
        locale: str,
        limit: int,
        near: tuple[float, float] | None = None,
        radius_km: float | None = None,
        period_code: str | None = None,
    ) -> dict[str, Any]:
        """Structured query, e.g. "Safavid buildings within 20 km of Ardabil"."""
        layers = tuple(
            {
                "building": "buildings", "mosque": "buildings", "mausoleum": "buildings",
                "castle": "buildings", "citadel": "buildings", "bazaar": "buildings",
                "archaeological_site": "archaeology", "city": "places", "town": "places",
                "route": "routes", "battle": "battles", "siege": "battles",
            }.get(kind, "places")
            for kind in kinds
        ) if kinds else ("places", "buildings", "archaeology", "events", "battles", "routes")
        result = atlas.features(
            FeatureQuery(
                bbox=bbox, zoom=zoom, window=window, layers=layers, kinds=kinds, locale=locale,
                limit=limit, near=near, radius_km=radius_km, period_code=period_code,
            )
        )
        result.meta["applied_filters"] = {
            "kinds": list(kinds),
            "period": period_code,
            "near": list(near) if near else None,
            "radius_km": radius_km,
            "time": {"from": window.year_from, "to": window.year_to, "mode": window.mode},
        }
        return result.to_geojson()
