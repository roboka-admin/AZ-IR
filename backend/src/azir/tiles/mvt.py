"""Mapbox Vector Tile encoding (MVT 2.1) without a dependency on protobuf or a tile server.

The atlas needs tiles for one reason: a GeoJSON payload for the whole corpus at z8 does not fit the
byte budget (ADR-0011). Everything here is the specification, written out longhand:

* protobuf wire format: varints, zigzag, length-delimited fields (``tiles/protobuf.py``)
* tile addressing and the archive itself live in ``tiles/pmtiles.py``
* geometry commands: MoveTo / LineTo / ClosePath with zigzag delta parameters
* one layer per map layer, keys/values deduplicated per layer as the spec requires

Why hand-rolled: the alternatives are a C extension (mapbox-vector-tile pulls in protobuf and
shapely ops we already do ourselves), PostGIS ``ST_AsMVT`` (which locks tile generation to the
database -- the fixtures driver could not produce a tile at all, breaking ADR-0014), or shipping
pre-baked tiles from a service we do not control. ``ST_AsMVT`` stays available as an optimisation
for large corpora; this module is what makes the *contract* portable.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .protobuf import encode_message, read_fields, read_packed, unzigzag, varint, zigzag

#: The spec default. 4096 gives sub-metre resolution at city zoom and keeps tiles small.
EXTENT = 4096

#: Geometry types, as the spec numbers them.
UNKNOWN = 0
POINT = 1
LINESTRING = 2
POLYGON = 3

_COMMAND_MOVE_TO = 1
_COMMAND_LINE_TO = 2
_COMMAND_CLOSE_PATH = 7

WEB_MERCATOR_MAX = 20037508.342789244


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """(min_lon, min_lat, max_lon, max_lat) of one tile, in WGS84 degrees."""
    span = 360.0 / (1 << z)
    min_lon = -180.0 + x * span
    max_lon = min_lon + span
    max_lat = _mercator_lat(1.0 - (y / (1 << z)) * 2.0)
    min_lat = _mercator_lat(1.0 - ((y + 1) / (1 << z)) * 2.0)
    return (min_lon, min_lat, max_lon, max_lat)


def _mercator_lat(n: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * n)))


def lonlat_to_tile(
    lon: float, lat: float, z: int, x: int, y: int, extent: int = EXTENT
) -> tuple[int, int]:
    """Project one lon/lat into tile coordinates (0..extent, y down).

    Values outside ``[0, extent]`` are legitimate: the spec allows geometry to reach beyond the tile
    so that lines and labels do not break at the seam. Clipping is the caller's decision.
    """
    size = 1 << z
    world_x = (lon + 180.0) / 360.0 * size
    sin_lat = math.sin(math.radians(max(min(lat, 89.99999), -89.99999)))
    # Parenthesised on purpose: ``0.5 - log(...) / 4pi * size`` would scale only the log term and put
    # every feature a world away. The whole normalised Mercator position is what gets scaled.
    world_y = (0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * size
    return (
        round((world_x - x) * extent),
        round((world_y - y) * extent),
    )


def tiles_for_bounds(
    bounds: tuple[float, float, float, float], max_zoom: int
) -> Iterable[tuple[int, int, int]]:
    """Every (z, x, y) that intersects ``bounds``, for z in ``0..max_zoom``."""
    min_lon, min_lat, max_lon, max_lat = bounds
    for z in range(max_zoom + 1):
        size = 2**z
        x_min = max(0, math.floor((min_lon + 180.0) / 360.0 * size))
        x_max = min(size - 1, math.floor((max_lon + 180.0) / 360.0 * size))
        y_min = max(0, _lat_to_tile_y(max_lat, z))
        y_max = min(size - 1, _lat_to_tile_y(min_lat, z))
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                yield (z, x, y)


def _lat_to_tile_y(lat: float, z: int) -> int:
    sin_lat = math.sin(math.radians(max(min(lat, 89.99999), -89.99999)))
    return math.floor(
        (0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * (1 << z)
    )


@dataclass(frozen=True, slots=True)
class MvtFeature:
    """One feature in tile coordinates.

    ``geometry`` is the shape the spec wants: points and lines are lists of positions, polygons are
    lists of rings (first outer, then holes), each position a ``(x, y)`` integer pair in
    ``0..extent``.
    """

    geometry_type: int
    geometry: tuple[Any, ...]
    properties: Mapping[str, Any] = field(default_factory=dict)
    #: A stable numeric id. MVT ids are uint64, so a text id cannot be used directly: the caller
    # passes a hash of the entity id and keeps the text id in the properties.
    feature_id: int | None = None

    def encode(self, keys: list[str], values: list[Any], index: dict[str, int]) -> bytes:
        """Encode against the layer's deduplicated key/value tables (the spec's whole point)."""
        tags: list[int] = []
        for name, value in self.properties.items():
            key_at = _intern(keys, index, "k", name)
            value_at = _intern_value(values, index, value)
            tags.extend((key_at, value_at))
        fields: list[tuple[int, Any]] = []
        if self.feature_id is not None:
            fields.append((1, ("varint", int(self.feature_id))))
        if tags:
            fields.append((2, ("packed", [varint(item) for item in tags])))
        fields.append((3, ("varint", int(self.geometry_type))))
        fields.append((4, ("packed", [varint(item) for item in self.commands()])))
        return encode_message(fields)

    def commands(self) -> list[int]:
        """The command sequence: ``[id | count<<3, zigzag(dx), zigzag(dy), ...]``."""
        out: list[int] = []
        cursor_x = cursor_y = 0

        def move_to(points: Sequence[tuple[int, int]]) -> None:
            nonlocal cursor_x, cursor_y
            out.append((_COMMAND_MOVE_TO & 0x7) | (len(points) << 3))
            for x, y in points:
                out.append(zigzag(int(x) - cursor_x))
                out.append(zigzag(int(y) - cursor_y))
                cursor_x, cursor_y = int(x), int(y)

        def line_to(points: Sequence[tuple[int, int]]) -> None:
            nonlocal cursor_x, cursor_y
            out.append((_COMMAND_LINE_TO & 0x7) | (len(points) << 3))
            for x, y in points:
                out.append(zigzag(int(x) - cursor_x))
                out.append(zigzag(int(y) - cursor_y))
                cursor_x, cursor_y = int(x), int(y)

        if self.geometry_type is POINT:
            move_to([tuple(point) for point in self.geometry])
        elif self.geometry_type is LINESTRING:
            for line in self.geometry:
                positions = _distinct_positions(line)
                if len(positions) < 2:
                    continue
                move_to(positions[:1])
                line_to(positions[1:])
        elif self.geometry_type is POLYGON:
            for polygon in self.geometry:
                for ring in polygon:
                    positions = _distinct_ring(ring)
                    if len(positions) < 3:
                        continue  # a ring needs three distinct vertices to enclose anything
                    move_to(positions[:1])
                    line_to(positions[1:])
                    out.append((_COMMAND_CLOSE_PATH & 0x7) | (1 << 3))
                    cursor_x, cursor_y = positions[0]
        return out


def _distinct_positions(line: Sequence[Any]) -> list[tuple[int, int]]:
    """Integer positions with consecutive duplicates removed (rounding leaves plenty)."""
    out: list[tuple[int, int]] = []
    for point in line:
        position = (int(point[0]), int(point[1]))
        if not out or out[-1] != position:
            out.append(position)
    return out


def _distinct_ring(ring: Sequence[Any]) -> list[tuple[int, int]]:
    """The distinct vertices of a ring, in order.

    Callers hand over rings either open or already closed (GeoJSON closes them; a clipped shapely
    ring does too), and both must encode identically: ClosePath closes the ring, so a repeated final
    vertex would be a zero-length segment.
    """
    positions = _distinct_positions(ring)
    if len(positions) > 1 and positions[0] == positions[-1]:
        positions.pop()
    return positions


def _intern(keys: list[str], index: dict[str, int], prefix: str, name: str) -> int:
    token = f"{prefix}:{name}"
    if token not in index:
        index[token] = len(keys)
        keys.append(name)
    return index[token]


def _intern_value(values: list[Any], index: dict[str, int], value: Any) -> int:
    token = f"v:{type(value).__name__}:{value!r}"
    if token not in index:
        index[token] = len(values)
        values.append(value)
    return index[token]


def encode_value(value: Any) -> bytes:
    """One ``Tile.Value``: exactly one of the seven optional fields, per the spec."""
    if isinstance(value, bool):  # before int: bool is a subclass of int in Python
        return encode_message([(7, ("varint", 1 if value else 0))])
    if isinstance(value, int):
        if value < 0:
            return encode_message([(6, ("varint", zigzag(value)))])  # sint_value
        return encode_message([(5, ("varint", value))])  # uint_value
    if isinstance(value, float):
        return encode_message([(3, ("fixed64", value))])  # double_value
    return encode_message([(1, ("string", str(value)))])


@dataclass(frozen=True, slots=True)
class MvtLayer:
    """One named layer of a tile. Layer order in the file is the painter's order."""

    name: str
    features: tuple[MvtFeature, ...]
    extent: int = EXTENT
    version: int = 2

    def encode(self) -> bytes:
        keys: list[str] = []
        values: list[Any] = []
        index: dict[str, int] = {}
        encoded_features = [
            feature.encode(keys, values, index) for feature in self.features
        ]
        fields: list[tuple[int, Any]] = [(1, ("string", self.name))]
        fields.extend((2, ("bytes", item)) for item in encoded_features)
        fields.extend((3, ("string", key)) for key in keys)
        fields.extend((4, ("bytes", encode_value(value))) for value in values)
        fields.append((5, ("varint", int(self.extent))))
        fields.append((15, ("varint", int(self.version))))
        return encode_message(fields)


def encode_tile(layers: Sequence[MvtLayer]) -> bytes:
    """A whole tile. ``Tile.layers`` is field 3 -- the one quirk of the spec worth remembering."""
    return b"".join(encode_message([(3, ("bytes", layer.encode()))]) for layer in layers)



def decode_tile(data: bytes) -> list[dict[str, Any]]:
    """Decode a tile into plain dictionaries.

    Serving tiles never needs this; checking that we *wrote* them correctly does. It is what
    ``azir tiles inspect`` prints, and what the tests assert against, so a byte-level mistake in the
    encoder shows up as a wrong value here rather than as a blank map in the browser.

    Tags are resolved in a second pass: a layer lists its features before its key/value tables, so
    the tables are not known yet when a feature is read.
    """
    layers: list[dict[str, Any]] = []
    for number, _wire_type, payload in read_fields(data):
        if number != 3:
            continue
        layers.append(_decode_layer(bytes(payload)))
    return layers


def _decode_layer(data: bytes) -> dict[str, Any]:
    name = ""
    extent = EXTENT
    version = 1
    keys: list[str] = []
    values: list[Any] = []
    raw_features: list[tuple[int | None, list[int], int, list[int]]] = []
    for number, _wire_type, payload in read_fields(data):
        if number == 1:
            name = bytes(payload).decode("utf-8")
        elif number == 2:
            raw_features.append(_decode_feature(bytes(payload)))
        elif number == 3:
            keys.append(bytes(payload).decode("utf-8"))
        elif number == 4:
            values.append(_decode_value(bytes(payload)))
        elif number == 5:
            extent = int(payload)
        elif number == 15:
            version = int(payload)
    features = [
        {
            "id": feature_id,
            "type": geometry_type,
            "properties": {
                keys[tags[index]]: values[tags[index + 1]]
                for index in range(0, len(tags) - 1, 2)
            },
            "geometry": _decode_commands(geometry_type, commands),
        }
        for feature_id, tags, geometry_type, commands in raw_features
    ]
    return {
        "name": name,
        "version": version,
        "extent": extent,
        "keys": keys,
        "values": values,
        "features": features,
    }


def _decode_feature(data: bytes) -> tuple[int | None, list[int], int, list[int]]:
    feature_id: int | None = None
    tags: list[int] = []
    geometry_type = UNKNOWN
    commands: list[int] = []
    for number, _wire_type, payload in read_fields(data):
        if number == 1:
            feature_id = int(payload)
        elif number == 2:
            tags = read_packed(bytes(payload))
        elif number == 3:
            geometry_type = int(payload)
        elif number == 4:
            commands = read_packed(bytes(payload))
    return (feature_id, tags, geometry_type, commands)


def _decode_value(data: bytes) -> Any:
    for number, _wire_type, payload in read_fields(data):
        if number == 1:
            return bytes(payload).decode("utf-8")
        if number == 3:
            return float(payload)
        if number == 5:
            return int(payload)
        if number == 6:
            return unzigzag(int(payload))
        if number == 7:
            return bool(int(payload))
    return None


def _decode_commands(
    geometry_type: int, commands: Sequence[int]
) -> list[Any]:
    """Turn the command stream back into positions.

    The result mirrors what the encoder was given, so a round-trip can be asserted directly:

    * ``POINT`` -> ``[(x, y), ...]``
    * ``LINESTRING`` -> ``[[(x, y), ...], ...]`` one list per line
    * ``POLYGON`` -> ``[[(x, y), ...], ...]`` one list per *ring* (closed, first outer then holes).
      Regrouping rings into polygons needs winding-order analysis, which a decoder used for checking
      does not have to do: the rings are what the renderer walks anyway.
    """
    shapes: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    cursor_x = cursor_y = 0
    index = 0
    while index < len(commands):
        command = commands[index]
        index += 1
        command_id = command & 0x7
        count = command >> 3
        if command_id == _COMMAND_MOVE_TO:
            if current:  # a MoveTo always starts a new shape
                shapes.append(current)
            current = []
            for _ in range(count):
                cursor_x += unzigzag(commands[index])
                cursor_y += unzigzag(commands[index + 1])
                index += 2
                current.append((cursor_x, cursor_y))
        elif command_id == _COMMAND_LINE_TO:
            for _ in range(count):
                cursor_x += unzigzag(commands[index])
                cursor_y += unzigzag(commands[index + 1])
                index += 2
                current.append((cursor_x, cursor_y))
        elif command_id == _COMMAND_CLOSE_PATH:
            current.append(current[0])
            shapes.append(current)
            current = []
        else:
            raise ValueError(f"unknown geometry command id {command_id}")
    if current:
        shapes.append(current)
    if geometry_type == POINT:
        return [position for shape in shapes for position in shape]
    return shapes


__all__ = [
    "EXTENT",
    "LINESTRING",
    "POINT",
    "POLYGON",
    "UNKNOWN",
    "MvtFeature",
    "MvtLayer",
    "decode_tile",
    "encode_tile",
    "encode_value",
    "lonlat_to_tile",
    "tile_bounds",
    "tiles_for_bounds",
]
