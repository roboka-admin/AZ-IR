"""Seed PostgreSQL+PostGIS from the fixture corpus (``azir seed``).

The fixtures loader is the *normalizer*: it turns YAML into domain records (names folded, calendars
converted, rank/layer/zoom bands computed, derived loci flagged). The seeder persists exactly those
records, which is why the PostGIS driver and the fixtures driver agree from the very first row
(ADR-0014). Nothing here re-implements domain rules.

Write order follows the foreign keys; the whole seed is one transaction, so a failure leaves the
database exactly as it was.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ...domain import text as textnorm
from ...domain.model import EntityRecord
from ...domain.temporal import TemporalInterval
from ..fixtures import INVERSE_PREDICATES, FixturesRepository, build_temporal
from . import schema

TRUNCATE_ORDER = (
    "evidence", "assertion", "article_entity", "event_link", "event_participant", "event_place",
    "place_link", "entity_geometry", "name_variant", "revision_snapshot", "audit_log",
    "slug_redirect", "lint_run", "editorial_state", "app_session", "article", "political_entity",
    "event", "person", "place", "source", "period", "period_scheme", "predicate", "entity_kind",
    "app_user",
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What was written, so `azir seed` can be verified instead of trusted."""

    tables: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"driver": "postgis", "tables": dict(self.tables), "rows": sum(self.tables.values())}


# ------------------------------------------------------------------ spine mappers


def _temporal_columns(temporal: TemporalInterval | None) -> dict[str, Any]:
    if temporal is None:
        return {
            "year_from": None,
            "year_to": None,
            "precision": "unknown",
            "confidence": "medium",
            "calendar": "gregorian_proleptic",
            "temporal_display_fa": None,
            "temporal_display_en": None,
        }
    return {
        "year_from": temporal.year_from,
        "year_to": temporal.year_to,
        "precision": temporal.precision.value,
        "confidence": temporal.confidence.value,
        "calendar": temporal.calendar.value,
        "temporal_display_fa": temporal.display,
        "temporal_display_en": None,
    }


def _entity_row(record: EntityRecord) -> dict[str, Any]:
    """Columns shared by every narrative entity table."""
    return {
        "id": record.id,
        "kind": record.kind,
        "slug": record.slug,
        "status": record.status.value,
        "revision": record.revision,
        "importance": record.importance,
        "rank": record.rank,
        "layer": record.layer,
        "min_zoom": record.min_zoom,
        "max_zoom": record.max_zoom,
        **_temporal_columns(record.temporal),
        "summary_fa": record.summary,
        "summary_en": record.summary_en,
        "coverage_note_fa": record.extra.get("coverage_note_fa"),
        "coverage_note_en": record.extra.get("coverage_note_en"),
        "certainty": record.certainty,
        "sources": list(record.source_ids),
        "extra": dict(record.extra),
    }


def _ref_row(record: EntityRecord) -> dict[str, Any]:
    """Presentation spine for reference data (sources, periods)."""
    return {
        "status": record.status.value,
        "rank": record.rank,
        "layer": record.layer,
        "min_zoom": record.min_zoom,
        "max_zoom": record.max_zoom,
        **_temporal_columns(record.temporal),
        "summary_fa": record.summary,
        "summary_en": record.summary_en,
        "certainty": record.certainty,
        "extra": dict(record.extra),
    }


def _name_rows(record: EntityRecord) -> list[dict[str, Any]]:
    """One row per name variant, with the folded search form written at seed time (ADR-0007)."""
    rows: list[dict[str, Any]] = []
    for name in record.names:
        rows.append(
            {
                "entity_type": record.entity_type.value,
                "entity_id": record.id,
                "form": name.form,
                "lang": name.lang,
                "script": name.script,
                "kind": name.kind,
                "transliteration": name.transliteration,
                "year_from": name.year_from,
                "year_to": name.year_to,
                "source_id": name.source_id,
                "note": name.note,
                "search_form": textnorm.build_search_text(name.form),
            }
        )
    return rows


def _geometry_rows(record: EntityRecord) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for geometry in record.geometries:
        rows.append(
            {
                "entity_type": record.entity_type.value,
                "entity_id": record.id,
                "kind": geometry.kind.value,
                "certainty": geometry.certainty.value,
                # A dict, never a JSON string: the column is JSONB and SQLAlchemy serializes it.
                # A pre-encoded string would be stored as a JSON *scalar* and ST_GeomFromGeoJSON
                # below would then fail on every geometry (found by the real-PostGIS CI run).
                "geom_json": dict(geometry.geojson),
                "year_from": geometry.year_from,
                "year_to": geometry.year_to,
                "lod_min_zoom": geometry.lod_min_zoom,
                "lod_max_zoom": geometry.lod_max_zoom,
                "source_id": geometry.source_id,
                "note_fa": geometry.note,
                "note_en": geometry.note_en,
                "needs_digitisation": geometry.needs_digitisation,
            }
        )
    return rows


def _first(values: Iterable[Any]) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _name_in(record: EntityRecord | None, lang: str) -> str | None:
    if record is None:
        return None
    for name in record.names:
        if name.lang == lang:
            return name.form
    return None


# ------------------------------------------------------------------ seeder


def seed_database(
    engine: Engine, fixtures_dir: str | Path, *, truncate: bool = True, analyze: bool = True
) -> SeedReport:
    """Load the fixture corpus into PostgreSQL+PostGIS. Idempotent when ``truncate`` is true."""
    loader = FixturesRepository(fixtures_dir)
    docs: Mapping[str, list[dict[str, Any]]] = loader.raw_documents
    records = loader.records
    by_type: dict[str, list[EntityRecord]] = {}
    for record in records:
        by_type.setdefault(record.entity_type.value, []).append(record)
    by_id = {record.id: record for record in records}
    raw_by_id: dict[str, dict[str, Any]] = {}
    for key in ("places", "people", "events", "political_entities", "articles", "sources", "periods"):
        for item in docs.get(key, []):
            raw_by_id[str(item["id"])] = item

    report = SeedReport()
    counts = report.tables

    with engine.begin() as connection:
        if truncate:
            connection.execute(
                text(f"TRUNCATE TABLE {', '.join(f'public.{name}' for name in TRUNCATE_ORDER)} "
                     "RESTART IDENTITY CASCADE")
            )

        # -- vocabulary ---------------------------------------------------
        kinds = [
            {
                "code": str(item["code"]),
                "category": str(item.get("kind") or item.get("category") or "kind"),
                "label_fa": str(item.get("label_fa") or item["code"]),
                "label_en": str(item.get("label_en") or item["code"]),
                "weight": float(item.get("weight", 0.5)),
                "is_active": True,
            }
            for item in docs.get("taxonomy", [])
        ]
        _insert(connection, schema.entity_kind, kinds, counts)

        predicates = [
            {
                "code": str(item["code"]),
                "label_fa": str(item.get("label_fa") or item["code"]),
                "label_en": str(item.get("label_en") or item["code"]),
                "inverse_code": INVERSE_PREDICATES.get(str(item["code"])),
                "applies_to": list(item.get("applies_to") or []),
                "is_active": True,
            }
            for item in docs.get("predicates", [])
        ]
        # A predicate used by the corpus but missing from the vocabulary would break the FK, and
        # silently dropping the claim is worse than inventing a label from the code.
        known = {row["code"] for row in predicates}
        for code in sorted({str(item.get("predicate")) for item in docs.get("assertions", [])} - known - {""}):
            predicates.append(
                {
                    "code": code,
                    "label_fa": code.replace("_", " "),
                    "label_en": code.replace("_", " "),
                    "inverse_code": INVERSE_PREDICATES.get(code),
                    "applies_to": [],
                    "is_active": True,
                }
            )
        _insert(connection, schema.predicate, predicates, counts)

        schemes = [
            {
                "id": str(item["id"]),
                "code": str(item.get("code") or item["id"]),
                "name_fa": str(item.get("name_fa") or item.get("name") or item["id"]),
                "name_en": str(item.get("name_en") or item.get("name") or item["id"]),
                "description_fa": item.get("description_fa"),
                "owner": item.get("owner"),
                "is_default": bool(item.get("is_default")),
                "sources": list(item.get("sources") or []),
            }
            for item in docs.get("schemes", [])
        ]
        _insert(connection, schema.period_scheme, schemes, counts)

        # -- reference data -----------------------------------------------
        period_rows = []
        for index, item in enumerate(docs.get("periods", [])):
            period_record = by_id.get(str(item["id"]))
            raw = raw_by_id.get(str(item["id"]), {})
            period_rows.append(
                {
                    "id": str(item["id"]),
                    "scheme_id": str(item.get("scheme") or (schemes[0]["id"] if schemes else "")),
                    "code": str(item.get("code") or item["id"]),
                    "label_fa": str(item.get("label_fa") or _name_in(period_record, "fa") or item["id"]),
                    "label_en": str(item.get("label_en") or _name_in(period_record, "en") or item["id"]),
                    "note_fa": item.get("note_fa"),
                    "note_en": item.get("note_en"),
                    "sort_order": int(item.get("sort_order", index)),
                    **(_ref_row(period_record) if period_record else {}),
                    **_override_years(period_record, raw),
                }
            )
        _insert(connection, schema.period, period_rows, counts)

        source_rows = []
        for item in docs.get("sources", []):
            source_record = by_id.get(str(item["id"]))
            source_rows.append(
                {
                    "id": str(item["id"]),
                    "kind": str(item.get("kind") or "secondary"),
                    "title": str(item.get("title") or item.get("title_fa") or item["id"]),
                    "title_fa": item.get("title_fa"),
                    "title_en": item.get("title_en") or item.get("title"),
                    "author": item.get("author"),
                    "author_fa": item.get("author_fa"),
                    "origin_year": item.get("origin_year"),
                    "origin_temporal": item.get("origin_temporal"),
                    "reliability": str(item.get("reliability") or "secondary"),
                    "language": item.get("language"),
                    "publisher": item.get("publisher"),
                    "citation": item.get("citation"),
                    "url": item.get("url"),
                    "license": item.get("license"),
                    "needs_review": bool(item.get("needs_review")),
                    "external_ids": dict(item.get("external_ids") or {}),
                    **(_ref_row(source_record) if source_record else {}),
                }
            )
        _insert(connection, schema.source, source_rows, counts)

        # -- narrative entities -------------------------------------------
        for entity_type, table in (
            ("place", schema.place),
            ("person", schema.person),
            ("political_entity", schema.political_entity),
        ):
            _insert(
                connection,
                table,
                [_entity_row(record) for record in by_type.get(entity_type, [])],
                counts,
            )

        event_rows = []
        for record in by_type.get("event", []):
            event_rows.append(
                {
                    **_entity_row(record),
                    "attestation": record.attestation.value if record.attestation else None,
                }
            )
        _insert(connection, schema.event, event_rows, counts)

        article_rows = []
        for record in by_type.get("article", []):
            raw = raw_by_id.get(record.id, {})
            article_rows.append(
                {
                    **_entity_row(record),
                    "title_fa": _name_in(record, "fa"),
                    "title_en": _name_in(record, "en"),
                    "body_fa_md": raw.get("body_fa_md") or record.body_md,
                    "body_en_md": raw.get("body_en_md"),
                    "author": raw.get("author"),
                    "published_at": raw.get("published_at"),
                    "reading_time_min": raw.get("reading_time_min"),
                    "lang": raw.get("lang") or "fa",
                    "map_state": raw.get("map_state") or record.extra.get("map_state"),
                }
            )
        _insert(connection, schema.article, article_rows, counts)

        # -- editorial state ------------------------------------------------
        # Every editable record gets a workflow row, so the review queue and the audit trail have
        # somewhere to hang metadata even for corpus that arrived through the seeder (ADR-0010).
        editable = {"place", "person", "event", "political_entity", "article"}
        state_rows = [
            {
                "entity_type": record.entity_type.value,
                "entity_id": record.id,
                "revision": record.revision,
                "head_id": None,
                "published_at": None,
            }
            for record in records
            if record.entity_type.value in editable
        ]
        _insert(connection, schema.editorial_state, state_rows, counts)
        published_keys = [
            (record.entity_type.value, record.id)
            for record in records
            if record.entity_type.value in editable and record.status.value == "published"
        ]
        if published_keys:
            # SQL expressions in executemany parameter mappings are sent to psycopg2 as values,
            # not rendered. Stamp published rows in one explicit statement so PostgreSQL's clock
            # remains authoritative (and draft timestamps remain NULL).
            connection.execute(
                schema.editorial_state.update()
                .where(
                    sa.tuple_(
                        schema.editorial_state.c.entity_type,
                        schema.editorial_state.c.entity_id,
                    ).in_(published_keys)
                )
                .values(published_at=sa.func.now())
            )

        # -- facets --------------------------------------------------------
        name_rows = [row for record in records for row in _name_rows(record)]
        _insert(connection, schema.name_variant, name_rows, counts)

        geometry_rows = [row for record in records for row in _geometry_rows(record)]
        _insert(connection, schema.entity_geometry, geometry_rows, counts)
        # `geom` is the authoritative spatial column and is derived from the cached GeoJSON, so a
        # malformed shape fails the seed loudly instead of appearing later as a blank map.
        connection.execute(
            text(
                "UPDATE public.entity_geometry "
                "SET geom = ST_SetSRID(ST_GeomFromGeoJSON(geom_json::text), 4326) "
                "WHERE geom IS NULL"
            )
        )

        # -- structural links ---------------------------------------------
        link_rows = []
        for item in docs.get("place_links", []):
            temporal = build_temporal(item.get("temporal"))
            link_rows.append(
                {
                    "parent_id": str(item["parent"]),
                    "child_id": str(item["child"]),
                    "kind": str(item.get("kind") or "contains"),
                    **_interval_columns(temporal),
                    "certainty": item.get("certainty"),
                    "sources": list(item.get("sources") or []),
                    "note_fa": item.get("note_fa"),
                    "note_en": item.get("note_en"),
                }
            )
        _insert(connection, schema.place_link, link_rows, counts)

        place_rows: list[dict[str, Any]] = []
        participant_rows: list[dict[str, Any]] = []
        event_link_rows: list[dict[str, Any]] = []
        for item in docs.get("events", []):
            event_id = str(item["id"])
            event_record = by_id.get(event_id)
            interval = _interval_columns(event_record.temporal if event_record else None)
            for place_ref in item.get("places") or []:
                place_rows.append(
                    {
                        "event_id": event_id,
                        "place_id": str(place_ref["id"]),
                        "role": str(place_ref.get("role") or "site_of"),
                        "certainty": place_ref.get("certainty"),
                        **interval,
                    }
                )
            for actor in item.get("participants") or []:
                participant_rows.append(
                    {
                        "event_id": event_id,
                        "participant_type": str(actor.get("type") or "person"),
                        "participant_id": str(actor["id"]),
                        "role": actor.get("role"),
                        "side": actor.get("side"),
                    }
                )
            if item.get("part_of"):
                event_link_rows.append(
                    {"event_id": event_id, "relation": "part_of", "other_event_id": str(item["part_of"])}
                )
        _insert(connection, schema.event_place, place_rows, counts)
        _insert(connection, schema.event_participant, participant_rows, counts)
        _insert(connection, schema.event_link, event_link_rows, counts)

        article_link_rows = []
        for item in docs.get("articles", []):
            for ref in item.get("entities") or []:
                article_link_rows.append(
                    {
                        "article_id": str(item["id"]),
                        "entity_type": str(ref.get("type") or "place"),
                        "entity_id": str(ref["id"]),
                        "relation": str(ref.get("relation") or "mentions"),
                    }
                )
        _insert(connection, schema.article_entity, article_link_rows, counts)

        # -- claims (the graph heart) --------------------------------------
        assertion_rows: list[dict[str, Any]] = []
        evidence_rows: list[dict[str, Any]] = []
        for item in docs.get("assertions", []):
            assertion_id = str(item["id"])
            subject = item["subject"]
            target = item.get("object") or {}
            temporal = build_temporal(item.get("temporal"))
            assertion_rows.append(
                {
                    "id": assertion_id,
                    "subject_type": str(subject["type"]),
                    "subject_id": str(subject["id"]),
                    "predicate": str(item["predicate"]),
                    "object_type": target.get("type"),
                    "object_id": target.get("id"),
                    "value": item.get("value"),
                    **_interval_columns(temporal),
                    "calendar": (temporal.calendar.value if temporal else "gregorian_proleptic"),
                    "temporal_display_fa": (temporal.display if temporal else None),
                    "status": str(item.get("status") or "accepted"),
                    "topic_fa": item.get("topic_fa") or item.get("topic"),
                    "topic_en": item.get("topic_en") or item.get("topic"),
                    "note_fa": item.get("note_fa") or item.get("note"),
                    "note_en": item.get("note_en"),
                    "role": item.get("role"),
                    "created_by": item.get("created_by"),
                }
            )
            for evidence in item.get("evidence") or []:
                evidence_rows.append(
                    {
                        "assertion_id": assertion_id,
                        "source_id": str(evidence["source"]),
                        "locator_type": str(evidence.get("locator_type") or "page"),
                        "locator": evidence.get("locator"),
                        "quote_original": evidence.get("quote_original"),
                        "quote_translation": evidence.get("quote_translation"),
                        "stance": str(evidence.get("stance") or "supports"),
                    }
                )
        _insert(connection, schema.assertion, assertion_rows, counts)
        _insert(connection, schema.evidence, evidence_rows, counts)

        if analyze:
            for name in ("place", "person", "event", "political_entity", "article",
                         "entity_geometry", "name_variant", "assertion"):
                connection.execute(text(f"ANALYZE public.{name}"))

    return report


def _interval_columns(temporal: TemporalInterval | None) -> dict[str, Any]:
    if temporal is None:
        return {"year_from": None, "year_to": None, "precision": "range", "confidence": "medium"}
    return {
        "year_from": temporal.year_from,
        "year_to": temporal.year_to,
        "precision": temporal.precision.value,
        "confidence": temporal.confidence.value,
    }


def _override_years(record: EntityRecord | None, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Periods keep their own year columns; the spine must not overwrite them with NULL."""
    if record is None:
        return {}
    out: dict[str, Any] = {}
    if raw.get("from") is not None:
        out["year_from"] = int(raw["from"])
    if raw.get("to") is not None:
        out["year_to"] = int(raw["to"])
    if raw.get("precision"):
        out["precision"] = str(raw["precision"])
    if raw.get("confidence"):
        out["confidence"] = str(raw["confidence"])
    return out


def _insert(connection: Any, table: Any, rows: Sequence[Mapping[str, Any]], counts: dict[str, int]) -> None:
    if not rows:
        counts[table.name] = 0
        return
    connection.execute(table.insert(), [dict(row) for row in rows])
    counts[table.name] = counts.get(table.name, 0) + len(rows)


__all__ = ["SeedReport", "seed_database"]
