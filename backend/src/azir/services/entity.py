"""EntityService + ArticleService: the detail/graph/article read-model.

Articles are presentations of entities (AGENTS.md rule 8): an article response always carries the
entities it is about and a ``map_state`` so the UI can offer "View on Map".
"""

from __future__ import annotations

from typing import Any

from ..core.config import Settings
from ..core.errors import GoneError, NotFoundError
from ..domain.enums import EntityType, Locale
from ..domain.model import EntityRecord
from ..domain.temporal import TimeWindow
from ..repositories.ports import AtlasRepository


class EntityService:
    def __init__(self, repository: AtlasRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings

    def get(self, entity_type: str, id_or_slug: str, locale: str) -> dict[str, Any]:
        record = self._repo.entity(entity_type, id_or_slug)
        if record is None:
            raise NotFoundError(f"no {entity_type} with id or slug {id_or_slug!r}")
        if record.status.value == "archived":
            # Tombstone: the id stays resolvable, the content is gone (ADR-0008).
            raise GoneError(f"{entity_type} {id_or_slug} has been archived", id=record.id)
        return self.detail(record, locale)

    def detail(self, record: EntityRecord, locale: str) -> dict[str, Any]:
        geometry = record.primary_geometry()
        rep = geometry.representative_point() if geometry else None
        temporal = record.temporal
        summary = record.summary if locale == Locale.FA else (record.summary_en or record.summary)
        body = record.body_md
        if locale != Locale.FA and record.extra.get("body_en_md"):
            body = str(record.extra["body_en_md"])

        payload: dict[str, Any] = {
            "id": record.id,
            "entity_type": record.entity_type.value,
            "kind": record.kind,
            "kind_label": _kind_label(record, locale),
            "slug": record.slug,
            "status": record.status.value,
            "revision": record.revision,
            "rank": record.rank,
            "min_zoom": record.min_zoom,
            "max_zoom": record.max_zoom,
            "layer": record.layer,
            "names": {
                "display": record.display_name(locale),
                "display_secondary": record.secondary_name(locale),
                "alternatives": [
                    {
                        "form": n.form,
                        "lang": n.lang,
                        "script": n.script,
                        "kind": n.kind,
                        "year_from": n.year_from,
                        "year_to": n.year_to,
                        "note": n.note,
                    }
                    for n in record.alternates(locale)
                ],
            },
            "temporal": {
                "year_from": temporal.year_from if temporal else None,
                "year_to": temporal.year_to if temporal else None,
                "precision": temporal.precision.value if temporal else None,
                "calendar": temporal.calendar.value if temporal else None,
                "confidence": temporal.confidence.value if temporal else None,
                "display": record.temporal_display(locale),
            },
            "geometry": {
                "geojson": geometry.geojson if geometry else None,
                "kind": geometry.kind.value if geometry else None,
                "certainty": geometry.certainty.value if geometry else None,
                "note": geometry.note_for(locale) if geometry else None,
                "note_fa": geometry.note if geometry else None,
                "note_en": geometry.note_en if geometry else None,
                "needs_digitisation": bool(geometry and geometry.needs_digitisation),
                "source_id": geometry.source_id if geometry else None,
                "year_from": geometry.year_from if geometry else None,
                "year_to": geometry.year_to if geometry else None,
                "center": list(rep) if rep else None,
                "other_geometries": [
                    {
                        "kind": g.kind.value,
                        "certainty": g.certainty.value,
                        "year_from": g.year_from,
                        "year_to": g.year_to,
                        "note": g.note_for(locale),
                    }
                    for g in record.geometries
                    if geometry is None or g is not geometry
                ],
            },
            "summary": summary,
            "body_md": body,
            "attestation": record.attestation.value if record.attestation else None,
            "counts": {
                "sources": record.counts.sources,
                "articles": record.counts.articles,
                "assertions": record.counts.assertions,
                "periods": record.counts.periods,
            },
            "relationships": [
                {
                    "predicate": r.predicate,
                    "label": r.label(locale),
                    "direction": r.direction,
                    "object_type": r.object_type.value if r.object_type else None,
                    "object_id": r.object_id,
                    "object_label": r.object_label or r.object_value,
                    "object_slug": r.object_slug,
                    "object_value": r.object_value,
                    "role": r.role,
                    "side": r.side,
                    "certainty": r.certainty,
                    "confidence": r.confidence.value,
                    "status": r.status.value,
                    "t_display": r.temporal.display_text(locale) if r.temporal else None,
                    "t_from": r.temporal.year_from if r.temporal else None,
                    "t_to": r.temporal.year_to if r.temporal else None,
                    "evidence": [
                        {
                            "source_id": e.source_id,
                            "locator_type": e.locator_type,
                            "locator": e.locator,
                            "quote_translation": e.quote_translation,
                            "stance": e.stance,
                        }
                        for e in r.evidence
                    ],
                }
                for r in record.relationships
            ],
            "disagreements": [
                {
                    "topic": d.topic if locale == Locale.FA else (d.topic_en or d.topic),
                    "topic_fa": d.topic,
                    "topic_en": d.topic_en or d.topic,
                    "positions": [
                        {
                            "value": p.object_label or p.object_value,
                            "confidence": p.confidence.value,
                            "status": p.status.value,
                            "note": p.note,
                            "t_display": p.temporal.display_text(locale) if p.temporal else None,
                            "evidence": [
                                {"source_id": e.source_id, "stance": e.stance, "locator": e.locator}
                                for e in p.evidence
                            ],
                        }
                        for p in d.positions
                    ],
                }
                for d in record.disagreements
            ],
            "articles": [
                _article_brief(a, locale)
                for a in self._repo.articles(locale=locale, entity_id=record.id)
            ],
            "sources": [
                _source_brief(s, locale)
                for s in (
                    self._repo.entity("source", sid) for sid in dict.fromkeys(record.source_ids)
                )
                if s is not None
            ],
            "coverage_note": _coverage_note(record, locale),
            "links": self._links(record, rep),
        }
        if record.entity_type is EntityType.ARTICLE:
            payload["article"] = {
                "map_state": record.extra.get("map_state"),
                "author": record.extra.get("author"),
                "published_at": record.extra.get("published_at"),
                "reading_time_min": record.extra.get("reading_time_min"),
                "lang": record.extra.get("lang", Locale.FA),
                "title": record.extra.get("title_fa") or record.display_name(locale),
                "title_en": record.extra.get("title_en"),
            }
        return payload

    def related(self, entity_type: str, id_or_slug: str, locale: str, depth: int, limit: int) -> dict[str, Any]:
        record = self._repo.entity(entity_type, id_or_slug)
        if record is None:
            raise NotFoundError(f"no {entity_type} with id or slug {id_or_slug!r}")
        rows = self._repo.related(record.entity_type.value, record.id, depth=depth, limit=limit)
        return {
            "entity": _brief(record, locale),
            "data": [
                {
                    "predicate": r.predicate,
                    "label": r.label(locale),
                    "direction": r.direction,
                    "object_type": r.object_type.value if r.object_type else None,
                    "object_id": r.object_id,
                    "object_label": r.object_label or r.object_value,
                    "object_slug": r.object_slug,
                    "confidence": r.confidence.value,
                    "status": r.status.value,
                }
                for r in rows
            ],
            "meta": {"driver": self._repo.driver_name, "depth": depth},
        }

    def list_entities(
        self, entity_type: str, locale: str, limit: int, window: TimeWindow | None
    ) -> dict[str, Any]:
        try:
            kind = EntityType(entity_type)
        except ValueError as exc:
            raise NotFoundError(f"unknown entity type {entity_type!r}") from exc
        rows = self._repo.list_entities(kind, locale=locale, limit=limit, window=window)
        return {
            "data": [_brief(r, locale) for r in rows],
            "page": {"limit": limit, "next_cursor": None, "total_estimate": len(rows)},
            "meta": {"driver": self._repo.driver_name, "entity_type": entity_type},
        }

    # ------------------------------------------------------------------ links

    def _links(self, record: EntityRecord, rep: tuple[float, float] | None) -> dict[str, Any]:
        """Deep links into the map. Articles carry their own ``map_state`` (handoff §12)."""
        base = self._settings.public_base_url.rstrip("/")
        key = record.slug or record.id
        state = record.extra.get("map_state") if isinstance(record.extra.get("map_state"), dict) else None
        if state:
            center = state.get("center") or ([rep[1], rep[0]] if rep else None)
            time = state.get("time") or {}
            parts = [f"entity={record.id}"]
            if center:
                parts.append(f"c={center[0]},{center[1]}")
            if state.get("zoom"):
                parts.append(f"z={state['zoom']}")
            if time.get("from") is not None and time.get("to") is not None:
                parts.append(f"from={time['from']}&to={time['to']}&mode={time.get('mode', 'overlaps')}")
            elif time.get("year") is not None:
                parts.append(f"t={time['year']}")
            layers = state.get("layers")
            if layers:
                parts.append("l=" + ",".join(layers))
            query = "?" + "&".join(parts)
        else:
            temporal = record.temporal
            year = (
                int(temporal.midpoint)
                if temporal and not temporal.is_open_ended
                else self._settings.timeline_default_year
            )
            query = f"?entity={record.id}&t={year}"
            if rep:
                query += f"&c={rep[1]:.4f},{rep[0]:.4f}&z={max(record.min_zoom, 12):.1f}"
        return {
            "self": f"{base}{self._settings.api_prefix}/entities/{record.entity_type.value}/{key}",
            "related": f"{base}{self._settings.api_prefix}/entities/{record.entity_type.value}/{key}/related",
            "map": f"/{query}",
        }


class ArticleService:
    def __init__(self, repository: AtlasRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings
        self._entities = EntityService(repository, settings)

    def list_articles(self, locale: str, limit: int) -> dict[str, Any]:
        rows = self._repo.articles(locale=locale, limit=limit)
        return {
            "data": [_article_brief(r, locale) for r in rows],
            "page": {"limit": limit, "next_cursor": None, "total_estimate": len(rows)},
            "meta": {"driver": self._repo.driver_name},
        }

    def _linked_entity_briefs(self, record: EntityRecord, locale: str) -> list[dict[str, Any]]:
        """Articles point at entities; the payload carries a brief of each, never a copy."""
        briefs: list[dict[str, Any]] = []
        for link in record.extra.get("entities", []):
            linked = self._repo.entity(str(link["type"]), str(link["id"]))
            if linked is None:
                continue
            brief = _brief(linked, locale)
            brief["relation"] = link.get("relation")
            briefs.append(brief)
        return briefs

    def get(self, id_or_slug: str, locale: str) -> dict[str, Any]:
        """Article payload = the shared entity shape plus an ``article`` block with the prose.

        Bodies live inside ``article`` so the frontend can tell "entity facts" (which must come
        from entities, never be duplicated) apart from "editorial prose" (AGENTS.md rule 6).
        """
        record = self._repo.entity(EntityType.ARTICLE.value, id_or_slug)
        if record is None:
            raise NotFoundError(f"no article with id or slug {id_or_slug!r}")
        if record.status.value == "archived":
            raise GoneError(f"article {id_or_slug} has been archived", id=record.id)
        payload = self._entities.detail(record, locale)
        body = payload.pop("body_md", None)
        article = dict(payload.get("article") or {})
        article.update(
            {
                "title": record.display_name(locale),
                "title_secondary": record.secondary_name(locale),
                "title_fa": record.display_name(Locale.FA),
                "title_en": record.display_name(Locale.EN),
                "body_md": body,
                "body_fa_md": record.body_md,
                "body_en_md": record.extra.get("body_en_md"),
                "entities": self._linked_entity_briefs(record, locale),
            }
        )
        payload["article"] = article
        return payload


# --------------------------------------------------------------------- helpers


def _href(record: EntityRecord) -> str:
    """Canonical read URL. Articles have their own route; everything else shares one."""
    if record.entity_type is EntityType.ARTICLE:
        return f"/api/v1/articles/{record.slug or record.id}"
    return f"/api/v1/entities/{record.entity_type.value}/{record.slug or record.id}"


def _brief(record: EntityRecord, locale: str) -> dict[str, Any]:
    geometry = record.primary_geometry()
    rep = geometry.representative_point() if geometry else None
    return {
        "id": record.id,
        "entity_type": record.entity_type.value,
        "kind": record.kind,
        "kind_label": _kind_label(record, locale),
        "slug": record.slug,
        "label": record.display_name(locale),
        "label_secondary": record.secondary_name(locale),
        "t_display": record.temporal_display(locale),
        "t_from": record.temporal.year_from if record.temporal else None,
        "t_to": record.temporal.year_to if record.temporal else None,
        "rank": record.rank,
        "layer": record.layer,
        "center": list(rep) if rep else None,
        "has_disagreements": record.has_disagreements,
        "status": record.status.value,
        "href": _href(record),
    }


def _article_brief(record: EntityRecord, locale: str) -> dict[str, Any]:
    base = _brief(record, locale)
    base.update(
        {
            "title": record.extra.get("title_fa") or record.display_name(locale),
            "title_en": record.extra.get("title_en"),
            "summary": record.summary if locale == Locale.FA else (record.summary_en or record.summary),
            "map_state": record.extra.get("map_state"),
            "published_at": record.extra.get("published_at"),
            "reading_time_min": record.extra.get("reading_time_min"),
            "relation": None,
        }
    )
    return base


def _source_brief(record: EntityRecord, locale: str) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "title": record.extra.get("title_fa") or record.display_name(locale),
        "title_en": record.extra.get("title_en") or record.display_name(Locale.EN),
        "author": record.extra.get("author_fa") or record.extra.get("author"),
        "year": record.extra.get("year") or record.extra.get("origin_year"),
        "publisher": record.extra.get("publisher"),
        "reliability": record.extra.get("reliability"),
        "citation": record.extra.get("citation"),
        "url": record.extra.get("url"),
    }


def _coverage_note(record: EntityRecord, locale: str) -> str | None:
    key = "coverage_note_fa" if locale == Locale.FA else "coverage_note_en"
    note = record.extra.get(key) or record.extra.get("coverage_note_fa")
    return str(note) if note else None


def _kind_label(record: EntityRecord, locale: str) -> str | None:
    """Kind labels come from the taxonomy, which the API exposes via /meta (rule 17)."""
    if not record.kind:
        return None
    key = "kind_fa" if locale == Locale.FA else "kind_en"
    if record.extra.get(key):
        return str(record.extra[key])
    return record.kind.replace("_", " ")
