"""HTTP contract tests for /api/v1 (docs/05, ADR-0009).

These assert the *contract the frontend depends on*: media types, cache headers, locale handling,
problem+json errors, field budgets and pagination. If one of these breaks, the map breaks.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

GEOJSON = "application/geo+json"
PROBLEM = "application/problem+json"


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    ready = client.get("/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] in {"ready", "degraded"}
    assert body["driver"] == "fixtures"
    assert body["counts"]["place"] > 0


def test_every_response_carries_request_and_driver_headers(client: TestClient) -> None:
    response = client.get("/api/v1/meta")
    assert response.headers["X-Request-Id"]
    assert response.headers["X-AZIR-Driver"] == "fixtures"


def test_meta_bootstraps_the_frontend(client: TestClient) -> None:
    """No historical constant may live in the frontend, so /meta must carry them all."""
    body = client.get("/api/v1/meta").json()
    assert body["study_area"]["name_fa"] and body["study_area"]["bbox"]
    assert [locale["code"] for locale in body["locales"]] == ["fa", "en"]
    assert body["locales"][0]["dir"] == "rtl"
    assert {level["level"] for level in body["zoom_levels"]} == {
        "L0_region", "L1_area", "L2_city", "L3_fabric", "L4_monument",
    }
    layer_ids = {layer["id"] for layer in body["layers"]}
    assert {"places", "buildings", "political_entities", "modern_borders"} <= layer_ids
    assert body["timeline"]["floor"] < body["timeline"]["ceil"]
    assert body["coverage"]["provisional_geometries"] > 0
    assert "disclaimer" in body and body["license"]["data"]


def test_meta_layers_endpoint_matches_meta(client: TestClient) -> None:
    from_meta = client.get("/api/v1/meta").json()["layers"]
    from_layers = client.get("/api/v1/atlas/layers").json()["data"]
    assert [layer["id"] for layer in from_meta] == [layer["id"] for layer in from_layers]


def test_features_is_geojson_with_cache_headers(client: TestClient) -> None:
    response = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1450})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(GEOJSON)
    assert "max-age=" in response.headers["Cache-Control"]
    assert response.headers["ETag"].startswith('"')
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert body["meta"]["zoom_level"] == "L1_area"
    assert body["meta"]["payload_bytes"] <= 512 * 1024
    for feature in body["features"]:
        assert feature["type"] == "Feature"
        # certainty ships with every feature, even on the smallest budget (AGENTS.md rule 8)
        assert "certainty" in feature["properties"]


def test_etag_is_stable_and_content_dependent(client: TestClient) -> None:
    params = {"zoom": 7, "t": 1450}
    first = client.get("/api/v1/atlas/features", params=params)
    second = client.get("/api/v1/atlas/features", params=params)
    third = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1650})
    assert first.headers["ETag"] == second.headers["ETag"]
    assert first.headers["ETag"] != third.headers["ETag"]

    conditional = client.get(
        "/api/v1/atlas/features",
        params=params,
        headers={"If-None-Match": first.headers["ETag"]},
    )
    assert conditional.status_code == 304
    assert conditional.content == b""
    assert conditional.headers["ETag"] == first.headers["ETag"]


def test_invalid_near_coordinates_are_rejected_before_the_repository(client: TestClient) -> None:
    for near in ("181,38", "48,91", "nan,38", "48,inf"):
        response = client.get(
            "/api/v1/atlas/features",
            params={"zoom": 7, "near": near, "radius_km": 10},
        )
        assert response.status_code == 422, near
        assert response.json()["type"] == "https://errors.azir.dev/validation"


def test_map_labels_follow_sourced_time_bounded_name_variants(client: TestClient) -> None:
    historical = client.get(
        "/api/v1/atlas/features",
        params={"zoom": 7, "t": 1930, "locale": "fa", "layers": "places"},
    ).json()
    current = client.get(
        "/api/v1/atlas/features",
        params={"zoom": 7, "t": 2000, "locale": "fa", "layers": "places"},
    ).json()

    def label(payload: dict[str, Any]) -> str:
        return next(
            feature["properties"]["label"]
            for feature in payload["features"]
            if feature["id"] == "plc_urmia"
        )

    assert label(historical) == "رضائیه"
    assert label(current) == "ارومیه"


def test_time_changes_the_map(client: TestClient) -> None:
    def ids(year: int) -> set[str]:
        body = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": year}).json()
        return {feature["id"] for feature in body["features"]}

    assert "pol_safavid" not in ids(1400)
    assert "pol_safavid" in ids(1510)
    assert ids(1400) != ids(1510)


def test_semantic_zoom_ladder(client: TestClient) -> None:
    region = client.get("/api/v1/atlas/features", params={"zoom": 5, "t": 1510}).json()
    fabric = client.get(
        "/api/v1/atlas/features",
        params={"zoom": 13, "t": 1510, "bbox": "48.2,38.2,48.4,38.3"},
    ).json()
    assert region["meta"]["zoom_level"] == "L0_region"
    assert fabric["meta"]["zoom_level"] == "L3_fabric"
    assert any(f["properties"]["kind"] in {"mosque", "mausoleum", "bazaar", "building"} for f in fabric["features"])
    assert not any(f["properties"]["kind"] in {"mosque", "bazaar"} for f in region["features"])


def test_all_time_features_are_not_limited_to_the_default_year(client: TestClient) -> None:
    body = client.get("/api/v1/atlas/features", params={"zoom": 9, "all_time": True}).json()
    ids = {feature["id"] for feature in body["features"]}
    assert "plc_babak_castle" in ids
    assert body["meta"]["time"]["from"] < 900
    assert body["meta"]["time"]["to"] >= 2000


def test_non_gregorian_calendars_are_accepted(client: TestClient) -> None:
    ah = client.get("/api/v1/atlas/features", params={"zoom": 7, "cal": "islamic_lunar", "t": 907}).json()
    assert ah["meta"]["time"]["calendar"] == "islamic_lunar"
    assert 1500 <= ah["meta"]["time"]["from"] <= 1503
    sh = client.get("/api/v1/atlas/features", params={"zoom": 7, "cal": "persian_solar", "t": 1357}).json()
    assert 1978 <= sh["meta"]["time"]["from"] <= 1979


@pytest.mark.parametrize(
    "params",
    [
        {"t": 1514},
        {"t": 907, "cal": "islamic_lunar"},
        {"t": 1357, "cal": "persian_solar"},
        {"t": 1514, "cal": "julian"},
        {"from": 900, "to": 910, "cal": "islamic_lunar", "mode": "overlaps"},
        {"from": 1500, "to": 1520, "mode": "during"},
    ],
)
def test_the_window_endpoint_agrees_with_features(client: TestClient, params: dict[str, Any]) -> None:
    """Tiles filter client-side, so "what does this calendar year mean?" must be the server's answer.

    The two endpoints share ``_build_window``; this pins that they cannot drift, because a frontend
    that converted calendars itself would be doing history in the browser (AGENTS.md rule 5) and
    would silently disagree with the GeoJSON path.
    """
    window = client.get("/api/v1/atlas/window", params=params).json()["data"]
    features = client.get("/api/v1/atlas/features", params={**params, "zoom": 7}).json()
    assert window["from"] == features["meta"]["time"]["from"]
    assert window["to"] == features["meta"]["time"]["to"]
    assert window["mode"] == features["meta"]["time"]["mode"]
    assert window["normalized_calendar"] == "gregorian_proleptic"


def test_timeline_exposes_selected_calendar_labels_without_changing_bucket_coordinates(client: TestClient) -> None:
    gregorian = client.get("/api/v1/atlas/timeline", params={"from": 1500, "to": 1520}).json()
    hijri = client.get(
        "/api/v1/atlas/timeline",
        params={"from": 1500, "to": 1520, "cal": "islamic_lunar"},
    ).json()
    assert [(row["from"], row["to"]) for row in hijri["data"]] == [
        (row["from"], row["to"]) for row in gregorian["data"]
    ]
    assert hijri["meta"]["display_calendar"] == "islamic_lunar"
    assert all("display_from" in row and "display_to" in row for row in hijri["data"])


def test_calendar_year_preserves_the_historical_instant(client: TestClient) -> None:
    solar = client.get("/api/v1/atlas/calendar-year", params={"t": 2024, "cal": "persian_solar"}).json()["data"]
    hijri = client.get("/api/v1/atlas/calendar-year", params={"t": 2024, "cal": "islamic_lunar"}).json()["data"]
    assert solar == {"year": 1403, "calendar": "persian_solar", "normalized_year": 2024}
    assert hijri["year"] == 1445
    assert hijri["normalized_year"] == 2024


def test_the_window_endpoint_normalizes_calendars(client: TestClient) -> None:
    hijri = client.get("/api/v1/atlas/window", params={"t": 900, "cal": "islamic_lunar"}).json()["data"]
    assert (hijri["from"], hijri["to"]) == (1494, 1495)  # AH 900 spans two Gregorian years
    jalali = client.get("/api/v1/atlas/window", params={"from": 1, "to": 1, "cal": "persian_solar"}).json()["data"]
    assert jalali["from"] == 622  # AP 1 begins in 622 CE
    plain = client.get("/api/v1/atlas/window", params={"t": 1514}).json()["data"]
    assert (plain["from"], plain["to"], plain["mode"]) == (1514, 1514, "at")


def test_locale_switches_labels_and_direction(client: TestClient) -> None:
    fa = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1510, "locale": "fa"}).json()
    en = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1510, "locale": "en"}).json()
    fa_tabriz = next(f for f in fa["features"] if f["id"] == "plc_tabriz")
    en_tabriz = next(f for f in en["features"] if f["id"] == "plc_tabriz")
    assert fa_tabriz["properties"]["label"] == "تبریز"
    assert fa_tabriz["properties"]["dir"] == "rtl"
    assert en_tabriz["properties"]["label"] == "Tabriz"
    assert en_tabriz["properties"]["dir"] == "ltr"
    assert fa_tabriz["properties"]["label_secondary"] == "Tabriz"


def test_unsupported_locale_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/meta", params={"locale": "de"})
    assert response.status_code == 422


def test_field_budget_degrades_gracefully(client: TestClient) -> None:
    minimal = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1510, "fields": "min"}).json()
    full = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1510, "fields": "full"}).json()
    assert minimal["meta"]["payload_bytes"] < full["meta"]["payload_bytes"]
    assert "summary" not in minimal["features"][0]["properties"]
    assert "summary" in full["features"][0]["properties"]
    assert minimal["meta"]["truncated"] is False


def test_limit_and_cursor_pagination(client: TestClient) -> None:
    page_one = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1510, "limit": 3}).json()
    assert len(page_one["features"]) == 3
    cursor = page_one["meta"]["next_cursor"]
    assert cursor
    page_two = client.get(
        "/api/v1/atlas/features", params={"zoom": 7, "t": 1510, "limit": 3, "cursor": cursor}
    ).json()
    first_ids = {f["id"] for f in page_one["features"]}
    second_ids = {f["id"] for f in page_two["features"]}
    assert first_ids.isdisjoint(second_ids)
    assert page_two["features"][0]["properties"]["rank"] <= page_one["features"][-1]["properties"]["rank"]


def test_unknown_layer_is_a_validation_problem(client: TestClient) -> None:
    response = client.get("/api/v1/atlas/features", params={"zoom": 7, "layers": "unicorns"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM)
    body = response.json()
    assert body["type"].startswith("https://errors.azir.dev/")
    assert body["status"] == 422 and body["title"]
    assert body["instance"]


def test_malformed_bbox_is_a_validation_problem(client: TestClient) -> None:
    response = client.get("/api/v1/atlas/features", params={"zoom": 7, "bbox": "not-a-bbox"})
    assert response.status_code == 422
    assert response.json()["type"] == "https://errors.azir.dev/validation"


def test_timeline_buckets_and_notable_events(client: TestClient) -> None:
    body = client.get(
        "/api/v1/atlas/timeline", params={"from": 1200, "to": 1600, "bucket": 100}
    ).json()
    assert [bucket["from"] for bucket in body["data"]] == [1200, 1300, 1400, 1500, 1600]
    assert body["meta"]["bucket"] == 100
    notable = [event for bucket in body["data"] for event in bucket["notable"]]
    assert any(event["id"] == "evt_battle_chaldiran" for event in notable)


def test_timeline_auto_bucketing(client: TestClient) -> None:
    narrow = client.get("/api/v1/atlas/timeline", params={"from": 1500, "to": 1520}).json()
    assert narrow["meta"]["bucket"] <= 5
    wide = client.get("/api/v1/atlas/timeline", params={"from": -500, "to": 2000}).json()
    assert wide["meta"]["bucket"] >= 100


def test_entity_detail_links_everything(client: TestClient) -> None:
    body = client.get("/api/v1/entities/place/sheikh-safi-complex").json()
    assert body["id"] == "plc_sheikh_safi_complex"
    assert body["names"]["display"] and body["names"]["display_secondary"]
    assert body["geometry"]["geojson"]["type"] in {"Point", "Polygon"}
    assert body["counts"]["articles"] >= 1 and body["counts"]["sources"] >= 1
    assert body["relationships"], "an entity with no graph edges is a data bug"
    assert body["sources"] and all(source["id"].startswith("src_") for source in body["sources"])
    assert body["articles"] and body["articles"][0]["href"].startswith("/api/v1/articles/")
    assert body["links"]["map"].startswith("/?entity=plc_sheikh_safi_complex")
    assert body["links"]["self"].endswith("/api/v1/entities/place/sheikh-safi-complex")


def test_disagreements_are_exposed_with_both_positions(client: TestClient) -> None:
    body = client.get("/api/v1/entities/political_entity/safavid-dynasty").json()
    assert body["disagreements"], "the disputed Safavid end date must not be flattened away"
    group = body["disagreements"][0]
    assert group["topic"] and group["topic_en"]
    values = {position["value"] for position in group["positions"]}
    assert {"1722", "1736"} <= values
    for position in group["positions"]:
        assert position["status"] == "disputed"
        assert position["note"]


def test_entity_detail_honours_locale(client: TestClient) -> None:
    fa = client.get("/api/v1/entities/place/plc_tabriz", params={"locale": "fa"}).json()
    en = client.get("/api/v1/entities/place/plc_tabriz", params={"locale": "en"}).json()
    assert fa["names"]["display"] == "تبریز" and en["names"]["display"] == "Tabriz"
    assert fa["names"]["display_secondary"] == "Tabriz"


def test_unknown_entity_is_problem_json_404(client: TestClient) -> None:
    response = client.get("/api/v1/entities/place/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM)
    assert response.json()["status"] == 404


def test_unknown_entity_type_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/entities/unicorn/plc_tabriz")
    assert response.status_code == 404


def test_related_endpoint_walks_the_graph(client: TestClient) -> None:
    body = client.get("/api/v1/entities/place/plc_tabriz/related", params={"depth": 1}).json()
    assert body["data"]
    assert all(row["predicate"] and row["label"] for row in body["data"])


def test_articles_and_single_article(client: TestClient) -> None:
    listing = client.get("/api/v1/articles").json()
    assert listing["data"] and listing["data"][0]["id"].startswith("art_")
    body = client.get("/api/v1/articles/rise-of-the-safavids").json()
    assert body["article"]["body_md"].strip()
    assert body["article"]["body_en_md"].strip()
    assert body["article"]["title_en"]
    assert "body_md" not in body, "prose lives in the article block, facts in the entity shape"
    assert [row["id"] for row in body["article"]["entities"]][:2] == ["pol_safaviyya", "pol_safavid"]
    assert body["article"]["map_state"]["time"]["from"] == 1300
    # Article -> Map deep link must carry the article's own time window, not a default year.
    assert "from=1300" in body["links"]["map"]
    assert body["relationships"], "an article that duplicates entity data instead of linking is a bug"


def test_unpublished_articles_are_not_served(client: TestClient) -> None:
    listing = client.get("/api/v1/articles").json()
    assert all(row["status"] == "published" for row in listing["data"])


def test_sources_endpoint(client: TestClient) -> None:
    body = client.get("/api/v1/sources").json()
    assert body["data"]
    assert all(row["id"].startswith("src_") for row in body["data"])
    assert any(row["reliability"] == "primary" for row in body["data"])


def test_search_persian_and_english(client: TestClient) -> None:
    fa = client.get("/api/v1/search", params={"q": "صفوی"}).json()
    assert fa["data"][0]["label"].startswith("صفوی")
    en = client.get("/api/v1/search", params={"q": "tabriz", "locale": "en"}).json()
    assert en["data"][0]["label"] == "Tabriz"
    assert en["meta"]["normalized_query"] == "tabriz"


def test_search_is_type_filtered(client: TestClient) -> None:
    body = client.get("/api/v1/search", params={"q": "تبریز", "types": "event"}).json()
    assert body["data"] and all(row["entity_type"] == "event" for row in body["data"])
    assert client.get("/api/v1/search", params={"q": "تبریز", "types": "unicorn"}).status_code == 404


def test_search_rejects_empty_query(client: TestClient) -> None:
    assert client.get("/api/v1/search", params={"q": ""}).status_code == 422


def test_atlas_context_answers_what_was_here(client: TestClient) -> None:
    body = client.get("/api/v1/atlas/context", params={"place_id": "plc_ardabil"}).json()
    assert body["data"]
    assert {row["predicate"] for row in body["data"]} & {"contains", "birthplace_of", "historically_in"}


def test_atlas_context_by_point(client: TestClient) -> None:
    body = client.get(
        "/api/v1/atlas/context", params={"lat": 38.2464, "lon": 48.2934, "radius_km": 30}
    ).json()
    assert body["data"]
    assert any(row["predicate"] == "located_near" for row in body["data"])


def test_atlas_context_requires_a_target(client: TestClient) -> None:
    assert client.get("/api/v1/atlas/context").status_code == 422


def test_structured_query_endpoint(client: TestClient) -> None:
    body = client.get(
        "/api/v1/atlas/query",
        params={"kinds": "mosque,mausoleum", "near": "48.2934,38.2464", "radius_km": 50, "t": 1550},
    ).json()
    assert body["features"]
    assert body["meta"]["applied_filters"]["radius_km"] == 50
    assert {feature["properties"]["kind"] for feature in body["features"]} <= {"mosque", "mausoleum", "bazaar", "building"}


def test_openapi_is_served_under_the_versioned_prefix(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    paths: list[str] = list(response.json()["paths"])
    assert all(path.startswith("/api/v1") or path in {"/healthz", "/readyz", "/"} for path in paths)


def test_methods_other_than_get_are_not_allowed(client: TestClient) -> None:
    """The public API v1 is read-only; editorial writes live behind a separate surface (ADR-0010)."""
    response = client.post("/api/v1/atlas/features")
    assert response.status_code == 405


@pytest.mark.parametrize("path", ["/api/v1/meta", "/api/v1/atlas/layers", "/api/v1/atlas/zoom-levels"])
def test_bootstrap_endpoints_never_require_a_body(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_payload_is_json_serialisable_and_ascii_free(client: TestClient) -> None:
    """Persian must travel as UTF-8, not escaped, so payloads stay small and readable."""
    response = client.get("/api/v1/atlas/features", params={"zoom": 7, "t": 1510})
    assert "تبریز" in response.text or "\\u" not in response.text
    body: Any = response.json()
    assert body["features"]
