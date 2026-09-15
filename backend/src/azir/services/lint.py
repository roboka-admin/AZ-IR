"""Data lint: the rules that keep the atlas honest (docs/07 §3, ADR-0010).

Findings are *data*, not exceptions. The same report gates publication, feeds ``azir lint``, is
persisted as a ``lint_run`` row and is rendered in the editorial panel -- because a rule that only
lives in CI is a rule an editor cannot see until somebody tells them.

Levels follow docs/07: ``error`` blocks publication, ``warning`` is shown and never enforced. Only
rules that the read model can actually decide are implemented here; the rest are listed in
``UNIMPLEMENTED`` with the reason, so the gap is documented rather than silently assumed away.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from ..core.config import Settings
from ..domain.editorial import BLOCKING_RULES, claim_is_evidenced
from ..domain.enums import Attestation, Confidence, EntityType, GeometryKind, Precision, Status
from ..domain.geo import BBox
from ..domain.model import EntityRecord
from ..repositories.ports import AtlasRepository

Level = Literal["error", "warning"]

#: Rules from docs/07 §3 that need data the read model does not expose yet. Declared so the report
#: is honest about coverage instead of implying "all green".
UNIMPLEMENTED: dict[str, str] = {
    "D1": "birth/death bounds live in assertions; needs a claim-shaped query (roadmap)",
    "D6": "place_link cycle detection runs in the fixture tests, not per record",
    "D10": "duplicate-identity review needs a name similarity index across records",
    "D12": "bibliographic completeness is checked on the sources fixture, not per entity",
    # The "a disputed claim must name its topic" half of D13 *is* enforced, in the service, at the
    # moment a reviewer disputes a claim. What is missing is the harder half below.
    "D13": "contradictory accepted claims need topic grouping in the read model",
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One lint result. Bilingual by design: the panel is Persian-first (ADR-0006)."""

    rule: str
    level: Level
    message_fa: str
    message_en: str
    entity_type: str | None = None
    entity_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.level == "error" and self.rule in BLOCKING_RULES

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "level": self.level,
            "blocking": self.blocking,
            "message_fa": self.message_fa,
            "message_en": self.message_en,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "detail": dict(self.detail),
        }


def _rule_order(rules: Iterable[str]) -> list[str]:
    """D2 before D10: rule numbers sort as numbers, because a human reads this list."""
    return sorted(rules, key=lambda rule: (int(rule[1:]) if rule[1:].isdigit() else 999, rule))


def _gaps() -> list[dict[str, str]]:
    """The rules this linter does not check yet, with the reason. Never silently omitted."""
    return [
        {"rule": rule, "reason": UNIMPLEMENTED[rule]} for rule in _rule_order(UNIMPLEMENTED)
    ]


def geometry_bounds(geojson: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """(minLon, minLat, maxLon, maxLat) of any GeoJSON geometry, without a spatial dependency."""
    lons: list[float] = []
    lats: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and all(isinstance(part, (int, float)) for part in node[:2]):
                lons.append(float(node[0]))
                lats.append(float(node[1]))
                return
            for item in node:
                walk(item)

    walk(geojson.get("coordinates"))
    if not lons:
        return None
    return (min(lons), min(lats), max(lons), max(lats))


class DataLinter:
    """Applies the data-quality rules to one record or to the whole published corpus."""

    def __init__(self, repository: AtlasRepository, settings: Settings) -> None:
        self._repo = repository
        self._settings = settings
        self._study_area = BBox(*settings.study_area_bbox)
        self._corpus_area = BBox(*settings.corpus_bbox)

    # ------------------------------------------------------------------ single record

    def record(self, entity: EntityRecord) -> list[Finding]:
        findings: list[Finding] = []
        entity_type = entity.entity_type.value
        published = entity.status is Status.PUBLISHED

        # D4 -- a published record without a source is an opinion, not research.
        if published and not entity.source_ids:
            findings.append(
                Finding(
                    rule="D4",
                    level="error",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="رکورد منتشرشده هیچ منبعی ندارد.",
                    message_en="A published record must cite at least one source.",
                )
            )

        # D5 -- Persian is the primary language; a published record needs a preferred fa name.
        if published and not any(
            name.lang == "fa" and name.kind == "preferred" for name in entity.names
        ):
            findings.append(
                Finding(
                    rule="D5",
                    level="error",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="نام ترجیحی فارسی وجود ندارد.",
                    message_en="A published record needs a preferred Persian name variant.",
                )
            )

        # D2 -- two rings. Inside the study area: nothing to say. Between the study area and the
        # corpus box: legitimate context (Qazvin, where the Safavid capital moved) and worth a
        # warning, because a reader will ask why the map jumped there. Beyond the corpus box: a
        # mistake -- somebody stored the wrong city, or a modern boundary slipped into history.
        context: list[str] = []
        outside: list[str] = []
        for geometry in entity.geometries:
            if geometry.kind is GeometryKind.MODERN_ADMIN:
                continue  # basemap context, never a historical claim (AGENTS.md rule 9)
            bounds = geometry_bounds(geometry.geojson)
            if bounds is None:
                continue
            if not _intersects(bounds, self._corpus_area):
                outside.append(geometry.kind.value)
            elif not _intersects(bounds, self._study_area):
                context.append(geometry.kind.value)
        if outside:
            findings.append(
                Finding(
                    rule="D2",
                    level="error",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="هندسه بیرون از محدودهٔ پیکرهٔ اطلس است.",
                    message_en="A geometry falls outside the corpus bbox entirely.",
                    detail={"kinds": outside, "corpus_bbox": list(self._corpus_area.as_tuple())},
                )
            )
        elif context:
            findings.append(
                Finding(
                    rule="D2",
                    level="warning",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="هندسه بیرون از حوزهٔ مطالعه است؛ به‌عنوان زمینه نگه داشته شده است.",
                    message_en="A geometry is outside the study area but inside the corpus: context.",
                    detail={
                        "kinds": context,
                        "study_area_bbox": list(self._study_area.as_tuple()),
                    },
                )
            )

        # D8 -- an unknown date must never look confident (ADR-0005).
        temporal = entity.temporal
        if temporal and temporal.precision is Precision.UNKNOWN and temporal.confidence not in (
            Confidence.LOW,
            Confidence.DISPUTED,
        ):
            findings.append(
                Finding(
                    rule="D8",
                    level="warning",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="تاریخ نامعلوم با اطمینان بالا ثبت شده است.",
                    message_en="An unknown date should carry low or disputed confidence.",
                    detail={"confidence": temporal.confidence.value},
                )
            )

        # D9 -- no geometry means the map cannot draw it; say so on the record. Articles are
        # exempt: a text is not a feature, and its places are linked entities of their own.
        if (
            entity.entity_type is not EntityType.ARTICLE
            and not entity.geometries
            and not (
                entity.extra.get("coverage_note_fa") or entity.extra.get("coverage_note_en")
            )
        ):
            findings.append(
                Finding(
                    rule="D9",
                    level="warning",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="بدون هندسه است و یادداشت پوشش ندارد.",
                    message_en="No geometry and no coverage note: the map cannot show this record.",
                )
            )

        # D11 -- a legendary event presented with high confidence is misinformation.
        if entity.attestation is Attestation.LEGENDARY and temporal and temporal.confidence not in (
            Confidence.LOW,
            Confidence.DISPUTED,
        ):
            findings.append(
                Finding(
                    rule="D11",
                    level="error",
                    entity_type=entity_type,
                    entity_id=entity.id,
                    message_fa="رویداد افسانه‌ای با اطمینان بالا ثبت شده است.",
                    message_en="A legendary attestation requires low or disputed confidence.",
                    detail={"confidence": temporal.confidence.value},
                )
            )

        # D3 -- an accepted claim needs evidence that carries it (supports or qualifies).
        for relation in entity.relationships:
            if not relation.is_claim or relation.status.value != "accepted":
                continue  # structural links are not claims; D3 does not apply to them
            if not claim_is_evidenced(relation.evidence):
                findings.append(
                    Finding(
                        rule="D3",
                        level="error",
                        entity_type=entity_type,
                        entity_id=entity.id,
                        message_fa=f"ادعای پذیرفته‌شده «{relation.predicate}» شاهد پشتیبان ندارد.",
                        message_en=f"Accepted claim {relation.predicate!r} has no supporting evidence.",
                        detail={"predicate": relation.predicate},
                    )
                )
        return findings

    def gates(self, entity: EntityRecord) -> list[Finding]:
        """Only what may block a transition -- evaluated as if the record were already published.

        That last clause is the whole point. At gate time the record is still ``in_review``, so the
        rules about the public corpus (D4 sources, D5 Persian name) would never fire if they were
        asked about the present. A gate has to answer "what would be true *after* this move?".
        """
        prospective = entity if entity.status is Status.PUBLISHED else replace(
            entity, status=Status.PUBLISHED
        )
        return [finding for finding in self.record(prospective) if finding.blocking]

    # ------------------------------------------------------------------ corpus

    def corpus(self, *, limit: int = 500) -> list[Finding]:
        findings: list[Finding] = []
        for record in self._repo.all_published()[:limit]:
            findings.extend(self.record(record))
        findings.sort(key=lambda item: (item.level != "error", item.rule, item.entity_id or ""))
        return findings

    def report(self, findings: list[Finding], *, scope: str) -> dict[str, Any]:
        errors = [item for item in findings if item.level == "error"]
        warnings = [item for item in findings if item.level == "warning"]
        return {
            "data": {
                "scope": scope,
                "errors": len(errors),
                "warnings": len(warnings),
                "blocking": sum(1 for item in findings if item.blocking),
                "findings": [item.as_dict() for item in findings],
                "not_evaluated": _gaps(),
            },
            "meta": {
                "rules": _rule_order(BLOCKING_RULES | set(UNIMPLEMENTED)),
                "driver": self._repo.driver_name,
            },
        }

    # ------------------------------------------------------------------ internals

def _intersects(bounds: tuple[float, float, float, float], area: BBox) -> bool:
    """Does this (min_lon, min_lat, max_lon, max_lat) touch the area at all?

    Touching is the test, not containment: a dynasty's extent legitimately straddles a border, and
    rejecting it would force editors to clip history to a rectangle.
    """
    return (
        bounds[0] <= area.max_lon
        and bounds[2] >= area.min_lon
        and bounds[1] <= area.max_lat
        and bounds[3] >= area.min_lat
    )


__all__ = ["UNIMPLEMENTED", "DataLinter", "Finding", "geometry_bounds"]
