"""Cursor pagination (ADR-0009): opaque, base64url, stable under insertions."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Cursor:
    rank: float
    id: str

    def encode(self) -> str:
        raw = json.dumps({"r": self.rank, "i": self.id}, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, token: str | None) -> Cursor | None:
        if not token:
            return None
        padded = token + "=" * (-len(token) % 4)
        try:
            data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded.encode()))
            return cls(rank=float(data["r"]), id=str(data["i"]))
        except (ValueError, KeyError, TypeError) as exc:  # malformed cursor is a client error
            from .errors import ValidationError

            raise ValidationError("cursor is malformed or expired") from exc


@dataclass(frozen=True, slots=True)
class Page:
    limit: int
    next_cursor: str | None = None
    total_estimate: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": self.limit, "next_cursor": self.next_cursor}
        if self.total_estimate is not None:
            payload["total_estimate"] = self.total_estimate
        return payload


def paginate(
    rows: list[tuple[float, str]], limit: int, cursor: Cursor | None
) -> tuple[list[tuple[float, str]], Page]:
    """Apply a ``(rank DESC, id ASC)`` cursor over already-sorted rows.

    Sorting happens in the repository (SQL ``ORDER BY`` or Python ``sorted``); this only slices,
    so both drivers paginate identically.
    """
    ordered = rows
    if cursor is not None:
        ordered = [row for row in rows if (row[0], _invert(row[1])) < (cursor.rank, _invert(cursor.id))]
    page_rows = ordered[:limit]
    next_cursor = None
    if len(ordered) > limit and page_rows:
        rank, ident = page_rows[-1]
        next_cursor = Cursor(rank=rank, id=ident).encode()
    return page_rows, Page(limit=limit, next_cursor=next_cursor, total_estimate=len(rows))


def _invert(value: str) -> tuple[int, ...]:
    """Make string ids sort ascending while ranks sort descending in a single comparison."""
    return tuple(-ord(ch) for ch in value)
