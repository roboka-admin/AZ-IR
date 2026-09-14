"""PMTiles v3: a single-file tile archive that a CDN can serve with HTTP range requests.

Why this format (ADR-0011): the deployment target is a PaaS with object storage in front, so the
tile pyramid has to be *static*. PMTiles is one file, addressed by byte ranges, which means no tile
server, no cache invalidation problem, and no per-tile request cost -- the reader fetches the
127-byte header, then the root directory, then exactly the bytes of the tile it wants.

What is implemented here, from the specification (v3, chapters 3-5):

* the 127-byte header, little-endian, with positions as ``degrees * 1e7`` int32 (lon first)
* directories: ``num_entries``, delta-encoded TileIDs, RunLengths, Lengths, Offsets
* TileIDs on the **Hilbert curve** (not row-major) -- the one detail that silently produces an
  unreadable archive if you guess it
* leaf directories, so an archive that outgrows the 16 KiB root budget keeps working
* run-length deduplication of byte-identical neighbours, which is why empty ocean tiles cost nothing

Reading is implemented too (``read_header``/``read_directory``/``find_tile``): a writer nobody can
check is a writer nobody trusts, and ``azir tiles inspect`` uses it instead of an external tool.
"""

from __future__ import annotations

import gzip
import json
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"PMTiles"
SPEC_VERSION = 3
HEADER_LENGTH = 127

#: The header plus the root directory must fit in the first 16 KiB, so a latency-optimised client
#: can fetch both in one range request (spec §4).
ROOT_BUDGET = 16384 - HEADER_LENGTH

COMPRESSION_UNKNOWN = 0
COMPRESSION_NONE = 1
COMPRESSION_GZIP = 2
COMPRESSION_BROTLI = 3
COMPRESSION_ZSTD = 4

TILE_TYPE_UNKNOWN = 0
TILE_TYPE_MVT = 1
TILE_TYPE_PNG = 2
TILE_TYPE_JPEG = 3
TILE_TYPE_WEBP = 4
TILE_TYPE_AVIF = 5
TILE_TYPE_MAPLIBRE_VECTOR = 6

NOT_CLUSTERED = 0
CLUSTERED = 1

#: Entries per leaf directory once the root budget is exceeded.
LEAF_ENTRIES = 4096


def hilbert_xy2d(order: int, x: int, y: int) -> int:
    """Position of ``(x, y)`` on the Hilbert curve of an ``order x order`` grid.

    The classic Wikipedia ``xy2d``. PMTiles numbers tiles by walking a Hilbert curve per zoom level,
    which keeps neighbours close in the file -- the reason a pan loads a contiguous byte range.
    """
    distance = 0
    size = order // 2
    while size > 0:
        rx = 1 if x & size else 0
        ry = 1 if y & size else 0
        distance += size * size * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = order - 1 - x
                y = order - 1 - y
            x, y = y, x
        size //= 2
    return distance


def tile_id(z: int, x: int, y: int) -> int:
    """The PMTiles TileID: cumulative Hilbert position over zoom levels ``0..z``.

    ``z=0`` is id 0; ``z=1`` is ids 1-4; ``z=2`` starts at 5 -- matching the table in spec §4.1.
    """
    return ((1 << (2 * z)) - 1) // 3 + hilbert_xy2d(1 << z, x, y)


@dataclass(frozen=True, slots=True)
class DirEntry:
    """One directory entry: a tile (``run_length > 0``) or a leaf directory (``run_length == 0``)."""

    tile_id: int
    offset: int
    length: int
    run_length: int = 1

    @property
    def is_leaf(self) -> bool:
        return self.run_length == 0


@dataclass(frozen=True, slots=True)
class Header:
    """The 127 bytes that make the rest of the archive decodable."""

    root_dir_offset: int
    root_dir_length: int
    metadata_offset: int
    metadata_length: int
    leaf_dirs_offset: int
    leaf_dirs_length: int
    tile_data_offset: int
    tile_data_length: int
    addressed_tiles: int
    tile_entries: int
    tile_contents: int
    clustered: int
    internal_compression: int
    tile_compression: int
    tile_type: int
    min_zoom: int
    max_zoom: int
    bounds: tuple[float, float, float, float]
    center_zoom: int
    center: tuple[float, float]

    def encode(self) -> bytes:
        min_lon, min_lat, max_lon, max_lat = self.bounds
        center_lon, center_lat = self.center
        return b"".join(
            (
                MAGIC,
                struct.pack("<B", SPEC_VERSION),
                # Eleven 8-byte fields: bytes 8..96 of the header (spec §3.1).
                struct.pack(
                    "<11Q",
                    self.root_dir_offset,
                    self.root_dir_length,
                    self.metadata_offset,
                    self.metadata_length,
                    self.leaf_dirs_offset,
                    self.leaf_dirs_length,
                    self.tile_data_offset,
                    self.tile_data_length,
                    self.addressed_tiles,
                    self.tile_entries,
                    self.tile_contents,
                ),
                struct.pack(
                    "<6B",
                    self.clustered,
                    self.internal_compression,
                    self.tile_compression,
                    self.tile_type,
                    self.min_zoom,
                    self.max_zoom,
                ),
                _position(min_lon, min_lat),
                _position(max_lon, max_lat),
                struct.pack("<B", self.center_zoom),
                _position(center_lon, center_lat),
            )
        )


def _position(lon: float, lat: float) -> bytes:
    """Spec §3.4: longitude first, then latitude, each ``degrees * 1e7`` as an int32 LE."""
    return struct.pack("<2i", round(lon * 1e7), round(lat * 1e7))


def _unpack_position(data: bytes) -> tuple[float, float]:
    lon_e7, lat_e7 = struct.unpack("<2i", data)
    return (lon_e7 / 1e7, lat_e7 / 1e7)


def read_header(data: bytes) -> Header:
    """Decode a header, refusing anything that is not a PMTiles v3 archive."""
    if len(data) < HEADER_LENGTH:
        raise ValueError(f"a PMTiles header is {HEADER_LENGTH} bytes, got {len(data)}")
    if data[:7] != MAGIC:
        raise ValueError("not a PMTiles archive (magic number missing)")
    version = data[7]
    if version != SPEC_VERSION:
        raise ValueError(f"unsupported PMTiles spec version {version}; this reads version 3")
    numbers = struct.unpack("<11Q", data[8:96])
    flags = struct.unpack("<6B", data[96:102])
    min_position = _unpack_position(data[102:110])
    max_position = _unpack_position(data[110:118])
    center_zoom = data[118]
    center = _unpack_position(data[119:127])
    return Header(
        root_dir_offset=numbers[0],
        root_dir_length=numbers[1],
        metadata_offset=numbers[2],
        metadata_length=numbers[3],
        leaf_dirs_offset=numbers[4],
        leaf_dirs_length=numbers[5],
        tile_data_offset=numbers[6],
        tile_data_length=numbers[7],
        addressed_tiles=numbers[8],
        tile_entries=numbers[9],
        tile_contents=numbers[10],
        clustered=flags[0],
        internal_compression=flags[1],
        tile_compression=flags[2],
        tile_type=flags[3],
        min_zoom=flags[4],
        max_zoom=flags[5],
        bounds=(min_position[0], min_position[1], max_position[0], max_position[1]),
        center_zoom=center_zoom,
        center=center,
    )


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(out)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7


def encode_directory(entries: Sequence[DirEntry]) -> bytes:
    """Spec §4.2 and appendix A.1: count, delta TileIDs, run lengths, lengths, offsets."""
    if not entries:
        raise ValueError("a directory with no entries is not valid PMTiles")
    out = bytearray(_varint(len(entries)))
    last_id = 0
    for entry in entries:
        out += _varint(entry.tile_id - last_id)
        last_id = entry.tile_id
    for entry in entries:
        out += _varint(entry.run_length)
    for entry in entries:
        out += _varint(entry.length)
    next_byte = 0
    for index, entry in enumerate(entries):
        if index > 0 and entry.offset == next_byte:
            out += _varint(0)  # contiguous with the previous blob
        else:
            out += _varint(entry.offset + 1)
        next_byte = entry.offset + entry.length
    return bytes(out)


def decode_directory(data: bytes) -> list[DirEntry]:
    """Spec §4.3 and appendix A.2: four sections, each ``count`` varints long."""
    offset = 0
    count, offset = _read_varint(data, offset)
    ids: list[int] = []
    last_id = 0
    for _ in range(count):
        delta, offset = _read_varint(data, offset)
        last_id += delta
        ids.append(last_id)
    run_lengths: list[int] = []
    for _ in range(count):
        value, offset = _read_varint(data, offset)
        run_lengths.append(value)
    lengths: list[int] = []
    for _ in range(count):
        value, offset = _read_varint(data, offset)
        lengths.append(value)
    offsets: list[int] = []
    for index in range(count):
        value, offset = _read_varint(data, offset)
        if value == 0 and index > 0:
            # 0 means "directly after the previous blob", which is how a clustered archive stays small.
            offsets.append(offsets[index - 1] + lengths[index - 1])
        else:
            offsets.append(value - 1)
    return [
        DirEntry(tile_id=ids[i], offset=offsets[i], length=lengths[i], run_length=run_lengths[i])
        for i in range(count)
    ]


def _compress(data: bytes, compression: int) -> bytes:
    if compression == COMPRESSION_GZIP:
        return gzip.compress(data, mtime=0)  # mtime=0: identical input, identical bytes
    if compression in (COMPRESSION_NONE, COMPRESSION_UNKNOWN):
        return data
    raise ValueError(
        f"compression {compression} is not supported by this writer (gzip or none only)"
    )


def _decompress(data: bytes, compression: int) -> bytes:
    if compression == COMPRESSION_GZIP:
        return gzip.decompress(data)
    if compression in (COMPRESSION_NONE, COMPRESSION_UNKNOWN):
        return data
    raise ValueError(f"compression {compression} is not supported by this reader")


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    """What a build produced; the CLI prints it and CI can assert on it."""

    path: str
    bytes: int
    tiles: int
    entries: int
    contents: int
    leaf_directories: int
    min_zoom: int
    max_zoom: int
    bounds: tuple[float, float, float, float]
    largest_tile_bytes: int
    empty_tiles_skipped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "bytes": self.bytes,
            "tiles": self.tiles,
            "entries": self.entries,
            "contents": self.contents,
            "leaf_directories": self.leaf_directories,
            "min_zoom": self.min_zoom,
            "max_zoom": self.max_zoom,
            "bounds": list(self.bounds),
            "largest_tile_bytes": self.largest_tile_bytes,
            "empty_tiles_skipped": self.empty_tiles_skipped,
        }


def write_archive(
    destination: str | Path,
    tiles: Iterable[tuple[int, int, int, bytes]],
    *,
    metadata: dict[str, Any],
    bounds: tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
    center: tuple[float, float] | None = None,
    center_zoom: int | None = None,
    tile_compression: int = COMPRESSION_GZIP,
    internal_compression: int = COMPRESSION_NONE,
) -> ArchiveReport:
    """Write a PMTiles v3 archive.

    ``tiles`` yields ``(z, x, y, raw_tile_bytes)``; compression is applied here, so callers hand over
    plain MVT. Tiles with no content should simply not be yielded: a missing tile is how the format
    says "nothing here", and shipping empty blobs would multiply the archive for no benefit.
    """
    blobs: dict[bytes, tuple[int, int]] = {}  # compressed bytes -> (offset, length)
    ordered: list[tuple[int, bytes]] = []
    contents = 0
    largest = 0
    skipped = 0
    for z, x, y, raw in tiles:
        if not raw:
            skipped += 1  # an empty tile is simply absent: that is how the format says "nothing here"
            continue
        compressed = _compress(raw, tile_compression)
        largest = max(largest, len(compressed))
        ordered.append((tile_id(z, x, y), compressed))
    ordered.sort(key=lambda item: item[0])

    # Run-length encode neighbours that share bytes: the archive stores the blob once and the entry
    # says how many consecutive tiles it covers.
    entries: list[DirEntry] = []
    tile_data = bytearray()
    for identifier, compressed in ordered:
        if entries and _extends_run(entries[-1], identifier, compressed, blobs):
            previous = entries[-1]
            entries[-1] = DirEntry(
                tile_id=previous.tile_id,
                offset=previous.offset,
                length=previous.length,
                run_length=previous.run_length + 1,
            )
            continue
        known = blobs.get(compressed)
        if known is not None:
            offset, length = known
        else:
            offset, length = len(tile_data), len(compressed)
            tile_data += compressed
            blobs[compressed] = (offset, length)
            contents += 1
        entries.append(DirEntry(tile_id=identifier, offset=offset, length=length, run_length=1))

    if not entries:
        raise ValueError("refusing to write an archive with no tiles")

    root_entries, leaf_blobs = _split_directories(entries, internal_compression)
    root_dir = _compress(encode_directory(root_entries), internal_compression)
    if len(root_dir) > ROOT_BUDGET and not leaf_blobs:
        raise ValueError(
            f"the root directory needs {len(root_dir)} bytes but the spec allows {ROOT_BUDGET}; "
            "leaf directories should have been produced"
        )
    metadata_bytes = json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode("utf-8")

    root_offset = HEADER_LENGTH
    metadata_offset = root_offset + len(root_dir)
    leaf_offset = metadata_offset + len(metadata_bytes)
    tile_offset = leaf_offset + len(leaf_blobs)

    center_lon, center_lat = center or (
        (bounds[0] + bounds[2]) / 2.0,
        (bounds[1] + bounds[3]) / 2.0,
    )
    header = Header(
        root_dir_offset=root_offset,
        root_dir_length=len(root_dir),
        metadata_offset=metadata_offset,
        metadata_length=len(metadata_bytes),
        leaf_dirs_offset=leaf_offset if leaf_blobs else 0,
        leaf_dirs_length=len(leaf_blobs),
        tile_data_offset=tile_offset,
        tile_data_length=len(tile_data),
        addressed_tiles=sum(entry.run_length for entry in entries),
        tile_entries=len(entries),
        tile_contents=contents,
        clustered=CLUSTERED,
        internal_compression=internal_compression,
        tile_compression=tile_compression,
        tile_type=TILE_TYPE_MVT,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        bounds=bounds,
        center_zoom=center_zoom if center_zoom is not None else min_zoom,
        center=(center_lon, center_lat),
    )

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(header.encode())
        handle.write(root_dir)
        handle.write(metadata_bytes)
        handle.write(leaf_blobs)
        handle.write(tile_data)

    return ArchiveReport(
        path=str(path),
        bytes=path.stat().st_size,
        tiles=sum(entry.run_length for entry in entries),
        entries=len(entries),
        contents=contents,
        leaf_directories=len(root_entries) if leaf_blobs else 0,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        bounds=bounds,
        largest_tile_bytes=largest,
        empty_tiles_skipped=skipped,
    )


def _extends_run(
    entry: DirEntry, identifier: int, compressed: bytes, blobs: dict[bytes, tuple[int, int]]
) -> bool:
    """Whether this tile continues the previous entry's run.

    Two conditions, and both matter. A run covers *consecutive TileIDs* (spec §4.1), and TileIDs
    follow the Hilbert curve, so tiles that look adjacent on the map are usually not adjacent in the
    file: merging on identical bytes alone would claim tiles that were never written and hide the
    ones that were. And the bytes must actually be the same blob, or one run would serve two
    different tiles.
    """
    if entry.run_length == 0:  # a leaf pointer never continues into a tile
        return False
    if identifier != entry.tile_id + entry.run_length:
        return False
    return blobs.get(compressed) == (entry.offset, entry.length)


def _split_directories(
    entries: Sequence[DirEntry], internal_compression: int
) -> tuple[list[DirEntry], bytes]:
    """Keep the root inside the 16 KiB budget, spilling into leaf directories when needed.

    Returns ``(root_entries, leaf_section_bytes)``. When everything fits in the root -- the pilot
    corpus does -- there are no leaves at all, which is also the fastest thing for a reader.
    """
    if len(_compress(encode_directory(entries), internal_compression)) <= ROOT_BUDGET:
        return list(entries), b""

    leaves: list[DirEntry] = []
    section = bytearray()
    for start in range(0, len(entries), LEAF_ENTRIES):
        chunk = list(entries[start : start + LEAF_ENTRIES])
        encoded = _compress(encode_directory(chunk), internal_compression)
        leaves.append(
            DirEntry(
                tile_id=chunk[0].tile_id,
                offset=len(section),
                length=len(encoded),
                run_length=0,  # 0 means "this entry points at a leaf directory"
            )
        )
        section += encoded
    root = _compress(encode_directory(leaves), internal_compression)
    if len(root) > ROOT_BUDGET:  # pragma: no cover - would need ~1.6k leaves
        raise ValueError(
            f"even {len(leaves)} leaf directories need a {len(root)}-byte root; "
            "this archive needs more than one level of leaves, which the spec discourages"
        )
    return leaves, bytes(section)


def find_tile(
    handle: BinaryIO, header: Header, z: int, x: int, y: int
) -> bytes | None:
    """Read one tile's bytes out of an open archive, following leaves if there are any."""
    wanted = tile_id(z, x, y)
    handle.seek(header.root_dir_offset)
    entries = decode_directory(
        _decompress(handle.read(header.root_dir_length), header.internal_compression)
    )
    entry = _search(entries, wanted)
    if entry is None:
        return None
    if entry.is_leaf:
        handle.seek(header.leaf_dirs_offset + entry.offset)
        entries = decode_directory(
            _decompress(handle.read(entry.length), header.internal_compression)
        )
        entry = _search(entries, wanted)
        if entry is None:
            return None
    handle.seek(header.tile_data_offset + entry.offset)
    return _decompress(handle.read(entry.length), header.tile_compression)


def _search(entries: Sequence[DirEntry], wanted: int) -> DirEntry | None:
    """Find the entry that covers ``wanted``.

    Leaf entries have no run length: a leaf covers everything from its TileID up to the next leaf,
    so the *last* leaf at or before the id is the one to open. Tile entries carry a run length, and
    the id must fall inside that run.
    """
    if entries and entries[0].is_leaf:
        candidate: DirEntry | None = None
        for entry in entries:
            if entry.tile_id > wanted:
                break
            candidate = entry
        return candidate
    for entry in entries:
        if entry.tile_id > wanted:
            return None
        if entry.tile_id <= wanted < entry.tile_id + entry.run_length:
            return entry
    return None


def read_metadata(handle: BinaryIO, header: Header) -> dict[str, Any]:
    handle.seek(header.metadata_offset)
    payload: dict[str, Any] = json.loads(handle.read(header.metadata_length).decode("utf-8"))
    return payload


__all__ = [
    "COMPRESSION_GZIP",
    "COMPRESSION_NONE",
    "HEADER_LENGTH",
    "LEAF_ENTRIES",
    "ROOT_BUDGET",
    "TILE_TYPE_MVT",
    "ArchiveReport",
    "DirEntry",
    "Header",
    "decode_directory",
    "encode_directory",
    "find_tile",
    "hilbert_xy2d",
    "read_header",
    "read_metadata",
    "tile_id",
    "write_archive",
]
