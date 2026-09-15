"""PostGIS driver against a real database (marker ``postgis``).

Skipped unless ``AZIR_TEST_DB_URL`` points at a PostgreSQL+PostGIS database; CI provides one as a
service container. These tests cover what the shared contract suite cannot: SQL/Python folding
parity, spatial predicates done by PostGIS, seeder idempotency and -- the point of ADR-0014 --
agreement with the fixtures driver on identical queries.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from azir.core.config import Settings
from azir.domain import text as textnorm
from azir.domain.enums import Certainty, EntityType, GeometryKind
from azir.domain.geo import BBox
from azir.domain.temporal import TemporalMode, TimeWindow
from azir.repositories.fixtures import FixturesRepository
from azir.repositories.ports import AtlasQuery

DB_URL = os.environ.get("AZIR_TEST_DB_URL")
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "fixtures"
STUDY_BBOX = BBox(*Settings().study_area_bbox)
ALL_LAYERS = (
    "places", "political_entities", "events", "battles", "buildings", "people", "routes",
    "archaeology",
)

pytestmark = pytest.mark.postgis

FOLDING_CORPUS = (
    "صفی\u200cالدین اردبیلی",
    "تبريز",
    "اردبیلٔ",
    "۱۵۱۴ میلادی",
    "دربارِ شاه اسماعیل",
    "نبرد چالــــدران",
    "الصفویة",
    "Urmia",
    "Lake Urmia (Daryāche-ye Orūmīyeh)",
)


@pytest.fixture(scope="module")
def db_repository() -> Iterator[object]:
    """Migrate, seed and hand back a live PostGIS repository."""
    if not DB_URL:
        pytest.skip("AZIR_TEST_DB_URL is not set")
    from alembic import command
    from alembic.config import Config

    from azir.repositories.postgis import PostgisRepository, build_engine, seed_database

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    config.set_main_option("sqlalchemy.url", DB_URL)
    command.upgrade(config, "head")

    engine = build_engine(DB_URL)
    seed_database(engine, FIXTURES_DIR)
    repository = PostgisRepository(DB_URL)
    repository.healthcheck()
    try:
        yield repository
    finally:
        repository.close()
        engine.dispose()


@pytest.fixture(scope="module")
def fixtures_repository() -> FixturesRepository:
    return FixturesRepository(FIXTURES_DIR)


def query(
    repository: object,
    *,
    bbox: BBox = STUDY_BBOX,
    zoom: float = 7.0,
    window: TimeWindow | None = None,
    layers: tuple[str, ...] = ALL_LAYERS,
    **extra: object,
) -> object:
    from azir.domain.semantic_zoom import lod_tolerance, min_rank_for

    return repository.features(  # type: ignore[attr-defined]
        AtlasQuery(
            bbox=bbox,
            zoom=zoom,
            window=window or TimeWindow.at(1510),
            layers=layers,
            min_rank=min_rank_for(zoom),
            lod_tolerance=lod_tolerance(zoom),
            limit=int(extra.get("limit", 200)),
            **{key: value for key, value in extra.items() if key != "limit"},
        )
    )


# ------------------------------------------------------------------ parity with the fixtures driver


def test_the_same_query_returns_the_same_entities(db_repository, fixtures_repository) -> None:
    """ADR-0014: two drivers, one behaviour."""
    for zoom, year in ((4.0, 1500), (7.0, 1510), (11.0, 1514), (14.0, 1600)):
        window = TimeWindow.at(year)
        left = query(db_repository, zoom=zoom, window=window)
        right = query(fixtures_repository, zoom=zoom, window=window)
        assert [(round(rank, 2), ident) for rank, ident in left.rows] == [
            (round(rank, 2), ident) for rank, ident in right.rows
        ], f"zoom={zoom} year={year}"


def test_every_published_record_survives_the_round_trip(db_repository, fixtures_repository) -> None:
    left = {record.id: record for record in db_repository.all_published()}  # type: ignore[attr-defined]
    right = {record.id: record for record in fixtures_repository.all_published()}
    assert set(left) == set(right)
    for ident, record in right.items():
        other = left[ident]
        assert other.status is record.status
        assert other.layer == record.layer
        assert other.kind == record.kind
        assert round(other.rank, 2) == round(record.rank, 2)
        assert other.display_name("fa") == record.display_name("fa")
        assert other.display_name("en") == record.display_name("en")
        assert (other.temporal is None) == (record.temporal is None)
        if other.temporal and record.temporal:
            assert other.temporal.year_from == record.temporal.year_from
            assert other.temporal.year_to == record.temporal.year_to
            assert other.temporal.precision is record.temporal.precision
        assert len(other.geometries) == len(record.geometries)
        assert other.counts.sources == record.counts.sources
        assert other.counts.articles == record.counts.articles
        assert other.counts.assertions == record.counts.assertions


def test_stats_agree(db_repository, fixtures_repository) -> None:
    assert db_repository.stats() == fixtures_repository.stats()  # type: ignore[attr-defined]


def test_relationships_agree(db_repository, fixtures_repository) -> None:
    for ident in ("prs_shah_ismail_i", "plc_tabriz", "evt_battle_chaldiran", "pol_safavid"):
        left = db_repository.entity("", ident)  # type: ignore[attr-defined]
        right = fixtures_repository.entity("", ident)
        assert left is not None and right is not None, ident
        left_edges = {(item.predicate, item.object_id, item.direction) for item in left.relationships}
        right_edges = {(item.predicate, item.object_id, item.direction) for item in right.relationships}
        assert left_edges == right_edges, f"{ident}: {left_edges ^ right_edges}"
        assert len(left.disagreements) == len(right.disagreements)


# ------------------------------------------------------------------ spatial work belongs to PostGIS


def test_radius_search_uses_geography(db_repository) -> None:
    """A 25 km radius around Ardabil keeps Ardabil and drops Tabriz (~200 km away)."""
    near = db_repository.search(  # type: ignore[attr-defined]
        "اردبیل", types=("place",), near=(38.25, 48.29), radius_km=25.0, limit=50
    )
    assert {hit.entity.id for hit in near} >= {"plc_ardabil"}
    far = db_repository.search(  # type: ignore[attr-defined]
        "تبریز", types=("place",), near=(38.25, 48.29), radius_km=25.0, limit=50
    )
    assert far == [], "ST_DWithin on geography must exclude Tabriz from a 25 km Ardabil radius"


def test_an_empty_query_returns_nothing(db_repository) -> None:
    assert db_repository.search("", types=("place",), limit=10) == []  # type: ignore[attr-defined]


def test_bbox_actually_clips(db_repository) -> None:
    ardabil = query(db_repository, bbox=BBox(48.2, 38.2, 48.4, 38.3), zoom=10.0)
    whole = query(db_repository, zoom=10.0)
    assert ardabil.rows, "the Ardabil box must contain features"
    assert len(ardabil.rows) < len(whole.rows)
    assert {ident for _, ident in ardabil.rows} < {ident for _, ident in whole.rows}


def test_geometry_changes_with_the_window(db_repository) -> None:
    """Time-varying geometry: the Safavid extent of 1510 is not the extent of 1650 (ADR-0004)."""
    early = query(db_repository, zoom=6.4, window=TimeWindow.at(1510), layers=("political_entities",))
    late = query(db_repository, zoom=6.4, window=TimeWindow.at(1650), layers=("political_entities",))
    assert "pol_safavid" in early.geometries and "pol_safavid" in late.geometries
    assert early.geometries["pol_safavid"] != late.geometries["pol_safavid"]
    # ... and before the dynasty exists there is no shape at all.
    before = query(db_repository, zoom=6.4, window=TimeWindow.at(1400), layers=("political_entities",))
    assert "pol_safavid" not in {ident for _, ident in before.rows}


def test_reconstructed_extents_are_never_exact(db_repository) -> None:
    record = db_repository.entity("", "plc_azerbaijan_historical")  # type: ignore[attr-defined]
    assert record is not None
    for geometry in record.geometries:
        if geometry.kind is GeometryKind.EXTENT_RECONSTRUCTED:
            assert geometry.certainty is not Certainty.EXACT


def test_lod_simplification_shrinks_payloads_without_dropping_features(db_repository) -> None:
    """Same features, coarser tolerance at low zoom: fewer bytes, identical id set (docs/04)."""
    import json

    from azir.domain.semantic_zoom import lod_tolerance, min_rank_for

    def page(tolerance: float) -> object:
        return db_repository.features(  # type: ignore[attr-defined]
            AtlasQuery(
                bbox=STUDY_BBOX,
                zoom=6.4,
                window=TimeWindow.at(1510),
                layers=("political_entities",),
                min_rank=min_rank_for(6.4),
                lod_tolerance=tolerance,
                limit=200,
            )
        )

    coarse, fine = page(lod_tolerance(4.0)), page(0.0)
    assert {ident for _, ident in coarse.rows} == {ident for _, ident in fine.rows}  # type: ignore[attr-defined]
    assert len(json.dumps(coarse.geometries)) <= len(json.dumps(fine.geometries))  # type: ignore[attr-defined]


def test_during_mode_is_stricter_than_overlaps(db_repository) -> None:
    overlaps = query(
        db_repository, zoom=6.0, window=TimeWindow.span(1500, 1520, TemporalMode.OVERLAPS)
    )
    during = query(db_repository, zoom=6.0, window=TimeWindow.span(1500, 1520, TemporalMode.DURING))
    assert len(during.rows) <= len(overlaps.rows)


# ------------------------------------------------------------------ search


def test_sql_folding_matches_the_domain_folding(db_repository) -> None:
    """ADR-0007: index and query must agree, or search silently misses."""
    from sqlalchemy import text

    with db_repository.engine.connect() as connection:  # type: ignore[attr-defined]
        for raw in FOLDING_CORPUS:
            expected = textnorm.normalize_fa(raw)
            actual = connection.execute(
                text("SELECT public.azir_normalize_fa(:value)"), {"value": raw}
            ).scalar()
            assert actual == expected, f"{raw!r}: SQL={actual!r} python={expected!r}"
            joined_expected = textnorm.normalize_fa(raw, half_space="join")
            joined_actual = connection.execute(
                text("SELECT public.azir_normalize_fa(:value, 'join')"), {"value": raw}
            ).scalar()
            assert joined_actual == joined_expected, f"{raw!r} (join)"


def test_search_finds_both_scripts(db_repository) -> None:
    persian = db_repository.search("تبریز", types=("place",), limit=5)  # type: ignore[attr-defined]
    english = db_repository.search("Tabriz", types=("place",), limit=5)  # type: ignore[attr-defined]
    assert persian and persian[0].entity.id == "plc_tabriz"
    assert english and english[0].entity.id == "plc_tabriz"
    # Typing without the half-space must still match.
    glued = db_repository.search("صفیالدین", types=("person",), limit=5)  # type: ignore[attr-defined]
    assert glued and glued[0].entity.id == "prs_sheikh_safi"


def test_search_respects_the_temporal_window(db_repository) -> None:
    hits = db_repository.search(  # type: ignore[attr-defined]
        "چالدران", types=("event",), window=TimeWindow.at(1200), limit=10
    )
    assert all(hit.entity.id != "evt_battle_chaldiran" for hit in hits)


# ------------------------------------------------------------------ seeder


def test_seeding_twice_is_idempotent(db_repository) -> None:
    from azir.repositories.postgis import build_engine, seed_database

    engine = build_engine(DB_URL or "")
    first = seed_database(engine, FIXTURES_DIR)
    second = seed_database(engine, FIXTURES_DIR)
    engine.dispose()
    assert first.as_dict()["rows"] == second.as_dict()["rows"]
    assert first.tables["entity_geometry"] == second.tables["entity_geometry"]


def test_seed_stamps_published_editorial_state_with_the_database_clock(db_repository) -> None:
    import sqlalchemy as sa

    from azir.repositories.postgis import build_engine, schema

    engine = build_engine(DB_URL or "")
    with engine.connect() as connection:
        published_at = connection.scalar(
            sa.select(schema.editorial_state.c.published_at).where(
                schema.editorial_state.c.entity_type == "event",
                schema.editorial_state.c.entity_id == "evt_battle_chaldiran",
            )
        )
    engine.dispose()

    assert published_at is not None


def test_unpublished_entities_are_not_served(db_repository) -> None:
    page = query(db_repository, zoom=6.0, window=TimeWindow.at(1500))
    records = list(page.entities.values())
    assert all(record.is_public for record in records)
    inclusive = query(db_repository, zoom=6.0, window=TimeWindow.at(1500), include_unpublished=True)
    assert len(inclusive.rows) >= len(page.rows)


def test_entity_types_are_all_addressable(db_repository) -> None:
    for entity_type in EntityType:
        if entity_type in (EntityType.SOURCE, EntityType.PERIOD):
            continue
        records = db_repository.list_entities(entity_type, limit=5)  # type: ignore[attr-defined]
        assert isinstance(records, list)
