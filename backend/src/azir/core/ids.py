"""Identifiers: UUIDv7, entity prefixes, and slugs (ADR-0008)."""

from __future__ import annotations

import os
import re
import time
import unicodedata
import uuid
from typing import Final

from ..domain.enums import EntityType

#: Human-readable prefixes so a leaked id tells you what it is.
PREFIX: Final[dict[str, str]] = {
    EntityType.PLACE: "plc",
    EntityType.PERSON: "prs",
    EntityType.EVENT: "evt",
    EntityType.POLITICAL_ENTITY: "pol",
    EntityType.PERIOD: "prd",
    EntityType.ARTICLE: "art",
    EntityType.SOURCE: "src",
    "assertion": "asn",
    "evidence": "evd",
    "user": "usr",
}

_SLUG_SAFE = re.compile(r"[^a-z0-9]+")
_SLUG_LEADING = re.compile(r"^-+|-+$")


def uuid7() -> uuid.UUID:
    """Time-ordered UUID (RFC 9562) -- index friendly, no external dependency.

    Layout: 48 bits of Unix time in **milliseconds**, the version nibble ``7``, then random bits
    with the RFC 4122 variant. Milliseconds are what the RFC specifies and what fits: 48 bits of
    microseconds overflowed in 1978, and of nanoseconds in 1970, so the earlier version of this
    function raised ``OverflowError`` the first time anything actually minted an id.
    """
    milliseconds = unix_ms()
    rand = os.urandom(10)
    b = bytearray(16)
    b[0:6] = milliseconds.to_bytes(6, "big")
    b[6:16] = rand
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(b))


def unix_ms() -> int:
    """Milliseconds since the epoch: the 48-bit timestamp field of a UUIDv7 (good until 10889)."""
    return time.time_ns() // 1_000_000


def new_id(entity_type: str) -> str:
    prefix = PREFIX.get(entity_type, "ent")
    return f"{prefix}_{uuid7().hex}"


def split_id(raw: str) -> tuple[str | None, str]:
    """``plc_ab12`` -> (``plc``, ``ab12``); ids without a prefix return (None, raw)."""
    if "_" in raw:
        prefix, rest = raw.split("_", 1)
        if len(prefix) <= 4 and prefix.isalpha():
            return prefix, rest
    return None, raw


def slugify(text: str, *, fallback: str = "entity") -> str:
    """Latin slug.

    Persian/Arabic script has no lossless ASCII form, so a slug is built from the Latin name when
    one exists; otherwise the caller passes a deterministic ``fallback`` (``<type>-<shortid>``).
    """
    if not text:
        return fallback
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = _SLUG_LEADING.sub("", _SLUG_SAFE.sub("-", ascii_only.lower())).strip("-")
    slug = _SLUG_LEADING.sub("", slug)
    return slug or fallback


def is_prefixed(raw: str) -> bool:
    return bool(raw and re.fullmatch(r"[a-z]{2,4}_[a-z0-9]+", raw))
