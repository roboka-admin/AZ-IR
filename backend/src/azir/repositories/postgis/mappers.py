"""Row -> domain mappers for the PostGIS driver.

Every value a service sees is a domain object; nothing from SQLAlchemy escapes this module
(AGENTS.md rule 4). Mappers are deliberately tolerant: an unknown enum spelling in the database
degrades to a safe default instead of taking the API down, because a data-entry mistake must never
become an outage. The data linter (``azir doctor``) is what reports it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import Enum
from typing import Any, TypeVar

from ...domain.enums import (
    AssertionStatus,
    Attestation,
    Calendar,
    Certainty,
    Confidence,
    EntityType,
    GeometryKind,
    Precision,
    Status,
)
from ...domain.geo import GeometryRecord
from ...domain.model import (
    Disagreement,
    EntityCounts,
    EntityRecord,
    EvidenceRef,
    NameVariant,
    Relationship,
)
from ...domain.temporal import TemporalInterval

Row = Mapping[str, Any]
E = TypeVar("E", bound=Enum)


def _enum(kind: type[E], value: Any, default: E) -> E:
    if value is None or value == "":
        return default
    if isinstance(value, kind):
        return value
    try:
        return kind(str(value))
    except ValueError:
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _texts(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


# ------------------------------------------------------------------ temporal


def temporal_from(row: Row) -> TemporalInterval | None:
    """Rebuild the normalized interval. ``year_from``/``year_to`` are always both set or both null."""
    year_from = _int(row.get("year_from"))
    year_to = _int(row.get("year_to"))
    if year_from is None or year_to is None:
        return None
    return TemporalInterval(
        year_from=year_from,
        year_to=year_to,
        precision=_enum(Precision, row.get("precision"), Precision.UNKNOWN),
        calendar=_enum(Calendar, row.get("calendar"), Calendar.GREGORIAN_PROLEPTIC),
        confidence=_enum(Confidence, row.get("confidence"), Confidence.MEDIUM),
        display=_text(row.get("temporal_display_fa")) or _text(row.get("temporal_display_en")),
    )


# ------------------------------------------------------------------ facets


def name_from(row: Row) -> NameVariant:
    return NameVariant(
        form=str(row["form"]),
        lang=str(row.get("lang") or "fa"),
        script=str(row.get("script") or "Arab"),
        kind=str(row.get("kind") or "preferred"),
        transliteration=_text(row.get("transliteration")),
        year_from=_int(row.get("year_from")),
        year_to=_int(row.get("year_to")),
        source_id=_text(row.get("source_id")),
        note=_text(row.get("note")),
    )


def geometry_from(row: Row) -> GeometryRecord:
    geojson = row.get("geojson")
    if isinstance(geojson, str):  # ST_AsGeoJSON returns text unless cast in SQL
        import json

        geojson = json.loads(geojson)
    return GeometryRecord(
        geojson=dict(geojson or {"type": "Point", "coordinates": [0.0, 0.0]}),
        kind=_enum(GeometryKind, row.get("kind"), GeometryKind.POINT),
        certainty=_enum(Certainty, row.get("certainty"), Certainty.EXACT),
        lod_min_zoom=_float(row.get("lod_min_zoom"), 0.0),
        lod_max_zoom=_float(row.get("lod_max_zoom"), 22.0),
        year_from=_int(row.get("year_from")),
        year_to=_int(row.get("year_to")),
        source_id=_text(row.get("source_id")),
        note=_text(row.get("note_fa")),
        note_en=_text(row.get("note_en")),
        needs_digitisation=bool(row.get("needs_digitisation")),
    )


def entity_from(
    row: Row,
    *,
    names: Sequence[NameVariant] = (),
    geometries: Sequence[GeometryRecord] = (),
    relationships: Sequence[Relationship] = (),
    disagreements: Sequence[Disagreement] = (),
    counts: EntityCounts | None = None,
    article_ids: Sequence[str] = (),
) -> EntityRecord:
    """Assemble the single read-model record services consume."""
    extra = dict(row.get("extra") or {})
    summary_fa = _text(row.get("summary_fa"))
    summary_en = _text(row.get("summary_en"))
    return EntityRecord(
        id=str(row["id"]),
        entity_type=_enum(EntityType, row.get("entity_type"), EntityType.PLACE),
        kind=_text(row.get("kind")),
        slug=_text(row.get("slug")),
        status=_enum(Status, row.get("status"), Status.DRAFT),
        revision=_int(row.get("revision")) or 1,
        importance=_float(row.get("importance"), 0.5),
        rank=_float(row.get("rank"), 0.0),
        min_zoom=_float(row.get("min_zoom"), 0.0),
        max_zoom=_float(row.get("max_zoom"), 22.0),
        layer=str(row.get("layer") or "places"),
        temporal=temporal_from(row),
        names=tuple(names),
        geometries=tuple(geometries),
        summary=summary_fa or summary_en,
        summary_en=summary_en,
        body_md=_text(row.get("body_fa_md")) or _text(row.get("body_en_md")),
        attestation=(
            _enum(Attestation, row.get("attestation"), Attestation.UNKNOWN)
            if row.get("attestation")
            else None
        ),
        certainty=_text(row.get("certainty")),
        source_ids=_texts(row.get("sources")),
        article_ids=tuple(article_ids),
        relationships=tuple(relationships),
        disagreements=tuple(disagreements),
        counts=counts or EntityCounts(),
        extra=extra,
    )


# ------------------------------------------------------------------ claims


def evidence_from(rows: Iterable[Row]) -> tuple[EvidenceRef, ...]:
    out: list[EvidenceRef] = []
    for row in rows:
        if not row.get("source_id"):
            continue
        out.append(
            EvidenceRef(
                source_id=str(row["source_id"]),
                locator_type=str(row.get("locator_type") or "page"),
                locator=_text(row.get("locator")),
                quote_original=_text(row.get("quote_original")),
                quote_translation=_text(row.get("quote_translation")),
                stance=str(row.get("stance") or "supports"),
            )
        )
    return tuple(out)


def relationship_from(row: Row, *, labels: Mapping[str, tuple[str, str]]) -> Relationship:
    """One resolved edge.

    ``row`` is the common shape produced by every relationship query in the repository: the
    predicate plus its labels, the resolved object (type/id/label/slug) or a literal value, the
    temporal spine, confidence/status/certainty and any evidence.
    """
    predicate = str(row.get("predicate") or "related_to")
    label_fa, label_en = labels.get(predicate, (predicate.replace("_", " "), predicate.replace("_", " ")))
    if row.get("label_fa"):
        label_fa = str(row["label_fa"])
    if row.get("label_en"):
        label_en = str(row["label_en"])
    evidence = row.get("evidence") or ()
    return Relationship(
        predicate=predicate,
        label_fa=label_fa,
        label_en=label_en,
        direction=str(row.get("direction") or "out"),
        object_type=(
            _enum(EntityType, row.get("object_type"), EntityType.PLACE)
            if row.get("object_type")
            else None
        ),
        object_id=_text(row.get("object_id")),
        object_label=_text(row.get("object_label")),
        object_slug=_text(row.get("object_slug")),
        object_value=_text(row.get("object_value")),
        role=_text(row.get("role")),
        side=_text(row.get("side")),
        certainty=_text(row.get("certainty")),
        temporal=temporal_from(row),
        confidence=_enum(Confidence, row.get("confidence"), Confidence.MEDIUM),
        is_claim=bool(row.get("is_claim")),
        status=_enum(AssertionStatus, row.get("status"), AssertionStatus.ACCEPTED),
        evidence=evidence_from(evidence) if isinstance(evidence, Iterable) and not isinstance(evidence, str) else (),
        note=_text(row.get("note_fa")) or _text(row.get("note")),
    )


def group_disagreements(
    rows: Iterable[Row], *, labels: Mapping[str, tuple[str, str]]
) -> list[Disagreement]:
    """Competing positions on one topic, in database order (AGENTS.md rule 20)."""
    grouped: dict[tuple[str, str], list[Relationship]] = {}
    topics: dict[tuple[str, str], str] = {}
    for row in rows:
        subject = str(row.get("subject_id") or row.get("id") or "")
        topic_fa = str(row.get("topic_fa") or row.get("predicate") or "")
        topic_en = str(row.get("topic_en") or topic_fa)
        key = (subject, topic_fa)
        grouped.setdefault(key, []).append(relationship_from(row, labels=labels))
        topics.setdefault(key, topic_en)
    return [
        Disagreement(topic=key[1], topic_en=topics[key], positions=tuple(positions))
        for key, positions in grouped.items()
    ]


__all__ = [
    "Disagreement",
    "entity_from",
    "evidence_from",
    "geometry_from",
    "group_disagreements",
    "name_from",
    "relationship_from",
    "temporal_from",
]
