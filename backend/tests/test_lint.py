"""Data lint (docs/07 §3): the rules that keep the corpus honest, tested rule by rule.

The linter is pure: it reads records and returns findings. It never raises, because a report that
crashes on the first bad record is a report nobody runs twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from azir.core.config import Settings
from azir.domain.enums import (
    AssertionStatus,
    Attestation,
    Certainty,
    Confidence,
    EntityType,
    GeometryKind,
    Precision,
    Status,
)
from azir.domain.geo import GeometryRecord
from azir.domain.model import EntityRecord, NameVariant, Relationship
from azir.domain.temporal import TemporalInterval
from azir.repositories.fixtures import FixturesRepository
from azir.services.lint import BLOCKING_RULES, UNIMPLEMENTED, DataLinter, geometry_bounds

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "fixtures"

ARDABIL = {"type": "Point", "coordinates": [48.35, 38.25]}
PARIS = {"type": "Point", "coordinates": [2.35, 48.85]}


@pytest.fixture()
def linter(settings: Settings) -> DataLinter:
    return DataLinter(FixturesRepository(FIXTURES_DIR), settings)


def make_record(**overrides: Any) -> EntityRecord:
    """A record that passes every rule, so a test can break exactly one thing."""
    fields: dict[str, Any] = {
        "id": "plc_test",
        "entity_type": EntityType.PLACE,
        "kind": "city",
        "slug": "test",
        "status": Status.PUBLISHED,
        "revision": 1,
        "names": (NameVariant(form="شهر آزمون", lang="fa", kind="preferred"),),
        "temporal": TemporalInterval(
            year_from=1500, year_to=1600, precision=Precision.RANGE, confidence=Confidence.MEDIUM
        ),
        "geometries": (
            GeometryRecord(geojson=ARDABIL, kind=GeometryKind.POINT, certainty=Certainty.EXACT),
        ),
        "summary": "چکیده.",
        "source_ids": ("src_tabari_tarikh",),
        "importance": 0.5,
    }
    fields.update(overrides)
    return EntityRecord(**fields)


def rules_of(findings: list[Any]) -> set[str]:
    return {finding.rule for finding in findings}


def test_a_clean_record_produces_no_findings(linter: DataLinter) -> None:
    assert linter.record(make_record()) == []


def test_d4_a_published_record_needs_a_source(linter: DataLinter) -> None:
    findings = linter.record(make_record(source_ids=()))
    assert "D4" in rules_of(findings)
    assert next(item for item in findings if item.rule == "D4").blocking is True
    # A draft is not an offence yet: D4 is about what the public sees.
    assert "D4" not in rules_of(linter.record(make_record(source_ids=(), status=Status.DRAFT)))


def test_d5_a_published_record_needs_a_persian_name(linter: DataLinter) -> None:
    findings = linter.record(
        make_record(names=(NameVariant(form="Test Town", lang="en", kind="preferred"),))
    )
    assert "D5" in rules_of(findings)
    # Strict on purpose: with only an *alternate* Persian name the fa title would be whatever
    # variant happens to come first. D5 makes the Persian title a decision, not an accident.
    alternate_only = make_record(
        names=(
            NameVariant(form="Test Town", lang="en", kind="preferred"),
            NameVariant(form="شهر آزمون", lang="fa", kind="alternate"),
        )
    )
    assert "D5" in rules_of(linter.record(alternate_only))
    decided = make_record(
        names=(
            NameVariant(form="Test Town", lang="en", kind="preferred"),
            NameVariant(form="شهر آزمون", lang="fa", kind="preferred"),
        )
    )
    assert "D5" not in rules_of(linter.record(decided))


def test_d2_geometry_must_be_inside_the_study_area(linter: DataLinter) -> None:
    outside = make_record(
        geometries=(
            GeometryRecord(geojson=PARIS, kind=GeometryKind.POINT, certainty=Certainty.EXACT),
        )
    )
    assert "D2" in rules_of(linter.record(outside))
    # Modern administrative context is explicitly exempt: it is basemap, not history (rule 9).
    modern = make_record(
        geometries=(
            GeometryRecord(geojson=PARIS, kind=GeometryKind.MODERN_ADMIN, certainty=Certainty.EXACT),
        )
    )
    assert "D2" not in rules_of(linter.record(modern))


def test_d2_tolerates_a_boundary_that_straddles_the_edge(linter: DataLinter) -> None:
    """A polygon crossing the study-area border is partly inside, which is enough."""
    straddling = {
        "type": "Polygon",
        "coordinates": [
            [
                [47.0, 37.5],
                [50.0, 37.5],
                [50.0, 39.5],
                [47.0, 39.5],
                [47.0, 37.5],
            ]
        ],
    }
    record = make_record(
        geometries=(
            GeometryRecord(
                geojson=straddling, kind=GeometryKind.EXTENT_RECONSTRUCTED, certainty=Certainty.APPROXIMATE
            ),
        )
    )
    assert "D2" not in rules_of(linter.record(record))


def test_d8_an_unknown_date_must_not_look_confident(linter: DataLinter) -> None:
    # The first line of defence is the domain model itself: it refuses to *represent* an unknown
    # date with confidence, so a record built in Python can never violate D8.
    normalized = TemporalInterval(
        year_from=-10000,
        year_to=2100,
        precision=Precision.UNKNOWN,
        confidence=Confidence.HIGH,
        display="نامعلوم",
    )
    assert normalized.confidence is Confidence.LOW, "the interval downgrades itself (ADR-0005)"
    assert "D8" not in rules_of(linter.record(make_record(temporal=normalized)))

    # The linter keeps the rule anyway: a row whose columns were written by an import, a migration
    # or a bug can still disagree with the domain invariant, and the report is where that shows up.
    smuggled = make_record(temporal=TemporalInterval.unknown("نامعلوم"))
    object.__setattr__(smuggled.temporal, "confidence", Confidence.HIGH)
    findings = linter.record(smuggled)
    assert "D8" in rules_of(findings)
    assert next(item for item in findings if item.rule == "D8").level == "warning"
    assert "D8" not in BLOCKING_RULES, "D8 warns, it does not stop publication"


def test_d9_a_record_the_map_cannot_draw_says_so(linter: DataLinter) -> None:
    findings = linter.record(make_record(geometries=()))
    assert "D9" in rules_of(findings)
    noted = make_record(geometries=(), extra={"coverage_note_fa": "هندسهٔ این رویداد نامعلوم است."})
    assert "D9" not in rules_of(linter.record(noted))


def test_d11_a_legend_needs_low_confidence(linter: DataLinter) -> None:
    legendary = make_record(
        attestation=Attestation.LEGENDARY,
        temporal=TemporalInterval(
            year_from=1000, year_to=1000, precision=Precision.EXACT_YEAR, confidence=Confidence.HIGH
        ),
    )
    assert "D11" in rules_of(linter.record(legendary))
    cautious = make_record(
        attestation=Attestation.LEGENDARY,
        temporal=TemporalInterval(
            year_from=900, year_to=1100, precision=Precision.CIRCA_YEAR, confidence=Confidence.LOW
        ),
    )
    assert "D11" not in rules_of(linter.record(cautious))


def test_d3_an_accepted_claim_needs_supporting_evidence(linter: DataLinter) -> None:
    """D3 reads the graph edges a record carries, so it is evaluated per entity."""
    from azir.repositories.ports import EvidenceRef

    contradicted = Relationship(
        predicate="located_in",
        label_fa="در",
        label_en="located in",
        object_type=EntityType.PLACE,
        object_id="plc_other",
        status=AssertionStatus.ACCEPTED,
        evidence=(EvidenceRef(source_id="src_tabari_tarikh", stance="contradicts"),),
        is_claim=True,
    )
    findings = linter.record(make_record(relationships=(contradicted,)))
    assert "D3" in rules_of(findings)
    assert next(item for item in findings if item.rule == "D3").detail["predicate"] == "located_in"

    # No evidence at all is just as bad as contradicting evidence.
    bare = Relationship(
        predicate="capital", label_fa="پایتخت", label_en="capital", is_claim=True
    )
    assert "D3" in rules_of(linter.record(make_record(relationships=(bare,))))

    # A source that qualifies the claim still carries it: Maragheh *was* an Ilkhanid capital, for
    # part of the period. Demanding "supports" would push nuanced research towards overstated claims.
    qualified = Relationship(
        predicate="capital",
        label_fa="پایتخت",
        label_en="capital",
        evidence=(EvidenceRef(source_id="src_tabari_tarikh", stance="qualifies"),),
        is_claim=True,
    )
    assert "D3" not in rules_of(linter.record(make_record(relationships=(qualified,))))

    # Structural links are part of a record's shape, not an argument about the world: D3 must not
    # ask an article reference or a containment edge for a citation.
    structural = Relationship(
        predicate="article:about", label_fa="دربارهٔ", label_en="about"
    )
    assert structural.is_claim is False
    assert linter.record(make_record(relationships=(structural,))) == []


def test_gates_answer_the_question_about_after_publication(linter: DataLinter) -> None:
    """At gate time a record is still ``in_review``; the gate must ask what publication would mean."""
    draft = make_record(status=Status.IN_REVIEW, source_ids=())
    assert linter.record(draft) == [] or "D4" not in rules_of(linter.record(draft))
    assert "D4" in rules_of(linter.gates(draft)), "the gate sees the future, not the present"
    assert all(finding.blocking for finding in linter.gates(draft))
    assert linter.gates(make_record()) == []


def test_the_corpus_report_is_sorted_and_counts(linter: DataLinter) -> None:
    findings = linter.corpus(limit=500)
    report = linter.report(findings, scope="corpus")
    assert report["data"]["scope"] == "corpus"
    assert report["data"]["errors"] + report["data"]["warnings"] == len(findings)
    assert report["data"]["errors"] == sum(1 for f in findings if f.level == "error")
    levels = [finding.level for finding in findings]
    assert levels == sorted(levels, key=lambda level: level != "error"), "errors come first"
    assert report["meta"]["driver"] == "fixtures"


def test_the_report_admits_what_it_does_not_check(linter: DataLinter) -> None:
    """Honesty about coverage: an unimplemented rule is declared, never silently skipped."""
    report = linter.report([], scope="corpus")
    declared = {item["rule"] for item in report["data"]["not_evaluated"]}
    assert declared == set(UNIMPLEMENTED)
    assert all(item["reason"] for item in report["data"]["not_evaluated"])
    assert set(report["meta"]["rules"]) >= BLOCKING_RULES | set(UNIMPLEMENTED)


def test_geometry_bounds_handles_every_geojson_shape() -> None:
    assert geometry_bounds({"type": "Point", "coordinates": [10.0, 20.0]}) == (10.0, 20.0, 10.0, 20.0)
    line = {"type": "LineString", "coordinates": [[0.0, 0.0], [5.0, -3.0]]}
    assert geometry_bounds(line) == (0.0, -3.0, 5.0, 0.0)
    polygon = {
        "type": "Polygon",
        "coordinates": [[[1.0, 1.0], [4.0, 1.0], [4.0, 6.0], [1.0, 6.0], [1.0, 1.0]]],
    }
    assert geometry_bounds(polygon) == (1.0, 1.0, 4.0, 6.0)
    multi = {"type": "MultiPoint", "coordinates": [[-2.0, 8.0], [3.0, 1.0]]}
    assert geometry_bounds(multi) == (-2.0, 1.0, 3.0, 8.0)
    assert geometry_bounds({"type": "Point", "coordinates": []}) is None
    assert geometry_bounds({"type": "GeometryCollection", "geometries": []}) is None
