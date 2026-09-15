from __future__ import annotations

from itertools import pairwise

from azir.domain.enums import EntityType
from azir.domain.semantic_zoom import (
    BANDS,
    band_for,
    compute_rank,
    layer_for,
    max_zoom_for,
    min_rank_for,
    min_zoom_for,
)


def test_bands_are_contiguous_and_ordered() -> None:
    for left, right in pairwise(BANDS):
        assert left.max_zoom == right.min_zoom
        assert left.min_rank >= right.min_rank


def test_semantic_levels() -> None:
    assert band_for(4).level.value == "L0_region"
    assert band_for(7.5).level.value == "L1_area"
    assert band_for(10).level.value == "L2_city"
    assert band_for(13.5).level.value == "L3_fabric"
    assert band_for(17).level.value == "L4_monument"


def test_lod_tolerance_shrinks_with_zoom() -> None:
    tolerances = [band.lod_tolerance for band in BANDS]
    assert tolerances == sorted(tolerances, reverse=True)


def test_rank_is_monotonic_in_evidence() -> None:
    base = {"importance": 0.5, "kind": "city", "assertion_count": 2,
            "article_count": 0, "period_coverage": 2}
    poor = compute_rank(source_count=0, **base)
    rich = compute_rank(source_count=20, **base)
    assert rich > poor
    assert 0 <= poor <= 100 and 0 <= rich <= 100


def test_rank_rewards_editorial_importance_most() -> None:
    low = compute_rank(importance=0.1, kind="city", source_count=10, assertion_count=10, article_count=3)
    high = compute_rank(importance=0.9, kind="city", source_count=10, assertion_count=10, article_count=3)
    assert high - low > 20


def test_min_zoom_follows_rank() -> None:
    assert min_zoom_for(95, EntityType.PLACE, "city") <= 6
    assert min_zoom_for(30, EntityType.PLACE, "village") >= 9
    assert min_zoom_for(95, EntityType.PERSON, None) >= 9  # people never at region level


def test_layers_are_assigned_by_kind_not_by_ui() -> None:
    assert layer_for(EntityType.PLACE, "mosque") == "buildings"
    assert layer_for(EntityType.PLACE, "archaeological_site") == "archaeology"
    assert layer_for(EntityType.PLACE, "route") == "routes"
    assert layer_for(EntityType.EVENT, "battle") == "battles"
    assert layer_for(EntityType.EVENT, "earthquake") == "events"
    assert layer_for(EntityType.POLITICAL_ENTITY, "dynasty") == "political_entities"
    assert layer_for(EntityType.PERSON, None) == "people"


def test_polygonal_kinds_stop_before_monument_zoom() -> None:
    assert max_zoom_for("region") < max_zoom_for("building")
    assert min_rank_for(20) == 0.0
