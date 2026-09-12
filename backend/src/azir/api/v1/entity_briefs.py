"""Small helpers shared by catalog routers (kept out of routers per AGENTS.md rule 2)."""

from __future__ import annotations

from typing import Any

from ...domain.enums import EntityType
from ...repositories.ports import AtlasRepository


def source_list(repo: AtlasRepository, locale: str, limit: int) -> dict[str, Any]:
    rows = repo.list_entities(EntityType.SOURCE, status=None, locale=locale, limit=limit)
    data = [
        {
            "id": record.id,
            "kind": record.kind,
            "title": record.display_name(locale),
            "title_en": record.display_name("en"),
            "author": record.extra.get("author_fa") or record.extra.get("author"),
            "year": record.extra.get("year") or record.extra.get("origin_year"),
            "publisher": record.extra.get("publisher"),
            "reliability": record.extra.get("reliability"),
            "citation": record.extra.get("citation"),
            "url": record.extra.get("url"),
            "needs_review": bool(record.extra.get("needs_review")),
        }
        for record in rows
    ]
    return {
        "data": data,
        "page": {"limit": limit, "next_cursor": None, "total_estimate": len(data)},
        "meta": {"driver": repo.driver_name},
    }
