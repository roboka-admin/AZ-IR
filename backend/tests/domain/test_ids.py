"""Identifiers are the join keys of the whole system, so they get their own tests (ADR-0008)."""

from __future__ import annotations

import time
import uuid

from azir.core.ids import PREFIX, is_prefixed, new_id, slugify, split_id, unix_ms, uuid7
from azir.domain.enums import EntityType


def test_uuid7_is_version_7_and_time_ordered() -> None:
    first = uuid7()
    second = uuid7()
    assert first.version == 7, "the version nibble must say 7 or indexes will not sort by time"
    assert first.variant == uuid.RFC_4122
    # The 48-bit timestamp is milliseconds; a microsecond clock would have overflowed in 1978.
    embedded = int.from_bytes(first.bytes[0:6], "big")
    assert abs(embedded - unix_ms()) < 60_000
    assert first != second
    # The timestamp prefix never goes backwards. Within one millisecond the random tail decides the
    # order: RFC 9562 allows a plain random tail (a monotonic counter is optional), so only the
    # millisecond resolution is promised here.
    assert first.bytes[0:6] <= second.bytes[0:6]
    time.sleep(0.003)
    assert uuid7().int > second.int, "a later millisecond sorts later"


def test_uuid7_timestamp_field_is_48_bits() -> None:
    assert uuid7().bytes[0:6] != b"\\x00" * 6
    assert unix_ms() < 2**48, "the timestamp must fit the field the RFC gives it"


def test_new_id_carries_a_readable_prefix() -> None:
    for entity_type, prefix in PREFIX.items():
        identifier = new_id(entity_type)
        assert identifier.startswith(f"{prefix}_")
        assert is_prefixed(identifier)
        assert split_id(identifier) == (prefix, identifier.split("_", 1)[1])
    assert new_id("nonsense").startswith("ent_"), "an unknown type still gets a usable id"
    assert new_id(EntityType.PLACE).startswith("plc_")


def test_ids_are_unique() -> None:
    minted = {new_id("place") for _ in range(500)}
    assert len(minted) == 500


def test_slugify_is_latin_and_lossy_on_purpose() -> None:
    assert slugify("Tabriz, East Azerbaijan") == "tabriz-east-azerbaijan"
    assert slugify("  Multiple   Spaces & Signs! ") == "multiple-spaces-signs"
    # Persian has no lossless ASCII form: the caller's fallback wins instead of a mangled slug.
    assert slugify("تبریز") == "place-abc123" or slugify("تبریز", fallback="x") == "x"
    assert slugify("", fallback="entity") == "entity"
