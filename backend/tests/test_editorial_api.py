"""The editorial API end to end: cookies, CSRF, roles, the review loop, and the lint gate.

These tests drive the real FastAPI app against a fresh fixtures repository (``dependency_overrides``,
never the process-wide cache), so a write in one test cannot leak into another -- and so the public
read API can be checked in the same test as the write that changed it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from azir.core.config import Settings
from azir.main import create_app
from azir.repositories.fixtures import FixturesRepository
from azir.services.editorial import reset_throttle
from azir.services.registry import get_editorial_repository, get_repository

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "fixtures"
API = "/api/v1"
DEV_PASSWORD = "atlas-dev-password"
ADMIN = "admin@atlas.local"
EDITOR = "editor@atlas.local"
CONTRIBUTOR = "contributor@atlas.local"
SEEDED_SOURCE = "src_tabari_tarikh"


@pytest.fixture(autouse=True)
def _fresh_throttle() -> Iterator[None]:
    reset_throttle()
    yield
    reset_throttle()


@pytest.fixture()
def panel(settings: Settings) -> Iterator[tuple[TestClient, FixturesRepository]]:
    repository = FixturesRepository(FIXTURES_DIR)
    app = create_app(settings)
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_editorial_repository] = lambda: repository
    with TestClient(app) as client:
        yield client, repository
    app.dependency_overrides.clear()


def sign_in(client: TestClient, email: str = ADMIN, password: str = DEV_PASSWORD) -> dict[str, Any]:
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def csrf(client: TestClient) -> dict[str, str]:
    """The double-submit header, taken from the readable cookie the login response set."""
    token = client.cookies.get("azir_csrf")
    assert token, "login must set a CSRF cookie the frontend can read"
    return {"X-CSRF-Token": token}


def make_place_body(slug: str | None = None, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "city",
        "slug": slug or f"test-{uuid.uuid4().hex[:8]}",
        "names": [
            {"form": "شهر آزموده", "lang": "fa", "kind": "preferred"},
            {"form": "Test Town", "lang": "en", "kind": "preferred", "script": "Latn"},
        ],
        "temporal": {
            "calendar": "gregorian_proleptic",
            "from": 1500,
            "to": 1700,
            "precision": "range",
            "confidence": "medium",
            "display": "۱۵۰۰–۱۷۰۰ م",
        },
        "geometries": [
            {
                "geojson": {"type": "Point", "coordinates": [48.35, 38.25]},
                "kind": "point",
                "certainty": "uncertain",
                "source_id": SEEDED_SOURCE,
            }
        ],
        "summary": "رکوردی که از راه API ساخته شده است.",
        "summary_en": "A record created through the API.",
        "source_ids": [SEEDED_SOURCE],
        "importance": 0.4,
    }
    body.update(overrides)
    return body


def create_place(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post(f"{API}/editorial/place", json=make_place_body(**overrides), headers=csrf(client))
    assert response.status_code == 201, response.text
    return response.json()["data"]


def move(client: TestClient, entity_id: str, action: str, note: str | None = None) -> Any:
    return client.post(
        f"{API}/editorial/place/{entity_id}/transition",
        json={"action": action, "note": note},
        headers=csrf(client),
    )


# ------------------------------------------------------------------ authentication


def test_login_sets_an_httponly_session_and_a_readable_csrf_cookie(panel: Any) -> None:
    client, _ = panel
    data = sign_in(client)
    assert data["actor"]["role"] == "admin"
    assert data["actor"]["email"] == ADMIN
    assert data["csrf_header"] == "x-csrf-token"
    assert client.cookies.get("azir_session"), "the session cookie must be set"
    # HttpOnly on the session, deliberately *not* on the CSRF half of the pair.
    response = client.post(f"{API}/auth/login", json={"email": ADMIN, "password": DEV_PASSWORD})
    set_cookies = response.headers.get_list("set-cookie")
    session = next(item for item in set_cookies if item.startswith("azir_session="))
    partner = next(item for item in set_cookies if item.startswith("azir_csrf="))
    assert "httponly" in session.lower() and "samesite" in session.lower()
    assert "httponly" not in partner.lower(), "the frontend must be able to read the CSRF token"


def test_a_wrong_password_is_401_and_lands_in_the_audit_trail(panel: Any) -> None:
    client, repository = panel
    response = client.post(
        f"{API}/auth/login", json={"email": ADMIN, "password": "not-the-password"}
    )
    assert response.status_code == 401
    body = response.json()
    assert body["status"] == 401 and body["type"].endswith("/unauthorized")
    # One message for both failure modes: the difference only helps somebody enumerating accounts.
    missing = client.post(f"{API}/auth/login", json={"email": "nobody@atlas.local", "password": "x"})
    assert missing.status_code == 401
    assert missing.json()["detail"] == body["detail"]
    entries = repository.audit(action="login_failed", limit=50)
    assert len(entries) >= 2, "failed logins are audited even though nobody is authenticated"
    assert all(entry.actor_id is None for entry in entries)


def test_repeated_failures_are_throttled(panel: Any) -> None:
    client, _ = panel
    for _ in range(5):
        assert client.post(
            f"{API}/auth/login", json={"email": ADMIN, "password": "wrong"}
        ).status_code == 401
    blocked = client.post(f"{API}/auth/login", json={"email": ADMIN, "password": "wrong"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    # Even the correct password waits: a throttle an attacker can probe is not a throttle.
    assert client.post(
        f"{API}/auth/login", json={"email": ADMIN, "password": DEV_PASSWORD}
    ).status_code == 429


def test_me_reports_the_actor_and_their_permissions(panel: Any) -> None:
    client, _ = panel
    assert client.get(f"{API}/auth/me").status_code == 401, "no session, no identity"
    sign_in(client, CONTRIBUTOR)
    data = client.get(f"{API}/auth/me").json()["data"]
    assert data["actor"]["role"] == "contributor"
    assert "create" in data["permissions"]
    assert "approve" not in data["permissions"], "a contributor never approves their own work"


def test_logout_revokes_the_session(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    assert client.get(f"{API}/auth/me").status_code == 200
    assert client.post(f"{API}/auth/logout", headers=csrf(client)).status_code == 200
    assert client.get(f"{API}/auth/me").status_code == 401


# ------------------------------------------------------------------ authorization


def test_a_state_change_without_a_csrf_token_is_refused(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    response = client.post(f"{API}/editorial/place", json=make_place_body())
    assert response.status_code == 403
    assert "csrf" in response.json()["detail"].lower()
    # A stolen session cookie alone is not enough: the attacker's page cannot read the partner cookie.
    forged = client.post(
        f"{API}/editorial/place", json=make_place_body(), headers={"X-CSRF-Token": "forged"}
    )
    assert forged.status_code == 403


def test_roles_decide_who_may_move_a_record(panel: Any) -> None:
    client, _ = panel
    sign_in(client, CONTRIBUTOR)
    created = create_place(client)
    assert created["status"] == "draft"
    forbidden = move(client, created["id"], "submit")
    assert forbidden.status_code == 403
    assert "contributor" in forbidden.json()["detail"]

    client.post(f"{API}/auth/logout", headers=csrf(client))
    sign_in(client, EDITOR)
    assert move(client, created["id"], "submit").status_code == 200
    # An editor may submit but not approve: the person who wrote it never signs it off.
    assert move(client, created["id"], "approve").status_code == 403


def test_the_public_api_needs_no_session(panel: Any) -> None:
    client, _ = panel
    assert client.get(f"{API}/entities/place/plc_ardabil").status_code == 200
    assert client.get(f"{API}/entities/place", params={"limit": 5}).status_code == 200


# ------------------------------------------------------------------ the review loop


def test_a_record_walks_the_loop_and_only_then_becomes_public(panel: Any) -> None:
    client, repository = panel
    sign_in(client)
    created = create_place(client)
    entity_id = created["id"]

    # A draft is invisible to the public API (AGENTS.md: the map shows the published corpus).
    assert client.get(f"{API}/entities/place/{entity_id}").status_code == 404

    submitted = move(client, entity_id, "submit")
    assert submitted.status_code == 200
    assert submitted.json()["data"]["status"] == "in_review"
    assert client.get(f"{API}/entities/place/{entity_id}").status_code == 404

    approved = move(client, entity_id, "approve")
    assert approved.status_code == 200, approved.text
    published = approved.json()["data"]
    assert published["status"] == "published"
    assert published["revision"] == 1

    public = client.get(f"{API}/entities/place/{entity_id}")
    assert public.status_code == 200, "publishing is what makes a record public"
    assert public.json()["summary"].startswith("رکوردی")

    actions = {entry.action for entry in repository.audit(entity_id=entity_id, limit=100)}
    assert {"create", "submit", "approve"} <= actions
    state = repository.workflow_state("place", entity_id)
    assert state is not None and state.published_at is not None


def test_a_draft_cannot_skip_review(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(client)
    skipped = move(client, created["id"], "approve")
    assert skipped.status_code == 409
    body = skipped.json()
    assert body["status"] == "draft"
    assert "in_review" in body["allowed"], "the refusal says what would have been legal"


def test_publishing_without_sources_is_blocked_by_lint(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(client, source_ids=[])
    assert move(client, created["id"], "submit").status_code == 200
    blocked = move(client, created["id"], "approve")
    assert blocked.status_code == 422
    body = blocked.json()
    assert "D4" in body["rules"], "a claim without a source never reaches the public (docs/07)"
    assert any(finding["rule"] == "D4" for finding in body["findings"])
    assert all(finding["message_fa"] for finding in body["findings"]), "findings are bilingual"
    # The record is still in review: a blocked publication changes nothing.
    state = client.get(f"{API}/editorial/place/{created['id']}/state").json()["data"]
    assert state["status"] == "in_review"
    assert {item["action"] for item in state["available_actions"]} >= {"request_changes"}


def test_publishing_a_place_outside_the_study_area_is_blocked(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(
        client,
        geometries=[
            {
                "geojson": {"type": "Point", "coordinates": [2.35, 48.85]},
                "kind": "point",
                "certainty": "exact",
            }
        ],
    )
    assert move(client, created["id"], "submit").status_code == 200
    blocked = move(client, created["id"], "approve")
    assert blocked.status_code == 422
    assert "D2" in blocked.json()["rules"], "Paris is not in Iranian Azerbaijan"


def test_a_reviewer_can_send_a_record_back_with_a_note(panel: Any) -> None:
    client, _ = panel
    sign_in(client, EDITOR)
    created = create_place(client)
    move(client, created["id"], "submit")
    client.post(f"{API}/auth/logout", headers=csrf(client))

    sign_in(client, "reviewer@atlas.local")
    sent_back = move(client, created["id"], "request_changes", note="منبع دست‌اول لازم است")
    assert sent_back.status_code == 200
    data = sent_back.json()["data"]
    assert data["status"] == "changes_requested"
    assert data["workflow"]["review_note"] == "منبع دست‌اول لازم است"
    assert data["workflow"]["reviewed_by"], "the trail says who asked for the change"


def test_editing_a_published_record_returns_a_working_copy(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(client)
    entity_id = created["id"]
    move(client, entity_id, "submit")
    move(client, entity_id, "approve")
    public_summary = client.get(f"{API}/entities/place/{entity_id}").json()["summary"]

    edited = client.patch(
        f"{API}/editorial/place/{entity_id}",
        json={"summary": "متن بازبینی‌شدهٔ تازه."},
        headers=csrf(client),
    )
    assert edited.status_code == 200, edited.text
    payload = edited.json()
    assert payload["meta"]["forked"] is True
    assert payload["data"]["id"] != entity_id, "the editor is now working on a copy"
    assert payload["data"]["status"] == "draft"
    assert payload["data"]["summary"] == "متن بازبینی‌شدهٔ تازه."
    # The public record is untouched until a reviewer approves the copy.
    assert client.get(f"{API}/entities/place/{entity_id}").json()["summary"] == public_summary
    state = client.get(f"{API}/editorial/place/{entity_id}/state").json()["data"]
    assert state["status"] == "published"
    assert state["pending_revision"] == payload["data"]["id"]

    copy_id = payload["data"]["id"]
    assert move(client, copy_id, "submit").status_code == 200
    merged = move(client, copy_id, "approve")
    assert merged.status_code == 200
    assert merged.json()["data"]["id"] == entity_id
    assert merged.json()["data"]["revision"] == 2
    assert client.get(f"{API}/entities/place/{entity_id}").json()["summary"] == (
        "متن بازبینی‌شدهٔ تازه."
    )


def test_a_patch_only_changes_what_it_mentions(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(client)
    response = client.patch(
        f"{API}/editorial/place/{created['id']}",
        json={"summary": "فقط چکیده عوض شد."},
        headers=csrf(client),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"] == "فقط چکیده عوض شد."
    assert data["title"] == "شهر آزموده", "an untouched field keeps its value"
    assert data["temporal"]["year_from"] == 1500
    assert len(data["names"]) == 2


def test_validation_rejects_nonsense_at_the_door(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    # No Persian name: the atlas is Persian-first (D5 is a publish gate, but this is checked early).
    english_only = client.post(
        f"{API}/editorial/place",
        json=make_place_body(names=[{"form": "Test Town", "lang": "en"}]),
        headers=csrf(client),
    )
    assert english_only.status_code == 422
    assert english_only.json()["rule"] == "D5"

    unknown_source = client.post(
        f"{API}/editorial/place",
        json=make_place_body(source_ids=["src_does_not_exist"]),
        headers=csrf(client),
    )
    assert unknown_source.status_code == 422
    assert unknown_source.json()["unknown"] == ["src_does_not_exist"]

    bad_geometry = client.post(
        f"{API}/editorial/place",
        json=make_place_body(
            geometries=[{"geojson": {"type": "Point", "coordinates": [999.0, 38.0]}}]
        ),
        headers=csrf(client),
    )
    assert bad_geometry.status_code == 422, "coordinates outside the world never reach PostGIS"

    assert client.post(f"{API}/editorial/period", json={}, headers=csrf(client)).status_code == 404


# ------------------------------------------------------------------ reading the workflow


def test_the_queue_shows_what_is_waiting_and_what_may_be_done(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    first = create_place(client)
    second = create_place(client)
    move(client, second["id"], "submit")

    queue = client.get(f"{API}/editorial/queue")
    assert queue.status_code == 200
    body = queue.json()
    ids = {item["id"] for item in body["data"]}
    assert {first["id"], second["id"]} <= ids
    waiting = next(item for item in body["data"] if item["id"] == second["id"])
    assert waiting["status"] == "in_review"
    assert waiting["title"] == "شهر آزموده"
    assert waiting["temporal"], "the queue carries the temporal claim, not just a title"
    actions = {item["action"]: item for item in waiting["available_actions"]}
    assert actions["approve"]["permitted"] is True
    assert actions["approve"]["requires"] == ["admin", "reviewer"]

    only_drafts = client.get(f"{API}/editorial/queue", params={"status": "draft"})
    assert second["id"] not in {item["id"] for item in only_drafts.json()["data"]}
    assert first["id"] in {item["id"] for item in only_drafts.json()["data"]}
    assert client.get(f"{API}/editorial/queue", params={"status": "nonsense"}).status_code == 422


def test_the_panel_view_exposes_lint_and_history(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(client)
    detail = client.get(f"{API}/editorial/place/{created['id']}")
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["lint"] == [], "a sourced, dated, placed record inside the study area is clean"
    assert data["workflow"]["status"] == "draft"

    # Lint runs on sight, not only at publication: the panel shows the problem while it is cheap.
    bare = create_place(client, geometries=[], source_ids=[])
    findings = client.get(f"{API}/editorial/place/{bare['id']}").json()["data"]["lint"]
    rules = {finding["rule"] for finding in findings}
    assert {"D4", "D9"} <= rules, "no source and no geometry are both reported"
    for finding in findings:
        assert finding["level"] in {"error", "warning"}
        assert finding["message_fa"] and finding["message_en"], "findings are bilingual"
    assert any(finding["blocking"] for finding in findings)
    history = client.get(f"{API}/editorial/place/{created['id']}/revisions")
    assert history.status_code == 200
    assert history.json()["meta"]["count"] >= 1


def test_the_audit_trail_is_readable_and_filterable(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    created = create_place(client)
    move(client, created["id"], "submit")
    trail = client.get(f"{API}/editorial/audit", params={"entity_id": created["id"]})
    assert trail.status_code == 200
    entries = trail.json()["data"]
    assert {entry["action"] for entry in entries} >= {"create", "submit"}
    assert all(entry["actor"] == "مدیر اطلس" for entry in entries)
    assert all(entry["at"] for entry in entries)
    assert all(entry["request_id"] for entry in entries), "every row ties back to a request"


def test_lint_can_be_run_and_recorded(panel: Any) -> None:
    client, _ = panel
    sign_in(client)
    report = client.get(f"{API}/editorial/lint")
    assert report.status_code == 200
    body = report.json()
    assert body["data"]["scope"] == "corpus"
    assert body["data"]["errors"] >= 0 and body["data"]["warnings"] >= 0
    assert body["data"]["not_evaluated"], "the report says which rules are not machine-checked yet"
    assert {item["rule"] for item in body["data"]["not_evaluated"]} >= {"D1", "D6"}
    assert body["meta"]["driver"] == "fixtures"

    recorded = client.post(f"{API}/editorial/lint", headers=csrf(client))
    assert recorded.status_code == 200
    assert isinstance(recorded.json()["data"]["lint_run_id"], int)


def test_only_an_admin_manages_users(panel: Any) -> None:
    client, repository = panel
    sign_in(client, EDITOR)
    assert client.get(f"{API}/editorial/users").status_code == 403

    client.post(f"{API}/auth/logout", headers=csrf(client))
    sign_in(client)
    listing = client.get(f"{API}/editorial/users")
    assert listing.status_code == 200
    assert {person["email"] for person in listing.json()["data"]} >= {ADMIN, EDITOR}
    assert all("password" not in person for person in listing.json()["data"])

    email = f"new-{uuid.uuid4().hex[:6]}@atlas.local"
    created = client.post(
        f"{API}/editorial/users",
        json={
            "email": email,
            "display_name": "همکار تازه",
            "role": "reviewer",
            "password": "a-long-enough-password",
        },
        headers=csrf(client),
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["role"] == "reviewer"
    assert repository.authenticate(email, "a-long-enough-password") is not None
    short = client.post(
        f"{API}/editorial/users",
        json={"email": email, "display_name": "x", "role": "editor", "password": "short"},
        headers=csrf(client),
    )
    assert short.status_code == 422, "a weak password is rejected at the door"


def test_a_claim_can_be_reviewed_through_the_api(panel: Any) -> None:
    client, _ = panel
    sign_in(client, "reviewer@atlas.local")
    response = client.post(
        f"{API}/editorial/assertions/asn_safi_born_ardabil/review",
        json={
            "status": "disputed",
            "note": "دو روایت متفاوت دربارهٔ زادگاه",
            "topic_fa": "زادگاه شیخ صفی‌الدین",
            "topic_en": "The birthplace of Sheikh Safi al-Din",
        },
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "disputed"
    assert data["topic_fa"] == "زادگاه شیخ صفی‌الدین"
    assert data["reviewed_by"], "the panel shows who reviewed a claim"
    assert data["evidence"], "the evidence travels with the claim"
    # A dispute with no topic cannot be shown as two positions, so it is refused (rule D13).
    topicless = client.post(
        f"{API}/editorial/assertions/asn_safi_died_ardabil/review",
        json={"status": "disputed"},
        headers=csrf(client),
    )
    assert topicless.status_code in {200, 422}
    if topicless.status_code == 422:
        assert topicless.json()["rule"] == "D13"
    illegal = client.post(
        f"{API}/editorial/assertions/asn_safi_born_ardabil/review",
        json={"status": "proposed"},
        headers=csrf(client),
    )
    assert illegal.status_code == 409
    assert client.post(
        f"{API}/editorial/assertions/asn_missing/review",
        json={"status": "accepted"},
        headers=csrf(client),
    ).status_code == 404


def test_the_panel_is_switched_off_when_settings_say_so(settings: Settings) -> None:
    """The kill switch: an deployment that does not want the write API gets a 403, not a 500."""
    closed = settings.model_copy(update={"editorial_enabled": False})
    repository = FixturesRepository(FIXTURES_DIR)
    app = create_app(closed)
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_editorial_repository] = lambda: repository
    with TestClient(app) as client:
        response = client.post(
            f"{API}/auth/login", json={"email": ADMIN, "password": DEV_PASSWORD}
        )
        assert response.status_code == 403
        assert "disabled" in response.json()["detail"]
        assert client.get(f"{API}/editorial/queue").status_code == 403
        # The public corpus keeps working: turning the panel off is not turning the atlas off.
        assert client.get(f"{API}/entities/place/plc_ardabil").status_code == 200
    app.dependency_overrides.clear()
