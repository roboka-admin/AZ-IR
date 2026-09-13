"""Repository contract tests.

Run against *every* driver available in the environment (ADR-0014): the fixtures driver here,
the PostGIS driver in CI when ``AZIR_TEST_DB_URL`` is set. Both must behave identically, so
these tests describe the contract in terms of the port only -- never driver internals.
"""

from __future__ import annotations

import pytest

from azir.core.config import Settings
from azir.domain.enums import EntityType
from azir.domain.geo import BBox
from azir.domain.model import EntityRecord
from azir.domain.temporal import TemporalMode, TimeWindow
from azir.repositories.ports import AtlasQuery, AtlasRepository
from conftest import build_repositories

STUDY_BBOX = BBox(*Settings().study_area_bbox)  # the real study area, never a hand-typed guess
ARDABIL_BBOX = BBox(48.2, 38.2, 48.4, 38.3)
ALL_LAYERS = ("places", "political_entities", "events", "battles", "buildings", "people", "routes", "archaeology")

REPOSITORIES = build_repositories()
driver = pytest.mark.parametrize("repo", REPOSITORIES, ids=[item.driver_name for item in REPOSITORIES])


def query(
    repo: AtlasRepository,
    *,
    bbox: BBox = STUDY_BBOX,
    zoom: float = 7.0,
    window: TimeWindow | None = None,
    layers: tuple[str, ...] = ALL_LAYERS,
    locale: str = "fa",
    limit: int = 200,
    **extra: object,
) -> dict[str, object]:
    page = repo.features(
        AtlasQuery(
            bbox=bbox, zoom=zoom, window=window or TimeWindow.at(1450), layers=layers,
            locale=locale, limit=limit, **extra,  # type: ignore[arg-type]
        )
    )
    return {row_id: page.entities[row_id] for _, row_id in page.rows}, page


@driver
def test_features_are_ranked_and_limited(repo: AtlasRepository) -> None:
    page = repo.features(
        AtlasQuery(bbox=STUDY_BBOX, zoom=7.0, window=TimeWindow.at(1450), layers=ALL_LAYERS, locale="fa", limit=5)
    )
    ranks = [rank for rank, _ in page.rows]
    assert 0 < len(page.rows) <= 5
    assert ranks == sorted(ranks, reverse=True)
    assert set(page.entities) >= {row_id for _, row_id in page.rows}


@driver
def test_time_actually_filters(repo: AtlasRepository) -> None:
    def count(year: int) -> int:
        return len(
            repo.features(
                AtlasQuery(bbox=STUDY_BBOX, zoom=7.0, window=TimeWindow.at(year),
                           layers=ALL_LAYERS, locale="fa", limit=200)
            ).rows
        )

    assert count(1450) > count(500)
    assert count(1450) >= count(2010)


@driver
def test_semantic_zoom_changes_the_feature_set(repo: AtlasRepository) -> None:
    """Region level shows areas; fabric level shows the city's buildings (docs/02)."""
    wide, _ = query(repo, zoom=7.0, window=TimeWindow.at(1510))
    tight, _ = query(repo, bbox=ARDABIL_BBOX, zoom=13.0, window=TimeWindow.at(1510))
    assert "plc_ardabil" in wide
    assert "plc_sheikh_safi_complex" in tight
    assert tight.keys() != wide.keys()


@driver
def test_bbox_restricts_results(repo: AtlasRepository) -> None:
    records, _ = query(repo, bbox=ARDABIL_BBOX, zoom=13.0, window=TimeWindow.at(1510))
    assert records
    assert "plc_tabriz" not in records


@driver
def test_every_record_has_a_geometry_and_a_locus(repo: AtlasRepository) -> None:
    _, page = query(repo, zoom=13.0, window=TimeWindow.at(1510), bbox=ARDABIL_BBOX)
    for _, entity_id in page.rows:
        geometry = page.geometries.get(entity_id)
        assert geometry is not None, entity_id
        assert geometry["type"] in {"Point", "Polygon", "MultiPolygon", "LineString"}


@driver
def test_locale_switches_display_names(repo: AtlasRepository) -> None:
    fa, _ = query(repo, locale="fa", window=TimeWindow.at(1450))
    en, _ = query(repo, locale="en", window=TimeWindow.at(1450))
    assert fa["plc_tabriz"].display_name("fa") == "تبریز"
    assert en["plc_tabriz"].display_name("en") == "Tabriz"


@driver
def test_entity_lookup_by_slug_and_id(repo: AtlasRepository) -> None:
    by_slug = repo.entity("place", "sheikh-safi-complex")
    assert by_slug is not None
    by_id = repo.entity("place", by_slug.id)
    assert by_id is not None and by_id.id == by_slug.id
    assert by_slug.display_name("fa") == by_id.display_name("fa")
    assert by_slug.primary_geometry() is not None


@driver
def test_unknown_entity_is_none_not_an_exception(repo: AtlasRepository) -> None:
    assert repo.entity("place", "does-not-exist") is None


@driver
def test_relationships_carry_predicate_and_certainty(repo: AtlasRepository) -> None:
    record = repo.entity("place", "plc_ardabil")
    assert record is not None
    rels = repo.related("place", record.id, depth=1, limit=50)
    assert rels
    for rel in rels:
        assert rel.predicate
        assert rel.label("fa") and rel.label("en")
        assert rel.object_id or rel.object_value


@driver
def test_disputed_assertions_are_preserved_not_flattened(repo: AtlasRepository) -> None:
    """AGENTS.md rule 20: competing claims stay visible, with their own evidence."""
    record = repo.entity("political_entity", "safavid-dynasty")
    assert record is not None
    disagreements = record.disagreements
    assert disagreements, "the Safavid end-date cluster must survive as a disagreement"
    values = {
        position.object_value
        for group in disagreements
        for position in group.positions
    }
    assert {"1722", "1736"} <= values
    assert all(position.status.value == "disputed" for group in disagreements for position in group.positions)


@driver
def test_search_matches_both_languages(repo: AtlasRepository) -> None:
    fa = repo.search("صفوی", types=("place", "political_entity", "event", "person", "article"), locale="fa", limit=5)
    en = repo.search("tabriz", types=("place", "political_entity", "event", "person", "article"), locale="en", limit=5)
    assert fa and fa[0].entity.display_name("fa").startswith("صفوی")
    assert en and en[0].entity.display_name("en") == "Tabriz"
    assert en[0].score > 0.5


@driver
def test_search_is_scored_and_ordered(repo: AtlasRepository) -> None:
    hits = repo.search("تبریز", types=("place", "event", "person", "political_entity", "article"), locale="fa", limit=10)
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert repo.search("zzqqxx-nothing", types=("place",), locale="fa", limit=5) == []


@driver
def test_timeline_buckets_are_contiguous(repo: AtlasRepository) -> None:
    buckets = repo.timeline(
        STUDY_BBOX, TimeWindow.span(1200, 1600, mode=TemporalMode.OVERLAPS), 100, ALL_LAYERS, "fa"
    )
    assert [bucket.year_from for bucket in buckets] == [1200, 1300, 1400, 1500, 1600]
    assert all(bucket.total >= 0 for bucket in buckets)
    notable = [item for bucket in buckets for item in bucket.notable]
    assert notable and all("label" in item and "id" in item for item in notable)


@driver
def test_events_without_geometry_are_reported_not_hidden(repo: AtlasRepository) -> None:
    """docs/06: a coverage gap is data about our data, never a silent hole in the map."""
    page = repo.features(
        AtlasQuery(bbox=STUDY_BBOX, zoom=7.0, window=TimeWindow.at(1450), layers=ALL_LAYERS, locale="fa")
    )
    assert any("geometry" in gap for gap in page.coverage_gaps)


@driver
def test_entities_can_inherit_a_locus_from_linked_places(repo: AtlasRepository) -> None:
    """ADR-0004: a derived location is possible, but never presented as exact."""
    records, page = query(repo, zoom=8.0, window=TimeWindow.at(1514), layers=("events", "battles"))
    assert "evt_battle_chaldiran" in records, sorted(records)
    geometry = records["evt_battle_chaldiran"].primary_geometry(at_year=1514)
    assert geometry is not None
    assert geometry.kind.value == "uncertain_locus"
    assert geometry.certainty.value == "uncertain"
    assert geometry.note, "a derived locus must explain itself"
    assert page.geometries["evt_battle_chaldiran"]["type"] == "Point"


@driver
def test_polygonal_places_keep_their_polygons(repo: AtlasRepository) -> None:
    _, page = query(repo, zoom=7.0, window=TimeWindow.at(1510))
    assert page.geometries["pol_safavid"]["type"] in {"Polygon", "MultiPolygon"}


@driver
def test_list_entities_is_ordered_by_rank(repo: AtlasRepository) -> None:
    rows = repo.list_entities(EntityType.PLACE, status="published", locale="fa", limit=10)
    assert len(rows) == 10
    ranks = [record.rank for record in rows]
    assert ranks == sorted(ranks, reverse=True)
    # Modern provinces are deliberately draft and geometry-free: a documented coverage gap.
    drafts = repo.list_entities(EntityType.PLACE, status="draft", locale="fa", limit=50)
    assert {record.id for record in drafts} >= {"plc_ardabil_province"}
    assert all(record.id not in {p.id for p in published_ids(repo)} for record in drafts)


@driver
def test_articles_link_to_entities_and_carry_map_state(repo: AtlasRepository) -> None:
    article = repo.entity("article", "rise-of-the-safavids")
    assert article is not None
    assert article.extra["map_state"]["time"]["from"] == 1300
    assert article.article_ids or article.relationships
    linked = repo.articles(locale="fa", limit=50, entity_id="plc_tabriz")
    assert any(item.id == article.id for item in linked)


@driver
def test_radius_queries_read_longitude_then_latitude(repo: AtlasRepository) -> None:
    """Every coordinate pair in this codebase is (lon, lat): GeoJSON order, `?near=lon,lat`.

    Swapping them compiles, runs, and quietly queries a point 1200 km away -- which is exactly why
    the real-PostGIS CI run is worth its weight. Ardabil is (48.2934, 38.2464).
    """
    ardabil = (48.2934, 38.2464)
    swapped = (ardabil[1], ardabil[0])

    def around(point: tuple[float, float]) -> int:
        page = repo.features(
            AtlasQuery(
                bbox=STUDY_BBOX, zoom=8.0, window=TimeWindow.at(1500), layers=ALL_LAYERS,
                near=point, radius_km=30.0, limit=50,
            )
        )
        return len(page.rows)

    assert around(ardabil) > 0, "a 30 km radius around Ardabil must find Ardabil"
    assert around(swapped) == 0, "the swapped pair points at empty steppe north of the Caucasus"

    assert repo.search("اردبیل", types=("place",), locale="fa", limit=10, near=ardabil, radius_km=30.0)
    assert not repo.search(
        "اردبیل", types=("place",), locale="fa", limit=10, near=swapped, radius_km=30.0
    )


@driver
def test_context_answers_what_was_here(repo: AtlasRepository) -> None:
    rels = repo.context(place_id="plc_ardabil", point=None, radius_km=50.0, locale="fa", limit=25)
    assert rels
    predicates = {rel.predicate for rel in rels}
    assert predicates & {"contains", "historically_in", "birthplace_of", "site_of", "capital_of"}
    by_point = repo.context(place_id=None, point=(48.2934, 38.2464), radius_km=25.0, locale="fa", limit=25)
    assert by_point


def published_ids(repo: AtlasRepository) -> list[EntityRecord]:
    return repo.all_published()


@driver
def test_stats_and_published_set(repo: AtlasRepository) -> None:
    stats = repo.stats()
    assert stats["place"] > 0 and stats["article"] > 0
    assert stats["provisional_geometries"] > 0  # drawn shapes we owe the reader a caveat about
    published = repo.all_published()
    assert published
    assert all(record.status.value == "published" for record in published)
    assert {record.entity_type for record in published} >= {EntityType.PLACE, EntityType.EVENT}


@driver
def test_lod_tolerance_never_drops_or_inflates(repo: AtlasRepository) -> None:
    """docs/02: simplification serves the payload budget, so it may only ever remove detail."""

    def fetch(tolerance: float) -> tuple[set[str], int]:
        page = repo.features(
            AtlasQuery(
                bbox=STUDY_BBOX, zoom=7.0, window=TimeWindow.at(1510),
                layers=("political_entities",), locale="fa", lod_tolerance=tolerance,
            )
        )
        geometry = page.geometries["pol_safavid"]
        ring = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["coordinates"][0][0]
        return {entity_id for _, entity_id in page.rows}, len(ring)

    exact_ids, exact_vertices = fetch(0.0)
    coarse_ids, coarse_vertices = fetch(0.05)
    assert coarse_ids == exact_ids
    assert coarse_vertices <= exact_vertices


@driver
def test_geometry_changes_with_time(repo: AtlasRepository) -> None:
    """The Safavid extent of 1510 is not the extent of 1650 (ADR-0004, docs/04)."""

    def area(year: int) -> float:
        page = repo.features(
            AtlasQuery(bbox=STUDY_BBOX, zoom=6.4, window=TimeWindow.at(year),
                       layers=("political_entities",), locale="fa")
        )
        geometry = page.geometries["pol_safavid"]
        assert geometry["type"] == "Polygon"
        ring = geometry["coordinates"][0]
        return sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1))

    assert area(1510) != area(1650)


@driver
def test_no_geometry_before_the_entity_exists(repo: AtlasRepository) -> None:
    page = repo.features(
        AtlasQuery(bbox=STUDY_BBOX, zoom=6.4, window=TimeWindow.at(1400),
                   layers=("political_entities",), locale="fa")
    )
    assert "pol_safavid" not in {entity_id for _, entity_id in page.rows}


@driver
def test_reconstructed_extents_are_never_marked_exact(repo: AtlasRepository) -> None:
    page = repo.features(
        AtlasQuery(bbox=STUDY_BBOX, zoom=6.4, window=TimeWindow.at(1510),
                   layers=("political_entities",), locale="fa")
    )
    for _, entity_id in page.rows:
        geometry = page.entities[entity_id].primary_geometry(at_year=1510)
        if geometry is not None and geometry.kind.value == "extent_reconstructed":
            assert geometry.certainty.value == "reconstructed", entity_id
            assert geometry.note and geometry.source_id, entity_id


@driver
def test_lake_has_a_dated_shoreline(repo: AtlasRepository) -> None:
    """A point for the lake in 1500, polygons for the modern shorelines later."""
    old = repo.features(
        AtlasQuery(bbox=STUDY_BBOX, zoom=7.0, window=TimeWindow.at(1500), layers=("places",), locale="fa")
    )
    new = repo.features(
        AtlasQuery(bbox=STUDY_BBOX, zoom=7.0, window=TimeWindow.at(2020), layers=("places",), locale="fa")
    )
    assert old.geometries["plc_lake_urmia"]["type"] == "Point"
    assert new.geometries["plc_lake_urmia"]["type"] == "Polygon"
