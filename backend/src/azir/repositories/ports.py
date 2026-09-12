"""Repository ports.

These protocols are the *only* thing services may depend on. Two adapters implement them
(``postgis`` and ``fixtures``, ADR-0014) and the same contract test-suite runs against both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.pagination import Cursor
from ..domain.enums import EntityType
from ..domain.geo import BBox
from ..domain.model import EntityRecord, Relationship
from ..domain.temporal import TimeWindow


@dataclass(frozen=True, slots=True)
class AtlasQuery:
    """A fully-resolved map query. Routers never build this; the service does."""

    bbox: BBox
    zoom: float
    window: TimeWindow
    layers: tuple[str, ...]
    kinds: tuple[str, ...] = ()
    locale: str = "fa"
    fields: str = "default"
    limit: int = 800
    cursor: Cursor | None = None
    min_rank: float = 0.0
    lod_tolerance: float = 0.0
    include_unpublished: bool = False
    near: tuple[float, float] | None = None
    radius_km: float | None = None
    period_code: str | None = None

    def cache_key(self) -> str:
        b = self.bbox
        return "|".join(
            [
                f"{b.min_lon:.2f},{b.min_lat:.2f},{b.max_lon:.2f},{b.max_lat:.2f}",
                f"z{round(self.zoom * 2) / 2:.1f}",
                f"t{self.window.year_from}:{self.window.year_to}:{self.window.mode}",
                ",".join(self.layers) or "-",
                ",".join(self.kinds) or "-",
                self.locale,
                self.fields,
            ]
        )


@dataclass(frozen=True, slots=True)
class FeaturePage:
    rows: list[tuple[float, str]]           # (rank, id) ordered pairs used for pagination
    entities: dict[str, EntityRecord]
    geometries: dict[str, Any]               # id -> GeoJSON geometry (already simplified)
    total_estimate: int
    coverage_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchHit:
    entity: EntityRecord
    score: float
    matched_on: str = "name"
    snippet: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineBucket:
    year_from: int
    year_to: int
    counts: dict[str, int]
    total: int
    top_kinds: list[str]
    notable: list[dict[str, Any]]


@runtime_checkable
class AtlasRepository(Protocol):
    """Data access contract for the whole atlas read-model."""

    driver_name: str

    def features(self, query: AtlasQuery) -> FeaturePage: ...

    def timeline(
        self, bbox: BBox, window: TimeWindow, bucket: int, layers: tuple[str, ...], locale: str
    ) -> list[TimelineBucket]: ...

    def entity(self, entity_type: str, id_or_slug: str) -> EntityRecord | None: ...

    def related(
        self, entity_type: str, entity_id: str, *, depth: int = 1, limit: int = 50
    ) -> list[Relationship]: ...

    def list_entities(
        self,
        entity_type: EntityType,
        *,
        status: str | None = "published",
        locale: str = "fa",
        limit: int = 50,
        window: TimeWindow | None = None,
    ) -> list[EntityRecord]: ...

    def articles(self, *, locale: str = "fa", limit: int = 50, entity_id: str | None = None) -> list[EntityRecord]: ...

    def search(
        self,
        term: str,
        *,
        types: tuple[str, ...],
        locale: str = "fa",
        limit: int = 20,
        near: tuple[float, float] | None = None,
        radius_km: float | None = None,
        window: TimeWindow | None = None,
    ) -> list[SearchHit]: ...

    def context(
        self, *, place_id: str | None, point: tuple[float, float] | None, radius_km: float,
        locale: str, limit: int,
    ) -> list[Relationship]: ...

    def stats(self) -> dict[str, Any]: ...

    def all_published(self) -> list[EntityRecord]: ...
