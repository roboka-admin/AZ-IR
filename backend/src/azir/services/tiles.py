"""TileService: the same features the GeoJSON API returns, serialised as MVT and archived as PMTiles.

Two rules shaped this module:

**One definition of "visible at zoom z".** Tile building goes through :class:`AtlasService`, so
semantic zoom, ranking, LOD, label choice and the published-only filter are decided in exactly one
place (AGENTS.md rule 5). A tile and a GeoJSON viewport of the same area cannot disagree, because
neither implements its own policy.

**Tiles are time-agnostic; the timeline stays in the client.** A static archive cannot be baked per
year -- that would be a pyramid per year, i.e. the end of static hosting. So every feature carries
its own temporal window (``t_from``/``t_to`` for the entity, ``g_from``/``g_to`` for the geometry
variant) and MapLibre filters with expressions (ADR-0005 survives the switch to tiles: no fake
precision, and a boundary is only drawn during its own years).

One entity may appear as several features in one tile. That is not duplication: each feature is a
different *period* of the same thing. Level of detail, on the other hand, is collapsed here -- at a
given zoom only one LOD variant of a geometry is emitted, because drawing both would be drawing the
same period twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np
from shapely import clip_by_rect
from shapely import transform as shapely_transform
from shapely.errors import ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    shape,
)
from shapely.geometry.base import BaseGeometry

from ..core.config import Settings
from ..domain.geo import BBox, GeometryRecord
from ..domain.model import EntityRecord
from ..domain.temporal import TimeWindow
from ..repositories.ports import AtlasRepository
from ..tiles.mvt import (
    EXTENT,
    LINESTRING,
    POINT,
    POLYGON,
    MvtFeature,
    MvtLayer,
    decode_tile,
    encode_tile,
    tile_bounds,
    tiles_for_bounds,
)
from ..tiles.pmtiles import ArchiveReport, write_archive
from .atlas import LAYER_STYLE_TOKENS, AtlasService, FeatureQuery

logger = logging.getLogger(__name__)

#: Web Mercator cannot represent the poles; clipping to this box keeps every projection finite.
MERCATOR_LAT_LIMIT: Final[float] = 85.05112877980659

#: Version of the *tile contract*: the property names in :data:`TILE_PROPERTIES`, the layer set and
#: the meaning of a tile in time. Bump it when any of those change, because the archive filename
#: carries it and the frontend style is written against it (AGENTS.md rule 12: contracts are
#: versioned, never silently changed). The data revision next to it covers content changes.
TILESET_VERSION: Final[str] = "1.2.0"

#: TileJSON ``vector_layers`` field types, and the contract the frontend style is written against.
#: MVT has no null and no nested values, so a property that is unknown is *absent*: styles must
#: coalesce (``["coalesce", ["get", "t_from"], -1000000]``) rather than compare blindly.
TILE_PROPERTIES: Final[dict[str, str]] = {
    "id": "String",
    "entity_type": "String",
    "kind": "String",
    "layer": "String",
    "slug": "String",
    "label": "String",
    "label_anchor": "Boolean",
    "label_secondary": "String",
    "dir": "String",
    "href": "String",
    "status": "String",
    "style_color": "String",
    "certainty": "String",
    "geometry_kind": "String",
    "t_display": "String",
    "t_precision": "String",
    "confidence": "String",
    "rank": "Number",
    "min_zoom": "Number",
    "max_zoom": "Number",
    "importance": "Number",
    "t_from": "Number",
    "t_to": "Number",
    "article_count": "Number",
    "source_count": "Number",
    "assertion_count": "Number",
    "g_index": "Number",
    "g_from": "Number",
    "g_to": "Number",
    "g_lod_min_zoom": "Number",
    "g_lod_max_zoom": "Number",
    "has_disagreements": "Boolean",
    "needs_digitisation": "Boolean",
}

#: Layers in paint order: areas first, then lines, then points and labels on top. MapLibre paints in
#: the order layers appear in the style, and the tile's layer order is what a reader falls back to.
LAYER_ORDER: Final[tuple[str, ...]] = (
    "modern_borders",
    "political_entities",
    "places",
    "archaeology",
    "routes",
    "buildings",
    "battles",
    "events",
    "people",
    "articles",
)


@dataclass(frozen=True, slots=True)
class TileBuildReport:
    """What a build did, in numbers an operator can act on."""

    archive: ArchiveReport
    tileset_version: str
    data_revision: str
    generated_at: str
    locale: str
    driver: str
    features: int
    tiles_written: int
    tiles_empty: int
    degraded_tiles: int
    seconds: float
    filename: str
    pointer: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = self.archive.as_dict()
        payload.update(
            {
                "tileset_version": self.tileset_version,
                "data_revision": self.data_revision,
                "generated_at": self.generated_at,
                "locale": self.locale,
                "driver": self.driver,
                "features": self.features,
                "tiles_written": self.tiles_written,
                "tiles_empty": self.tiles_empty,
                "degraded_tiles": self.degraded_tiles,
                "seconds": round(self.seconds, 3),
                "filename": self.filename,
                "pointer": self.pointer,
            }
        )
        return payload


class TileService:
    """Builds one tile on demand, or the whole archive for the CDN."""

    def __init__(
        self, atlas: AtlasService, repository: AtlasRepository, settings: Settings
    ) -> None:
        self._atlas = atlas
        self._repo = repository
        self._settings = settings

    # ------------------------------------------------------------------ single tile

    def tile(self, z: int, x: int, y: int, *, locale: str = "fa") -> bytes:
        """MVT bytes for one tile. Empty bytes means "nothing here", which is a valid answer."""
        return self._render(z, x, y, locale=locale)[0]

    def _render(
        self, z: int, x: int, y: int, *, locale: str = "fa"
    ) -> tuple[bytes, int, bool]:
        """Build one tile, returning ``(bytes, feature_count, degraded)``.

        The query bbox is buffered (``tiles_buffer_ratio``) so a road or a boundary does not stop at
        the tile edge; the clip uses the same box, so what is queried is what is drawn.

        ``degraded`` means the tile hit :data:`Settings.tiles_feature_limit` and the least important
        features were dropped. A build reports the count loudly: a degraded tile is a signal to raise
        the limit, to deepen ``min_rank``, or to digitise less at that zoom -- never to ignore.
        """
        self._check_coordinates(z, x, y)
        bounds = tile_bounds(z, x, y)
        query_bbox, clip_bbox = self._buffered(bounds)
        limit = self._settings.tiles_feature_limit

        result = self._atlas.features(
            FeatureQuery(
                bbox=query_bbox,
                zoom=float(z),
                window=self._all_time_window(),
                layers=LAYER_ORDER,
                locale=locale,
                # Tiles are not paginated: a tile either has its features or it does not, so the
                # limit is a ceiling on pathological density rather than a page size.
                fields="default",
                limit=limit,
            )
        )

        by_layer: dict[str, list[MvtFeature]] = {}
        count = 0
        for feature in result.features:
            entity = self._entity_of(feature)
            if entity is None:
                continue
            properties = _tile_properties(feature.get("properties") or {})
            properties["importance"] = round(entity.importance, 4)
            # Polygon fragments can span many tiles. They draw the extent but must not each host a
            # copy of the entity name; one dedicated point below is the sole label anchor.
            properties["label_anchor"] = False
            for index, geometry in enumerate(_geometries_for_zoom(entity, float(z))):
                encoded = self._encode_geometry(geometry, z, x, y, clip_bbox)
                if encoded is None:
                    continue
                geometry_type, positions = encoded
                merged = dict(properties)
                merged.update(_geometry_properties(geometry, index))
                by_layer.setdefault(entity.layer, []).append(
                    MvtFeature(
                        geometry_type=geometry_type,
                        geometry=positions,
                        properties=merged,
                        feature_id=_numeric_id(f"{entity.id}#{index}"),
                    )
                )
                count += 1

            primary = entity.primary_geometry()
            anchor = primary.representative_point() if primary else None
            if anchor and _point_in_tile(anchor, bounds):
                anchor_geometry = GeometryRecord(
                    geojson={"type": "Point", "coordinates": [anchor[0], anchor[1]]}
                )
                encoded_anchor = self._encode_geometry(anchor_geometry, z, x, y, clip_bbox)
                if encoded_anchor is not None:
                    geometry_type, positions = encoded_anchor
                    anchor_properties = dict(properties)
                    anchor_properties["label_anchor"] = True
                    by_layer.setdefault(entity.layer, []).append(
                        MvtFeature(
                            geometry_type=geometry_type,
                            geometry=positions,
                            properties=anchor_properties,
                            feature_id=_numeric_id(f"{entity.id}#label"),
                        )
                    )
                    count += 1

        degraded = False
        layers: list[MvtLayer] = []
        for name in [*LAYER_ORDER, *(n for n in sorted(by_layer) if n not in LAYER_ORDER)]:
            features = by_layer.get(name)
            if not features:
                continue
            if len(features) > limit:
                # Degrade by the same rank ladder the GeoJSON API degrades by (docs/08 §3).
                degraded = True
                features = sorted(
                    features, key=lambda item: -float(item.properties.get("rank", 0.0))
                )[:limit]
                count -= len(by_layer[name]) - len(features)
            layers.append(MvtLayer(name=name, features=tuple(features)))
        return (encode_tile(layers), count, degraded)

    def tile_layers(self, z: int, x: int, y: int, *, locale: str = "fa") -> dict[str, Any]:
        """The same tile, decoded -- for ``azir tiles inspect`` and for tests.

        Serving never calls this. It exists so that "what did we actually write?" can be answered
        from the bytes rather than from the code that produced them.
        """
        raw = self.tile(z, x, y, locale=locale)
        decoded = decode_tile(raw)
        return {
            "z": z,
            "x": x,
            "y": y,
            "locale": locale,
            "bounds": list(tile_bounds(z, x, y)),
            "bytes": len(raw),
            "layers": [
                {
                    "name": layer["name"],
                    "features": len(layer["features"]),
                    "keys": layer["keys"],
                    "sample": layer["features"][:3],
                }
                for layer in decoded
            ],
        }

    def _entity_of(self, feature: Mapping[str, Any]) -> EntityRecord | None:
        """Look the entity up again by id.

        The GeoJSON properties are the presentation contract; the tile needs the *records* behind
        them (every geometry variant, its LOD range, its own years). Re-reading is cheap: the
        repository has already loaded these entities to build the page, and the fixtures driver
        keeps them in memory.
        """
        properties = feature.get("properties") or {}
        entity_id = properties.get("id")
        entity_type = properties.get("entity_type")
        if not isinstance(entity_id, str) or not isinstance(entity_type, str):
            return None
        return self._repo.entity(entity_type, entity_id)

    def _encode_geometry(
        self,
        geometry: GeometryRecord,
        z: int,
        x: int,
        y: int,
        clip_bbox: tuple[float, float, float, float],
    ) -> tuple[int, tuple[Any, ...]] | None:
        """Clip to the tile and project into tile coordinates, or ``None`` if nothing survives."""
        try:
            prepared = shape(geometry.geojson)
        except (ValueError, TypeError, KeyError, AttributeError, ShapelyError):
            # A malformed geometry costs one feature, not the whole tile -- and not the build.
            logger.warning("skipping unparseable geometry on tile %s/%s/%s", z, x, y)
            return None
        if prepared.is_empty:
            return None
        clipped = clip_by_rect(prepared, *clip_bbox)
        if clipped.is_empty:
            return None
        projected = shapely_transform(
            clipped, lambda coords: _project_array(coords, z, x, y)
        )
        return _mvt_geometry(projected)

    def _buffered(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[BBox, tuple[float, float, float, float]]:
        min_lon, min_lat, max_lon, max_lat = bounds
        ratio = max(0.0, self._settings.tiles_buffer_ratio)
        pad_lon = (max_lon - min_lon) * ratio
        pad_lat = (max_lat - min_lat) * ratio
        clip = (
            max(-180.0, min_lon - pad_lon),
            max(-MERCATOR_LAT_LIMIT, min_lat - pad_lat),
            min(180.0, max_lon + pad_lon),
            min(MERCATOR_LAT_LIMIT, max_lat + pad_lat),
        )
        return (
            BBox(min_lon=clip[0], min_lat=clip[1], max_lon=clip[2], max_lat=clip[3]),
            clip,
        )

    def _all_time_window(self) -> TimeWindow:
        """A window wide enough that no feature is filtered out by time.

        Tiles answer "what is here, and when was it here", never "what is here *now*". The client
        narrows the answer with the timeline.
        """
        return TimeWindow.span(
            min(-100000, self._settings.timeline_floor - 100000),
            max(100000, self._settings.timeline_ceil + 100000),
        )

    @staticmethod
    def _check_coordinates(z: int, x: int, y: int) -> None:
        if not 0 <= z <= 22:
            raise ValueError(f"zoom {z} is outside the supported range 0..22")
        size = 1 << z
        if not 0 <= x < size or not 0 <= y < size:
            raise ValueError(f"tile {x}/{y} does not exist at zoom {z} (grid is {size}x{size})")

    # ------------------------------------------------------------------ archive

    def data_revision(self) -> str:
        """A short hash of the published corpus, used to name the archive.

        The name has to change when the data changes, or a CDN will happily keep serving yesterday's
        tiles under today's URL. Content-addressing the archive is what makes an immutable,
        forever-cacheable file possible.
        """
        digest = hashlib.blake2b(digest_size=8)
        for entity in sorted(self._repo.all_published(), key=lambda item: item.id):
            digest.update(
                json.dumps(
                    [
                        entity.id,
                        entity.entity_type.value,
                        entity.revision,
                        entity.status.value,
                        entity.rank,
                        entity.layer,
                        [
                            [
                                geometry.geometry_type,
                                geometry.certainty.value,
                                geometry.year_from,
                                geometry.year_to,
                                geometry.lod_min_zoom,
                                geometry.lod_max_zoom,
                            ]
                            for geometry in entity.geometries
                        ],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        return digest.hexdigest()

    def vector_layers(self) -> list[dict[str, Any]]:
        """TileJSON ``vector_layers`` -- required metadata for an MVT archive (PMTiles spec §5)."""
        return [
            {
                "id": layer,
                "description": LAYER_STYLE_TOKENS.get(layer, layer),
                "minzoom": self._settings.tiles_min_zoom,
                "maxzoom": self._settings.tiles_max_zoom,
                "fields": dict(TILE_PROPERTIES),
            }
            for layer in LAYER_ORDER
        ]

    def archive_metadata(self, *, revision: str, generated_at: str) -> dict[str, Any]:
        settings = self._settings
        return {
            "name": f"{settings.study_area_name_en} historical atlas",
            "description": (
                "Vector tiles of the published corpus. Every feature carries its own temporal "
                "window: filter client-side, never assume a tile means 'now'."
            ),
            "attribution": settings.app_name,
            "type": "overlay",
            # TileJSON wants semver here; the data revision lives in the azir block and the filename.
            "version": TILESET_VERSION,
            "vector_layers": self.vector_layers(),
            "minzoom": settings.tiles_min_zoom,
            "maxzoom": settings.tiles_max_zoom,
            "bounds": list(settings.corpus_bbox),
            "center": [*settings.study_area_center, settings.study_area_default_zoom],
            "azir": {
                "driver": self._repo.driver_name,
                "tileset_version": TILESET_VERSION,
                "data_revision": revision,
                "generated_at": generated_at,
                "editorial_note": "published records only; drafts and in-review copies are absent",
                "temporal_note": "t_from/t_to are entity years, g_from/g_to geometry-variant years",
            },
        }

    def build_archive(
        self,
        destination: str | Path | None = None,
        *,
        min_zoom: int | None = None,
        max_zoom: int | None = None,
        locale: str = "fa",
        write_pointer: bool = True,
    ) -> TileBuildReport:
        """Build the whole pyramid for the corpus bbox into one PMTiles file.

        The archive is named from the locale, the tileset version and the data revision, and
        ``latest.json`` next to it maps every locale to its archive: an upload is therefore atomic
        (write a new file, flip the pointer) and a rollback is just an older pointer.

        The locale is part of the *name* because it is part of the content: labels are resolved from
        ``NameVariant`` at build time, so a Persian archive and an English archive of the same
        revision are different files that must not overwrite each other.
        """
        settings = self._settings
        low = settings.tiles_min_zoom if min_zoom is None else min_zoom
        high = settings.tiles_max_zoom if max_zoom is None else max_zoom
        if low > high:
            raise ValueError(f"min_zoom {low} is greater than max_zoom {high}")
        revision = self.data_revision()
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        filename = f"atlas-{locale}-{TILESET_VERSION}-{revision}.pmtiles"
        directory = Path(destination) if destination is not None else Path(settings.tiles_dir)
        if destination is None:
            directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename if directory.suffix != ".pmtiles" else directory

        stats = _BuildStats()
        started = time.monotonic()
        archive = write_archive(
            path,
            self._tile_stream(low, high, locale, stats),
            metadata=self.archive_metadata(revision=revision, generated_at=generated_at),
            bounds=settings.corpus_bbox,
            min_zoom=low,
            max_zoom=high,
            center=settings.study_area_center,
            center_zoom=int(settings.study_area_default_zoom),
        )
        seconds = time.monotonic() - started

        pointer = {
            "url": self.public_url(filename),
            "filename": filename,
            "tileset_version": TILESET_VERSION,
            "data_revision": revision,
            "generated_at": generated_at,
            "bytes": archive.bytes,
            "tiles": archive.tiles,
            "min_zoom": archive.min_zoom,
            "max_zoom": archive.max_zoom,
            "bounds": list(settings.corpus_bbox),
            "center": [*settings.study_area_center, settings.study_area_default_zoom],
            "driver": self._repo.driver_name,
            "locale": locale,
        }
        if write_pointer:
            self._write_pointer(path.parent / "latest.json", locale, pointer)
        if stats.degraded:
            logger.warning(
                "%s tile(s) exceeded the feature limit (%s) and were degraded by rank",
                stats.degraded,
                settings.tiles_feature_limit,
            )
        return TileBuildReport(
            archive=archive,
            tileset_version=TILESET_VERSION,
            data_revision=revision,
            generated_at=generated_at,
            locale=locale,
            driver=self._repo.driver_name,
            features=stats.features,
            tiles_written=stats.written,
            tiles_empty=stats.empty,
            degraded_tiles=stats.degraded,
            seconds=seconds,
            filename=filename,
            pointer=pointer,
        )

    def _tile_stream(
        self, min_zoom: int, max_zoom: int, locale: str, stats: _BuildStats
    ) -> Iterator[tuple[int, int, int, bytes]]:
        """Every tile that intersects the corpus bbox, low zoom first.

        Order matters twice: the archive is *clustered* (tile data in TileID order, which the writer
        sorts out), and a build fails fast on a low-zoom problem instead of after ten minutes.
        """
        for z, x, y in tiles_for_bounds(self._settings.corpus_bbox, max_zoom):
            if z < min_zoom:
                continue
            raw, count, degraded = self._render(z, x, y, locale=locale)
            if not raw:
                stats.empty += 1
                continue
            stats.written += 1
            stats.features += count
            stats.degraded += 1 if degraded else 0
            yield (z, x, y, raw)

    # ------------------------------------------------------------------ serving

    @property
    def driver_name(self) -> str:
        return self._repo.driver_name

    def tileset_version(self) -> str:
        return TILESET_VERSION

    def property_schema(self) -> dict[str, str]:
        """The tile property contract, as the frontend needs it (names -> TileJSON types)."""
        return dict(TILE_PROPERTIES)

    def _write_pointer(self, path: Path, locale: str, pointer: dict[str, Any]) -> None:
        """Record one locale's archive in ``latest.json``, keeping the other locales.

        Building the Persian archive must not make the English one disappear: the pointer is a map,
        and each build replaces exactly its own entry.
        """
        payload: dict[str, Any] = {"locales": {}}
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.warning("rewriting unreadable tile pointer at %s", path)
                existing = None
            if isinstance(existing, dict):
                locales = existing.get("locales")
                if isinstance(locales, dict):
                    payload = existing
                elif isinstance(existing.get("filename"), str):
                    # A pointer from before per-locale archives: adopt it under its own locale.
                    payload = {
                        "locales": {str(existing.get("locale", locale)): existing},
                    }
        locales = payload.setdefault("locales", {})
        locales[locale] = pointer
        payload["tileset_version"] = TILESET_VERSION
        payload["default_locale"] = self._settings.default_locale
        payload["updated_at"] = pointer["generated_at"]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def pointer(self, locale: str | None = None) -> dict[str, Any]:
        """``latest.json``'s entry for ``locale``, or ``{}`` when there is none to serve.

        A pointer that names a missing file is worse than no pointer: the frontend would switch to
        PMTiles mode and then 404 on every tile. So the file has to be there, and readable.

        Serving *another* locale's archive is worse still -- an English visitor would read Persian
        labels with no error anywhere -- so a missing entry means "no archive", and the caller falls
        back to the live endpoints.
        """
        wanted = locale or self._settings.default_locale
        for entry in self._pointer_entries().values():
            if entry.get("locale") == wanted:
                return entry
        return self._pointer_entries().get(wanted, {})

    def pointer_locales(self) -> list[str]:
        """Locales that have a usable archive, for diagnostics and for an honest UI message."""
        return sorted(self._pointer_entries())

    def _pointer_entries(self) -> dict[str, dict[str, Any]]:
        """Every locale entry in ``latest.json`` whose archive actually exists on disk."""
        path = Path(self._settings.tiles_dir) / "latest.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("ignoring unreadable tile pointer at %s", path)
            return {}
        if not isinstance(payload, dict):
            return {}
        locales = payload.get("locales")
        if isinstance(locales, dict):
            entries = {
                str(key): value for key, value in locales.items() if isinstance(value, dict)
            }
        elif isinstance(payload.get("filename"), str):
            entries = {str(payload.get("locale", self._settings.default_locale)): payload}
        else:
            return {}
        usable: dict[str, dict[str, Any]] = {}
        for key, entry in entries.items():
            filename = entry.get("filename")
            compatible = entry.get("tileset_version") == TILESET_VERSION
            if isinstance(filename, str) and (path.parent / filename).is_file() and compatible:
                usable[key] = entry
            else:
                logger.warning(
                    "tile pointer at %s names a missing or incompatible archive for %s", path, key
                )
        return usable

    def archive_path(self, filename: str) -> Path | None:
        """Resolve a requested archive name inside ``tiles_dir``, or ``None``.

        The filename arrives from a URL, so this is the path-traversal guard: no separators, no
        ``..``, an expected suffix, and a resolved path that is still inside the directory. All four,
        because any one of them alone has a way of being wrong on some filesystem.
        """
        directory = Path(self._settings.tiles_dir).resolve()
        if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
            return None
        candidate = Path(filename)
        if candidate.name != filename or candidate.suffix not in {".pmtiles", ".json"}:
            return None
        resolved = (directory / candidate.name).resolve()
        if not resolved.is_relative_to(directory) or not resolved.is_file():
            return None
        return resolved

    def public_url(self, filename: str) -> str:
        """Where a browser should fetch the archive from.

        The default is this API's own archive endpoint, so a local run works with no CDN configured.
        Set ``AZIR_TILES_PUBLIC_BASE_URL`` to the object-storage/CDN prefix in a real deployment.
        """
        settings = self._settings
        base = settings.tiles_public_base_url
        if base:
            return f"{base.rstrip('/')}/{filename}"
        return f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}/tiles/archive/{filename}"


@dataclass(slots=True)
class _BuildStats:
    features: int = 0
    written: int = 0
    empty: int = 0
    degraded: int = 0


def _point_in_tile(
    point: tuple[float, float], bounds: tuple[float, float, float, float]
) -> bool:
    """Assign a label anchor to exactly one tile, even when geometry buffers overlap."""
    lon, lat = point
    west, south, east, north = bounds
    return west <= lon < east and south <= lat < north


def _project_array(coords: Any, z: int, x: int, y: int) -> Any:
    """shapely hands over an ``(n, 2)`` array of lon/lat; hand back tile coordinates.

    Vectorised because a build projects every vertex of every geometry: the Python loop version is
    the difference between seconds and minutes on a z10 pyramid. It is the same arithmetic as
    :func:`azir.tiles.mvt.lonlat_to_tile`, and a test asserts the two agree point for point so the
    optimisation cannot drift from the reference implementation.
    """
    array = np.asarray(coords, dtype="float64")
    lons = array[:, 0]
    lats = np.clip(array[:, 1], -89.99999, 89.99999)
    size = float(1 << z)
    world_x = (lons + 180.0) / 360.0 * size
    sin_lat = np.sin(np.radians(lats))
    world_y = (0.5 - np.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * size
    out = np.empty_like(array)
    out[:, 0] = np.round((world_x - x) * EXTENT)
    out[:, 1] = np.round((world_y - y) * EXTENT)
    return out


def _mvt_geometry(projected: BaseGeometry) -> tuple[int, tuple[Any, ...]] | None:
    """Convert a projected shapely geometry into the spec's command input.

    ``isinstance`` branches do double duty: they pick the encoding *and* narrow ``BaseGeometry`` to
    the concrete class, which is what makes ``.geoms``/``.exterior`` type-safe without casts.

    Polygons get their winding fixed here: MVT requires exterior rings clockwise and holes
    counter-clockwise *in tile coordinates*, where y grows downwards. GeoJSON (RFC 7946) says the
    opposite, so copying a ring without re-orienting it turns every hole into an island.
    """
    if isinstance(projected, Point):
        position = _position(projected)
        return None if position is None else (POINT, (position,))
    if isinstance(projected, MultiPoint):
        points = [position for part in projected.geoms if (position := _position(part)) is not None]
        return (POINT, tuple(points)) if points else None
    if isinstance(projected, LineString):
        line = _line(projected)
        return (LINESTRING, (line,)) if line else None
    if isinstance(projected, MultiLineString):
        lines = [line for part in projected.geoms if (line := _line(part))]
        return (LINESTRING, tuple(lines)) if lines else None
    if isinstance(projected, Polygon):
        rings = _rings(projected)
        return (POLYGON, (rings,)) if rings else None
    if isinstance(projected, MultiPolygon):
        polygons = [rings for part in projected.geoms if (rings := _rings(part))]
        return (POLYGON, tuple(polygons)) if polygons else None
    if isinstance(projected, GeometryCollection):
        # Mixed geometry is legal GeoJSON but cannot be one MVT feature, so the first non-empty part
        # wins. A genuine collection is a data-modelling smell (two things stored as one) rather than
        # something a tile should paper over.
        for member in projected.geoms:
            encoded = _mvt_geometry(member)
            if encoded is not None:
                return encoded
    return None


def _position(point: Point) -> tuple[int, int] | None:
    if point.is_empty:
        return None
    return (round(point.x), round(point.y))


def _line(part: LineString) -> list[tuple[int, int]]:
    return _dedupe([(round(x), round(y)) for x, y, *_ in part.coords])


def _dedupe(positions: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Drop repeats introduced by rounding: a zero-length segment is not valid MVT."""
    out: list[tuple[int, int]] = []
    for position in positions:
        if not out or out[-1] != position:
            out.append(position)
    return out


def _rings(polygon: Polygon) -> tuple[list[tuple[int, int]], ...]:
    exterior = _orient(_dedupe([(round(x), round(y)) for x, y, *_ in polygon.exterior.coords]), exterior=True)
    if len(exterior) < 4:
        return ()
    holes = []
    for interior in polygon.interiors:
        ring = _orient(
            _dedupe([(round(x), round(y)) for x, y, *_ in interior.coords]),
            exterior=False,
        )
        if len(ring) >= 4:
            holes.append(ring)
    return (exterior, *holes)


def _orient(ring: Sequence[tuple[int, int]], *, exterior: bool) -> list[tuple[int, int]]:
    """Force the winding the spec wants, in tile coordinates (y down)."""
    positions = list(ring)
    if len(positions) < 3:
        return positions
    area = _signed_area(positions)
    # Positive shoelace area with y pointing down is clockwise on screen.
    clockwise = area > 0
    if clockwise != exterior:
        positions.reverse()
    if positions[0] != positions[-1]:
        positions.append(positions[0])
    return positions


def _signed_area(ring: Sequence[tuple[int, int]]) -> float:
    total = 0
    for index in range(len(ring) - 1):
        x0, y0 = ring[index]
        x1, y1 = ring[index + 1]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def _geometries_for_zoom(entity: EntityRecord, zoom: float) -> list[GeometryRecord]:
    """One geometry per temporal variant, each at the LOD that fits this zoom."""
    variants: dict[tuple[int | None, int | None], list[GeometryRecord]] = {}
    for geometry in entity.geometries:
        # A geometry with no coordinates is a digitisation placeholder, not a variant: emitting it
        # would add a feature the renderer can never draw.
        if not geometry.geojson or not geometry.geojson.get("coordinates"):
            continue
        variants.setdefault((geometry.year_from, geometry.year_to), []).append(geometry)
    return [_best_lod(group, zoom) for group in variants.values()]


def _best_lod(group: Sequence[GeometryRecord], zoom: float) -> GeometryRecord:
    return min(group, key=lambda geometry: _lod_distance(geometry, zoom))


def _lod_distance(geometry: GeometryRecord, zoom: float) -> float:
    if zoom < geometry.lod_min_zoom:
        return geometry.lod_min_zoom - zoom
    if zoom > geometry.lod_max_zoom:
        return zoom - geometry.lod_max_zoom
    return 0.0


def _tile_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    """GeoJSON properties -> MVT properties: scalars only, no nulls, unknown = absent."""
    out: dict[str, Any] = {}
    for key, value in properties.items():
        if value is None or isinstance(value, (dict, list, tuple)):
            continue
        if isinstance(value, (bool, int)):
            out[key] = value
        elif isinstance(value, float):
            out[key] = round(value, 4)  # rank and zoom are not meaningful beyond 4 decimals
        elif isinstance(value, str):
            out[key] = value
    out["importance"] = out.get("importance", 0.0)
    return out


def _geometry_properties(geometry: GeometryRecord, index: int) -> dict[str, Any]:
    return {
        "certainty": geometry.certainty.value,
        "geometry_kind": geometry.kind.value,
        "needs_digitisation": bool(geometry.needs_digitisation),
        "g_index": index,
        "g_lod_min_zoom": round(geometry.lod_min_zoom, 2),
        "g_lod_max_zoom": round(geometry.lod_max_zoom, 2),
        **({"g_from": geometry.year_from} if geometry.year_from is not None else {}),
        **({"g_to": geometry.year_to} if geometry.year_to is not None else {}),
    }


def _numeric_id(text: str) -> int:
    """MVT ids are uint64 and our ids are text, so the id is a hash.

    The text id stays in the properties and is what the frontend uses; the numeric id exists so
    MapLibre can key feature state and deduplicate. 64 bits of blake2 over a slug makes collisions
    a non-issue at corpus scale.
    """
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(), "big")


__all__ = [
    "LAYER_ORDER",
    "MERCATOR_LAT_LIMIT",
    "TILESET_VERSION",
    "TILE_PROPERTIES",
    "TileBuildReport",
    "TileService",
]
