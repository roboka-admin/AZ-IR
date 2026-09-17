"""Tile endpoints: a dynamic MVT endpoint and the static PMTiles archive (ADR-0011, ADR-0018).

Two ways to get tiles, one definition of what a tile contains:

* ``GET /api/v1/tiles/{z}/{x}/{y}.pbf`` renders on demand. Development and small deployments use
  this: no build step, always current, at the cost of a query per tile.
* ``GET /api/v1/tiles/archive/{filename}`` serves what ``azir tiles build`` produced, with HTTP range
  support, so a browser can read it through the PMTiles protocol. In production the same file sits on
  a CDN and this endpoint is switched off (``AZIR_TILES_DYNAMIC_ENABLED=0``).

Neither endpoint knows anything about history: policy lives in :class:`TileService`, which delegates
to :class:`AtlasService` (AGENTS.md rules 2, 3 and 5).
"""

from __future__ import annotations

import gzip
import hashlib
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ...core.config import Settings
from ...core.errors import NotFoundError, UnavailableError
from ..deps import LocaleDep, SettingsDep, TilesDep

router = APIRouter(prefix="/tiles", tags=["tiles"])

MVT_MEDIA_TYPE = "application/vnd.mapbox-vector-tile"
PMTILES_MEDIA_TYPE = "application/vnd.pmtiles"

#: A rendered tile follows the corpus, so it is cacheable but not immortal. An archive is
#: content-addressed -- its name changes when its bytes change -- so it can be cached forever.
ARCHIVE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def _coordinates(z: int, x: int, y: int) -> tuple[int, int, int]:
    """Reject nonsense tiles at the edge of the pyramid instead of inside the service."""
    if not 0 <= z <= 22:
        raise NotFoundError(f"zoom {z} is outside 0..22", zoom=z)
    size = 1 << z
    if not 0 <= x < size or not 0 <= y < size:
        raise NotFoundError(f"tile {x}/{y} does not exist at zoom {z}", zoom=z, x=x, y=y)
    return (z, x, y)


@router.get("/index.json", summary="How to fetch tiles: the archive pointer, or the dynamic endpoint")
def tiles_index(tiles: TilesDep, settings: SettingsDep, locale: LocaleDep) -> JSONResponse:
    """What the frontend asks before it builds a style.

    The answer is deliberately boring: either an archive URL or a dynamic template. A client that
    guesses wrong pays for it on every tile, so the choice is published rather than inferred.
    """
    # Per locale, because the labels inside an archive are resolved at build time: an English
    # visitor must never be handed the Persian file (ADR-0018). No entry for this locale means no
    # archive, and the client falls back to the live endpoints.
    pointer = tiles.pointer(locale)
    data: dict[str, Any] = {
        "mode": "pmtiles" if pointer.get("url") else "dynamic",
        "dynamic_enabled": settings.tiles_dynamic_enabled,
        "dynamic_template": _dynamic_template(settings) if settings.tiles_dynamic_enabled else None,
        "archive": pointer,
        "archive_locales": tiles.pointer_locales(),
        "tileset_version": tiles.tileset_version(),
        "layers": [layer["id"] for layer in tiles.vector_layers()],
        "properties": tiles.property_schema(),
        "min_zoom": settings.tiles_min_zoom,
        "max_zoom": settings.tiles_max_zoom,
        "locale": locale,
        "bounds": list(settings.corpus_bbox),
        "center": [*settings.study_area_center, settings.study_area_default_zoom],
    }
    return JSONResponse(
        {"data": data, "meta": {"driver": tiles.driver_name}},
        headers={"cache-control": f"public, max-age={settings.cache_ttl_seconds}"},
    )


def _dynamic_template(settings: Settings) -> str:
    return (
        f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}/tiles/{{z}}/{{x}}/{{y}}.pbf"
    )


@router.get("/{z}/{x}/{y}.pbf", summary="One rendered vector tile (gzip)")
def get_tile(
    z: int, x: int, y: int, request: Request, tiles: TilesDep, settings: SettingsDep, locale: LocaleDep
) -> Response:
    """A live tile. Empty bytes are a real answer ("nothing here"), not an error."""
    if not settings.tiles_dynamic_enabled:
        raise UnavailableError(
            "dynamic tiles are disabled on this deployment; fetch the PMTiles archive instead",
            hint="GET /api/v1/tiles/index.json",
        )
    z, x, y = _coordinates(z, x, y)
    raw = tiles.tile(z, x, y, locale=locale)
    etag = f'W/"{hashlib.blake2b(raw, digest_size=8).hexdigest()}-{locale}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"etag": etag})
    headers = {
        "content-encoding": "gzip",
        "cache-control": f"public, max-age={settings.cache_ttl_seconds}",
        "etag": etag,
        "x-azir-tile-bytes": str(len(raw)),
    }
    if not raw:
        headers["x-azir-empty"] = "1"
    return Response(
        content=gzip.compress(raw, mtime=0) if raw else b"",
        media_type=MVT_MEDIA_TYPE,
        headers=headers,
    )


@router.get("/archive/{filename}", summary="A built PMTiles archive, with HTTP range support")
def get_archive(filename: str, tiles: TilesDep) -> FileResponse:
    """Serve an archive byte-range by byte-range.

    A PMTiles reader asks for the header, then the root directory, then exactly the slice holding one
    tile: three ranged requests instead of a download. Starlette's ``FileResponse`` does the range
    mechanics; the filename *validation* lives in the service, so a crafted path cannot escape
    ``tiles_dir`` no matter which endpoint asks.
    """
    path = tiles.archive_path(filename)
    if path is None:
        raise NotFoundError(f"no such tile archive: {filename}", filename=filename)
    is_archive = path.suffix == ".pmtiles"
    return FileResponse(
        path,
        media_type=PMTILES_MEDIA_TYPE if is_archive else "application/json",
        headers={
            "cache-control": ARCHIVE_CACHE_CONTROL if is_archive else "public, max-age=60",
            # The archive is public data and a CDN or another origin must be able to range-read it.
            "access-control-allow-origin": "*",
        },
    )


__all__ = ["router"]
