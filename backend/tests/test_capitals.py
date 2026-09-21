"""Regression tests for capital markers (polity name placed at its capital).

The feature contract:
* Political extents are not labelled on their polygon; their name appears at the capital point
  (is_capital=true, label_anchor=true, style_color set, geometry Point).
* The backend decides which capital is valid when; the frontend only renders (AGENTS.md rule 5).
* No hardcoded historical names in frontend; names come from sourced temporally bounded backend.
* all_time mode merges distinct places per polity (Safavid 2, Ilkhanate 3).
* time-filtered mode returns only overlapping capitals (1505 Safavid→Tabriz only).
* /api/v1/meta political_entities[].capitals[] is data-driven.
* Tile contract 1.3.0 includes is_capital, capital_place_id, capital_label, style_color.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from azir.services.tiles import TILE_PROPERTIES, TILESET_VERSION


def test_capital_marker_appears_at_valid_time(client: TestClient) -> None:
    # 1505 is inside Safavid Tabriz period 1501-1548
    body = client.get(
        "/api/v1/atlas/features",
        params={"t": 1505, "zoom": 7, "layers": "political_entities", "limit": 100},
    ).json()
    caps = [f for f in body["features"] if f["properties"].get("is_capital")]
    safavid = [f for f in caps if f["properties"]["id"] == "pol_safavid"]
    assert len(safavid) == 1, "Safavid at 1505 should have exactly one capital"
    assert safavid[0]["properties"]["capital_place_id"] == "plc_tabriz"
    assert safavid[0]["geometry"]["type"] == "Point"
    assert safavid[0]["properties"]["label_anchor"] is True
    assert safavid[0]["properties"]["is_capital"] is True
    assert safavid[0]["properties"]["style_color"]
    # Label is polity display name, secondary is capital place name, from backend
    assert safavid[0]["properties"]["label"]
    assert safavid[0]["properties"]["capital_label"]


def test_all_time_capitals_are_distinct_places(client: TestClient) -> None:
    body = client.get(
        "/api/v1/atlas/features",
        params={"all_time": True, "zoom": 7, "layers": "political_entities", "limit": 100},
    ).json()
    caps = [f for f in body["features"] if f["properties"].get("is_capital")]

    def distinct_places(polity_id: str) -> set[str]:
        return {f["properties"]["capital_place_id"] for f in caps if f["properties"]["id"] == polity_id}

    # Fixture data: Safavid has Tabriz (1501-1548) and Qazvin (1548-1601) → 2 distinct
    assert distinct_places("pol_safavid") == {"plc_tabriz", "plc_qazvin"}
    # Ilkhanate: Maragheh 1256-1265, Tabriz 1265-1305, Soltaniyeh 1295-1345 → 3 distinct
    assert distinct_places("pol_ilkhanate") == {"plc_maragheh", "plc_tabriz", "plc_soltaniyeh"}


def test_meta_political_entities_include_sourced_capitals(client: TestClient) -> None:
    meta = client.get("/api/v1/meta?locale=en").json()
    polities = {p["id"]: p for p in meta["political_entities"]}
    assert "pol_safavid" in polities
    assert "pol_ilkhanate" in polities
    safavid = polities["pol_safavid"]
    assert "capitals" in safavid
    assert len(safavid["capitals"]) == 2
    assert {c["place_id"] for c in safavid["capitals"]} == {"plc_tabriz", "plc_qazvin"}
    for cap in safavid["capitals"]:
        assert cap["label"]
        assert cap["t_from"] is not None
        assert cap["confidence"] in {"high", "medium", "low"}
        assert cap["status"] in {"accepted", "disputed", "proposed"}

    ilkhanate = polities["pol_ilkhanate"]
    assert len(ilkhanate["capitals"]) == 3


def test_tile_contract_includes_capital_properties() -> None:
    assert TILESET_VERSION == "1.3.0"
    assert TILE_PROPERTIES["is_capital"] == "Boolean"
    assert TILE_PROPERTIES["capital_place_id"] == "String"
    assert TILE_PROPERTIES["capital_label"] == "String"
    # style_color is required for polity colour key and capital markers
    assert "style_color" in TILE_PROPERTIES
    assert "label_anchor" in TILE_PROPERTIES
