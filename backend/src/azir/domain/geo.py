"""Geometry value objects, driver-agnostic.

PostGIS owns spatial predicates in production (AGENTS.md rule 11). This module only holds the
value objects and the *policy* (LOD tolerances, bbox guards, payload shape) that both drivers
share, so behaviour cannot drift between them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .enums import Certainty, GeometryKind

GeoJSONGeometry = dict[str, Any]

#: Maximum size of a public bbox query, in degrees. Guards against full-table scans.
MAX_BBOX_DEGREES: float = 25.0
WORLD_BBOX: tuple[float, float, float, float] = (-180.0, -90.0, 180.0, 90.0)


@dataclass(frozen=True, slots=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, raw: str | Sequence[float]) -> BBox:
        values = [float(p) for p in raw.split(",")] if isinstance(raw, str) else [float(v) for v in raw]
        if len(values) != 4:
            raise ValueError("bbox must have exactly 4 numbers: minLon,minLat,maxLon,maxLat")
        min_lon, min_lat, max_lon, max_lat = values
        if min_lon > max_lon or min_lat > max_lat:
            raise ValueError("bbox must be ordered minLon,minLat,maxLon,maxLat")
        for v in values:
            if not -180.0 <= v <= 180.0 and not -90.0 <= v <= 90.0:
                raise ValueError("bbox coordinates out of range")
        if (max_lon - min_lon) > MAX_BBOX_DEGREES or (max_lat - min_lat) > MAX_BBOX_DEGREES:
            raise ValueError(f"bbox is too large (max {MAX_BBOX_DEGREES} degrees per side)")
        return cls(min_lon, min_lat, max_lon, max_lat)

    @property
    def width(self) -> float:
        return self.max_lon - self.min_lon

    @property
    def height(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lon + self.max_lon) / 2, (self.min_lat + self.max_lat) / 2)

    def contains_point(self, lon: float, lat: float) -> bool:
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    def as_polygon_geojson(self) -> GeoJSONGeometry:
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [self.min_lon, self.min_lat],
                    [self.max_lon, self.min_lat],
                    [self.max_lon, self.max_lat],
                    [self.min_lon, self.max_lat],
                    [self.min_lon, self.min_lat],
                ]
            ],
        }


@dataclass(frozen=True, slots=True)
class GeometryRecord:
    """One geometry of one entity, with its own temporal extent and provenance (ADR-0004)."""

    geojson: GeoJSONGeometry
    kind: GeometryKind = GeometryKind.POINT
    certainty: Certainty = Certainty.EXACT
    lod_min_zoom: float = 0.0
    lod_max_zoom: float = 22.0
    year_from: int | None = None
    year_to: int | None = None
    source_id: str | None = None
    note: str | None = None
    note_en: str | None = None
    needs_digitisation: bool = False

    def note_for(self, locale: str) -> str | None:
        """Locale-aware provenance note: the map must be able to explain its own hatching."""
        if locale == "fa":
            return self.note or self.note_en
        return self.note_en or self.note

    @property
    def geometry_type(self) -> str:
        return str(self.geojson.get("type", ""))

    @property
    def is_polygonal(self) -> bool:
        return self.geometry_type in {"Polygon", "MultiPolygon"}

    def valid_at(self, year: int | None) -> bool:
        """Time-varying geometry: a boundary is only a boundary for its own years (ADR-0004)."""
        if year is None:
            return True
        if self.year_from is not None and year < self.year_from:
            return False
        return not (self.year_to is not None and year > self.year_to)

    def representative_point(self) -> tuple[float, float] | None:
        """A lon/lat usable for labels/popups; ``None`` for empty geometries."""
        coords = self.geojson.get("coordinates")
        if not coords:
            return None
        if self.geometry_type == "Point":
            return (float(coords[0]), float(coords[1]))
        flat = list(_flatten(coords))
        lons = [p[0] for p in flat]
        lats = [p[1] for p in flat]
        if not lons:
            return None
        return (sum(lons) / len(lons), sum(lats) / len(lats))


def _flatten(coords: Any) -> Sequence[Any]:
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)):
            return [coords]
        out: list[Any] = []
        for item in coords:
            out.extend(_flatten(item))
        return out
    return []


def is_historical_geometry(record: GeometryRecord) -> bool:
    """Rule: modern administrative boundaries are never mixed into historical layers."""
    return record.kind is not GeometryKind.MODERN_ADMIN
