"""``/api/v1/meta`` -- everything the frontend needs to boot without hard-coding data (rule 17)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...domain.enums import LOCALE_DIRECTION, EntityType
from ...domain.semantic_zoom import KIND_WEIGHT
from ...services.atlas import style_color_for
from ..deps import AtlasDep, LocaleDep, RepositoryDep, SettingsDep

router = APIRouter(prefix="/meta", tags=["meta"])

BORDERS_DISCLAIMER_FA = (
    "مرزهای تاریخی بازسازی پژوهشی‌اند و با مرزهای سیاسی امروزی مطابقت ندارند."
)
BORDERS_DISCLAIMER_EN = (
    "Historical boundaries are scholarly reconstructions and do not correspond to modern political borders."
)


@router.get("", summary="Bootstrap metadata for the client")
def meta(
    settings: SettingsDep, repo: RepositoryDep, atlas: AtlasDep, locale: LocaleDep
) -> dict[str, Any]:
    from ...domain.enums import Calendar, Precision

    stats = repo.stats()
    return {
        "api_version": settings.api_version,
        "app_name": settings.app_name,
        "driver": repo.driver_name,
        "env": settings.env,
        "locales": [
            {
                "code": code,
                "dir": LOCALE_DIRECTION.get(code, "ltr"),
                "default": code == settings.default_locale,
            }
            for code in settings.supported_locales
        ],
        "study_area": {
            "name": settings.study_area_name_fa if locale == "fa" else settings.study_area_name_en,
            "name_fa": settings.study_area_name_fa,
            "name_en": settings.study_area_name_en,
            "bbox": list(settings.study_area_bbox),
            "center": list(settings.study_area_center),
            "default_zoom": settings.study_area_default_zoom,
        },
        "timeline": {
            "floor": settings.timeline_floor,
            "ceil": settings.timeline_ceil,
            "default_year": settings.timeline_default_year,
            "buckets": settings.timeline_buckets,
            "calendars": [c.value for c in Calendar],
            "precisions": [p.value for p in Precision],
            "default_calendar": Calendar.GREGORIAN_PROLEPTIC.value,
        },
        "layers": atlas.layers(locale)["data"],
        "zoom_levels": atlas.zoom_levels(locale),
        "periods": _periods(repo, locale),
        "political_entities": _political_entities(repo, locale),
        "coverage": stats,
        "disclaimer": {
            "borders": BORDERS_DISCLAIMER_FA if locale == "fa" else BORDERS_DISCLAIMER_EN,
        },
        "kind_weights": KIND_WEIGHT,
        "limits": {
            "max_limit": settings.max_limit,
            "default_limit": settings.default_limit,
            "payload_budget_bytes": settings.payload_budget_bytes,
        },
        "license": {"code": "MIT", "data": "CC BY-SA 4.0"},
    }


def _periods(repo: Any, locale: str) -> list[dict[str, Any]]:
    """Periods come from the taxonomy/period scheme, never from the frontend (ADR-0012)."""
    rows: list[dict[str, Any]] = []
    for record in repo.list_entities(EntityType.PERIOD, status=None, limit=100):
        rows.append(
            {
                "id": record.id,
                "code": record.slug or record.id,
                "label": record.display_name(locale),
                "year_from": record.temporal.year_from if record.temporal else None,
                "year_to": record.temporal.year_to if record.temporal else None,
            }
        )
    rows.sort(key=lambda r: (r["year_from"] is None, r["year_from"] or 0))
    return rows


def _political_entities(repo: Any, locale: str) -> list[dict[str, Any]]:
    """Published, spatially modelled polities for the map's data-driven colour key.

    Each entry now also carries its sourced capital history (if any) so the legend can
    explain that a polity's name on the map is placed at its capital, not at a polygon
    centre, and so the frontend never hard-codes a capital (AGENTS.md rule 17).
    """
    rows = []
    for record in repo.all_published():
        if record.entity_type is not EntityType.POLITICAL_ENTITY or not record.has_geometry:
            continue
        geometry = record.primary_geometry()
        # Resolve capital assertions for this polity
        capitals: list[dict[str, Any]] = []
        for rel in record.relationships:
            if rel.predicate != "capital" or not rel.object_id:
                continue
            if rel.status.value not in {"accepted", "disputed", "proposed"}:
                continue
            place = repo.entity("place", rel.object_id) if rel.object_id else None
            place_label = place.display_name(locale) if place else rel.object_label or rel.object_id
            capitals.append(
                {
                    "place_id": rel.object_id,
                    "label": place_label,
                    "t_from": rel.temporal.year_from if rel.temporal else None,
                    "t_to": rel.temporal.year_to if rel.temporal else None,
                    "t_display": rel.temporal.display_text(locale) if rel.temporal else None,
                    "confidence": rel.confidence.value,
                    "status": rel.status.value,
                }
            )
        # Sort capitals by start year for stable presentation
        capitals.sort(key=lambda c: (c["t_from"] is None, c["t_from"] or 0, c["label"]))
        rows.append(
            {
                "id": record.id,
                "label": record.display_name(locale),
                "color": style_color_for(record.id),
                "t_display": record.temporal_display(locale),
                "certainty": geometry.certainty.value if geometry else None,
                "capitals": capitals,
            }
        )
    return sorted(rows, key=lambda row: row["label"])


atlas_layers_router = APIRouter(prefix="/atlas", tags=["atlas"])


@atlas_layers_router.get("/layers", summary="Available map layers with counts")
def layers(atlas: AtlasDep, locale: LocaleDep) -> dict[str, Any]:
    return atlas.layers(locale)


@atlas_layers_router.get("/zoom-levels", summary="Semantic zoom contract")
def zoom_levels(atlas: AtlasDep, locale: LocaleDep) -> dict[str, Any]:
    return {"data": atlas.zoom_levels(locale)}



