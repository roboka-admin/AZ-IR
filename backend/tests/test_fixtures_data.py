"""Data-quality tests for the seed corpus (AGENTS.md rule 10, docs/06, docs/09).

The fixtures are the reference dataset the whole team edits, so the rules that protect readers --
provenance, no fake precision, no orphan references, no modern boundary dressed up as history --
are enforced here rather than by convention.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml

from azir.domain.enums import EntityType
from azir.repositories.fixtures import FixturesRepository, load_fixtures
from conftest import FIXTURES_DIR

DOCS = load_fixtures(FIXTURES_DIR)
REPO = FixturesRepository(FIXTURES_DIR)
PREFIXES = {"plc": "place", "prs": "person", "evt": "event", "pol": "political_entity",
            "prd": "period", "art": "article", "src": "source", "asn": "assertion"}


def all_rows() -> dict[str, list[dict]]:
    return {key: DOCS.get(key, []) for key in
            ("places", "people", "events", "political_entities", "articles", "sources",
             "periods", "assertions", "place_links", "geometries")}


@pytest.fixture(scope="module")
def rows() -> dict[str, list[dict]]:
    return all_rows()


def test_every_fixture_file_parses_and_is_documented() -> None:
    files = sorted(Path(FIXTURES_DIR).glob("*.yaml"))
    assert len(files) >= 9
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert text.lstrip().startswith("#"), f"{path.name} must open with a comment explaining its role"
        assert yaml.safe_load(text) is not None


def test_ids_are_prefixed_unique_and_stable(rows: dict[str, list[dict]]) -> None:
    seen: Counter[str] = Counter()
    for collection in ("places", "people", "events", "political_entities", "articles", "sources", "periods"):
        for row in rows[collection]:
            entity_id = str(row["id"])
            seen[entity_id] += 1
            prefix = entity_id.split("_", 1)[0]
            assert prefix in PREFIXES, f"{entity_id}: unknown id prefix"
    duplicates = [key for key, count in seen.items() if count > 1]
    assert not duplicates, duplicates


def test_slugs_are_unique_per_type(rows: dict[str, list[dict]]) -> None:
    slugs: Counter[tuple[str, str]] = Counter()
    for collection in ("places", "people", "events", "political_entities", "articles"):
        for row in rows[collection]:
            if row.get("slug"):
                slugs[(collection, str(row["slug"]))] += 1
    assert [key for key, count in slugs.items() if count > 1] == []


def test_every_entity_has_both_languages(rows: dict[str, list[dict]]) -> None:
    """i18n from day one (locked decision): a Persian-only record cannot be published."""
    for collection in ("places", "people", "events", "political_entities", "articles"):
        for row in rows[collection]:
            langs = {str(name.get("lang", "fa")) for name in row.get("names", [])}
            assert {"fa", "en"} <= langs, f"{row['id']}: missing a name in {({'fa', 'en'} - langs)}"
            assert row.get("summary_fa"), f"{row['id']}: missing summary_fa"
            assert row.get("summary_en"), f"{row['id']}: missing summary_en"


def test_published_entities_carry_sources(rows: dict[str, list[dict]]) -> None:
    source_ids = {str(row["id"]) for row in rows["sources"]}
    for collection in ("places", "people", "events", "political_entities"):
        for row in rows[collection]:
            if row.get("status") == "published":
                assert row.get("sources"), f"{row['id']}: published without a source (AGENTS.md rule 7)"
                for source in row["sources"]:
                    assert source in source_ids, f"{row['id']}: unknown source {source}"


def test_temporal_blocks_are_well_formed(rows: dict[str, list[dict]]) -> None:
    precisions = {"exact_day", "exact_month", "exact_year", "circa_year", "quarter_century",
                  "decade", "century", "range", "before", "after", "unknown"}
    for collection in ("places", "people", "events", "political_entities", "assertions", "place_links"):
        for row in rows[collection]:
            temporal = row.get("temporal")
            if not temporal:
                continue
            assert temporal.get("precision") in precisions, f"{row.get('id')}: bad precision"
            start, end = temporal.get("from"), temporal.get("to")
            if start is not None and end is not None:
                assert start <= end, f"{row.get('id')}: temporal.from > temporal.to"
            if temporal.get("precision") == "exact_year" and start is not None:
                assert end in (None, start), f"{row.get('id')}: an exact year cannot span"


def _ref(value: object) -> str | None:
    """An assertion endpoint is either a bare id or ``{type, id}``."""
    if isinstance(value, dict):
        return str(value["id"])
    return str(value) if value else None


def test_no_assertion_without_evidence(rows: dict[str, list[dict]]) -> None:
    source_ids = {str(row["id"]) for row in rows["sources"]}
    known_ids = {str(row["id"]) for collection in
                 ("places", "people", "events", "political_entities", "articles") for row in rows[collection]}
    for assertion in rows["assertions"]:
        subject = _ref(assertion["subject"])
        assert subject in known_ids, f"{assertion['id']}: unknown subject {subject}"
        target = _ref(assertion.get("object"))
        if target:
            assert target in known_ids, f"{assertion['id']}: unknown object {target}"
        else:
            assert assertion.get("value") is not None, f"{assertion['id']}: needs an object or a value"
        evidence = assertion.get("evidence") or []
        assert evidence, f"{assertion['id']}: a claim without evidence (AGENTS.md rule 7)"
        for item in evidence:
            assert item["source"] in source_ids, f"{assertion['id']}: unknown source {item['source']}"
            assert item.get("stance") in {None, "supports", "contradicts", "qualifies"}, assertion["id"]
        if assertion.get("status") == "disputed":
            assert assertion.get("topic_fa") and assertion.get("topic_en"), \
                f"{assertion['id']}: a disputed claim must name its topic in both languages"


def test_disputed_claims_come_in_pairs(rows: dict[str, list[dict]]) -> None:
    """A single 'disputed' assertion is a contradiction with nothing to contradict.

    The one exception is a claim disputed against the *absence* of evidence, which must say so in
    its note.
    """
    clusters: Counter[tuple[str, str]] = Counter()
    singles: list[dict] = []
    for assertion in rows["assertions"]:
        if assertion.get("status") == "disputed":
            key = (_ref(assertion["subject"]) or "", str(assertion.get("topic_en") or assertion["predicate"]))
            clusters[key] += 1
            singles.append(assertion)
    for assertion in singles:
        key = (_ref(assertion["subject"]) or "", str(assertion.get("topic_en") or assertion["predicate"]))
        if clusters[key] < 2:
            assert assertion.get("note_fa") or assertion.get("note"), \
                f"{assertion['id']}: a lone disputed claim must explain what it disputes"


def test_predicates_are_declared_in_the_taxonomy(rows: dict[str, list[dict]]) -> None:
    declared = {str(item["code"]) for item in DOCS.get("predicates", [])}
    used = {str(assertion["predicate"]) for assertion in rows["assertions"]}
    used |= {str(link["kind"]) for link in rows["place_links"]}
    assert used <= declared, sorted(used - declared)


def test_place_links_form_a_dag(rows: dict[str, list[dict]]) -> None:
    known = {str(row["id"]) for row in rows["places"]}
    edges: dict[str, set[str]] = {}
    for link in rows["place_links"]:
        parent, child = str(link["parent"]), str(link["child"])
        assert parent in known and child in known, f"unknown place in link {link}"
        assert parent != child, f"self link {parent}"
        edges.setdefault(child, set()).add(parent)

    def has_cycle(node: str, stack: set[str]) -> bool:
        for parent in edges.get(node, ()):
            if parent in stack or has_cycle(parent, stack | {parent}):
                return True
        return False

    assert not any(has_cycle(node, {node}) for node in edges)


def test_events_reference_existing_places_and_people(rows: dict[str, list[dict]]) -> None:
    places = {str(row["id"]) for row in rows["places"]}
    people = {str(row["id"]) for row in rows["people"]}
    political = {str(row["id"]) for row in rows["political_entities"]}
    for event in rows["events"]:
        for place in event.get("places", []) or []:
            reference = place["id"] if isinstance(place, dict) else place
            assert reference in places, f"{event['id']}: unknown place {reference}"
        for participant in event.get("participants", []) or []:
            reference = participant["id"] if isinstance(participant, dict) else participant
            assert reference in people | political, f"{event['id']}: unknown participant {reference}"
        if event.get("part_of"):
            assert event["part_of"] in {str(row["id"]) for row in rows["events"]}


def test_articles_link_to_entities_and_never_duplicate_them(rows: dict[str, list[dict]]) -> None:
    known = {str(row["id"]) for collection in
             ("places", "people", "events", "political_entities") for row in rows[collection]}
    sources = {str(row["id"]) for row in rows["sources"]}
    for article in rows["articles"]:
        assert article.get("entities"), f"{article['id']}: an article must link to entities"
        for link in article["entities"]:
            assert link["id"] in known, f"{article['id']}: unknown entity {link['id']}"
            assert link.get("relation"), f"{article['id']}: a link without a relation is decoration"
            assert link.get("type"), f"{article['id']}: a link must say what kind of entity it is"
        for source in article.get("sources", []) or []:
            assert source in sources, f"{article['id']}: unknown source {source}"
        assert article.get("map_state"), f"{article['id']}: articles must be able to open the map (handoff §12)"
        state = article["map_state"]
        assert state.get("center") and state.get("zoom")
        assert (state.get("time") or {}).get("from") is not None


def test_geometry_references_resolve(rows: dict[str, list[dict]]) -> None:
    known = {str(row["id"]) for collection in
             ("places", "people", "events", "political_entities") for row in rows[collection]}
    sources = {str(row["id"]) for row in rows["sources"]}
    for spec in rows["geometries"]:
        assert spec["entity"] in known, f"geometry for unknown entity {spec['entity']}"
        assert spec.get("source") in sources, f"{spec['entity']}: geometry without a source"
        assert spec.get("certainty") != "exact", \
            f"{spec['entity']}: a hand-drawn extent may never claim exact certainty"
        assert spec.get("note_fa") and spec.get("note_en"), f"{spec['entity']}: geometry needs a caveat"
        assert spec.get("year_from") is not None and spec.get("year_to") is not None, \
            f"{spec['entity']}: an extent without years is a fake boundary"


def test_modern_admin_is_never_a_historical_place(rows: dict[str, list[dict]]) -> None:
    for row in rows["places"]:
        if row.get("kind") == "province_modern":
            assert row["id"].endswith("_province")
            assert not row.get("geometry"), "modern admin polygons must come from OSM, not be drawn here"
            assert row["status"] != "published", \
                f"{row['id']}: publish modern admin only from real OSM data, in its own layer"


def test_time_varying_geometries_do_not_overlap() -> None:
    for entity in REPO.all_published():
        spans = [(g.year_from, g.year_to) for g in entity.geometries
                 if g.year_from is not None and g.year_to is not None and g.is_polygonal]
        for index, (start_a, end_a) in enumerate(spans):
            for start_b, end_b in spans[index + 1:]:
                assert end_a < start_b or end_b < start_a, f"{entity.id}: overlapping extents"


def test_no_modern_boundary_published_as_history() -> None:
    """AGENTS.md rule 8: modern != historical, enforced at the data level too."""
    for entity in REPO.all_published():
        if entity.entity_type is EntityType.POLITICAL_ENTITY and entity.temporal:
            assert entity.temporal.year_to is None or entity.temporal.year_to < 1925 or \
                entity.kind == "autonomous_government", entity.id


def test_coverage_notes_explain_known_gaps(rows: dict[str, list[dict]]) -> None:
    gaps = [row for collection in ("places", "people", "events", "political_entities")
            for row in rows[collection] if row.get("coverage_note_fa")]
    assert gaps, "a corpus with no documented gaps is a corpus nobody has reviewed"
    for row in gaps:
        assert len(row["coverage_note_fa"]) > 20, row["id"]
