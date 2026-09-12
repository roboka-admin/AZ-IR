"""Atlas endpoints: the map's data contract (docs/05)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ...core.config import Settings
from ...core.errors import ValidationError
from ...domain.calendar import CalendarDate, convert
from ...domain.enums import Calendar
from ...domain.geo import BBox
from ...domain.temporal import TemporalMode, TimeWindow
from ...services.atlas import FeatureQuery
from ..deps import AtlasDep, LocaleDep, SearchDep, SettingsDep

router = APIRouter(prefix="/atlas", tags=["atlas"])


class GeoJSONResponse(JSONResponse):
    """RFC 7946 responses are served as application/geo+json."""

    media_type = "application/geo+json"


Fields = Literal["min", "default", "full"]
Mode = Literal["at", "during", "overlaps"]
CalendarParam = Literal["gregorian_proleptic", "julian", "islamic_lunar", "persian_solar"]


def _parse_layers(raw: str | None, settings: Settings, meta_layers: list[dict[str, Any]]) -> tuple[str, ...]:
    if not raw:
        return tuple(layer["id"] for layer in meta_layers if layer["default_on"])
    requested = tuple(part.strip() for part in raw.split(",") if part.strip())
    known = {layer["id"] for layer in meta_layers}
    unknown = [layer for layer in requested if layer not in known]
    if unknown:
        raise ValidationError(f"unknown layer(s): {', '.join(unknown)}", known=sorted(known))
    return requested


def _build_window(
    t: int | None, year_from: int | None, year_to: int | None, mode: Mode, cal: CalendarParam,
    settings: Settings,
) -> TimeWindow:
    calendar = Calendar(cal)
    if year_from is not None or year_to is not None:
        lo = year_from if year_from is not None else year_to
        hi = year_to if year_to is not None else year_from
        assert lo is not None and hi is not None
        if calendar is not Calendar.GREGORIAN_PROLEPTIC:
            lo, hi = _normalize_span(calendar, lo, hi)
        return TimeWindow.span(min(lo, hi), max(lo, hi), mode, calendar)
    year = t if t is not None else settings.timeline_default_year
    if calendar is not Calendar.GREGORIAN_PROLEPTIC:
        lo, hi = _normalize_span(calendar, year, year)
        return TimeWindow.span(lo, hi, TemporalMode.OVERLAPS if lo != hi else mode, calendar)
    return TimeWindow.at(year, calendar)


def _normalize_span(calendar: Calendar, year_from: int, year_to: int) -> tuple[int, int]:
    """Convert a non-Gregorian input span into normalized (astronomical) years."""
    start, _ = convert(CalendarDate(calendar, year_from), Calendar.GREGORIAN_PROLEPTIC)
    _, end = convert(CalendarDate(calendar, year_to, 12), Calendar.GREGORIAN_PROLEPTIC)
    return start.year, end.year


@router.get(
    "/features",
    summary="Viewport + time + layers -> GeoJSON",
    response_class=GeoJSONResponse,
    responses={200: {"content": {"application/geo+json": {}}}},
)
def features(
    atlas: AtlasDep,
    settings: SettingsDep,
    locale: LocaleDep,
    bbox: Annotated[str | None, Query(description="minLon,minLat,maxLon,maxLat")] = None,
    zoom: Annotated[float, Query(ge=0, le=22)] = 7.0,
    t: Annotated[int | None, Query(description="Normalized year (astronomical: 0 = 1 BCE)")] = None,
    cal: CalendarParam = "gregorian_proleptic",
    year_from: Annotated[int | None, Query(alias="from")] = None,
    year_to: Annotated[int | None, Query(alias="to")] = None,
    mode: Mode = "at",
    layers: Annotated[str | None, Query(description="Comma separated layer ids")] = None,
    kinds: Annotated[str | None, Query(description="Comma separated entity kinds")] = None,
    fields: Fields = "default",
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
    near: Annotated[str | None, Query(description="lon,lat for radius filtering")] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=500)] = None,
    period: Annotated[str | None, Query()] = None,
) -> GeoJSONResponse:
    area = _bbox_or_default(bbox, settings)
    resolved_layers = _parse_layers(layers, settings, atlas.layers(locale)["data"])
    window = _build_window(t, year_from, year_to, mode, cal, settings)
    near_point = _parse_point(near)
    query = FeatureQuery(
        bbox=area,
        zoom=zoom,
        window=window,
        layers=resolved_layers,
        kinds=tuple(k.strip() for k in kinds.split(",") if k.strip()) if kinds else (),
        locale=locale,
        fields=fields,
        limit=min(limit or settings.default_limit, settings.max_limit),
        cursor=cursor,
        near=near_point,
        radius_km=radius_km,
        period_code=period,
    )
    result = atlas.features(query)
    return GeoJSONResponse(
        content={"type": "FeatureCollection", "features": result.features, "meta": result.meta},
        headers={
            "Cache-Control": f"public, max-age={settings.cache_ttl_seconds}, stale-while-revalidate=600",
            "ETag": _etag(result),
        },
    )


def _etag(result: Any) -> str:
    import hashlib
    import json

    payload = json.dumps(result.meta, ensure_ascii=False, sort_keys=True) + str(len(result.features))
    return '"' + hashlib.sha1(payload.encode()).hexdigest()[:16] + '"'


def _bbox_or_default(raw: str | None, settings: Settings) -> BBox:
    """Domain raises ValueError for bad geometry input; the API turns it into RFC 9457."""
    if not raw:
        return BBox(*settings.study_area_bbox)
    try:
        return BBox.parse(raw)
    except ValueError as exc:
        raise ValidationError(str(exc), parameter="bbox") from exc


def _parse_point(raw: str | None) -> tuple[float, float] | None:
    if not raw:
        return None
    try:
        parts = [float(part) for part in raw.split(",")]
    except ValueError as exc:
        raise ValidationError("near must be two numbers: lon,lat", parameter="near") from exc
    if len(parts) != 2:
        raise ValidationError("near must be lon,lat", parameter="near")
    return (parts[0], parts[1])



@router.get("/timeline", summary="Density buckets for the timeline histogram")
def timeline(
    atlas: AtlasDep,
    settings: SettingsDep,
    locale: LocaleDep,
    bbox: Annotated[str | None, Query()] = None,
    year_from: Annotated[int | None, Query(alias="from")] = None,
    year_to: Annotated[int | None, Query(alias="to")] = None,
    bucket: Annotated[int | None, Query(ge=1, le=500)] = None,
    layers: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    area = _bbox_or_default(bbox, settings)
    lo = year_from if year_from is not None else settings.timeline_floor
    hi = year_to if year_to is not None else settings.timeline_ceil
    if lo > hi:
        raise ValidationError("from must be <= to")
    resolved_layers = _parse_layers(layers, settings, atlas.layers(locale)["data"])
    window = TimeWindow.span(lo, hi, TemporalMode.OVERLAPS)
    return atlas.timeline(area, window, resolved_layers, locale, bucket)


@router.get("/context", summary='"What was here?" — everything around a place or point, in time')
def context(
    atlas: AtlasDep,
    locale: LocaleDep,
    place_id: Annotated[str | None, Query()] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=200)] = 5.0,
    limit: Annotated[int, Query(ge=1, le=500)] = 60,
) -> dict[str, Any]:
    point = (lon, lat) if lat is not None and lon is not None else None
    return atlas.context(place_id=place_id, point=point, radius_km=radius_km, locale=locale, limit=limit)


@router.get("/query", summary="Structured atlas query (e.g. Safavid buildings near Ardabil)")
def structured_query(
    atlas: AtlasDep,
    search: SearchDep,
    settings: SettingsDep,
    locale: LocaleDep,
    kinds: Annotated[str | None, Query()] = None,
    bbox: Annotated[str | None, Query()] = None,
    zoom: Annotated[float, Query(ge=0, le=22)] = 9.0,
    t: Annotated[int | None, Query()] = None,
    year_from: Annotated[int | None, Query(alias="from")] = None,
    year_to: Annotated[int | None, Query(alias="to")] = None,
    mode: Mode = "at",
    near: Annotated[str | None, Query()] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=500)] = None,
    period: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    area = _bbox_or_default(bbox, settings)
    window = _build_window(t, year_from, year_to, mode, "gregorian_proleptic", settings)
    return search.atlas_query(
        atlas,
        kinds=tuple(k.strip() for k in kinds.split(",") if k.strip()) if kinds else (),
        bbox=area,
        zoom=zoom,
        window=window,
        locale=locale,
        limit=min(limit or settings.default_limit, settings.max_limit),
        near=_parse_point(near),
        radius_km=radius_km,
        period_code=period,
    )


