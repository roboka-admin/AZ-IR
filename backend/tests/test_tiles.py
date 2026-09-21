"""Tiles: the protobuf wire format, MVT, the PMTiles archive, and the service that ties them together.

Everything here is asserted on *bytes*, not on intentions. Both formats are specifications and the
reader is not ours -- MapLibre plus the ``pmtiles`` JavaScript library -- so a wrong guess does not
raise an exception, it renders a blank map. Two consequences for these tests:

* the specification's own examples are the test vectors (TileIDs from PMTiles v3 §4.1, the winding
  rule from MVT §4.3.5.5, the directory layout from appendix A.1/A.2)
* every writer is exercised by reading its output back, including a full archive round-trip

The last test in the file is the one that matters most operationally: the same tile, rendered from
the fixtures driver and from PostGIS, must describe the same features (ADR-0014).
"""

from __future__ import annotations

import gzip
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from azir.core.config import Settings
from azir.domain.enums import Certainty, EntityType, GeometryKind, Status
from azir.domain.geo import BBox, GeometryRecord
from azir.domain.model import EntityRecord
from azir.repositories.fixtures import FixturesRepository
from azir.services.atlas import AtlasService
from azir.services.tiles import (
    LAYER_ORDER,
    TILE_PROPERTIES,
    TILESET_VERSION,
    TileService,
    _geometries_for_zoom,
    _project_array,
)
from azir.tiles import pmtiles
from azir.tiles.mvt import (
    EXTENT,
    LINESTRING,
    POINT,
    POLYGON,
    MvtFeature,
    MvtLayer,
    decode_tile,
    encode_tile,
    lonlat_to_tile,
    tile_bounds,
    tiles_for_bounds,
)
from azir.tiles.protobuf import (
    encode_message,
    read_fields,
    read_packed,
    unvarint,
    unzigzag,
    varint,
    zigzag,
)

# --------------------------------------------------------------------------- helpers


def tile_of(lon: float, lat: float, z: int) -> tuple[int, int]:
    """The tile containing a lon/lat, computed independently of the module under test."""
    size = 1 << z
    x = int((lon + 180.0) / 360.0 * size)
    sin_lat = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * size)
    return (x, y)


@pytest.fixture(scope="module")
def tile_service(settings: Settings, fixtures_repo: FixturesRepository) -> TileService:
    return TileService(AtlasService(fixtures_repo, settings), fixtures_repo, settings)


@pytest.fixture(scope="module")
def center_tile(settings: Settings) -> tuple[int, int, int]:
    """A tile over the study area centre: derived from settings, so it cannot go stale."""
    lon, lat = settings.study_area_center
    x, y = tile_of(lon, lat, 7)
    return (7, x, y)


# --------------------------------------------------------------------------- protobuf


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 16384, 2**35, 2**64 - 1])
def test_varint_round_trips(value: int) -> None:
    assert unvarint(varint(value))[0] == value


def test_varint_uses_the_documented_widths() -> None:
    # One byte below 128, two bytes from there on: the wire format's only real promise.
    assert varint(127) == b"\x7f"
    assert varint(128) == b"\x80\x01"
    assert varint(300) == b"\xac\x02"


def test_varint_refuses_negative_numbers() -> None:
    with pytest.raises(ValueError, match="zigzag"):
        varint(-1)


@pytest.mark.parametrize("value", [0, 1, -1, 2, -2, 2**40, -(2**40)])
def test_zigzag_round_trips(value: int) -> None:
    encoded = zigzag(value)
    assert encoded >= 0
    assert unzigzag(encoded) == value
    assert unzigzag(unvarint(varint(encoded))[0]) == value


def test_message_round_trips_every_wire_type() -> None:
    payload = encode_message(
        [
            (1, ("string", "اردبیل")),
            (2, ("varint", 1500)),
            (3, ("fixed64", 38.25)),
            (4, ("packed", [varint(7), varint(9)])),
            (5, ("bytes", b"\x00\x01")),
            (6, ("fixed32", 0.5)),
        ]
    )
    fields = {number: value for number, _wire, value in read_fields(payload)}
    assert fields[1].decode("utf-8") == "اردبیل"
    assert fields[2] == 1500
    assert fields[3] == pytest.approx(38.25)
    assert read_packed(fields[4]) == [7, 9]
    assert fields[5] == b"\x00\x01"
    assert fields[6] == pytest.approx(0.5, abs=1e-6)


def test_truncated_varint_is_an_error_not_a_hang() -> None:
    with pytest.raises(ValueError, match="truncated"):
        unvarint(b"\x80\x80")


# --------------------------------------------------------------------------- MVT


def test_tile_ids_match_the_specification_examples() -> None:
    # PMTiles v3 §4.1: the Hilbert-curve numbering, taken verbatim from the spec's table.
    assert pmtiles.tile_id(0, 0, 0) == 0
    assert pmtiles.tile_id(1, 0, 0) == 1
    assert pmtiles.tile_id(1, 0, 1) == 2
    assert pmtiles.tile_id(1, 1, 1) == 3
    assert pmtiles.tile_id(1, 1, 0) == 4
    assert pmtiles.tile_id(2, 0, 0) == 5
    assert pmtiles.tile_id(12, 3423, 1763) == 19078479


@pytest.mark.parametrize(
    ("zoom", "x", "y", "expected"),
    [
        (0, 0, 0, 0),
        (1, 0, 0, 1),
        (1, 1, 1, 3),
        (3, 5, 2, 76),
        (5, 17, 11, 1209),
        (7, 81, 49, 18941),
        (9, 300, 190, 309507),
        (12, 3423, 1763, 19078479),
        (14, 9000, 5000, 318095829),
    ],
)
def test_tile_ids_match_the_reader_the_browser_uses(zoom: int, x: int, y: int, expected: int) -> None:
    # Every `expected` value was read out of `zxyToTileId` in pmtiles@4.5.0 -- the JavaScript library
    # MapLibre uses to fetch tiles. The spec table above proves the implementation follows the
    # document; this proves it agrees with the *other* implementation that has to read our archives.
    # A writer and a reader that disagree about tile ids agree on nothing else.
    assert pmtiles.tile_id(zoom, x, y) == expected


def test_every_tile_id_at_a_zoom_is_unique_and_contiguous() -> None:
    ids = sorted(pmtiles.tile_id(3, x, y) for x in range(8) for y in range(8))
    assert ids == list(range(21, 21 + 64))  # base for z=3 is (4**3 - 1) // 3 == 21


def test_point_line_and_polygon_survive_a_round_trip() -> None:
    layer = MvtLayer(
        name="places",
        features=(
            MvtFeature(
                feature_id=7,
                geometry_type=POINT,
                geometry=((100, 200), (300, 400)),
                properties={"label": "اردبیل", "rank": 79.31, "disputed": True, "t_from": -1500},
            ),
            MvtFeature(
                feature_id=8,
                geometry_type=LINESTRING,
                geometry=(((0, 0), (10, 20), (30, 40)),),
                properties={"label": "رود"},
            ),
            MvtFeature(
                feature_id=9,
                geometry_type=POLYGON,
                geometry=((((0, 0), (4096, 0), (4096, 4096), (0, 4096)),),),
                properties={"label": "شهرستان"},
            ),
        ),
    )
    decoded = decode_tile(encode_tile([layer]))
    assert len(decoded) == 1
    read = decoded[0]
    assert read["name"] == "places"
    assert read["version"] == 2
    assert read["extent"] == EXTENT

    point, line, polygon = read["features"]
    assert point["id"] == 7
    assert point["type"] == POINT
    assert point["geometry"] == [(100, 200), (300, 400)]
    assert point["properties"] == {
        "label": "اردبیل",
        "rank": 79.31,
        "disputed": True,
        "t_from": -1500,
    }
    assert line["geometry"] == [[(0, 0), (10, 20), (30, 40)]]
    # A closed ring comes back closed: the encoder emits ClosePath, the decoder re-adds the vertex.
    assert polygon["geometry"] == [[(0, 0), (4096, 0), (4096, 4096), (0, 4096), (0, 0)]]


def test_keys_and_values_are_deduplicated_per_layer() -> None:
    """The whole point of the key/value tables: repeated properties cost an index, not bytes."""
    features = tuple(
        MvtFeature(
            geometry_type=POINT,
            geometry=((index, index),),
            properties={"layer": "places", "rank": 10.0, "n": index},
        )
        for index in range(5)
    )
    read = decode_tile(encode_tile([MvtLayer(name="places", features=features)]))[0]
    assert read["keys"].count("layer") == 1
    assert read["keys"].count("rank") == 1
    assert read["values"].count("places") == 1
    assert read["values"].count(10.0) == 1
    assert len(read["features"]) == 5
    assert [feature["properties"]["n"] for feature in read["features"]] == [0, 1, 2, 3, 4]


def test_value_encoding_covers_the_seven_spec_types() -> None:
    read = decode_tile(
        encode_tile(
            [
                MvtLayer(
                    name="values",
                    features=(
                        MvtFeature(
                            geometry_type=POINT,
                            geometry=((0, 0),),
                            properties={
                                "text": "متن",
                                "positive": 42,
                                "negative": -42,
                                "float": 1.5,
                                "true": True,
                                "false": False,
                                "zero": 0,
                            },
                        ),
                    ),
                )
            ]
        )
    )[0]
    properties = read["features"][0]["properties"]
    assert properties == {
        "text": "متن",
        "positive": 42,
        "negative": -42,
        "float": 1.5,
        "true": True,
        "false": False,
        "zero": 0,
    }
    # bool must not be swallowed by the int branch: True and 1 are different tile values.
    assert 42 in read["values"] and True in read["values"] and False in read["values"]


def test_a_degenerate_ring_or_line_is_dropped_not_encoded() -> None:
    read = decode_tile(
        encode_tile(
            [
                MvtLayer(
                    name="thin",
                    features=(
                        MvtFeature(
                            geometry_type=LINESTRING, geometry=(((5, 5),),), properties={}
                        ),
                        MvtFeature(
                            geometry_type=POLYGON,
                            geometry=((((0, 0), (1, 1)),),),
                            properties={},
                        ),
                        MvtFeature(geometry_type=POINT, geometry=((1, 1),), properties={}),
                    ),
                )
            ]
        )
    )[0]
    assert [feature["geometry"] for feature in read["features"]] == [[], [], [(1, 1)]]


def test_an_empty_tile_encodes_to_nothing() -> None:
    assert encode_tile([]) == b""
    assert decode_tile(b"") == []


# --------------------------------------------------------------------------- geometry policy


def _record(
    entity_id: str,
    geometries: Sequence[GeometryRecord] = (),
    **kwargs: Any,
) -> EntityRecord:
    return EntityRecord(
        id=entity_id,
        entity_type=EntityType.PLACE,
        status=Status.PUBLISHED,
        geometries=tuple(geometries),
        **kwargs,
    )


def _geometry(
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    lod_min: float = 0.0,
    lod_max: float = 22.0,
    coordinates: Any = None,
) -> GeometryRecord:
    return GeometryRecord(
        geojson=coordinates
        or {"type": "Point", "coordinates": [48.29, 38.25]},  # type: ignore[typeddict-item]
        kind=GeometryKind.POINT,
        certainty=Certainty.EXACT,
        lod_min_zoom=lod_min,
        lod_max_zoom=lod_max,
        year_from=year_from,
        year_to=year_to,
    )


def test_one_geometry_per_period_and_the_lod_that_fits_the_zoom() -> None:
    """A tile holds every *period* of a thing, but only one *level of detail* per period."""
    coarse = _geometry(year_from=1501, year_to=1600, lod_min=0.0, lod_max=8.0)
    detailed = _geometry(year_from=1501, year_to=1600, lod_min=8.0, lod_max=22.0)
    later = _geometry(year_from=1601, year_to=1736, lod_min=0.0, lod_max=22.0)
    entity = _record("pol_test", geometries=[coarse, detailed, later])

    at_low_zoom = _geometries_for_zoom(entity, 5.0)
    assert coarse in at_low_zoom and detailed not in at_low_zoom
    assert later in at_low_zoom
    assert len(at_low_zoom) == 2  # two periods, not three geometries

    at_high_zoom = _geometries_for_zoom(entity, 12.0)
    assert detailed in at_high_zoom and coarse not in at_high_zoom


def test_an_lod_gap_never_hides_an_entity() -> None:
    """If no variant covers this zoom, the nearest one is used: a gap in LOD is a data problem, not
    a reason to make a city disappear from the map."""
    only_detail = _geometry(lod_min=14.0, lod_max=22.0)
    entity = _record("plc_test", geometries=[only_detail])
    assert _geometries_for_zoom(entity, 3.0) == [only_detail]


def test_a_geometry_without_coordinates_is_skipped() -> None:
    empty = GeometryRecord(
        geojson={"type": "Point", "coordinates": []},  # type: ignore[typeddict-item]
        kind=GeometryKind.POINT,
    )
    assert _geometries_for_zoom(_record("plc_empty", geometries=[empty]), 7.0) == []


# --------------------------------------------------------------------------- projection


def test_projection_puts_a_coordinate_inside_its_own_tile() -> None:
    """The invariant a wrong Mercator formula breaks: a point lands in the tile that contains it.

    Coordinates come from the corpus bbox corners plus the study centre, at several zooms.
    """
    for lon, lat in [(48.29, 38.25), (46.29, 38.08), (42.5, 34.0), (51.0, 41.3), (47.0, 38.0)]:
        for zoom in (0, 3, 7, 10):
            x, y = tile_of(lon, lat, zoom)
            tile_x, tile_y = lonlat_to_tile(lon, lat, zoom, x, y)
            assert 0 <= tile_x <= EXTENT, (lon, lat, zoom, tile_x)
            assert 0 <= tile_y <= EXTENT, (lon, lat, zoom, tile_y)


def test_tile_bounds_and_tiles_for_bounds_agree() -> None:
    bounds = (42.5, 34.0, 51.0, 41.3)
    tiles = list(tiles_for_bounds(bounds, 4))
    assert tiles[0] == (0, 0, 0)
    for z, x, y in tiles:
        min_lon, min_lat, max_lon, max_lat = tile_bounds(z, x, y)
        assert min_lon <= bounds[2] and max_lon >= bounds[0]
        assert min_lat <= bounds[3] and max_lat >= bounds[1]


def test_the_vectorised_projection_matches_the_reference_one() -> None:
    """``_project_array`` exists for speed; this is why it is allowed to exist."""
    z, x, y = 9, *tile_of(48.29, 38.25, 9)
    points = [(48.29, 38.25), (46.29, 38.08), (42.51, 34.02), (50.99, 41.29), (47.0, 38.0)]
    array = _project_array([[lon, lat] for lon, lat in points], z, x, y)
    for index, (lon, lat) in enumerate(points):
        expected = lonlat_to_tile(lon, lat, z, x, y)
        assert (round(float(array[index][0])), round(float(array[index][1]))) == expected


# --------------------------------------------------------------------------- directories


def test_directory_round_trips_runs_and_gaps() -> None:
    entries = [
        pmtiles.DirEntry(tile_id=0, offset=0, length=100, run_length=1),
        pmtiles.DirEntry(tile_id=1, offset=100, length=100, run_length=3),  # contiguous
        pmtiles.DirEntry(tile_id=9, offset=500, length=42, run_length=1),  # a gap
        pmtiles.DirEntry(tile_id=20, offset=0, length=77, run_length=0),  # a leaf pointer
    ]
    assert pmtiles.decode_directory(pmtiles.encode_directory(entries)) == entries


def test_directory_encoding_follows_appendix_a1() -> None:
    """Byte-level check of the offsets rule: ``0`` for contiguous, ``offset + 1`` otherwise."""
    encoded = pmtiles.encode_directory(
        [
            pmtiles.DirEntry(tile_id=5, offset=0, length=10, run_length=1),
            pmtiles.DirEntry(tile_id=6, offset=10, length=10, run_length=1),
            pmtiles.DirEntry(tile_id=8, offset=99, length=10, run_length=1),
        ]
    )
    # count=3, then delta ids 5,1,2; run lengths 1,1,1; lengths 10,10,10; offsets 1,0,100
    assert encoded == bytes([3, 5, 1, 2, 1, 1, 1, 10, 10, 10, 1, 0, 100])


def test_a_directory_with_no_entries_is_refused() -> None:
    with pytest.raises(ValueError, match="no entries"):
        pmtiles.encode_directory([])


# --------------------------------------------------------------------------- header


def test_header_is_127_bytes_and_round_trips() -> None:
    header = pmtiles.Header(
        root_dir_offset=127,
        root_dir_length=501,
        metadata_offset=628,
        metadata_length=900,
        leaf_dirs_offset=0,
        leaf_dirs_length=0,
        tile_data_offset=1528,
        tile_data_length=4096,
        addressed_tiles=100,
        tile_entries=98,
        tile_contents=90,
        clustered=pmtiles.CLUSTERED,
        internal_compression=pmtiles.COMPRESSION_NONE,
        tile_compression=pmtiles.COMPRESSION_GZIP,
        tile_type=pmtiles.TILE_TYPE_MVT,
        min_zoom=0,
        max_zoom=9,
        bounds=(42.5, 34.0, 51.0, 41.3),
        center_zoom=6,
        center=(47.0, 38.0),
    )
    raw = header.encode()
    assert len(raw) == pmtiles.HEADER_LENGTH == 127
    assert raw[:7] == b"PMTiles"
    assert raw[7] == 3
    read = pmtiles.read_header(raw)
    assert read == header
    # Positions are stored as degrees * 1e7, so they come back to within a ten-millionth.
    assert read.bounds == pytest.approx(header.bounds, abs=1e-7)


def test_a_header_that_is_not_pmtiles_is_refused() -> None:
    header = pmtiles.Header(
        127, 1, 128, 1, 0, 0, 129, 1, 1, 1, 1, 1, 1, 2, 1, 0, 1, (0.0, 0.0, 1.0, 1.0), 0, (0.5, 0.5)
    )
    with pytest.raises(ValueError, match="not a PMTiles"):
        pmtiles.read_header(b"NOPEles" + header.encode()[7:])
    with pytest.raises(ValueError, match="spec version"):
        pmtiles.read_header(header.encode()[:7] + b"\x02" + header.encode()[8:])
    with pytest.raises(ValueError, match="127 bytes"):
        pmtiles.read_header(header.encode()[:50])


# --------------------------------------------------------------------------- archive


def _sample_tiles(count: int) -> list[tuple[int, int, int, bytes]]:
    """Synthetic tiles: distinct payloads plus one repeated blob to exercise deduplication."""
    tiles = [(0, 0, 0, b"root tile")]
    for index in range(count):
        tiles.append((5, index, 0, b"identical" if index < 3 else f"tile {index}".encode()))
    return tiles


def test_archive_round_trips_every_tile(tmp_path: Path) -> None:
    path = tmp_path / "atlas.pmtiles"
    tiles = _sample_tiles(6)
    metadata = {
        "name": "test",
        "version": TILESET_VERSION,
        "vector_layers": [{"id": "places", "fields": {"id": "String"}}],
    }
    report = pmtiles.write_archive(
        path,
        tiles,
        metadata=metadata,
        bounds=(42.5, 34.0, 51.0, 41.3),
        min_zoom=0,
        max_zoom=5,
    )
    assert path.is_file()
    assert report.bytes == path.stat().st_size
    assert report.tiles == len(tiles)
    assert report.contents < len(tiles)  # the three identical blobs were stored once

    with path.open("rb") as handle:
        header = pmtiles.read_header(handle.read(pmtiles.HEADER_LENGTH))
        assert header.tile_type == pmtiles.TILE_TYPE_MVT
        assert header.tile_compression == pmtiles.COMPRESSION_GZIP
        assert header.addressed_tiles == len(tiles)
        assert pmtiles.read_metadata(handle, header) == metadata
        for z, x, y, payload in tiles:
            assert pmtiles.find_tile(handle, header, z, x, y) == payload
        assert pmtiles.find_tile(handle, header, 5, 4, 4) is None  # never written


def test_run_length_covers_consecutive_identical_tiles(tmp_path: Path) -> None:
    path = tmp_path / "runs.pmtiles"
    pmtiles.write_archive(
        path,
        [(3, x, 0, b"same") for x in range(8)],
        metadata={"vector_layers": []},
        bounds=(0.0, 0.0, 1.0, 1.0),
        min_zoom=3,
        max_zoom=3,
    )
    with path.open("rb") as handle:
        header = pmtiles.read_header(handle.read(pmtiles.HEADER_LENGTH))
        entries = pmtiles.decode_directory(
            handle.read(header.root_dir_length)[0 : header.root_dir_length]
        )
        assert header.tile_contents == 1
        assert sum(entry.run_length for entry in entries) == 8
        for x in range(8):
            assert pmtiles.find_tile(handle, header, 3, x, 0) == b"same"


def test_leaf_directories_are_used_when_the_root_would_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root must stay inside 16 KiB; beyond that, leaves -- and reads still work."""
    # The budget is squeezed to sit between the two sizes that matter: 36 flat entries do not fit
    # (~160 bytes), but 18 leaf pointers do (~74). That is the real situation, just at 1/200 scale.
    monkeypatch.setattr(pmtiles, "ROOT_BUDGET", 100)
    monkeypatch.setattr(pmtiles, "LEAF_ENTRIES", 2)
    path = tmp_path / "leaves.pmtiles"
    tiles = [(7, x, y, f"tile {x} {y}".encode()) for x in range(6) for y in range(6)]
    report = pmtiles.write_archive(
        path,
        tiles,
        metadata={"vector_layers": []},
        bounds=(0.0, 0.0, 1.0, 1.0),
        min_zoom=7,
        max_zoom=7,
    )
    assert report.leaf_directories == 18  # 36 tiles, two entries per leaf
    with path.open("rb") as handle:
        header = pmtiles.read_header(handle.read(pmtiles.HEADER_LENGTH))
        assert header.leaf_dirs_length > 0
        root = pmtiles.decode_directory(
            _read_at(handle, header.root_dir_offset, header.root_dir_length)
        )
        assert all(entry.is_leaf for entry in root)
        for z, x, y, payload in tiles:
            assert pmtiles.find_tile(handle, header, z, x, y) == payload


def test_an_archive_with_no_tiles_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no tiles"):
        pmtiles.write_archive(
            tmp_path / "empty.pmtiles",
            [],
            metadata={"vector_layers": []},
            bounds=(0.0, 0.0, 1.0, 1.0),
            min_zoom=0,
            max_zoom=1,
        )


def test_brotli_is_refused_rather_than_silently_written(tmp_path: Path) -> None:
    """An archive we cannot decompress on read is an archive a browser cannot read either."""
    with pytest.raises(ValueError, match="not supported"):
        pmtiles.write_archive(
            tmp_path / "brotli.pmtiles",
            [(0, 0, 0, b"x")],
            metadata={"vector_layers": []},
            bounds=(0.0, 0.0, 1.0, 1.0),
            min_zoom=0,
            max_zoom=0,
            tile_compression=pmtiles.COMPRESSION_BROTLI,
        )


def _read_at(handle: Any, offset: int, length: int) -> bytes:
    handle.seek(offset)
    return handle.read(length)


# --------------------------------------------------------------------------- service


def test_a_tile_carries_the_layers_and_properties_the_style_needs(
    tile_service: TileService, center_tile: tuple[int, int, int]
) -> None:
    z, x, y = center_tile
    raw = tile_service.tile(z, x, y)
    assert raw, "the study-area centre must not be an empty tile"
    layers = decode_tile(raw)
    names = [layer["name"] for layer in layers]
    assert names == [name for name in LAYER_ORDER if name in names]  # paint order is preserved

    properties = layers[0]["features"][0]["properties"]
    for key in properties:
        assert key in TILE_PROPERTIES, f"undocumented tile property {key!r}"
    for key in ("id", "entity_type", "layer", "label", "rank", "certainty", "href"):
        assert key in properties
    assert properties["status"] == "published"
    assert properties["dir"] in {"rtl", "ltr"}


def test_each_entity_has_at_most_one_dedicated_label_anchor_per_tile(
    tile_service: TileService, center_tile: tuple[int, int, int]
) -> None:
    z, x, y = center_tile
    features = [
        feature
        for layer in decode_tile(tile_service.tile(z, x, y))
        for feature in layer["features"]
        if feature["properties"].get("label_anchor") is True
    ]
    assert features, "atlas labels need dedicated anchors in vector delivery"
    # Regular entities must have exactly one anchor per tile; capital markers are an
    # exception: one polity may have several distinct capitals (different places) in the
    # same tile in all_time mode, so uniqueness is by (polity_id, capital_place_id).
    regular = [
        f["properties"]["id"]
        for f in features
        if not f["properties"].get("is_capital")
    ]
    assert len(regular) == len(set(regular)), "duplicate label anchors for regular entities"
    capitals = [
        (f["properties"]["id"], f["properties"].get("capital_place_id") or f["id"])
        for f in features
        if f["properties"].get("is_capital")
    ]
    assert len(capitals) == len(set(capitals)), "duplicate capital anchors for same place"
    # Combined check: at least the non-capital anchors are unique, and capital anchors are
    # unique by place.
    anchors = regular + [f"{pid}__{place}" for pid, place in capitals]
    assert len(anchors) == len(set(anchors))


def test_a_tile_is_time_agnostic_and_says_when_each_feature_was_true(
    tile_service: TileService, center_tile: tuple[int, int, int]
) -> None:
    """No tile means "now": features carry their own windows so the timeline can filter them."""
    z, x, y = center_tile
    features = [
        feature
        for layer in decode_tile(tile_service.tile(z, x, y))
        for feature in layer["features"]
    ]
    windows = {
        (feature["properties"].get("t_from"), feature["properties"].get("t_to"))
        for feature in features
    }
    assert len(windows) > 1, "a tile that only covers one period cannot drive a timeline"
    assert any(feature["properties"].get("t_from") is not None for feature in features)


def test_a_place_with_several_periods_appears_once_per_period(
    tile_service: TileService, settings: Settings
) -> None:
    """Lake Urmia in the pilot corpus: an undated point, 1980-2010, 2011-2026.

    This is the temporal model surviving the move to static tiles -- three features, three windows,
    and the client draws whichever the timeline selects.
    """
    lon, lat = 45.4, 37.7
    z = 9  # below this the lake's rank keeps it out of the tile, which is semantic zoom working
    x, y = tile_of(lon, lat, z)
    features = [
        feature
        for layer in decode_tile(tile_service.tile(z, x, y))
        for feature in layer["features"]
        if feature["properties"].get("id") == "plc_lake_urmia"
    ]
    assert len(features) == 3
    geometry_windows = {
        (feature["properties"].get("g_from"), feature["properties"].get("g_to"))
        for feature in features
    }
    assert geometry_windows == {(None, None), (1980, 2010), (2011, 2026)}
    assert {feature["properties"]["g_index"] for feature in features} == {0, 1, 2}


def test_reconstructed_geometry_is_labelled_as_such_in_the_tile(
    tile_service: TileService, center_tile: tuple[int, int, int]
) -> None:
    """AGENTS.md rule 8 reaches the tile: certainty travels with the geometry, so a hatched
    reconstruction cannot be drawn like a surveyed footprint."""
    z, x, y = center_tile
    certainties = {
        feature["properties"]["certainty"]
        for layer in decode_tile(tile_service.tile(z, x, y))
        for feature in layer["features"]
    }
    assert "reconstructed" in certainties


def test_an_empty_tile_is_empty_bytes_not_an_error(tile_service: TileService) -> None:
    assert tile_service.tile(3, 1, 1) == b""


def test_out_of_range_coordinates_are_refused(tile_service: TileService) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        tile_service.tile(3, 8, 0)
    with pytest.raises(ValueError, match="outside the supported range"):
        tile_service.tile(23, 0, 0)


def test_locale_changes_the_label_not_the_geometry(
    tile_service: TileService, center_tile: tuple[int, int, int]
) -> None:
    z, x, y = center_tile
    fa = {
        feature["properties"]["id"]: feature["properties"]["label"]
        for layer in decode_tile(tile_service.tile(z, x, y, locale="fa"))
        for feature in layer["features"]
    }
    en = {
        feature["properties"]["id"]: feature["properties"]["label"]
        for layer in decode_tile(tile_service.tile(z, x, y, locale="en"))
        for feature in layer["features"]
    }
    assert set(fa) == set(en)
    assert any(fa[key] != en[key] for key in fa if fa[key] and en[key])


def test_data_revision_is_stable_and_content_addressed(tile_service: TileService) -> None:
    first = tile_service.data_revision()
    assert first == tile_service.data_revision()
    assert len(first) == 16
    int(first, 16)


def test_vector_layers_metadata_covers_every_layer(tile_service: TileService) -> None:
    metadata = tile_service.archive_metadata(revision="abc", generated_at="2026-01-01T00:00:00Z")
    assert [layer["id"] for layer in metadata["vector_layers"]] == list(LAYER_ORDER)
    assert metadata["version"] == TILESET_VERSION
    assert metadata["azir"]["data_revision"] == "abc"
    for layer in metadata["vector_layers"]:
        assert layer["fields"] == TILE_PROPERTIES


def test_build_archive_writes_the_archive_and_its_pointer(
    tile_service: TileService,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both halves of the pointer have to be read from the same directory the build wrote to.
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    report = tile_service.build_archive(tmp_path, max_zoom=6)
    archive = tmp_path / report.filename
    assert archive.is_file()
    # The locale is in the name because it is in the content: labels are resolved at build time.
    assert report.filename.startswith(f"atlas-fa-{TILESET_VERSION}-")
    assert report.filename.endswith(".pmtiles")
    assert report.tiles_written == report.archive.tiles
    assert report.degraded_tiles == 0

    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert latest["default_locale"] == settings.default_locale
    assert latest["tileset_version"] == TILESET_VERSION
    assert sorted(latest["locales"]) == ["fa"]
    pointer = latest["locales"]["fa"]
    assert pointer == tile_service.pointer("fa")
    assert pointer["filename"] == report.filename
    assert pointer["locale"] == "fa"
    assert pointer["data_revision"] == report.data_revision
    # The pointer must resolve to a URL the API actually serves, or the frontend 404s per tile.
    assert pointer["url"].endswith(f"/api/v1/tiles/archive/{report.filename}")

    with archive.open("rb") as handle:
        header = pmtiles.read_header(handle.read(pmtiles.HEADER_LENGTH))
        metadata = pmtiles.read_metadata(handle, header)
        assert metadata["vector_layers"]
        assert header.min_zoom == settings.tiles_min_zoom
        assert header.max_zoom == 6
        assert header.bounds == pytest.approx(settings.corpus_bbox, abs=1e-7)
        raw = pmtiles.find_tile(handle, header, 6, *tile_of(48.29, 38.25, 6))
        assert raw and decode_tile(raw)


def test_build_archive_can_skip_the_pointer(
    tile_service: TileService, tmp_path: Path
) -> None:
    tile_service.build_archive(tmp_path, max_zoom=4, write_pointer=False)
    assert not (tmp_path / "latest.json").exists()


def test_each_locale_gets_its_own_archive_and_keeps_the_other(
    tile_service: TileService,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two builds of the same data in two languages are two files, and both stay served.

    Labels are baked into tiles at build time, so an archive is language-specific. Naming it without
    the locale would let the second build overwrite the first -- and then an English visitor reads
    Persian labels with nothing anywhere reporting an error.
    """
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    persian = tile_service.build_archive(tmp_path, max_zoom=3, locale="fa")
    english = tile_service.build_archive(tmp_path, max_zoom=3, locale="en")

    assert persian.filename != english.filename
    assert (tmp_path / persian.filename).is_file()
    assert (tmp_path / english.filename).is_file()
    assert tile_service.pointer("fa")["filename"] == persian.filename
    assert tile_service.pointer("en")["filename"] == english.filename
    assert tile_service.pointer_locales() == ["en", "fa"]

    latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert sorted(latest["locales"]) == ["en", "fa"]


def test_a_locale_without_an_archive_gets_no_pointer(
    tile_service: TileService,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serving the wrong language is worse than serving none: the fallback stays live GeoJSON."""
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    tile_service.build_archive(tmp_path, max_zoom=3, locale="fa")
    assert tile_service.pointer("fa") != {}
    assert tile_service.pointer("en") == {}
    assert tile_service.pointer() == tile_service.pointer("fa")  # the default locale


def test_an_archive_from_an_older_tile_contract_is_ignored(
    tile_service: TileService, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale archive must fall back to GeoJSON instead of silently losing new label anchors."""
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    (tmp_path / "old.pmtiles").write_bytes(b"PMTiles\x03")
    (tmp_path / "latest.json").write_text(
        json.dumps({
            "filename": "old.pmtiles",
            "url": "/old.pmtiles",
            "locale": "en",
            "tileset_version": "1.0.0",
        }),
        encoding="utf-8",
    )
    assert tile_service.pointer("en") == {}
    assert tile_service.pointer_locales() == []


def test_a_nonsense_zoom_range_is_refused(tile_service: TileService, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="greater than"):
        tile_service.build_archive(tmp_path, min_zoom=8, max_zoom=4)


def test_pointer_is_empty_when_nothing_has_been_built(
    tile_service: TileService, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path / "missing"))
    assert tile_service.pointer() == {}


def test_a_pointer_to_a_missing_archive_is_ignored(
    tile_service: TileService,
    tmp_path: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale pointer is worse than none: the client would switch mode and then fail per tile."""
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    (tmp_path / "latest.json").write_text(
        json.dumps({"filename": "gone.pmtiles", "url": "/gone.pmtiles"}), encoding="utf-8"
    )
    assert tile_service.pointer() == {}


def test_archive_path_refuses_to_escape_the_tiles_dir(
    tile_service: TileService, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    (tmp_path / "atlas.pmtiles").write_bytes(b"PMTiles")
    assert tile_service.archive_path("atlas.pmtiles") == tmp_path / "atlas.pmtiles"
    for hostile in ("../pyproject.toml", "..", ".", "", "a/b.pmtiles", "atlas.exe", "latest.yaml"):
        assert tile_service.archive_path(hostile) is None, hostile


# --------------------------------------------------------------------------- API


def test_tiles_index_publishes_the_mode(client: Any) -> None:
    body = client.get("/api/v1/tiles/index.json").json()
    data = body["data"]
    assert data["mode"] in {"pmtiles", "dynamic"}
    assert data["tileset_version"] == TILESET_VERSION
    assert data["layers"] == list(LAYER_ORDER)
    assert data["properties"] == TILE_PROPERTIES
    assert body["meta"]["driver"] in {"fixtures", "postgis"}


def test_a_tile_endpoint_serves_gzipped_mvt(client: Any, settings: Settings) -> None:
    x, y = tile_of(*settings.study_area_center, 7)
    response = client.get(f"/api/v1/tiles/7/{x}/{y}.pbf")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.mapbox-vector-tile")
    assert response.headers["content-encoding"] == "gzip"
    # The test client decodes Content-Encoding, so this is already MVT.
    layers = decode_tile(response.content)
    assert layers and layers[0]["features"]
    assert int(response.headers["x-azir-tile-bytes"]) > 0


def test_a_tile_is_cacheable_and_revalidates(client: Any, settings: Settings) -> None:
    x, y = tile_of(*settings.study_area_center, 7)
    url = f"/api/v1/tiles/7/{x}/{y}.pbf"
    first = client.get(url)
    assert "max-age=" in first.headers["cache-control"]
    second = client.get(url, headers={"if-none-match": first.headers["etag"]})
    assert second.status_code == 304


def test_an_empty_tile_is_a_200_with_a_hint(client: Any) -> None:
    response = client.get("/api/v1/tiles/3/1/1.pbf")
    assert response.status_code == 200
    assert response.headers["x-azir-empty"] == "1"
    assert response.content == b""


@pytest.mark.parametrize(
    "path", ["/api/v1/tiles/7/999/49.pbf", "/api/v1/tiles/40/0/0.pbf", "/api/v1/tiles/-1/0/0.pbf"]
)
def test_tiles_outside_the_pyramid_are_404_with_the_error_envelope(client: Any, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    body = response.json()
    assert body["status"] == 404
    assert body["type"].startswith("https://errors.azir.dev/")


def test_dynamic_tiles_can_be_switched_off(
    client: Any, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Production serves the archive from a CDN; the render endpoint is off and says so."""
    monkeypatch.setattr(settings, "tiles_dynamic_enabled", False)
    response = client.get("/api/v1/tiles/7/81/49.pbf")
    assert response.status_code == 503
    assert "PMTiles archive" in response.json()["detail"]
    index = client.get("/api/v1/tiles/index.json").json()["data"]
    assert index["dynamic_enabled"] is False
    assert index["dynamic_template"] is None


def test_the_index_never_hands_out_another_locales_archive(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The regression this pins: ``?locale=en`` used to return the Persian archive."""
    from azir.services.registry import get_repository

    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    repository = get_repository()
    service = TileService(AtlasService(repository, settings), repository, settings)
    service.build_archive(tmp_path, max_zoom=3, locale="fa")

    persian = client.get("/api/v1/tiles/index.json?locale=fa").json()["data"]
    assert persian["mode"] == "pmtiles"
    assert persian["archive"]["locale"] == "fa"
    assert persian["archive_locales"] == ["fa"]

    english = client.get("/api/v1/tiles/index.json?locale=en").json()["data"]
    assert english["mode"] == "dynamic"
    assert english["archive"] == {}
    assert english["archive_locales"] == ["fa"]  # published, so the UI can say why it fell back


def test_a_missing_archive_is_404_and_traversal_is_refused(client: Any) -> None:
    assert client.get("/api/v1/tiles/archive/nope.pmtiles").status_code == 404
    assert client.get("/api/v1/tiles/archive/..%2F..%2Fpyproject.toml").status_code == 404


def test_a_built_archive_is_served_with_range_support(
    client: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The browser reads an archive in slices; without 206 the PMTiles protocol cannot work."""
    from azir.services.registry import get_repository

    monkeypatch.setattr(settings, "tiles_dir", str(tmp_path))
    repository = get_repository()
    service = TileService(AtlasService(repository, settings), repository, settings)
    report = service.build_archive(tmp_path, max_zoom=4)

    index = client.get("/api/v1/tiles/index.json").json()["data"]
    assert index["mode"] == "pmtiles"
    assert index["archive"]["filename"] == report.filename

    url = f"/api/v1/tiles/archive/{report.filename}"
    whole = client.get(url)
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert whole.content[:7] == b"PMTiles"

    header_only = client.get(url, headers={"range": "bytes=0-126"})
    assert header_only.status_code == 206
    assert header_only.headers["content-range"].startswith("bytes 0-126/")
    assert len(header_only.content) == 127
    assert pmtiles.read_header(header_only.content).tile_type == pmtiles.TILE_TYPE_MVT

    # Three ranged reads -- header, root directory, one tile -- must reproduce a real tile.
    header = pmtiles.read_header(header_only.content)
    root = client.get(
        url,
        headers={
            "range": f"bytes={header.root_dir_offset}-{header.root_dir_offset + header.root_dir_length - 1}"
        },
    )
    entries = pmtiles.decode_directory(root.content)
    assert entries
    first = entries[0]
    start = header.tile_data_offset + first.offset
    blob = client.get(url, headers={"range": f"bytes={start}-{start + first.length - 1}"})
    assert blob.status_code == 206
    assert decode_tile(gzip.decompress(blob.content))  # a real tile, readable by a real decoder


# --------------------------------------------------------------------------- cross-driver


def test_the_same_tile_describes_the_same_features_on_every_driver(
    repositories: list[Any], settings: Settings, center_tile: tuple[int, int, int]
) -> None:
    """ADR-0014: the driver is an implementation detail, so tiles cannot depend on it.

    Byte equality is too strong a promise -- PostGIS may order rows or vertices differently -- but
    the *description* of a tile (which features, in which layers, with which temporal windows and
    certainty) is exactly what the frontend is written against, and that must match.
    """
    if len(repositories) < 2:
        pytest.skip("only the fixtures driver is available here")
    z, x, y = center_tile
    descriptions: list[tuple[str, dict[str, Any]]] = []
    for repository in repositories:
        service = TileService(AtlasService(repository, settings), repository, settings)
        layers = decode_tile(service.tile(z, x, y))
        descriptions.append(
            (
                repository.driver_name,
                {
                    layer["name"]: {
                        feature["properties"]["id"]: (
                            feature["properties"].get("layer"),
                            feature["properties"].get("rank"),
                            feature["properties"].get("t_from"),
                            feature["properties"].get("t_to"),
                            feature["properties"].get("certainty"),
                        )
                        for feature in layer["features"]
                    }
                    for layer in layers
                },
            )
        )
    first = descriptions[0]
    for driver, description in descriptions[1:]:
        assert description == first[1], f"{driver} disagrees with {first[0]} at {z}/{x}/{y}"


# --------------------------------------------------------------------------- fixtures sanity


def test_bbox_helper_still_builds() -> None:
    """Guards an import the service relies on (BBox is constructed, never parsed, for tiles)."""
    box = BBox(min_lon=0.0, min_lat=0.0, max_lon=1.0, max_lat=1.0)
    assert box.as_tuple() == (0.0, 0.0, 1.0, 1.0)
