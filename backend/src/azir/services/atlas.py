"""AtlasService: viewport + time + layers -> map payload.

This is where *all* the historical presentation policy lives (semantic zoom, LOD, field budget,
coverage honesty). MapLibre receives the result and only renders it (AGENTS.md rule 5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..core.config import Settings
from ..domain.geo import BBox
from ..domain.model import EntityRecord, FeatureProjection
from ..domain.semantic_zoom import BANDS, band_for, lod_tolerance, min_rank_for
from ..domain.temporal import TimeWindow
from ..repositories.ports import AtlasQuery, AtlasRepository, TimelineBucket

LAYER_STYLE_TOKENS: dict[str, str] = {
    "places": "layer.places",
    "buildings": "layer.buildings",
    "archaeology": "layer.archaeology",
    "events": "layer.events",
    "battles": "layer.battles",
    "people": "layer.people",
    "political_entities": "layer.political",
    "routes": "layer.routes",
    "modern_borders": "layer.modern",
    "articles": "layer.articles",
}


@dataclass(frozen=True, slots=True)
class FeatureQuery:
    bbox: BBox
    zoom: float
    window: TimeWindow
    layers: tuple[str, ...]
    kinds: tuple[str, ...] = ()
    locale: str = "fa"
    fields: str = "default"
    limit: int = 800
    cursor: str | None = None
    near: tuple[float, float] | None = None
    radius_km: float | None = None
    period_code: str | None = None


@dataclass(slots=True)
class AtlasResult:
    features: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False

    def to_geojson(self) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": self.features, "meta": self.meta}


class AtlasService:
    def __init__(self, repository: AtlasRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings

    # ------------------------------------------------------------------ features

    def features(self, query: FeatureQuery) -> AtlasResult:
        band = band_for(query.zoom)
        min_rank = min_rank_for(query.zoom)
        tolerance = lod_tolerance(query.zoom)

        page = self._repo.features(
            AtlasQuery(
                bbox=query.bbox,
                zoom=query.zoom,
                window=query.window,
                layers=query.layers,
                kinds=query.kinds,
                locale=query.locale,
                fields=query.fields,
                limit=query.limit,
                cursor=_decode_cursor(query.cursor),
                min_rank=min_rank,
                lod_tolerance=tolerance,
                near=query.near,
                radius_km=query.radius_km,
                period_code=query.period_code,
            )
        )

        features: list[dict[str, Any]] = []
        budget = self._settings.payload_budget_bytes
        fields = query.fields
        used = 0
        truncated = False
        year = query.window.representative_year
        for _rank, entity_id in page.rows:
            entity = page.entities[entity_id]
            geometry = page.geometries.get(entity_id)
            projection = FeatureProjection(
                entity=entity,
                geometry=entity.primary_geometry(at_year=year),
                label=entity.display_name(query.locale),
                label_secondary=entity.secondary_name(query.locale),
                locale=query.locale,
                fields=fields,
            )
            feature = projection.to_geojson(geometry_override=geometry)
            size = len(json.dumps(feature, ensure_ascii=False)) + 1
            if used + size > budget:
                # Degrade in the documented order (docs/08 §3): fewer fields first, then drop
                # the least important features. Never silently.
                if fields != "min":
                    fields = "min"
                    projection = FeatureProjection(
                        entity=entity, geometry=entity.primary_geometry(at_year=year),
                        label=projection.label, label_secondary=projection.label_secondary,
                        locale=query.locale, fields="min",
                    )
                    feature = projection.to_geojson(geometry_override=geometry)
                    size = len(json.dumps(feature, ensure_ascii=False)) + 1
                if used + size > budget:
                    truncated = True
                    break
            features.append(feature)
            used += size

        from ..core.pagination import Cursor

        # A cursor is owed whenever more matches exist than we shipped -- either because the
        # payload budget cut us off, or because the caller asked for a small page (ADR-0009).
        next_cursor = None
        has_more = truncated or page.total_estimate > len(features)
        if has_more and features:
            last_rank, last_id = page.rows[len(features) - 1]
            next_cursor = Cursor(rank=last_rank, id=last_id).encode()

        meta: dict[str, Any] = {
            "driver": self._repo.driver_name,
            "zoom_level": band.level.value,
            "zoom_level_label": band.label,
            "min_rank": min_rank,
            "lod_tolerance": tolerance,
            "time": {
                "mode": query.window.mode,
                "from": query.window.year_from,
                "to": query.window.year_to,
                "calendar": query.window.calendar.value,
            },
            "fields": fields,
            "requested": len(page.rows),
            "returned": len(features),
            "total_estimate": page.total_estimate,
            "truncated": truncated,
            "payload_bytes": used,
            "next_cursor": next_cursor,
            "coverage_gaps": page.coverage_gaps,
        }
        return AtlasResult(features=features, meta=meta, truncated=truncated)

    # ------------------------------------------------------------------ timeline

    def timeline(
        self,
        bbox: BBox,
        window: TimeWindow,
        layers: tuple[str, ...],
        locale: str,
        bucket: int | None = None,
    ) -> dict[str, Any]:
        size = bucket or _auto_bucket(window.year_to - window.year_from + 1, self._settings.timeline_buckets)
        buckets: list[TimelineBucket] = self._repo.timeline(bbox, window, size, layers, locale)
        return {
            "data": [
                {
                    "from": b.year_from,
                    "to": b.year_to,
                    "counts": b.counts,
                    "total": b.total,
                    "top_kinds": b.top_kinds,
                    "notable": b.notable,
                }
                for b in buckets
            ],
            "meta": {
                "bucket": size,
                "mode": window.mode,
                "driver": self._repo.driver_name,
                "calendar": window.calendar.value,
            },
        }

    # ------------------------------------------------------------------ layers/meta

    def layers(self, locale: str) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entity in self._repo.all_published():
            counts[entity.layer] = counts.get(entity.layer, 0) + 1
        data = [
            {
                "id": layer,
                "label_fa": _LAYER_LABELS[layer][0],
                "label_en": _LAYER_LABELS[layer][1],
                "label": _LAYER_LABELS[layer][0] if locale == "fa" else _LAYER_LABELS[layer][1],
                "default_on": layer not in {"modern_borders", "people", "articles"},
                "style_token": LAYER_STYLE_TOKENS[layer],
                "count": counts.get(layer, 0),
                "min_zoom": min(b.min_zoom for b in BANDS),
                "max_zoom": max(b.max_zoom for b in BANDS),
            }
            for layer in _LAYER_LABELS
        ]
        return {"data": data, "meta": {"driver": self._repo.driver_name}}

    def zoom_levels(self, locale: str) -> list[dict[str, Any]]:
        return [
            {
                "level": b.level.value,
                "label": b.label,
                "min_zoom": b.min_zoom,
                "max_zoom": b.max_zoom,
                "description": b.description_fa if locale == "fa" else b.description_en,
            }
            for b in BANDS
        ]

    # ------------------------------------------------------------------ context

    def context(
        self,
        *,
        place_id: str | None,
        point: tuple[float, float] | None,
        radius_km: float,
        locale: str,
        limit: int = 60,
    ) -> dict[str, Any]:
        if not place_id and not point:
            from ..core.errors import ValidationError

            raise ValidationError("either place_id or lat+lon is required")
        relations = self._repo.context(
            place_id=place_id, point=point, radius_km=radius_km, locale=locale, limit=limit
        )
        place = self._repo.entity("place", place_id) if place_id else None
        rows = []
        for relation in relations:
            target = (
                self._repo.entity(relation.object_type.value, relation.object_id)
                if relation.object_type and relation.object_id
                else None
            )
            rows.append(
                {
                    "predicate": relation.predicate,
                    "predicate_label": relation.label(locale),
                    "relation_of": "out" if place and relation.object_id != place.id else "in",
                    "entity_type": _entity_type_of(relation, target),
                    "id": relation.object_id,
                    "label": (target.display_name(locale) if target else relation.object_label) or relation.object_value,
                    "slug": target.slug if target else relation.object_slug,
                    "t_from": relation.temporal.year_from if relation.temporal else None,
                    "t_to": relation.temporal.year_to if relation.temporal else None,
                    "t_display": relation.temporal.display_text(locale) if relation.temporal else None,
                    "confidence": relation.confidence.value,
                    "status": relation.status.value,
                    "certainty": relation.certainty,
                    "source_count": len(relation.evidence),
                }
            )
        rows.sort(key=lambda row: (row["t_from"] is None, row["t_from"] or 0))
        return {
            "place": _brief(place, locale) if place else None,
            "radius_km": radius_km,
            "data": rows,
            "meta": {"driver": self._repo.driver_name, "locale": locale},
        }


def _entity_type_of(relation: Any, target: EntityRecord | None) -> str | None:
    if relation.object_type is not None:
        return str(relation.object_type.value)
    if target is not None:
        return target.entity_type.value
    return None


def _brief(entity: EntityRecord | None, locale: str) -> dict[str, Any] | None:
    if entity is None:
        return None
    return {
        "id": entity.id,
        "entity_type": entity.entity_type.value,
        "kind": entity.kind,
        "slug": entity.slug,
        "label": entity.display_name(locale),
        "t_display": entity.temporal_display(locale),
        "rank": entity.rank,
    }


def _decode_cursor(token: str | None) -> Any:
    if not token:
        return None
    from ..core.pagination import Cursor

    return Cursor.decode(token)


def _auto_bucket(span_years: int, available: list[int]) -> int:
    """Pick the smallest configured bucket that keeps the histogram readable (~<=60 bars)."""
    for size in sorted(available):
        if span_years / size <= 60:
            return size
    return max(available)


_LAYER_LABELS: dict[str, tuple[str, str]] = {
    "places": ("مکان‌ها", "Places"),
    "buildings": ("بناها", "Buildings"),
    "archaeology": ("باستان‌شناسی", "Archaeology"),
    "events": ("رویدادها", "Events"),
    "battles": ("نبردها", "Battles"),
    "people": ("شخصیت‌ها", "People"),
    "political_entities": ("حکومت‌ها", "Political entities"),
    "routes": ("مسیرها", "Routes"),
    "modern_borders": ("مرزهای اداری امروزی", "Modern administrative borders"),
    "articles": ("مقالات", "Articles"),
}
