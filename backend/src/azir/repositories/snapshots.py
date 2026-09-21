"""Shared write-side mappings: record <-> draft <-> revision snapshot.

Both adapters build the same rows from the same domain objects, so the mapping lives here exactly
once (ADR-0014). Nothing in this module touches a database, a YAML file or the network: it is pure
data shaping, which is why a fixtures-driver draft and a PostGIS draft are byte-identical in the
audit trail.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ConflictError
from ..domain.editorial import EDITABLE_STATUSES, Action
from ..domain.enums import Attestation, EntityType, Status
from ..domain.geo import GeometryRecord
from ..domain.model import EntityCounts, EntityRecord, NameVariant, Relationship
from ..domain.semantic_zoom import Presentation
from ..domain.temporal import TemporalInterval
from .ports import EntityDraft

#: Columns the presentation layer derives. A write never sets them by hand (AGENTS.md rule 10).
PRESENTATION_FIELDS = ("rank", "layer", "min_zoom", "max_zoom")


def draft_from_record(record: EntityRecord) -> EntityDraft:
    """The editable view of an existing record: what a revision copy starts from."""
    extra = dict(record.extra)
    return EntityDraft(
        entity_type=record.entity_type,
        kind=record.kind,
        slug=record.slug,
        names=record.names,
        temporal=record.temporal,
        geometries=record.geometries,
        summary=record.summary,
        summary_en=record.summary_en,
        coverage_note=_text(extra.get("coverage_note_fa")),
        coverage_note_en=_text(extra.get("coverage_note_en")),
        source_ids=record.source_ids,
        importance=record.importance,
        certainty=record.certainty,
        attestation=record.attestation,
        title=_text(extra.get("title_fa")),
        title_en=_text(extra.get("title_en")),
        body_md=record.body_md,
        body_en_md=_text(extra.get("body_en_md")),
        author=_text(extra.get("author")),
        map_state=extra.get("map_state") if isinstance(extra.get("map_state"), dict) else None,
        extra={k: v for k, v in extra.items() if k not in _DRAFT_OWNED_EXTRA},
    )


#: Keys that ``draft_from_record`` lifts into typed fields, so they are not written twice.
_DRAFT_OWNED_EXTRA = frozenset(
    {
        "coverage_note_fa",
        "coverage_note_en",
        "title_fa",
        "title_en",
        "body_en_md",
        "body_fa_md",
        "author",
        "map_state",
    }
)


def apply_patch(current: EntityDraft, patch: dict[str, Any]) -> EntityDraft:
    """Merge a partial request onto a complete draft.

    The repository port only ever receives a *complete* draft; deciding what "unchanged" means is
    business logic, so it happens here (in the service's hands) and not in an adapter.
    """
    from dataclasses import replace

    changes: dict[str, Any] = {}
    for key, value in patch.items():
        if value is None:
            continue
        if key == "names":
            changes["names"] = tuple(_as_name(item) for item in value)
        elif key == "geometries":
            changes["geometries"] = tuple(_as_geometry(item) for item in value)
        elif key == "geometry":
            changes["geometries"] = (_as_geometry(value),)
        elif key == "temporal":
            changes["temporal"] = _as_temporal(value)
        elif key == "attestation":
            changes["attestation"] = (
                value if isinstance(value, Attestation) else Attestation(str(value))
            )
        elif key == "source_ids":
            changes["source_ids"] = tuple(str(item) for item in value)
        elif key == "extra":
            merged = dict(current.extra)
            merged.update(dict(value))
            changes["extra"] = merged
        else:
            changes[key] = value
    return replace(current, **changes)


def _as_name(value: Any) -> NameVariant:
    if isinstance(value, NameVariant):
        return value
    data = dict(value)
    return NameVariant(
        form=str(data["form"]),
        lang=str(data.get("lang") or "fa"),
        script=str(data.get("script") or "Arab"),
        kind=str(data.get("kind") or "preferred"),
        transliteration=_text(data.get("transliteration")),
        year_from=_int(data.get("year_from")),
        year_to=_int(data.get("year_to")),
        source_id=_text(data.get("source_id")),
        note=_text(data.get("note")),
    )


def _as_geometry(value: Any) -> GeometryRecord:
    if isinstance(value, GeometryRecord):
        return value
    data = dict(value)
    geojson = data.get("geojson") or {"type": "Point", "coordinates": data.get("coordinates", [0, 0])}
    return GeometryRecord(
        geojson=dict(geojson),
        kind=_geometry_kind(data.get("kind")),
        certainty=_certainty(data.get("certainty")),
        lod_min_zoom=float(data.get("lod_min_zoom") or 0.0),
        lod_max_zoom=float(data.get("lod_max_zoom") or 22.0),
        year_from=_int(data.get("year_from")),
        year_to=_int(data.get("year_to")),
        source_id=_text(data.get("source_id")),
        note=_text(data.get("note_fa") or data.get("note")),
        note_en=_text(data.get("note_en")),
        needs_digitisation=bool(data.get("needs_digitisation")),
    )


def _geometry_kind(value: Any) -> Any:
    from ..domain.enums import GeometryKind

    if value is None:
        return GeometryKind.POINT
    return value if isinstance(value, GeometryKind) else GeometryKind(str(value))


def _certainty(value: Any) -> Any:
    from ..domain.enums import Certainty

    if value is None:
        return Certainty.EXACT
    return value if isinstance(value, Certainty) else Certainty(str(value))


def _as_temporal(value: Any) -> TemporalInterval | None:
    if value is None:
        return None
    if isinstance(value, TemporalInterval):
        return value
    from ..repositories.fixtures import build_temporal

    return build_temporal(dict(value))


def draft_extra(draft: EntityDraft) -> dict[str, Any]:
    """``extra`` as it is stored: the free-form bag plus the typed article/coverage fields."""
    extra: dict[str, Any] = dict(draft.extra)
    if draft.coverage_note:
        extra["coverage_note_fa"] = draft.coverage_note
    if draft.coverage_note_en:
        extra["coverage_note_en"] = draft.coverage_note_en
    if draft.entity_type is EntityType.ARTICLE:
        if draft.title:
            extra["title_fa"] = draft.title
        if draft.title_en:
            extra["title_en"] = draft.title_en
        if draft.body_md:
            extra["body_fa_md"] = draft.body_md
        if draft.body_en_md:
            extra["body_en_md"] = draft.body_en_md
        if draft.author:
            extra["author"] = draft.author
        if draft.map_state:
            extra["map_state"] = dict(draft.map_state)
    return extra


def snapshot_payload(record: EntityRecord) -> dict[str, Any]:
    """The frozen content of one revision, as stored in ``revision_snapshot.payload``."""
    temporal = record.temporal
    payload: dict[str, Any] = {
        "id": record.id,
        "entity_type": record.entity_type.value,
        "kind": record.kind,
        "slug": record.slug,
        "status": record.status.value,
        "revision": record.revision,
        "importance": record.importance,
        "certainty": record.certainty,
        "attestation": record.attestation.value if record.attestation else None,
        "summary": record.summary,
        "summary_en": record.summary_en,
        "source_ids": list(record.source_ids),
        "names": [
            {
                "form": name.form,
                "lang": name.lang,
                "script": name.script,
                "kind": name.kind,
                "transliteration": name.transliteration,
                "year_from": name.year_from,
                "year_to": name.year_to,
                "source_id": name.source_id,
                "note": name.note,
            }
            for name in record.names
        ],
        "geometries": [
            {
                "geojson": geometry.geojson,
                "kind": geometry.kind.value,
                "certainty": geometry.certainty.value,
                "year_from": geometry.year_from,
                "year_to": geometry.year_to,
                "lod_min_zoom": geometry.lod_min_zoom,
                "lod_max_zoom": geometry.lod_max_zoom,
                "source_id": geometry.source_id,
                "note_fa": geometry.note,
                "note_en": geometry.note_en,
                "needs_digitisation": geometry.needs_digitisation,
            }
            for geometry in record.geometries
        ],
        "temporal": (
            {
                "year_from": temporal.year_from,
                "year_to": temporal.year_to,
                "precision": temporal.precision.value,
                "calendar": temporal.calendar.value,
                "confidence": temporal.confidence.value,
                "display": temporal.display,
            }
            if temporal
            else None
        ),
        "extra": dict(record.extra),
    }
    return payload


def temporal_columns(temporal: TemporalInterval | None) -> dict[str, Any]:
    """The stored spine of a temporal claim. Never invents precision (ADR-0005)."""
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


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def not_editable_error(status: Status, entity_id: str) -> ConflictError:
    """The rejection both adapters raise for a record that may not be edited in place.

    Living here (rather than twice, once per driver) is what keeps the two drivers honest: the
    contract suite asserts the *behaviour*, and this keeps the wording identical too.
    """
    if status is Status.PUBLISHED:
        return ConflictError(
            "a published record is edited through a new revision, so the public version stays "
            "intact until a reviewer approves the copy (ADR-0010 rule 4)",
            entity_id=entity_id,
            status=status.value,
            action=Action.BEGIN_REVISION.value,
        )
    return ConflictError(
        f"a record that is {status.value} cannot be edited in place; send it back to draft first",
        entity_id=entity_id,
        status=status.value,
        editable=sorted(item.value for item in EDITABLE_STATUSES),
    )


__all__ = [
    "PRESENTATION_FIELDS",
    "apply_patch",
    "draft_extra",
    "draft_from_record",
    "not_editable_error",
    "record_from_draft",
    "snapshot_payload",
    "temporal_columns",
]


def record_from_draft(
    draft: EntityDraft,
    *,
    entity_id: str,
    slug: str | None,
    status: Status,
    revision: int,
    presentation: Presentation | None = None,
    counts: EntityCounts | None = None,
    relationships: tuple[Relationship, ...] = (),
    article_ids: tuple[str, ...] = (),
) -> EntityRecord:
    """Build the read-model record a draft write produces.

    The fixtures driver serves this object directly; the PostGIS driver writes the same values into
    columns and reads them back. Building it in one place is what keeps the two in agreement.
    """
    shown = presentation or Presentation(rank=0.0, layer="places", min_zoom=0.0, max_zoom=22.0)
    return EntityRecord(
        id=entity_id,
        entity_type=draft.entity_type,
        kind=draft.kind,
        slug=slug,
        status=status,
        revision=revision,
        importance=float(draft.importance),
        rank=shown.rank,
        min_zoom=shown.min_zoom,
        max_zoom=shown.max_zoom,
        layer=shown.layer,
        temporal=draft.temporal,
        names=tuple(draft.names),
        geometries=tuple(draft.geometries),
        summary=draft.summary,
        summary_en=draft.summary_en,
        body_md=draft.body_md,
        attestation=draft.attestation,
        certainty=draft.certainty,
        source_ids=tuple(dict.fromkeys(draft.source_ids)),
        article_ids=tuple(article_ids),
        relationships=tuple(relationships),
        counts=counts or EntityCounts(),
        extra=draft_extra(draft),
    )
