"""The slice of the protobuf wire format that MVT and PMTiles need -- nothing more.

This is not a protobuf implementation: there is no schema language, no reflection, no generated
code. It is the wire format itself (https://protobuf.dev/programming-guides/encoding/), which is
small enough to write down once and test exhaustively:

* unsigned varints, and zigzag for signed ones
* the two length-delimited forms (strings, embedded messages, packed repeated numbers)
* fixed32/fixed64 for floats and doubles

``read_fields`` exists so the encoder can be tested by decoding its own output, and so
``azir tiles inspect`` can tell a human what is inside a tile without a third-party tool.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Iterator
from typing import Any

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH = 2
WIRE_FIXED32 = 5


def varint(value: int) -> bytes:
    """Base-128 varint of a non-negative integer."""
    if value < 0:
        raise ValueError(f"varint cannot encode a negative number ({value}); zigzag it first")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def unvarint(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Decode a varint, returning ``(value, next_offset)``."""
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7


def zigzag(value: int) -> int:
    """Map a signed integer onto the non-negative integers (protobuf's signed encoding).

    Written with arithmetic rather than the usual ``(n << 1) ^ (n >> 63)`` so it stays correct for
    Python's unbounded integers instead of only for 64-bit ones.
    """
    return value * 2 if value >= 0 else (-value) * 2 - 1


def unzigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def tag(field_number: int, wire_type: int) -> bytes:
    return varint((field_number << 3) | wire_type)


def encode_message(fields: Iterable[tuple[int, tuple[str, Any]]]) -> bytes:
    """Encode ``(field_number, (kind, payload))`` pairs in the order given.

    Kinds: ``varint``, ``string``, ``bytes``, ``packed`` (already-encoded varints), ``fixed64``
    (a double), ``fixed32`` (a float). Field order matters to nobody but the reader, and the spec
    allows any order -- writing keys before values keeps a hexdump readable.
    """
    out = bytearray()
    for number, (kind, payload) in fields:
        if kind == "varint":
            out += tag(number, WIRE_VARINT)
            out += varint(int(payload))
        elif kind == "string":
            data = str(payload).encode("utf-8")
            out += tag(number, WIRE_LENGTH)
            out += varint(len(data))
            out += data
        elif kind == "bytes":
            data = bytes(payload)
            out += tag(number, WIRE_LENGTH)
            out += varint(len(data))
            out += data
        elif kind == "packed":
            data = b"".join(bytes(item) for item in payload)
            out += tag(number, WIRE_LENGTH)
            out += varint(len(data))
            out += data
        elif kind == "fixed64":
            out += tag(number, WIRE_FIXED64)
            out += struct.pack("<d", float(payload))
        elif kind == "fixed32":
            out += tag(number, WIRE_FIXED32)
            out += struct.pack("<f", float(payload))
        else:  # pragma: no cover - a typo in this module, not a runtime input
            raise ValueError(f"unknown field kind {kind!r}")
    return bytes(out)


def read_fields(data: bytes) -> Iterator[tuple[int, int, Any]]:
    """Yield ``(field_number, wire_type, value)`` for every field in a message.

    Length-delimited values come back as raw ``bytes``: only the caller knows whether a field holds
    a string, an embedded message or a packed list of numbers.
    """
    offset = 0
    value: Any
    while offset < len(data):
        key, offset = unvarint(data, offset)
        number, wire_type = key >> 3, key & 0x7
        if wire_type == WIRE_VARINT:
            value, offset = unvarint(data, offset)
        elif wire_type == WIRE_FIXED64:
            value = struct.unpack("<d", data[offset : offset + 8])[0]
            offset += 8
        elif wire_type == WIRE_FIXED32:
            value = struct.unpack("<f", data[offset : offset + 4])[0]
            offset += 4
        elif wire_type == WIRE_LENGTH:
            length, offset = unvarint(data, offset)
            value = data[offset : offset + length]
            offset += length
        else:
            raise ValueError(f"unsupported wire type {wire_type} at offset {offset}")
        yield number, wire_type, value


def read_packed(data: bytes) -> list[int]:
    """Decode a packed repeated-uint32 field."""
    out: list[int] = []
    offset = 0
    while offset < len(data):
        value, offset = unvarint(data, offset)
        out.append(value)
    return out


__all__ = [
    "WIRE_FIXED32",
    "WIRE_FIXED64",
    "WIRE_LENGTH",
    "WIRE_VARINT",
    "encode_message",
    "read_fields",
    "read_packed",
    "tag",
    "unvarint",
    "unzigzag",
    "varint",
    "zigzag",
]
