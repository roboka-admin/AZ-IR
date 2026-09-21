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
    all_time: bool = False


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
                label=entity.display_name(query.locale, year),
                label_secondary=entity.secondary_name(query.locale, year),
                locale=query.locale,
                fields=fields,
                style_color=style_color_for(entity.id) if entity.layer == "political_entities" else None,
            )
            feature = projection.to_geojson(geometry_override=geometry)
            size = len(json.dumps(feature, ensure_ascii=False)) + 1
            if used + size > budget:
                if fields != "min":
                    fields = "min"
                    projection = FeatureProjection(
                        entity=entity, geometry=entity.primary_geometry(at_year=year),
                        label=projection.label, label_secondary=projection.label_secondary,
                        locale=query.locale, fields="min",
                        style_color=projection.style_color,
                    )
                    feature = projection.to_geojson(geometry_override=geometry)
                    size = len(json.dumps(feature, ensure_ascii=False)) + 1
                if used + size > budget:
                    truncated = True
                    break
            features.append(feature)
            used += size

        coverage_gaps = list(page.coverage_gaps)
        main_features_count = len(features)
        if "political_entities" in query.layers:
            # Capital markers are derived from the polity entities already in the page.
            # They must not cause the response to exceed the caller's requested limit: a
            # limit=3 viewport must still be 3 features, not 3+capitals, otherwise cursor
            # pagination breaks (test_limit_and_cursor_pagination).
            remaining = query.limit - len(features)
            if remaining > 0:
                capital_features = self._capital_markers(
                    page=page,
                    query=query,
                    representative_year=year,
                    used_budget=used,
                    budget=budget,
                    coverage_gaps=coverage_gaps,
                )
                for feature in capital_features[:remaining]:
                    size = len(json.dumps(feature, ensure_ascii=False)) + 1
                    if used + size > budget:
                        truncated = True
                        break
                    features.append(feature)
                    used += size

        from ..core.pagination import Cursor

        next_cursor = None
        # Pagination is over the repository page (main features), not over derived capital
        # markers, otherwise a polity and its capital would be counted as two separate
        # pagination steps and cursors would skip or duplicate.
        has_more = truncated or page.total_estimate > main_features_count
        if has_more and features:
            main_count = min(len(page.rows), main_features_count)
            if main_count > 0:
                last_rank, last_id = page.rows[main_count - 1]
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
            "coverage_gaps": coverage_gaps,
        }
        return AtlasResult(features=features, meta=meta, truncated=truncated)

    def _capital_markers(
        self,
        *,
        page: Any,
        query: FeatureQuery,
        representative_year: int,
        used_budget: int,
        budget: int,
        coverage_gaps: list[str],
    ) -> list[dict[str, Any]]:
        """Derive one point marker per active capital for every polity in the page."""
        from ..domain.enums import EntityType

        markers: list[dict[str, Any]] = []
        polity_entities = [
            entity for entity in page.entities.values()
            if entity.layer == "political_entities"
        ]
        for polity in polity_entities:
            cap_rels = [
                rel
                for rel in polity.relationships
                if rel.predicate == "capital"
                and rel.object_type == EntityType.PLACE
                and rel.object_id
                and rel.status.value in {"accepted", "disputed", "proposed"}
            ]
            if not cap_rels:
                continue
            active: list[Any] = []
            if query.all_time:
                active = cap_rels
            else:
                window_interval = query.window.as_interval()
                for rel in cap_rels:
                    if rel.temporal is None:
                        active.append(rel)
                    else:
                        if rel.temporal.overlaps(window_interval):
                            active.append(rel)
            if not active:
                continue
            by_place: dict[str, list[Any]] = {}
            for rel in active:
                by_place.setdefault(rel.object_id, []).append(rel)
            for place_id, rels in by_place.items():
                place_entity = self._repo.entity("place", place_id)
                if place_entity is None:
                    coverage_gaps.append(f"political_entity:{polity.id}:capital:{place_id}:missing-place")
                    continue
                capital_year = representative_year
                t_from: int | None = None
                t_to: int | None = None
                t_display: str | None = None
                if query.all_time:
                    years = [(r.temporal.year_from, r.temporal.year_to) for r in rels if r.temporal]
                    if years:
                        t_from = min(f for f, _ in years)
                        t_to = max(t for _, t in years)
                        capital_year = (t_from + t_to) // 2
                    displays = [r.temporal.display_text(query.locale) for r in rels if r.temporal]
                    t_display = displays[0] if displays else None
                else:
                    chosen = None
                    for rel in rels:
                        if rel.temporal and rel.temporal.contains_year(representative_year):
                            chosen = rel
                            break
                    if chosen is None:
                        chosen = rels[0]
                    if chosen.temporal:
                        t_from = chosen.temporal.year_from
                        t_to = chosen.temporal.year_to
                        t_display = chosen.temporal.display_text(query.locale)
                        capital_year = int(chosen.temporal.midpoint)
                    else:
                        t_from = polity.temporal.year_from if polity.temporal else None
                        t_to = polity.temporal.year_to if polity.temporal else None
                geom_record = place_entity.primary_geometry(at_year=capital_year)
                if geom_record is None:
                    coverage_gaps.append(f"political_entity:{polity.id}:capital:{place_id}:no-geometry")
                    continue
                point = geom_record.representative_point()
                if point is None:
                    try:
                        coords = geom_record.geojson.get("coordinates")
                        if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                            point = (float(coords[0]), float(coords[1]))
                    except Exception:
                        point = None
                if point is None:
                    coverage_gaps.append(f"political_entity:{polity.id}:capital:{place_id}:no-point")
                    continue
                label = polity.display_name(query.locale, capital_year)
                capital_label = place_entity.display_name(query.locale, capital_year)
                is_rtl = any(
                    "\u0600" <= ch <= "\u06ff" or "\u0590" <= ch <= "\u05ff" or "\u0700" <= ch <= "\u074f"
                    for ch in label
                )
                feature_id = f"{polity.id}__capital__{place_id}"
                if query.all_time:
                    feature_id = f"{polity.id}__capital__{place_id}__all"
                else:
                    feature_id = f"{polity.id}__capital__{place_id}__{t_from or 0}"
                props: dict[str, Any] = {
                    "id": polity.id,
                    "entity_type": "political_entity",
                    "kind": "capital",
                    "layer": "political_entities",
                    "rank": polity.rank,
                    "min_zoom": 0,
                    "max_zoom": 22,
                    "label": label,
                    "label_secondary": capital_label,
                    "label_anchor": True,
                    "style_color": style_color_for(polity.id),
                    "is_capital": True,
                    "capital_place_id": place_id,
                    "capital_label": capital_label,
                    "dir": "rtl" if is_rtl else "ltr",
                    "status": polity.status.value,
                    "certainty": rels[0].confidence.value if rels else None,
                    "geometry_kind": "capital",
                    "t_from": t_from,
                    "t_to": t_to,
                    "t_display": t_display or polity.temporal_display(query.locale),
                    "slug": polity.slug,
                    "has_disagreements": polity.has_disagreements,
                    "source_count": polity.counts.sources,
                    "href": f"/api/v1/entities/political_entity/{polity.slug or polity.id}",
                }
                if rels and rels[0].temporal:
                    props["t_precision"] = rels[0].temporal.precision.value
                    props["confidence"] = rels[0].temporal.confidence.value
                geometry = {"type": "Point", "coordinates": [point[0], point[1]]}
                markers.append(
                    {"type": "Feature", "id": feature_id, "geometry": geometry, "properties": props}
                )
        return markers

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
    for size in sorted(available):
        if span_years / size <= 60:
            return size
    return max(available)


_POLITY_COLORS = ("#7b2cbf", "#d1495b", "#00798c", "#edae49", "#30638e", "#6a994e", "#bc6c25", "#8f2d56")


def style_color_for(entity_id: str) -> str:
    import hashlib

    digest = hashlib.blake2b(entity_id.encode("utf-8"), digest_size=2).digest()
    return _POLITY_COLORS[int.from_bytes(digest, "big") % len(_POLITY_COLORS)]


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
