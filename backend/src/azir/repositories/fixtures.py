"""Fixtures adapter (dev/demo driver, ADR-0014).

Reads the *same* YAML fixtures the PostgreSQL seeder reads and answers the same repository
contract. It exists so the project can be run, demoed and unit-tested without Docker or a PostGIS
cluster; it is never allowed in production (guarded in :mod:`azir.core.config`).

Spatial predicates here use ``shapely``; in production they are PostGIS ``ST_*`` calls. All
*policy* (time filtering, ranking, semantic zoom, field budget) lives in the domain/service layer
and is therefore identical between drivers.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from shapely.geometry import box, mapping, shape
from shapely.prepared import prep

from ..core.errors import RepositoryError
from ..core.pagination import paginate
from ..domain import text as textnorm
from ..domain.calendar import CalendarDate, convert, to_gregorian_year_range
from ..domain.enums import (
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
from ..domain.geo import BBox, GeometryRecord
from ..domain.model import (
    Disagreement,
    EntityCounts,
    EntityRecord,
    EvidenceRef,
    NameVariant,
    Relationship,
)
from ..domain.semantic_zoom import compute_rank, layer_for, max_zoom_for, min_zoom_for
from ..domain.temporal import RESEARCH_CEIL, RESEARCH_FLOOR, TemporalInterval, TimeWindow
from .ports import AtlasQuery, FeaturePage, SearchHit, TimelineBucket

__all__ = ["FixturesRepository", "load_fixtures"]

_COLLECTIONS: dict[str, EntityType] = {
    "places": EntityType.PLACE,
    "people": EntityType.PERSON,
    "events": EntityType.EVENT,
    "political_entities": EntityType.POLITICAL_ENTITY,
    "articles": EntityType.ARTICLE,
    "sources": EntityType.SOURCE,
    "periods": EntityType.PERIOD,
}

KM_PER_DEGREE = 111.32


def load_fixtures(directory: str | Path) -> dict[str, list[Any]]:
    """Load and concatenate every ``*.yaml`` in ``directory`` (sorted by filename)."""
    root = Path(directory)
    if not root.exists():
        raise RepositoryError(f"fixtures directory not found: {root}")
    documents: dict[str, list[Any]] = defaultdict(list)
    for path in sorted(root.glob("*.y*ml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise RepositoryError(f"cannot parse fixture {path.name}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RepositoryError(f"fixture {path.name} must contain a mapping at the top level")
        for key, value in raw.items():
            if isinstance(value, list):
                documents[key].extend(value)
            elif value is not None:
                documents[key].append(value)
    return dict(documents)


# --------------------------------------------------------------------- spec parsing


def _build_temporal(spec: dict[str, Any] | None) -> TemporalInterval | None:
    """Turn a fixture ``temporal`` block into a normalized interval (ADR-0005)."""
    if not spec:
        return None
    calendar = Calendar(spec.get("calendar", Calendar.GREGORIAN_PROLEPTIC))
    precision = Precision(spec.get("precision", Precision.RANGE))
    confidence = Confidence(spec.get("confidence", "medium"))
    start = spec.get("from")
    end = spec.get("to", start)
    if start is None:
        return TemporalInterval.unknown(spec.get("display_fa") or spec.get("display"))
    start = int(start)
    end = int(end) if end is not None else start
    month, day = spec.get("month"), spec.get("day")

    if calendar is Calendar.GREGORIAN_PROLEPTIC:
        year_from, year_to = start, end
    else:
        lo_a, hi_a = to_gregorian_year_range(calendar, start, month, day)
        lo_b, hi_b = to_gregorian_year_range(calendar, end, spec.get("month_to"), spec.get("day_to"))
        year_from, year_to = min(lo_a, lo_b), max(hi_a, hi_b)

    if precision is Precision.CENTURY:
        from ..domain.temporal import century_bounds

        year_from, year_to = century_bounds(year_from if calendar is Calendar.GREGORIAN_PROLEPTIC else start)
        if calendar is not Calendar.GREGORIAN_PROLEPTIC:
            year_from = min(year_from, lo_a)
            year_to = max(year_to, hi_a)
    elif precision is Precision.BEFORE:
        year_from = RESEARCH_FLOOR
    elif precision is Precision.AFTER:
        year_to = RESEARCH_CEIL
    elif precision is Precision.CIRCA_YEAR:
        from ..domain.temporal import CIRCA_FUZZ_YEARS

        year_from, year_to = year_from - CIRCA_FUZZ_YEARS, year_to + CIRCA_FUZZ_YEARS
    elif precision is Precision.UNKNOWN:
        return TemporalInterval.unknown(spec.get("display_fa") or spec.get("display"))

    if year_from > year_to:
        year_from, year_to = year_to, year_from

    display = spec.get("display") or spec.get("display_fa")
    if display is None:
        display = _auto_display(calendar, start, end, precision)
    display_en = spec.get("display_en") or (display if textnorm.has_persian(display or "") is False else None)
    return TemporalInterval(
        year_from=year_from,
        year_to=year_to,
        precision=precision,
        calendar=calendar,
        confidence=confidence,
        display=display,
        display_from=spec.get("display_from"),
        display_to=display_en,
    )


def _auto_display(calendar: Calendar, start: int, end: int, precision: Precision) -> str:
    suffix = {
        Calendar.ISLAMIC_LUNAR: " ق",
        Calendar.PERSIAN_SOLAR: " ش",
        Calendar.JULIAN: " (جولیان)",
        Calendar.GREGORIAN_PROLEPTIC: " م",
        Calendar.UNKNOWN: "",
    }[calendar]
    if precision is Precision.CIRCA_YEAR:
        return f"حدود {start}{suffix}"
    if precision is Precision.CENTURY:
        return f"سدهٔ {start}{suffix}"
    if precision is Precision.BEFORE:
        return f"پیش از {end}{suffix}"
    if precision is Precision.AFTER:
        return f"پس از {start}{suffix}"
    if start == end:
        return f"{start}{suffix}"
    return f"{start}–{end}{suffix}"


def _build_geometry(spec: dict[str, Any] | None) -> GeometryRecord | None:
    if not spec:
        return None
    geojson_type = spec.get("type") or ("Point" if spec.get("coordinates") and isinstance(spec["coordinates"][0], (int, float)) else "LineString")
    coordinates = spec.get("coordinates")
    if coordinates is None:
        return None
    return GeometryRecord(
        geojson={"type": geojson_type, "coordinates": coordinates},
        kind=GeometryKind(spec.get("kind", GeometryKind.POINT)),
        certainty=Certainty(spec.get("certainty", Certainty.EXACT)),
        lod_min_zoom=float(spec.get("lod_min_zoom", 0)),
        lod_max_zoom=float(spec.get("lod_max_zoom", 22)),
        year_from=spec.get("year_from"),
        year_to=spec.get("year_to"),
        source_id=spec.get("source"),
        note=spec.get("note_fa") or spec.get("note"),
        note_en=spec.get("note_en"),
        needs_digitisation=bool(spec.get("needs_digitisation", False)),
    )


def _normalize_raw(raw: dict[str, Any], entity_type: EntityType) -> dict[str, Any]:
    """Give the taxonomy-shaped collections the same fields as the narrative ones.

    Periods carry ``label_fa``/``label_en`` and a flat ``from``/``to``; sources carry
    ``title``/``title_fa``. Rather than special-casing every consumer, the loader lifts them into
    the shared ``names``/``temporal``/``slug`` shape that the rest of the system expects.
    """
    if entity_type is EntityType.PERIOD:
        raw.setdefault("names", _label_names(raw.get("label_fa"), raw.get("label_en")))
        raw.setdefault("slug", raw.get("code"))
        if not raw.get("temporal") and raw.get("from") is not None:
            raw["temporal"] = {
                "from": raw.get("from"),
                "to": raw.get("to", raw.get("from")),
                "precision": raw.get("precision", "range"),
                "confidence": raw.get("confidence", "medium"),
            }
    if entity_type is EntityType.SOURCE:
        raw.setdefault("names", _label_names(raw.get("title_fa"), raw.get("title") or raw.get("title_en")))
        raw.setdefault("importance", 0.3)
    return raw


def _label_names(fa: str | None, en: str | None) -> list[dict[str, Any]]:
    names: list[dict[str, Any]] = []
    if fa:
        names.append({"lang": "fa", "form": str(fa), "kind": "preferred"})
    if en:
        names.append({"lang": "en", "form": str(en), "kind": "preferred", "script": "Latn"})
    return names


def _build_names(spec: Iterable[dict[str, Any]] | None) -> tuple[NameVariant, ...]:
    if not spec:
        return ()
    out: list[NameVariant] = []
    for item in spec:
        form = item.get("form") or item.get("name")
        if not form:
            continue
        lang = item.get("lang", "fa")
        out.append(
            NameVariant(
                form=str(form),
                lang=lang,
                script=item.get("script") or textnorm.script_of(str(form)),
                kind=item.get("kind", "preferred"),
                transliteration=item.get("transliteration"),
                year_from=item.get("year_from"),
                year_to=item.get("year_to"),
                source_id=item.get("source"),
                note=item.get("note"),
            )
        )
    return tuple(out)


# --------------------------------------------------------------------- repository


class FixturesRepository:
    """In-memory implementation of :class:`azir.repositories.ports.AtlasRepository`."""

    driver_name = "fixtures"

    def __init__(self, fixtures_dir: str | Path) -> None:
        self._dir = Path(fixtures_dir)
        self._docs = load_fixtures(self._dir)
        self._entities: dict[str, EntityRecord] = {}
        self._by_slug: dict[tuple[str, str], str] = {}
        self._relations: dict[str, list[Relationship]] = defaultdict(list)
        self._disagreements: dict[str, list[Disagreement]] = defaultdict(list)
        self._disagreement_index: dict[tuple[str, str], list[Any]] = {}
        self._shape_cache: dict[tuple[str, int], tuple[Any, Any]] = {}
        self._assertion_counts: dict[str, int] = {}
        self._predicates: dict[str, dict[str, str]] = {}
        self._sources: dict[str, dict[str, Any]] = {}
        self._periods: list[dict[str, Any]] = []
        self._article_links: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._counts: dict[str, EntityCounts] = defaultdict(EntityCounts)
        self._load()

    # ------------------------------------------------------------------ loading

    def _load(self) -> None:
        for item in self._docs.get("predicates", []):
            self._predicates[str(item["code"])] = {
                "fa": str(item.get("label_fa", item["code"])),
                "en": str(item.get("label_en", item["code"])),
            }
        for item in self._docs.get("sources", []):
            self._sources[str(item["id"])] = item
        self._periods = list(self._docs.get("periods", []))

        drafts: dict[str, dict[str, Any]] = {}
        for collection, entity_type in _COLLECTIONS.items():
            for raw in self._docs.get(collection, []):
                record_id = str(raw["id"])
                drafts[record_id] = {
                    "raw": _normalize_raw(dict(raw), entity_type),
                    "entity_type": entity_type,
                }

        # Hand-drawn / reconstructed boundaries live in one reviewable file (09-geometries.yaml)
        # so every approximate polygon in the dataset can be audited in one place (ADR-0013).
        for spec in self._docs.get("geometries", []):
            entry = drafts.get(str(spec.get("entity")))
            if entry is None:
                raise RepositoryError(f"geometry refers to unknown entity: {spec.get('entity')!r}")
            block = {k: v for k, v in spec.items() if k != "entity"}
            geojson = {"type": block.pop("type"), "coordinates": block.pop("coordinates")}
            entry["raw"].setdefault("geometries", []).append({**block, **geojson})

        # First pass: names + geometry + temporal so relationships can resolve labels.
        for record_id, entry in drafts.items():
            raw = entry["raw"]
            entity_type = entry["entity_type"]
            names = _build_names(raw.get("names"))
            temporal = _build_temporal(raw.get("temporal"))
            geometries = tuple(g for g in (_build_geometry(raw.get("geometry")),) if g)
            if raw.get("geometries"):
                geometries = geometries + tuple(
                    g for g in (_build_geometry(spec) for spec in raw["geometries"]) if g
                )
            entity = EntityRecord(
                id=record_id,
                entity_type=entity_type,
                kind=raw.get("kind"),
                slug=raw.get("slug"),
                status=Status(raw.get("status", Status.DRAFT)),
                revision=int(raw.get("revision", 1)),
                importance=float(raw.get("importance", 0.5)),
                temporal=temporal,
                names=names,
                geometries=geometries,
                summary=raw.get("summary_fa") or raw.get("summary"),
                summary_en=raw.get("summary_en"),
                body_md=raw.get("body_fa_md") or raw.get("body_md"),
                attestation=Attestation(raw["attestation"]) if raw.get("attestation") else None,
                certainty=raw.get("certainty"),
                source_ids=tuple(raw.get("sources", ())),
                extra={
                    key: raw[key]
                    for key in (
                        "map_state", "entities", "author", "author_fa", "publisher", "year",
                        "origin_year", "reliability", "citation", "url", "kind_fa", "kind_en",
                        "lang", "language", "published_at", "reading_time_min", "coverage_note",
                        "coverage_note_fa", "coverage_note_en", "parent", "capital", "title",
                        "title_fa", "title_en", "body_en_md", "participants", "places", "part_of",
                        "scheme", "from", "to", "external_ids", "license", "code", "note_fa",
                        "note_en", "needs_review", "led_to", "is_default", "name_fa", "name_en",
                        "description_fa", "description_en", "owner",
                    )
                    if key in raw
                },
            )
            self._entities[record_id] = entity
            if entity.slug:
                self._by_slug[(entity_type.value, entity.slug)] = record_id
            for index, geom in enumerate(geometries):
                self._cache_shape(record_id, index, geom)

        self._load_graph(drafts)
        self._derive_inherited_geometry()
        self._compute_rankings()

    # ------------------------------------------------------------------ geometry

    def _cache_shape(self, record_id: str, index: int, geom: GeometryRecord) -> tuple[Any, Any]:
        try:
            raw = shape(geom.geojson)
        except Exception as exc:  # malformed fixture data must fail loudly, not silently
            raise RepositoryError(f"invalid geometry for {record_id}: {exc}") from exc
        pair = (raw, prep(raw))
        self._shape_cache[(record_id, index)] = pair
        return pair

    def _shape_pair(self, entity: EntityRecord, geom: GeometryRecord | None) -> tuple[Any, Any] | None:
        """Shapely shapes per (entity, geometry index) -- geometry is time-varying, so one shape
        per entity is not enough."""
        if geom is None:
            return None
        try:
            index = entity.geometries.index(geom)
        except ValueError:  # pragma: no cover - defensive
            index = 0
        cached = self._shape_cache.get((entity.id, index))
        return cached or self._cache_shape(entity.id, index, geom)

    def _derive_inherited_geometry(self) -> None:
        """Give events/people a map position from the places they are linked to.

        The inherited geometry is always flagged (kind=uncertain_locus, certainty=uncertain) so the
        UI can render it differently: a derived position is never presented as an exact location.
        """
        for record_id, entity in list(self._entities.items()):
            if entity.geometries:
                continue
            if entity.entity_type not in (EntityType.EVENT, EntityType.PERSON, EntityType.POLITICAL_ENTITY):
                continue
            for relation in self._relations.get(record_id, ()):
                if relation.object_type is not EntityType.PLACE or not relation.object_id:
                    continue
                if relation.predicate not in {"site_of", "besieged", "hosted", "affected",
                                             "born_in", "died_in", "lived_in", "worked_at",
                                             "burial_place", "capital"}:
                    continue
                place = self._entities.get(relation.object_id)
                if place is None:
                    continue
                geom = place.primary_geometry()
                if geom is None:
                    continue
                point = geom.representative_point()
                if point is None:
                    continue
                inherited = GeometryRecord(
                    geojson={"type": "Point", "coordinates": list(point)},
                    kind=GeometryKind.UNCERTAIN_LOCUS,
                    certainty=Certainty.UNCERTAIN,
                    note=f"مکان از رابطهٔ «{relation.label_fa}» با {place.display_name('fa')} مشتق شده است.",
                    source_id=geom.source_id,
                )
                self._entities[record_id] = replace(entity, geometries=(inherited,))
                self._cache_shape(record_id, 0, inherited)
                break

    def _load_graph(self, drafts: dict[str, dict[str, Any]]) -> None:
        # structural links: place hierarchy
        for link in self._docs.get("place_links", []):
            self._add_relationship(
                Relationship(
                    predicate=str(link.get("kind", "contains")),
                    label_fa=_label_for(self._predicates, link.get("kind", "contains"), "fa"),
                    label_en=_label_for(self._predicates, link.get("kind", "contains"), "en"),
                    direction="out",
                    object_type=EntityType.PLACE,
                    object_id=str(link["child"]),
                    role=None,
                    temporal=_build_temporal(link.get("temporal")),
                    confidence=Confidence(link.get("confidence", "high")),
                    status=AssertionStatus.ACCEPTED,
                ),
                subject_id=str(link["parent"]),
                inverse_predicate=str(link.get("kind", "contains")),
                inverse_id=str(link["child"]),
            )

        # event structure
        for record_id, entry in drafts.items():
            if entry["entity_type"] is not EntityType.EVENT:
                continue
            raw = entry["raw"]
            for place_ref in raw.get("places", []) or []:
                self._add_relationship(
                    Relationship(
                        predicate=str(place_ref.get("role", "site_of")),
                        label_fa=_label_for(self._predicates, place_ref.get("role", "site_of"), "fa"),
                        label_en=_label_for(self._predicates, place_ref.get("role", "site_of"), "en"),
                        object_type=EntityType.PLACE,
                        object_id=str(place_ref["id"]),
                        role=place_ref.get("role"),
                        certainty=str(place_ref.get("certainty", "exact")) if place_ref.get("certainty") else None,
                        temporal=entry_record_temporal(record_id, self._entities),
                        confidence=Confidence(place_ref.get("confidence", "high")),
                    ),
                    subject_id=record_id,
                    inverse_predicate="hosted",
                    inverse_id=str(place_ref["id"]),
                )
            for actor in raw.get("participants", []) or []:
                self._add_relationship(
                    Relationship(
                        predicate="participated_in",
                        label_fa=_label_for(self._predicates, "participated_in", "fa"),
                        label_en=_label_for(self._predicates, "participated_in", "en"),
                        object_type=EntityType(actor.get("type", "person")),
                        object_id=str(actor["id"]),
                        role=actor.get("role"),
                        side=actor.get("side"),
                        temporal=entry_record_temporal(record_id, self._entities),
                        confidence=Confidence(actor.get("confidence", "high")),
                    ),
                    subject_id=record_id,
                    inverse_predicate="participated_in",
                    inverse_id=str(actor["id"]),
                )
            if raw.get("part_of"):
                self._add_relationship(
                    Relationship(
                        predicate="part_of",
                        label_fa=_label_for(self._predicates, "part_of", "fa"),
                        label_en=_label_for(self._predicates, "part_of", "en"),
                        object_type=EntityType.EVENT,
                        object_id=str(raw["part_of"]),
                    ),
                    subject_id=record_id,
                    inverse_predicate="has_part",
                    inverse_id=str(raw["part_of"]),
                )

        # article <-> entity
        for record_id, entry in drafts.items():
            if entry["entity_type"] is not EntityType.ARTICLE:
                continue
            for ref in entry["raw"].get("entities", []) or []:
                target = str(ref["id"])
                relation = str(ref.get("relation", "mentions"))
                self._article_links[target].append({"article_id": record_id, "relation": relation})
                self._add_relationship(
                    Relationship(
                        predicate=f"article:{relation}",
                        label_fa={"about": "دربارهٔ", "mentions": "اشاره به", "related_to": "مرتبط با",
                                  "primary_focus": "موضوع اصلی"}.get(relation, relation),
                        label_en=relation.replace("_", " "),
                        object_type=EntityType.ARTICLE,
                        object_id=record_id,
                        role=relation,
                    ),
                    subject_id=target,
                    inverse_predicate=f"article:{relation}",
                    inverse_id=record_id,
                )

        # assertions (the graph heart, ADR-0003)
        assertion_count: dict[str, int] = defaultdict(int)
        for raw in self._docs.get("assertions", []):
            predicate = str(raw["predicate"])
            subject = raw["subject"]
            object_ref = raw.get("object")
            status = AssertionStatus(raw.get("status", AssertionStatus.ACCEPTED))
            confidence = Confidence(raw.get("confidence", "medium"))
            temporal = _build_temporal(raw.get("temporal"))
            evidence = tuple(
                EvidenceRef(
                    source_id=str(ev["source"]),
                    locator_type=str(ev.get("locator_type", "page")),
                    locator=ev.get("locator"),
                    quote_original=ev.get("quote_original"),
                    quote_translation=ev.get("quote_translation"),
                    stance=str(ev.get("stance", "supports")),
                )
                for ev in raw.get("evidence", []) or []
            )
            subject_id = str(subject["id"])
            object_type = EntityType(object_ref["type"]) if object_ref and object_ref.get("type") else None
            object_id = str(object_ref["id"]) if object_ref and object_ref.get("id") else None
            edge = Relationship(
                predicate=predicate,
                label_fa=_label_for(self._predicates, predicate, "fa"),
                label_en=_label_for(self._predicates, predicate, "en"),
                direction="out",
                object_type=object_type,
                object_id=object_id,
                object_value=raw.get("value"),
                role=raw.get("role"),
                temporal=temporal,
                confidence=confidence,
                status=status,
                evidence=evidence,
                note=raw.get("note"),
            )
            if status in (AssertionStatus.ACCEPTED, AssertionStatus.DISPUTED, AssertionStatus.PROPOSED):
                self._relations[subject_id].append(edge)
                assertion_count[subject_id] += 1
                if object_id:
                    self._relations[object_id].append(
                        Relationship(
                            predicate=_inverse(predicate),
                            label_fa=_label_for(self._predicates, _inverse(predicate), "fa"),
                            label_en=_label_for(self._predicates, _inverse(predicate), "en"),
                            direction="in",
                            object_type=EntityType(subject["type"]),
                            object_id=subject_id,
                            temporal=temporal,
                            confidence=confidence,
                            status=status,
                            evidence=evidence,
                            note=raw.get("note"),
                        )
                    )
                    assertion_count[object_id] += 1
            if status is AssertionStatus.DISPUTED:
                topic_fa = str(raw.get("topic_fa") or raw.get("topic") or predicate)
                topic_en = str(raw.get("topic_en") or raw.get("topic") or predicate)
                key = (subject_id, topic_fa)
                existing = self._disagreement_index.get(key)
                if existing is None:
                    self._disagreement_index[key] = [topic_fa, topic_en, [edge]]
                else:
                    existing[2].append(edge)
        for (subject_id, _topic), (topic_fa, topic_en, positions) in self._disagreement_index.items():
            self._disagreements[subject_id].append(
                Disagreement(topic=topic_fa, topic_en=topic_en, positions=tuple(positions))
            )
        self._assertion_counts = assertion_count

    def _add_relationship(
        self, relationship: Relationship, *, subject_id: str, inverse_predicate: str, inverse_id: str
    ) -> None:
        self._relations[subject_id].append(relationship)
        self._relations[inverse_id].append(
            Relationship(
                predicate=inverse_predicate,
                label_fa=relationship.label_fa,
                label_en=relationship.label_en,
                direction="in",
                object_type=self._entities[subject_id].entity_type if subject_id in self._entities else None,
                object_id=subject_id,
                role=relationship.role,
                side=relationship.side,
                temporal=relationship.temporal,
                confidence=relationship.confidence,
                status=relationship.status,
                evidence=relationship.evidence,
            )
        )

    def _compute_rankings(self) -> None:
        article_counts: dict[str, int] = {k: len(v) for k, v in self._article_links.items()}
        period_coverage = {p["id"]: _period_span(p) for p in self._periods}
        rebuilt: dict[str, EntityRecord] = {}
        for record_id, entity in self._entities.items():
            source_count = len(set(entity.source_ids))
            assertion_count = self._assertion_counts.get(record_id, 0)
            article_count = article_counts.get(record_id, 0)
            coverage = _count_period_coverage(entity.temporal, period_coverage)
            rank = compute_rank(
                importance=entity.importance,
                kind=entity.kind,
                source_count=source_count,
                assertion_count=assertion_count,
                article_count=article_count,
                period_coverage=coverage,
                kind_weight=_period_kind_weight(entity),
            )
            layer = layer_for(entity.entity_type, entity.kind)
            counts = EntityCounts(
                sources=source_count,
                articles=article_count,
                assertions=assertion_count,
                periods=coverage,
            )
            relationships = tuple(
                _with_labels(r, self._entities) for r in self._relations.get(record_id, ())
            )
            rebuilt[record_id] = replace(
                entity,
                rank=rank,
                min_zoom=min_zoom_for(rank, entity.entity_type, entity.kind),
                max_zoom=max_zoom_for(entity.kind),
                layer=layer,
                counts=counts,
                relationships=relationships,
                disagreements=tuple(self._disagreements.get(record_id, ())),
                article_ids=tuple(a["article_id"] for a in self._article_links.get(record_id, ())),
            )
        self._entities = rebuilt

    # ------------------------------------------------------------------ queries

    def features(self, query: AtlasQuery) -> FeaturePage:
        envelope = prep(box(*query.bbox.as_tuple()))
        near_point = shape({"type": "Point", "coordinates": list(query.near)}) if query.near else None
        candidates: list[tuple[float, str]] = []
        entities: dict[str, EntityRecord] = {}
        geometries: dict[str, Any] = {}
        gaps: set[str] = set()

        for entity in self._entities.values():
            if not (query.include_unpublished or entity.is_public):
                continue
            if entity.layer not in query.layers:
                continue
            if query.kinds and (entity.kind or "") not in query.kinds:
                continue
            if entity.rank < query.min_rank:
                continue
            if not _temporal_match(entity, query.window):
                continue
            geometry = entity.primary_geometry(at_year=query.window.representative_year)
            if geometry is None:
                gaps.add(f"{entity.entity_type.value}:{entity.id}:no-geometry")
                continue
            pair = self._shape_pair(entity, geometry)
            if pair is None or not envelope.intersects(pair[0]):
                continue
            # 1.35 compensates for the longitude shrink at ~38 deg N; PostGIS does this exactly
            # with geography/ST_DWithin (AGENTS.md rule 11).
            if (
                near_point is not None
                and query.radius_km
                and pair[0].distance(near_point) * KM_PER_DEGREE > query.radius_km * 1.35
            ):
                continue
            geojson = _simplify(geometry.geojson, query.lod_tolerance)
            candidates.append((entity.rank, entity.id))
            entities[entity.id] = entity
            geometries[entity.id] = geojson

        candidates.sort(key=lambda row: (-row[0], row[1]))
        rows, page = paginate(candidates, query.limit, query.cursor)
        kept_ids = {ident for _, ident in rows}
        return FeaturePage(
            rows=rows,
            entities={k: v for k, v in entities.items() if k in kept_ids},
            geometries={k: v for k, v in geometries.items() if k in kept_ids},
            total_estimate=page.total_estimate or len(candidates),
            coverage_gaps=sorted(gaps)[:20],
        )

    def timeline(
        self, bbox: BBox, window: TimeWindow, bucket: int, layers: tuple[str, ...], locale: str
    ) -> list[TimelineBucket]:
        size = max(1, bucket)
        start = (window.year_from // size) * size
        end = ((window.year_to // size) + 1) * size
        envelope = prep(box(*bbox.as_tuple())) if bbox else None
        buckets: list[TimelineBucket] = []
        for cursor in range(start, end, size):
            counts: dict[str, int] = defaultdict(int)
            kinds: dict[str, int] = defaultdict(int)
            notable: list[tuple[float, dict[str, Any]]] = []
            for entity in self._entities.values():
                if not entity.is_public or entity.layer not in layers:
                    continue
                if entity.temporal is None:
                    continue
                if not entity.temporal.overlaps(TemporalInterval(cursor, cursor + size - 1)):
                    continue
                if envelope is not None:
                    pair = self._shape_pair(
                        entity, entity.primary_geometry(at_year=cursor + size // 2)
                    )
                    if pair is None or not envelope.intersects(pair[0]):
                        continue
                counts[entity.layer] += 1
                kinds[entity.kind or entity.entity_type.value] += 1
                if entity.entity_type is EntityType.EVENT and entity.rank >= 40:
                    notable.append(
                        (
                            entity.rank,
                            {
                                "id": entity.id,
                                "label": entity.display_name(locale),
                                "year": entity.temporal.midpoint,
                                "kind": entity.kind,
                            },
                        )
                    )
            notable.sort(key=lambda item: -item[0])
            buckets.append(
                TimelineBucket(
                    year_from=cursor,
                    year_to=cursor + size - 1,
                    counts=dict(counts),
                    total=sum(counts.values()),
                    top_kinds=[k for k, _ in sorted(kinds.items(), key=lambda kv: -kv[1])[:3]],
                    notable=[item[1] for item in notable[:3]],
                )
            )
        return buckets

    def entity(self, entity_type: str, id_or_slug: str) -> EntityRecord | None:
        if id_or_slug in self._entities:
            return self._entities[id_or_slug]
        if entity_type in {e.value for e in EntityType}:
            key = (entity_type, id_or_slug)
            if key in self._by_slug:
                return self._entities[self._by_slug[key]]
        for slug_key, record_id in self._by_slug.items():
            if slug_key[1] == id_or_slug:
                return self._entities[record_id]
        return None

    def related(
        self, entity_type: str, entity_id: str, *, depth: int = 1, limit: int = 50
    ) -> list[Relationship]:
        entity = self.entity(entity_type, entity_id)
        if entity is None:
            return []
        return list(entity.relationships[:limit])

    def list_entities(
        self,
        entity_type: EntityType,
        *,
        status: str | None = "published",
        locale: str = "fa",
        limit: int = 50,
        window: TimeWindow | None = None,
    ) -> list[EntityRecord]:
        out = []
        for entity in self._entities.values():
            if entity.entity_type is not entity_type:
                continue
            if status and entity.status.value != status:
                continue
            if window and not _temporal_match(entity, window):
                continue
            out.append(entity)
        out.sort(key=lambda e: (-e.rank, e.id))
        return out[:limit]

    def articles(
        self, *, locale: str = "fa", limit: int = 50, entity_id: str | None = None
    ) -> list[EntityRecord]:
        if entity_id:
            ids = [a["article_id"] for a in self._article_links.get(entity_id, [])]
            return [self._entities[i] for i in ids if i in self._entities][:limit]
        return self.list_entities(EntityType.ARTICLE, locale=locale, limit=limit)

    def search(
        self,
        term: str,
        *,
        types: tuple[str, ...],
        locale: str = "fa",
        limit: int = 20,
        near: tuple[float, float] | None = None,
        radius_km: float | None = None,
        window: TimeWindow | None = None,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        allowed = set(types) if types else {e.value for e in EntityType}
        for entity in self._entities.values():
            if entity.entity_type.value not in allowed:
                continue
            if not entity.is_public:
                continue
            if window and not _temporal_match(entity, window):
                continue
            score = 0.0
            matched_on = "name"
            for name in entity.names:
                score = max(score, textnorm.score(term, name.form))
            body_score = textnorm.score(term, entity.summary or "")
            if body_score > score:
                score, matched_on = body_score, "summary"
            title_score = textnorm.score(term, str(entity.extra.get("title_fa", "")))
            if title_score > score:
                score, matched_on = title_score, "title"
            if score <= 0:
                continue
            if near and radius_km:
                geom = entity.primary_geometry()
                if geom is None:
                    continue
                point = geom.representative_point()
                if point is None or _distance_km(point, near) > radius_km:
                    continue
            snippet = textnorm.snippet(entity.summary or entity.display_name(locale), term)
            hits.append(
                SearchHit(entity=entity, score=round(score * (0.5 + entity.rank / 200), 4),
                          matched_on=matched_on, snippet=snippet)
            )
        hits.sort(key=lambda h: (-h.score, h.entity.id))
        return hits[:limit]

    def context(
        self,
        *,
        place_id: str | None,
        point: tuple[float, float] | None,
        radius_km: float,
        locale: str,
        limit: int,
    ) -> list[Relationship]:
        out: list[Relationship] = []
        seen: set[tuple[str, str]] = set()
        if place_id:
            entity = self.entity("place", place_id)
            if entity is None:
                return []
            for relation in entity.relationships:
                if relation.object_id and (relation.predicate, relation.object_id) not in seen:
                    seen.add((relation.predicate, relation.object_id))
                    out.append(relation)
        if point:
            for entity in self._entities.values():
                geom = entity.primary_geometry()
                if geom is None or not entity.is_public:
                    continue
                rep = geom.representative_point()
                if rep is None or _distance_km(rep, point) > radius_km:
                    continue
                out.append(
                    Relationship(
                        predicate="located_near",
                        label_fa="در نزدیکی",
                        label_en="located near",
                        object_type=entity.entity_type,
                        object_id=entity.id,
                        temporal=entity.temporal,
                        confidence=Confidence.HIGH,
                    )
                )
        out.sort(key=lambda r: r.temporal.year_from if r.temporal else 0)
        return out[:limit]

    def provisional_geometry_count(self) -> int:
        """How many drawn shapes are still flagged as needing real digitisation."""
        return sum(
            1
            for entity in self._entities.values()
            for geometry in entity.geometries
            if geometry.needs_digitisation
        )

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        disputed = 0
        for entity in self._entities.values():
            if entity.is_public:
                by_type[entity.entity_type.value] += 1
            disputed += len(entity.disagreements)
        return {
            **by_type,
            "sources": len(self._sources),
            "periods": len(self._periods),
            "disputed_assertions": disputed,
            "provisional_geometries": self.provisional_geometry_count(),
        }

    def all_published(self) -> list[EntityRecord]:
        return [e for e in self._entities.values() if e.is_public]


# --------------------------------------------------------------------- helpers


def _label_for(predicates: dict[str, dict[str, str]], code: str, locale: str) -> str:
    entry = predicates.get(code)
    if not entry:
        return code.replace("_", " ")
    return entry.get(locale, code.replace("_", " "))


_INVERSE: dict[str, str] = {
    "born_in": "birthplace_of",
    "died_in": "death_place_of",
    "lived_in": "hosted",
    "ruled": "ruled_by",
    "founded": "founded_by",
    "commissioned_by": "commissioned",
    "built_by": "built",
    "capital_of": "capital",
    "located_in": "contains",
    "contains": "located_in",
    "part_of": "has_part",
    "has_part": "part_of",
    "worked_at": "workplace_of",
    "participated_in": "has_participant",
    "site_of": "hosted",
    "hosted": "site_of",
    "mentioned_in": "mentions",
}


def _inverse(predicate: str) -> str:
    return _INVERSE.get(predicate, f"is_{predicate}_of")


def _with_labels(relation: Relationship, entities: dict[str, EntityRecord]) -> Relationship:
    if relation.object_id and relation.object_id in entities and not relation.object_label:
        target = entities[relation.object_id]
        return replace(
            relation,
            object_label=target.display_name("fa"),
            object_slug=target.slug,
            object_type=relation.object_type or target.entity_type,
        )
    return relation


def _temporal_match(entity: EntityRecord, window: TimeWindow) -> bool:
    if entity.temporal is None:
        # Entities without a date are always shown (and reported as coverage gaps upstream).
        return True
    if window.mode == "during":
        return window.contains(entity.temporal)
    return window.overlaps(entity.temporal)


def _simplify(geojson: dict[str, Any], tolerance: float) -> dict[str, Any]:
    """LOD simplification (docs/04 §2). Points never need it."""
    if tolerance <= 0 or geojson.get("type") in {"Point", "MultiPoint"}:
        return geojson
    try:
        return mapping(shape(geojson).simplify(tolerance, preserve_topology=True))
    except Exception:  # pragma: no cover - defensive: never fail a request on geometry quirks
        return geojson


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular approximation (dev driver only; PostGIS uses geography in production)."""
    lat0 = math.radians((a[1] + b[1]) / 2)
    dx = (a[0] - b[0]) * math.cos(lat0) * KM_PER_DEGREE
    dy = (a[1] - b[1]) * KM_PER_DEGREE
    return math.hypot(dx, dy)


def _period_span(period: dict[str, Any]) -> TemporalInterval:
    temporal = _build_temporal(
        {
            "from": period.get("from"),
            "to": period.get("to"),
            "precision": period.get("precision", "range"),
            "calendar": period.get("calendar", "gregorian_proleptic"),
            "confidence": period.get("confidence", "medium"),
        }
    )
    return temporal or TemporalInterval.unknown()


def _count_period_coverage(
    temporal: TemporalInterval | None, periods: dict[str, TemporalInterval]
) -> int:
    if temporal is None:
        return 0
    return sum(1 for span in periods.values() if span.overlaps(temporal))


def _period_kind_weight(entity: EntityRecord) -> float | None:
    """Political entities and articles use their own weights, not the place-kind table."""
    if entity.entity_type is EntityType.POLITICAL_ENTITY:
        return 0.92
    if entity.entity_type is EntityType.EVENT:
        return {"battle": 0.85, "siege": 0.8, "treaty": 0.7}.get(entity.kind or "", 0.6)
    if entity.entity_type is EntityType.PERSON:
        return 0.75
    return None


def entry_record_temporal(record_id: str, entities: dict[str, EntityRecord]) -> TemporalInterval | None:
    entity = entities.get(record_id)
    return entity.temporal if entity else None


def convert_display(calendar: Calendar, year: int, month: int | None, day: int | None) -> str:
    """Helper used by the seeder/tests to show a normalized Gregorian label."""
    start, _ = convert(CalendarDate(calendar, year, month, day), Calendar.GREGORIAN_PROLEPTIC)
    return f"{start.year}-{start.month:02d}-{start.day:02d}"
