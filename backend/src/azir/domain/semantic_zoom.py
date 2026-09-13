"""Semantic zoom: the contract between data and map (docs/04).

Zoom is not just magnification. At each level the *kind and amount* of information changes, and
the server -- never MapLibre -- decides what a level means (AGENTS.md rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import log
from typing import Final

from .enums import EntityType


class ZoomLevel(StrEnum):
    L0_REGION = "L0_region"
    L1_AREA = "L1_area"
    L2_CITY = "L2_city"
    L3_FABRIC = "L3_fabric"
    L4_MONUMENT = "L4_monument"


@dataclass(frozen=True, slots=True)
class ZoomBand:
    level: ZoomLevel
    min_zoom: float
    max_zoom: float
    lod_tolerance: float      # simplification tolerance in degrees
    min_rank: float           # features with rank < min_rank are not returned
    label: str
    description_fa: str
    description_en: str


#: Keep in sync with docs/04-spatial-and-semantic-zoom.md §2-§3 (golden-tested).
BANDS: Final[tuple[ZoomBand, ...]] = (
    ZoomBand(ZoomLevel.L0_REGION, 0.0, 6.0, 0.05, 70.0, "region",
             "کل آذربایجان: فقط مهم‌ترین مکان‌ها و حکومت‌ها",
             "Whole Azerbaijan: only the most significant places and polities"),
    ZoomBand(ZoomLevel.L1_AREA, 6.0, 9.0, 0.01, 45.0, "area",
             "منطقه: شهرها، محوطه‌های مهم، رویدادهای بزرگ",
             "Area: cities, major sites, large events"),
    ZoomBand(ZoomLevel.L2_CITY, 9.0, 12.0, 0.001, 25.0, "city",
             "شهر: بناها، رویدادها، شخصیت‌ها",
             "City: buildings, events, people"),
    ZoomBand(ZoomLevel.L3_FABRIC, 12.0, 15.0, 0.0, 8.0, "fabric",
             "بافت تاریخی: محله‌ها، همهٔ بناها، مسیرها",
             "Historic fabric: neighbourhoods, every building, routes"),
    ZoomBand(ZoomLevel.L4_MONUMENT, 15.0, 22.0, 0.0, 0.0, "monument",
             "بنا: footprint دقیق، اجزا، تغییرات تاریخی",
             "Monument: exact footprint, components, historical changes"),
)


def band_for(zoom: float) -> ZoomBand:
    for band in BANDS:
        if band.min_zoom <= zoom < band.max_zoom:
            return band
    return BANDS[-1]


def level_for(zoom: float) -> ZoomLevel:
    return band_for(zoom).level


def lod_tolerance(zoom: float) -> float:
    return band_for(zoom).lod_tolerance


def min_rank_for(zoom: float, *, density_penalty: float = 0.0) -> float:
    """Rank threshold for a viewport.

    ``density_penalty`` lets the service raise the bar when a viewport is crowded, which is how
    we keep the payload inside its budget without lying about what exists (docs/08 §3).
    """
    return max(0.0, band_for(zoom).min_rank + density_penalty)


# --------------------------------------------------------------------------- ranking

#: Editorial weight per kind. Everything else is derived from evidence (sources/assertions).
KIND_WEIGHT: Final[dict[str, float]] = {
    "historical_region": 1.00,
    "region": 0.95,
    "city": 0.90,
    "battlefield": 0.80,
    "archaeological_site": 0.78,
    "town": 0.62,
    "castle": 0.60,
    "mosque": 0.55,
    "mausoleum": 0.58,
    "caravanserai": 0.45,
    "bazaar": 0.55,
    "bridge": 0.40,
    "route": 0.50,
    "river": 0.45,
    "lake": 0.50,
    "mountain": 0.45,
    "district": 0.35,
    "neighborhood": 0.30,
    "village": 0.25,
    "site": 0.50,
    "building": 0.55,
    "cemetery": 0.30,
    "province_modern": 0.20,
}

#: Weight used when the kind is not in the table (e.g. persons/events/polities).
DEFAULT_KIND_WEIGHT: Final[float] = 0.5

#: Saturation constants for the log-scaled evidence terms (K in norm(x)=log(1+x)/log(1+K)).
_SATURATION: Final[dict[str, int]] = {
    "source_count": 12,
    "assertion_count": 20,
    "article_count": 5,
}

#: How much the number of distinct historical periods covered matters.
_PERIOD_COVERAGE_SATURATION: Final[int] = 6


def _norm(value: float, key: str) -> float:
    k = _SATURATION[key]
    return min(1.0, log(1.0 + max(0.0, value)) / log(1.0 + k))


def compute_rank(
    *,
    importance: float,
    kind: str | None,
    source_count: int,
    assertion_count: int,
    article_count: int,
    period_coverage: int = 0,
    kind_weight: float | None = None,
) -> float:
    """Transparent, auditable ranking used for semantic zoom and payload degrade.

    Formula (docs/04 §4)::

        rank = 35*importance + 20*kind_weight + 15*norm(sources)
             + 12*norm(assertions) + 10*norm(articles) + 8*norm(period_coverage)

    ``importance`` is the only human input; it is stored, audited and never silently derived.
    """
    weight = kind_weight if kind_weight is not None else KIND_WEIGHT.get(kind or "", DEFAULT_KIND_WEIGHT)
    rank = (
        35.0 * max(0.0, min(1.0, importance))
        + 20.0 * max(0.0, min(1.0, weight))
        + 15.0 * _norm(source_count, "source_count")
        + 12.0 * _norm(assertion_count, "assertion_count")
        + 10.0 * _norm(article_count, "article_count")
        + 8.0 * min(1.0, period_coverage / _PERIOD_COVERAGE_SATURATION)
    )
    return round(max(0.0, min(100.0, rank)), 2)


def min_zoom_for(rank: float, entity_type: EntityType, kind: str | None) -> float:
    """Smallest zoom at which a feature may appear. Inverse of the ``min_rank`` ladder."""
    for band in BANDS:  # L0 -> L4: the first band the rank qualifies for is the widest
        if rank >= band.min_rank:
            # People and small events are noise at region level even when important.
            if entity_type is EntityType.PERSON:
                return max(band.min_zoom, BANDS[2].min_zoom)
            if entity_type is EntityType.EVENT and kind in {"earthquake", "epidemic"}:
                return max(band.min_zoom, BANDS[1].min_zoom)
            return band.min_zoom
    return BANDS[-1].min_zoom


def max_zoom_for(kind: str | None) -> float:
    if kind in {"region", "historical_region", "province_modern", "lake", "mountain"}:
        return 12.0
    if kind in {"city", "town", "river", "route"}:
        return 16.0
    return 22.0


def layer_for(entity_type: EntityType, kind: str | None) -> str:
    """Map an entity onto exactly one primary layer (docs/02 §5)."""
    if entity_type is EntityType.EVENT:
        return "battles" if kind in {"battle", "siege", "sack"} else "events"
    if entity_type is EntityType.PERSON:
        return "people"
    if entity_type is EntityType.POLITICAL_ENTITY:
        return "political_entities"
    if entity_type is EntityType.ARTICLE:
        return "articles"
    if kind in {"province_modern", "county_modern", "district_modern", "country_modern"}:
        # Modern administration is its own layer and is never mixed with historical geometry
        # (AGENTS.md rule 8 / ADR-0013).
        return "modern_borders"
    if kind in {"archaeological_site"}:
        return "archaeology"
    if kind in {"route"}:
        return "routes"
    if kind in {
        "building", "mosque", "madrasa", "caravanserai", "bazaar", "bridge", "castle",
        "citadel", "mausoleum", "cemetery",
    }:
        return "buildings"
    return "places"



@dataclass(frozen=True, slots=True)
class Presentation:
    """The four derived columns every write must set: rank, layer and the zoom band."""

    rank: float
    layer: str
    min_zoom: float
    max_zoom: float


def presentation_for(
    *,
    importance: float,
    kind: str | None,
    entity_type: EntityType,
    source_count: int = 0,
    assertion_count: int = 0,
    article_count: int = 0,
    period_coverage: int = 0,
    kind_weight: float | None = None,
) -> Presentation:
    """One place that turns editorial input into presentation columns.

    Both write adapters call this, so a draft created through the panel is ranked exactly like a
    seeded record -- presentation is derived, never hand-typed (AGENTS.md rule 10).
    """
    rank = compute_rank(
        importance=importance,
        kind=kind,
        source_count=source_count,
        assertion_count=assertion_count,
        article_count=article_count,
        period_coverage=period_coverage,
        kind_weight=kind_weight,
    )
    return Presentation(
        rank=rank,
        layer=layer_for(entity_type, kind),
        min_zoom=min_zoom_for(rank, entity_type, kind),
        max_zoom=max_zoom_for(kind),
    )
