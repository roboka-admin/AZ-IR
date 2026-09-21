"""Atlas endpoints: the map's data contract (docs/05)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from ...core.config import Settings
from ...core.errors import ValidationError
from ...domain.calendar import CalendarDate, convert, from_gregorian_year
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
    request: Request,
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
    all_time: Annotated[bool, Query(description="Return the complete configured timeline extent")] = False,
) -> Response:
    area = _bbox_or_default(bbox, settings)
    resolved_layers = _parse_layers(layers, settings, atlas.layers(locale)["data"])
    window = (
        TimeWindow.span(settings.timeline_floor, settings.timeline_ceil, TemporalMode.OVERLAPS)
        if all_time
        else _build_window(t, year_from, year_to, mode, cal, settings)
    )
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
        all_time=all_time,
    )
    result = atlas.features(query)
    content = {"type": "FeatureCollection", "features": result.features, "meta": result.meta}
    etag = _etag(content)
    cache_control = f"public, max-age={settings.cache_ttl_seconds}, stale-while-revalidate=600"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"Cache-Control": cache_control, "ETag": etag})
    return GeoJSONResponse(
        content=content,
        headers={"Cache-Control": cache_control, "ETag": etag},
    )


@router.get(
    "/window",
    summary="Calendar year or span -> the normalized years the tiles are keyed by",
)
def normalize_window(
    settings: SettingsDep,
    t: Annotated[int | None, Query(description="A year in `cal`, or a normalized year")] = None,
    year_from: Annotated[int | None, Query(alias="from")] = None,
    year_to: Annotated[int | None, Query(alias="to")] = None,
    mode: Mode = "at",
    cal: CalendarParam = "gregorian_proleptic",
) -> JSONResponse:
    """Answer "which normalized years does this calendar selection mean?".

    Tiles store normalized (astronomical Gregorian) years, and a browser that converted a Jalali or
    Hijri year itself would be doing history in the frontend (AGENTS.md rule 5) with a table nobody
    can audit. So the conversion stays here: the GeoJSON path gets it for free inside ``/features``,
    and the tile path asks once per change of the timeline instead of once per viewport.
    """
    resolved = _build_window(t, year_from, year_to, mode, cal, settings)
    return JSONResponse(
        {
            "data": {
                "from": resolved.year_from,
                "to": resolved.year_to,
                "mode": resolved.mode,
                "calendar": cal,
                "normalized_calendar": Calendar.GREGORIAN_PROLEPTIC.value,
            },
            "meta": {"driver": settings.db_driver, "circa_fuzz_years": settings.circa_fuzz_years},
        },
        headers={"cache-control": f"public, max-age={settings.entity_cache_ttl_seconds}"},
    )


def _etag(content: Any) -> str:
    """Strong validator for the exact logical GeoJSON response, not merely its feature count."""
    import hashlib
    import json

    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(payload.encode()).hexdigest()[:24] + '"'


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
    lon, lat = parts
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise ValidationError(
            "near coordinates are outside valid longitude/latitude bounds",
            parameter="near",
            bounds={"longitude": [-180, 180], "latitude": [-90, 90]},
        )
    return (lon, lat)



@router.get("/calendar-year", summary="Normalized Gregorian year -> display-calendar year")
def calendar_year(
    settings: SettingsDep,
    t: Annotated[int, Query(description="Normalized astronomical Gregorian year")],
    cal: CalendarParam = "gregorian_proleptic",
) -> dict[str, Any]:
    """Convert the timeline's canonical cursor without moving it in historical time."""
    calendar = Calendar(cal)
    value = t if calendar is Calendar.GREGORIAN_PROLEPTIC else from_gregorian_year(calendar, t).year
    return {
        "data": {"year": value, "calendar": cal, "normalized_year": t},
        "meta": {"driver": settings.db_driver},
    }


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
    cal: CalendarParam = "gregorian_proleptic",
) -> dict[str, Any]:
    area = _bbox_or_default(bbox, settings)
    lo = year_from if year_from is not None else settings.timeline_floor
    hi = year_to if year_to is not None else settings.timeline_ceil
    if lo > hi:
        raise ValidationError("from must be <= to")
    resolved_layers = _parse_layers(layers, settings, atlas.layers(locale)["data"])
    window = TimeWindow.span(lo, hi, TemporalMode.OVERLAPS)
    result = atlas.timeline(area, window, resolved_layers, locale, bucket)
    calendar = Calendar(cal)
    if calendar is not Calendar.GREGORIAN_PROLEPTIC:
        for row in result["data"]:
            row["display_from"] = from_gregorian_year(calendar, row["from"]).year
            row["display_to"] = from_gregorian_year(calendar, row["to"]).year
            for notable in row["notable"]:
                notable["display_year"] = from_gregorian_year(calendar, notable["year"]).year
    result["meta"]["display_calendar"] = cal
    return result


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


