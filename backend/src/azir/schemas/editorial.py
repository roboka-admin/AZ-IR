"""Request models for the editorial API (ADR-0010).

Shape only: field names, lengths, vocabularies, geometry well-formedness. *Policy* -- who may act,
which transitions are legal, what must be true before publication -- lives in
``services/editorial.py``, and persistence lives in the repositories. A router that decides who may
publish is a router that will eventually be wrong (AGENTS.md rules 2-4).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.enums import (
    AssertionStatus,
    Attestation,
    Calendar,
    Certainty,
    Confidence,
    EntityType,
    GeometryKind,
    Precision,
)
from ..domain.geo import GeometryRecord
from ..domain.model import NameVariant
from ..repositories.ports import EntityDraft

#: GeoJSON types the atlas can draw. ``GeometryCollection`` is rejected on purpose: a record is one
#: place/extent, and a collection would make the certainty and LOD policy ambiguous.
GEOMETRY_TYPES = frozenset(
    {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
)

Locale = Literal["fa", "en"]


class NameVariantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    form: str = Field(min_length=1, max_length=300)
    lang: Locale = "fa"
    script: str = Field(default="Arab", max_length=16)
    kind: str = Field(default="preferred", max_length=32)
    transliteration: str | None = Field(default=None, max_length=64)
    year_from: int | None = None
    year_to: int | None = None
    source_id: str | None = None
    note: str | None = Field(default=None, max_length=500)

    def to_domain(self) -> NameVariant:
        return NameVariant(
            form=self.form.strip(),
            lang=self.lang,
            script=self.script,
            kind=self.kind,
            transliteration=self.transliteration,
            year_from=self.year_from,
            year_to=self.year_to,
            source_id=self.source_id,
            note=self.note,
        )


class TemporalIn(BaseModel):
    """A temporal claim as an editor states it, in the calendar they are reading (ADR-0005)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    calendar: Calendar = Calendar.GREGORIAN_PROLEPTIC
    year_from: int = Field(alias="from", ge=-3000, le=2100)
    year_to: int | None = Field(default=None, alias="to", ge=-3000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    precision: Precision = Precision.RANGE
    confidence: Confidence = Confidence.MEDIUM
    display: str | None = Field(default=None, max_length=120)
    display_en: str | None = Field(default=None, max_length=120)

    def to_spec(self) -> dict[str, Any]:
        return {
            "calendar": self.calendar.value,
            "from": self.year_from,
            "to": self.year_to if self.year_to is not None else self.year_from,
            "month": self.month,
            "day": self.day,
            "precision": self.precision.value,
            "confidence": self.confidence.value,
            "display": self.display,
            "display_en": self.display_en,
        }

    def to_domain(self) -> Any:
        """Normalized interval. The fixtures loader is *the* normalizer (ADR-0016), so the panel,
        the YAML corpus and the seeder cannot disagree about what a date means."""
        from ..repositories.fixtures import build_temporal

        return build_temporal(self.to_spec())


class GeometryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geojson: dict[str, Any]
    kind: GeometryKind = GeometryKind.POINT
    certainty: Certainty = Certainty.EXACT
    year_from: int | None = None
    year_to: int | None = None
    lod_min_zoom: float = Field(default=0.0, ge=0, le=22)
    lod_max_zoom: float = Field(default=22.0, ge=0, le=22)
    source_id: str | None = None
    note_fa: str | None = Field(default=None, max_length=500)
    note_en: str | None = Field(default=None, max_length=500)
    needs_digitisation: bool = False

    @field_validator("geojson")
    @classmethod
    def _check_geojson(cls, value: dict[str, Any]) -> dict[str, Any]:
        kind = value.get("type")
        if kind not in GEOMETRY_TYPES:
            raise ValueError(
                f"unsupported geometry type {kind!r}; expected one of {sorted(GEOMETRY_TYPES)}"
            )
        coordinates = value.get("coordinates")
        if not isinstance(coordinates, (list, tuple)) or not coordinates:
            raise ValueError("coordinates must be a non-empty array")
        for lon, lat in _positions(coordinates):
            if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
                raise ValueError(f"coordinate ({lon}, {lat}) is outside the world")
        if value.get("type") == "Polygon":
            for ring in coordinates:
                if len(ring) < 4:
                    raise ValueError("a polygon ring needs at least 4 positions")
                if ring[0] != ring[-1]:
                    raise ValueError("a polygon ring must be closed")
        return value

    @field_validator("certainty")
    @classmethod
    def _no_exact_reconstruction(cls, value: Certainty, info: Any) -> Certainty:
        # AGENTS.md rule 8: a reconstructed extent is never "exact". Caught at the door so the
        # editor learns immediately instead of at review time.
        kind = (info.data or {}).get("kind")
        if kind is GeometryKind.EXTENT_RECONSTRUCTED and value is Certainty.EXACT:
            raise ValueError("a reconstructed extent cannot be marked exact (rule D-lint)")
        return value

    def to_domain(self) -> GeometryRecord:
        return GeometryRecord(
            geojson=dict(self.geojson),
            kind=self.kind,
            certainty=self.certainty,
            lod_min_zoom=self.lod_min_zoom,
            lod_max_zoom=self.lod_max_zoom,
            year_from=self.year_from,
            year_to=self.year_to,
            source_id=self.source_id,
            note=self.note_fa,
            note_en=self.note_en,
            needs_digitisation=self.needs_digitisation,
        )


def _positions(coordinates: Any) -> list[tuple[float, float]]:
    """Every (lon, lat) pair in a nested GeoJSON coordinate array."""
    out: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and all(isinstance(part, (int, float)) for part in node[:2]):
                out.append((float(node[0]), float(node[1])))
                return
            for item in node:
                walk(item)

    walk(coordinates)
    return out


class EntityDraftIn(BaseModel):
    """A create (all fields) or a patch (only the fields present) on one editable record."""

    model_config = ConfigDict(extra="forbid")

    entity_type: EntityType | None = None
    kind: str | None = Field(default=None, max_length=64)
    slug: str | None = Field(default=None, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    names: list[NameVariantIn] = Field(default_factory=list, max_length=40)
    temporal: TemporalIn | None = None
    geometries: list[GeometryIn] = Field(default_factory=list, max_length=12)
    summary: str | None = Field(default=None, max_length=2000)
    summary_en: str | None = Field(default=None, max_length=2000)
    coverage_note: str | None = Field(default=None, max_length=1000)
    coverage_note_en: str | None = Field(default=None, max_length=1000)
    source_ids: list[str] = Field(default_factory=list, max_length=60)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    certainty: str | None = Field(default=None, max_length=32)
    attestation: Attestation | None = None
    # article-only
    title: str | None = Field(default=None, max_length=300)
    title_en: str | None = Field(default=None, max_length=300)
    body_md: str | None = Field(default=None, max_length=200_000)
    body_en_md: str | None = Field(default=None, max_length=200_000)
    author: str | None = Field(default=None, max_length=200)
    map_state: dict[str, Any] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_draft(self, entity_type: EntityType) -> EntityDraft:
        return EntityDraft(
            entity_type=entity_type,
            kind=self.kind,
            slug=self.slug,
            names=tuple(name.to_domain() for name in self.names),
            temporal=self.temporal.to_domain() if self.temporal else None,
            geometries=tuple(geometry.to_domain() for geometry in self.geometries),
            summary=self.summary,
            summary_en=self.summary_en,
            coverage_note=self.coverage_note,
            coverage_note_en=self.coverage_note_en,
            source_ids=tuple(dict.fromkeys(self.source_ids)),
            importance=self.importance,
            certainty=self.certainty,
            attestation=self.attestation,
            title=self.title,
            title_en=self.title_en,
            body_md=self.body_md,
            body_en_md=self.body_en_md,
            author=self.author,
            map_state=self.map_state,
            extra=dict(self.extra),
        )

    #: Fields that pass through unchanged; the rest need conversion to domain values.
    _PASSTHROUGH = (
        "kind",
        "slug",
        "summary",
        "summary_en",
        "coverage_note",
        "coverage_note_en",
        "importance",
        "certainty",
        "title",
        "title_en",
        "body_md",
        "body_en_md",
        "author",
    )

    def to_patch(self) -> dict[str, Any]:
        """Only the fields the client actually sent, converted to domain values.

        The repository accepts a *complete* draft (a half-filled row would silently erase the rest),
        so a PATCH is merged onto the current record by the service; this method just says which
        parts changed and what they mean in domain terms.
        """
        sent = self.model_dump(exclude_unset=True, exclude={"entity_type"})
        out: dict[str, Any] = {}
        if "names" in sent:
            out["names"] = tuple(name.to_domain() for name in self.names)
        if "temporal" in sent:
            out["temporal"] = self.temporal.to_domain() if self.temporal else None
        if "geometries" in sent:
            out["geometries"] = tuple(geometry.to_domain() for geometry in self.geometries)
        if "source_ids" in sent:
            out["source_ids"] = tuple(dict.fromkeys(self.source_ids))
        if "attestation" in sent:
            out["attestation"] = self.attestation
        if "extra" in sent:
            out["extra"] = dict(self.extra)
        if "map_state" in sent:
            out["map_state"] = self.map_state
        for key in self._PASSTHROUGH:
            if key in sent:
                out[key] = getattr(self, key)
        return out


class TransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["submit", "approve", "publish", "request_changes", "archive", "restore"]
    note: str | None = Field(default=None, max_length=2000)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class UserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "reviewer", "editor", "contributor"] = "editor"
    password: str = Field(min_length=10, max_length=512)


class AssertionReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AssertionStatus
    note: str | None = Field(default=None, max_length=2000)
    #: Required in practice when disputing (rule D13): say what the disagreement is about.
    topic_fa: str | None = Field(default=None, max_length=500)
    topic_en: str | None = Field(default=None, max_length=500)


__all__ = [
    "AssertionReviewIn",
    "EntityDraftIn",
    "GeometryIn",
    "LoginIn",
    "NameVariantIn",
    "TemporalIn",
    "TransitionIn",
    "UserIn",
]
