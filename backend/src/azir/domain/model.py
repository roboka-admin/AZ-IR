"""Domain records shared by every repository adapter.

Services only ever see these dataclasses -- never ORM rows, never dicts from a YAML file. That is
what keeps the ``postgis`` and ``fixtures`` drivers behaviourally identical (ADR-0014).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .enums import (
    AssertionStatus,
    Attestation,
    Confidence,
    EntityType,
    Locale,
    Status,
)
from .geo import GeometryRecord
from .temporal import TemporalInterval


@dataclass(frozen=True, slots=True)
class NameVariant:
    form: str
    lang: str = "fa"
    script: str = "Arab"
    kind: str = "preferred"
    transliteration: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    source_id: str | None = None
    note: str | None = None

    def valid_at(self, year: int | None) -> bool:
        if year is None:
            return True
        if self.year_from is not None and year < self.year_from:
            return False
        return not (self.year_to is not None and year > self.year_to)


@dataclass(frozen=True, slots=True)
class SourceRef:
    id: str
    kind: str
    title: str
    author: str | None = None
    year: int | None = None
    citation: str | None = None
    reliability: str = "secondary"
    url: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_id: str
    locator_type: str = "page"
    locator: str | None = None
    quote_original: str | None = None
    quote_translation: str | None = None
    stance: str = "supports"


@dataclass(frozen=True, slots=True)
class Relationship:
    """A resolved edge: either an assertion triple or a structural link."""

    predicate: str
    label_fa: str
    label_en: str
    direction: str = "out"
    object_type: EntityType | None = None
    object_id: str | None = None
    object_label: str | None = None
    object_slug: str | None = None
    object_value: str | None = None
    role: str | None = None
    side: str | None = None
    certainty: str | None = None
    temporal: TemporalInterval | None = None
    confidence: Confidence = Confidence.MEDIUM
    status: AssertionStatus = AssertionStatus.ACCEPTED
    evidence: tuple[EvidenceRef, ...] = ()
    note: str | None = None

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)

    def label(self, locale: str) -> str:
        return self.label_fa if locale == Locale.FA else self.label_en


@dataclass(frozen=True, slots=True)
class Disagreement:
    """Competing claims about the same topic -- surfaced, never hidden (AGENTS.md rule 20)."""

    topic: str
    topic_en: str
    positions: tuple[Relationship, ...]


@dataclass(frozen=True, slots=True)
class EntityCounts:
    sources: int = 0
    articles: int = 0
    assertions: int = 0
    periods: int = 0


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """The single read-model used by services, API and both drivers."""

    id: str
    entity_type: EntityType
    kind: str | None = None
    slug: str | None = None
    status: Status = Status.DRAFT
    revision: int = 1
    importance: float = 0.5
    rank: float = 0.0
    min_zoom: float = 0.0
    max_zoom: float = 22.0
    layer: str = "places"
    temporal: TemporalInterval | None = None
    names: tuple[NameVariant, ...] = ()
    geometries: tuple[GeometryRecord, ...] = ()
    summary: str | None = None
    summary_en: str | None = None
    body_md: str | None = None
    attestation: Attestation | None = None
    certainty: str | None = None
    source_ids: tuple[str, ...] = ()
    article_ids: tuple[str, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    disagreements: tuple[Disagreement, ...] = ()
    counts: EntityCounts = field(default_factory=EntityCounts)
    extra: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ names

    def name_for(self, locale: str, at_year: int | None = None) -> NameVariant | None:
        candidates = [n for n in self.names if n.valid_at(at_year)]
        if not candidates:
            candidates = list(self.names)
        if not candidates:
            return None
        preferred = [n for n in candidates if n.lang == locale and n.kind == "preferred"]
        if preferred:
            return preferred[0]
        same_lang = [n for n in candidates if n.lang == locale]
        if same_lang:
            return same_lang[0]
        fallback_lang = Locale.EN if locale == Locale.FA else Locale.FA
        fallback = [n for n in candidates if n.lang == fallback_lang]
        if fallback:
            return fallback[0]
        return candidates[0]

    def display_name(self, locale: str, at_year: int | None = None) -> str:
        variant = self.name_for(locale, at_year)
        if variant is not None:
            return variant.form
        return self.slug or self.id

    def secondary_name(self, locale: str) -> str | None:
        other = Locale.EN if locale == Locale.FA else Locale.FA
        variant = self.name_for(other)
        return variant.form if variant else None

    def alternates(self, locale: str) -> Sequence[NameVariant]:
        primary = self.name_for(locale)
        return tuple(n for n in self.names if n is not primary and n.kind != "preferred")

    @property
    def search_blob(self) -> str:
        return " ".join(n.form for n in self.names)

    # ------------------------------------------------------------------ geometry

    def primary_geometry(self, at_year: int | None = None) -> GeometryRecord | None:
        """The geometry to draw for this entity, optionally at a specific year.

        An entity may carry several time-bounded geometries (a dynasty's extent in 1510 is not its
        extent in 1700); the year picks among them and the kind ladder breaks the tie (ADR-0004).
        """
        if not self.geometries:
            return None
        candidates = [g for g in self.geometries if g.valid_at(at_year)] or list(self.geometries)
        for preferred in ("footprint", "extent_reconstructed", "point", "route_alignment"):
            for record in candidates:
                if record.kind.value == preferred:
                    return record
        return candidates[0]

    @property
    def has_geometry(self) -> bool:
        return bool(self.geometries)

    # ------------------------------------------------------------------ temporal

    def active_at(self, year: int) -> bool:
        return self.temporal is None or self.temporal.contains_year(year)

    @property
    def is_public(self) -> bool:
        return self.status is Status.PUBLISHED

    @property
    def has_disagreements(self) -> bool:
        return bool(self.disagreements)

    def temporal_display(self, locale: str) -> str | None:
        return self.temporal.display_text(locale) if self.temporal else None


@dataclass(frozen=True, slots=True)
class FeatureProjection:
    """What the map actually receives for one entity (field-budget aware)."""

    entity: EntityRecord
    geometry: GeometryRecord | None
    label: str
    label_secondary: str | None
    locale: str
    fields: str = "default"

    def to_geojson(self, geometry_override: Mapping[str, Any] | None = None) -> dict[str, Any]:
        entity = self.entity
        geometry = geometry_override or (self.geometry.geojson if self.geometry else None)
        props: dict[str, Any] = {
            "id": entity.id,
            "entity_type": entity.entity_type.value,
            "kind": entity.kind,
            "layer": entity.layer,
            "rank": entity.rank,
            "min_zoom": entity.min_zoom,
            "max_zoom": entity.max_zoom,
            "label": self.label,
            "dir": "rtl" if self.label and _is_rtl(self.label) else "ltr",
            "status": entity.status.value,
            # Certainty is not a nicety: the renderer must never draw a reconstructed extent like
            # a surveyed footprint, at any zoom or field budget (AGENTS.md rule 8).
            "certainty": self.geometry.certainty.value if self.geometry else None,
            "geometry_kind": self.geometry.kind.value if self.geometry else None,
            "href": f"/api/v1/entities/{entity.entity_type.value}/{entity.slug or entity.id}",
        }
        if self.fields != "min":
            temporal = entity.temporal
            props.update(
                {
                    "slug": entity.slug,
                    "label_secondary": self.label_secondary,
                    "t_from": temporal.year_from if temporal else None,
                    "t_to": temporal.year_to if temporal else None,
                    "t_display": entity.temporal_display(self.locale),
                    "t_precision": temporal.precision.value if temporal else None,
                    "confidence": temporal.confidence.value if temporal else None,
                    "article_count": entity.counts.articles,
                    "source_count": entity.counts.sources,
                    "assertion_count": entity.counts.assertions,
                    "has_disagreements": entity.has_disagreements,
                }
            )
        if self.fields == "full":
            props.update(
                {
                    "summary": entity.summary if self.locale == Locale.FA else entity.summary_en,
                    "certainty_note": self.geometry.note_for(self.locale) if self.geometry else None,
                    "needs_digitisation": bool(self.geometry and self.geometry.needs_digitisation),
                    "geometry_year_from": self.geometry.year_from if self.geometry else None,
                    "geometry_year_to": self.geometry.year_to if self.geometry else None,
                    "attestation": entity.attestation.value if entity.attestation else None,
                    "alternates": [n.form for n in entity.alternates(self.locale)][:6],
                    "relationships": [
                        {
                            "predicate": r.predicate,
                            "label": r.label(self.locale),
                            "object_label": r.object_label,
                            "object_id": r.object_id,
                            "object_type": r.object_type.value if r.object_type else None,
                            "confidence": r.confidence.value,
                        }
                        for r in entity.relationships[:12]
                    ],
                }
            )
        return {"type": "Feature", "id": entity.id, "geometry": geometry, "properties": props}


def _is_rtl(text: str) -> bool:
    return any(
        "\u0600" <= ch <= "\u06ff" or "\u0590" <= ch <= "\u05ff" or "\u0700" <= ch <= "\u074f"
        for ch in text
    )
