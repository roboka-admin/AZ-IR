"""The write-side contract: every adapter must behave identically (ADR-0014).

Runs against the fixtures driver always, and against PostgreSQL+PostGIS whenever
``AZIR_TEST_DB_URL`` is set (that is what CI does). Because the PostGIS database is shared with the
read-only contract suite, everything a test creates here is deleted again at teardown -- derived
from the audit trail, so a test cannot forget to clean up after itself.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from azir.core.errors import ConflictError, NotFoundError, ValidationError
from azir.domain.editorial import Action, Role
from azir.domain.enums import (
    AssertionStatus,
    Certainty,
    Confidence,
    EntityType,
    GeometryKind,
    Precision,
    Status,
)
from azir.domain.geo import GeometryRecord
from azir.domain.model import NameVariant
from azir.domain.temporal import TemporalInterval
from azir.repositories.fixtures import FixturesRepository
from azir.repositories.ports import Actor, EditorialRepository, EntityDraft

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "seeds" / "fixtures"
PASSWORD = "a-strong-test-password"
EDITABLE = ("place", "person", "event", "political_entity", "article")
SEEDED_ASSERTION = "asn_safi_born_ardabil"
SEEDED_SOURCE = "src_tabari_tarikh"


def _postgis_url() -> str | None:
    return os.environ.get("AZIR_TEST_DB_URL")


def build_write_repositories() -> list[EditorialRepository]:
    repos: list[EditorialRepository] = [FixturesRepository(FIXTURES_DIR)]
    url = _postgis_url()
    if url:  # pragma: no cover - only where CI provides a database
        from azir.repositories.postgis import PostgisRepository

        repos.append(PostgisRepository(url))
    return repos


def _purge(actor_id: str) -> None:
    """Delete everything one test actor touched, children before parents.

    The repository is asked which entities the actor wrote to (its own audit trail), then the
    product's schema metadata supplies the deletion order -- no table names are hard-coded here, so
    a new facet table is cleaned up automatically.
    """
    url = _postgis_url()
    if not url:  # pragma: no cover - fixtures need no cleanup, the instance dies with the test
        return
    import sqlalchemy as sa

    from azir.repositories.postgis import PostgisRepository
    from azir.repositories.postgis.schema import METADATA

    repository = PostgisRepository(url)
    ids: set[str] = {actor_id}
    # Only the records this actor *created* (a draft, or the copy a revision forked). Reviewing a
    # seeded claim also writes an audit row, and its entity_id is somebody else's data: deleting
    # that would quietly remove the corpus the read-only suite is about to assert on.
    ids.update(
        str(entry.entity_id)
        for entry in repository.audit(limit=2000)
        if entry.actor_id == actor_id
        and entry.entity_id
        and entry.entity_type in EDITABLE
        and entry.action in {"create", "begin_revision"}
    )
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            for table in reversed(METADATA.sorted_tables):
                for column in table.columns:
                    if column.name != "id" and not column.name.endswith("_id"):
                        continue
                    try:
                        with connection.begin_nested():
                            connection.execute(table.delete().where(column.in_(ids)))
                    except Exception:
                        continue
    finally:
        engine.dispose()


#: One driver name per available adapter. Building the repository *inside* the fixture keeps a
#: fresh corpus per test (the fixtures driver is in-memory, so state would otherwise leak).
DRIVERS = ["fixtures"] + (["postgis"] if _postgis_url() else [])

_ACTORS: dict[int, Actor] = {}


@pytest.fixture()
def repository(driver_name: str) -> Iterator[EditorialRepository]:
    repository: EditorialRepository
    if driver_name == "fixtures":
        repository = FixturesRepository(FIXTURES_DIR)
    else:  # pragma: no cover - CI only
        from azir.repositories.postgis import PostgisRepository

        repository = PostgisRepository(_postgis_url() or "")
    yield repository
    actor = _ACTORS.pop(id(repository), None)
    if actor is not None and driver_name != "fixtures":  # pragma: no cover - CI only
        _purge(actor.id)


@pytest.fixture()
def actor(repository: EditorialRepository) -> Actor:
    """An admin created by the test itself: no driver ships privileged accounts."""
    email = f"contract-{uuid.uuid4().hex[:10]}@atlas.test"
    created = repository.create_user(
        email=email, display_name="قرارداد نویس", role=Role.ADMIN, password=PASSWORD
    )
    _ACTORS[id(repository)] = created
    return created


driver = pytest.mark.parametrize("driver_name", DRIVERS)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_draft(slug: str | None = None, **overrides: Any) -> EntityDraft:
    """A realistic, publishable draft. ``overrides`` replaces any field, so a test can say exactly
    which part of a record it cares about."""
    fields: dict[str, Any] = {
        "entity_type": EntityType.PLACE,
        "kind": "city",
        "slug": slug or _unique("test-place"),
        "names": (
            NameVariant(form="شهر آزموده", lang="fa", kind="preferred"),
            NameVariant(form="Test Town", lang="en", kind="preferred", script="Latn"),
        ),
        "temporal": TemporalInterval(
            year_from=1500,
            year_to=1700,
            precision=Precision.RANGE,
            confidence=Confidence.MEDIUM,
            display="۱۵۰۰–۱۷۰۰ م",
        ),
        "geometries": (
            GeometryRecord(
                geojson={"type": "Point", "coordinates": [48.35, 38.25]},
                kind=GeometryKind.POINT,
                certainty=Certainty.UNCERTAIN,
                source_id=SEEDED_SOURCE,
            ),
        ),
        "summary": "یک رکورد آزمودنی برای لایهٔ نوشتن.",
        "summary_en": "A record created by the write-contract suite.",
        "source_ids": (SEEDED_SOURCE,),
        "importance": 0.42,
    }
    fields.update(overrides)
    return EntityDraft(**fields)


# ------------------------------------------------------------------ identity and sessions


@driver
def test_a_created_user_can_authenticate(repository: EditorialRepository) -> None:
    email = f"login-{uuid.uuid4().hex[:8]}@atlas.test"
    created = repository.create_user(
        email=email, display_name="Editor", role=Role.EDITOR, password=PASSWORD
    )
    _ACTORS.setdefault(id(repository), created)
    assert created.role is Role.EDITOR
    found = repository.authenticate(email.upper(), PASSWORD)
    assert found is not None and found.id == created.id
    assert repository.user_by_id(created.id) is not None
    assert any(person.id == created.id for person in repository.list_users())


@driver
def test_a_wrong_password_authenticates_nobody(repository: EditorialRepository, actor: Actor) -> None:
    assert repository.authenticate(actor.email, "not-the-password") is None
    assert repository.authenticate("nobody@atlas.test", PASSWORD) is None
    # The stored secret is a hash, never the password itself.
    assert PASSWORD not in str(repository.list_users())


@driver
def test_a_duplicate_email_is_rejected(repository: EditorialRepository, actor: Actor) -> None:
    with pytest.raises(ConflictError):
        repository.create_user(
            email=actor.email, display_name="Copy", role=Role.EDITOR, password=PASSWORD
        )
    with pytest.raises(ValidationError):
        repository.create_user(
            email="not-an-email", display_name="Bad", role=Role.EDITOR, password=PASSWORD
        )


@driver
def test_a_session_round_trips_and_carries_csrf(
    repository: EditorialRepository, actor: Actor
) -> None:
    ticket = repository.open_session(actor, ttl_seconds=3600, user_agent="pytest")
    assert ticket.token and ticket.csrf_token and ticket.token != ticket.csrf_token
    found = repository.session_actor(ticket.token)
    assert found is not None and found.id == actor.id
    # The raw token is never stored: only its digest, so a leaked table is not a leaked session.
    assert ticket.token not in str(repository.list_users())
    assert repository.session_csrf_ok(ticket.token, ticket.csrf_token) is True
    assert repository.session_csrf_ok(ticket.token, "forged") is False
    assert repository.session_csrf_ok(ticket.token, None) is False
    repository.close_session(ticket.token)
    assert repository.session_actor(ticket.token) is None
    assert repository.session_actor("nonsense") is None


@driver
def test_an_expired_session_is_refused(
    repository: EditorialRepository, actor: Actor, driver_name: str
) -> None:
    """Expiry is enforced by the storage, so the test has to move the clock where it lives."""
    ticket = repository.open_session(actor, ttl_seconds=3600)
    assert repository.session_actor(ticket.token) is not None
    _expire(repository, ticket.token, driver_name)
    assert repository.session_actor(ticket.token) is None
    # An expired session must not validate a CSRF token either, or logout would be cosmetic.
    assert repository.session_csrf_ok(ticket.token, ticket.csrf_token) is False


def _expire(repository: EditorialRepository, token: str, driver_name: str) -> None:
    from azir.core.security import token_digest

    digest = token_digest(token)
    if driver_name == "fixtures":
        # The in-memory adapter keeps (user_id, csrf_digest, expires_at) in one dict.
        store: dict[str, Any] = repository._sessions
        user_id, csrf, _ = store[digest]
        store[digest] = (user_id, csrf, datetime.now(UTC) - timedelta(seconds=1))
        return
    # pragma: no cover - CI only
    import sqlalchemy as sa

    engine = sa.create_engine(_postgis_url() or "")
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE app_session SET expires_at = now() - interval '1 minute' "
                        "WHERE id = :digest"),
                {"digest": digest},
            )
    finally:
        engine.dispose()


# ------------------------------------------------------------------ drafts


@driver
def test_creating_a_draft_yields_an_unpublished_ranked_record(
    repository: EditorialRepository, actor: Actor
) -> None:
    draft = make_draft()
    record = repository.create_draft(draft, actor=actor, request_id="req-create")
    assert record.id and record.slug == draft.slug
    assert record.status is Status.DRAFT, "a new record is never published by accident"
    assert record.revision == 1
    assert record.display_name("fa") == "شهر آزموده"
    assert record.display_name("en") == "Test Town"
    assert record.secondary_name("fa") == "Test Town", "the other language is the subtitle"
    assert record.rank > 0 and record.layer, "presentation is derived, never hand-written"
    assert record.temporal is not None and record.temporal.year_from == 1500
    state = repository.workflow_state("place", record.id)
    assert state is not None and state.status is Status.DRAFT
    assert state.revision == 1 and state.updated_at is not None
    # The public corpus must not serve it.
    assert repository.entity("place", record.id) is not None
    assert record.id not in {row.id for row in repository.list_entities(
        EntityType.PLACE, status="published", locale="fa", limit=500
    )}


@driver
def test_a_duplicate_slug_is_rejected(repository: EditorialRepository, actor: Actor) -> None:
    slug = _unique("dup")
    repository.create_draft(make_draft(slug=slug), actor=actor, request_id=None)
    with pytest.raises(ConflictError):
        repository.create_draft(make_draft(slug=slug), actor=actor, request_id=None)


@driver
def test_updating_a_draft_replaces_its_facets(
    repository: EditorialRepository, actor: Actor
) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id=None)
    updated = repository.update_draft(
        "place",
        record.id,
        make_draft(
            slug=record.slug,
            names=(NameVariant(form="نام تازه", lang="fa", kind="preferred"),),
            summary="به‌روزرسانی شد.",
            importance=0.9,
        ),
        actor=actor,
        request_id="req-update",
    )
    assert updated.display_name("fa") == "نام تازه"
    assert updated.summary == "به‌روزرسانی شد."
    assert updated.importance == pytest.approx(0.9)
    assert len(updated.names) == 1, "a draft update replaces facets, it does not accumulate them"
    assert updated.rank > 0 and updated.layer, "presentation is still derived after an edit"
    with pytest.raises(NotFoundError):
        repository.update_draft("place", "plc_missing", make_draft(), actor=actor, request_id=None)


@driver
def test_only_draft_and_changes_requested_rows_are_editable(
    repository: EditorialRepository, actor: Actor
) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id=None)
    repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
    with pytest.raises(ConflictError):
        repository.update_draft(
            "place", record.id, make_draft(slug=record.slug), actor=actor, request_id=None
        )


# ------------------------------------------------------------------ the state machine


@driver
def test_the_review_loop_runs_draft_to_published(
    repository: EditorialRepository, actor: Actor
) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id=None)
    submitted = repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
    assert submitted.status is Status.IN_REVIEW
    state = repository.workflow_state("place", record.id)
    assert state is not None and state.submitted_by == actor.id and state.submitted_at is not None

    published = repository.transition("place", record.id, Status.PUBLISHED, actor=actor, request_id=None)
    assert published.status is Status.PUBLISHED
    assert published.id in {
        row.id
        for row in repository.list_entities(
            EntityType.PLACE, status="published", locale="fa", limit=500
        )
    }, "a published record joins the public corpus"
    state = repository.workflow_state("place", record.id)
    assert state is not None and state.published_at is not None


@driver
def test_illegal_transitions_are_refused(repository: EditorialRepository, actor: Actor) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id=None)
    # Drafts cannot skip review, whatever the caller asks for.
    with pytest.raises(ConflictError):
        repository.transition("place", record.id, Status.PUBLISHED, actor=actor, request_id=None)
    # A draft *may* be archived (a discarded idea is still a recorded decision), but it may never
    # jump straight to the public corpus.
    assert repository.entity("place", record.id).status is Status.DRAFT


@driver
def test_changes_requested_returns_to_the_editor(
    repository: EditorialRepository, actor: Actor
) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id=None)
    repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
    sent_back = repository.transition(
        "place", record.id, Status.CHANGES_REQUESTED, actor=actor, request_id=None, note="منبع کافی نیست"
    )
    assert sent_back.status is Status.CHANGES_REQUESTED
    state = repository.workflow_state("place", record.id)
    assert state is not None and state.review_note == "منبع کافی نیست"
    # The editor may keep working, and may resubmit.
    repository.update_draft("place", record.id, make_draft(slug=record.slug), actor=actor, request_id=None)
    again = repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
    assert again.status is Status.IN_REVIEW


@driver
def test_archiving_and_restoring_keeps_history(
    repository: EditorialRepository, actor: Actor
) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id=None)
    repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
    repository.transition("place", record.id, Status.PUBLISHED, actor=actor, request_id=None)
    archived = repository.transition("place", record.id, Status.ARCHIVED, actor=actor, request_id=None)
    assert archived.status is Status.ARCHIVED
    assert archived.id not in {
        row.id
        for row in repository.list_entities(
            EntityType.PLACE, status="published", locale="fa", limit=500
        )
    }, "an archived record leaves the public corpus"
    restored = repository.transition("place", record.id, Status.PUBLISHED, actor=actor, request_id=None)
    assert restored.status is Status.PUBLISHED


# ------------------------------------------------------------------ the reviewed-copy pattern


@driver
def test_editing_a_published_record_forks_a_reviewed_copy(
    repository: EditorialRepository, actor: Actor
) -> None:
    head = repository.create_draft(make_draft(), actor=actor, request_id=None)
    repository.transition("place", head.id, Status.IN_REVIEW, actor=actor, request_id=None)
    repository.transition("place", head.id, Status.PUBLISHED, actor=actor, request_id=None)
    public_summary = repository.entity("place", head.id).summary

    copy = repository.begin_revision("place", head.id, actor=actor, request_id=None)
    assert copy.id != head.id and copy.revision == head.revision + 1
    assert copy.status is Status.DRAFT
    assert repository.pending_revision("place", head.id) is not None
    assert repository.pending_revision("place", head.id).id == copy.id

    # The public record is untouched while the copy is being worked on (ADR-0010 rule 4).
    assert repository.entity("place", head.id).summary == public_summary
    assert repository.entity("place", head.id).status is Status.PUBLISHED

    edited = repository.update_draft(
        "place",
        copy.id,
        make_draft(slug=None, summary="متن بازبینی‌شده.", names=copy.names),
        actor=actor,
        request_id=None,
    )
    assert edited.summary == "متن بازبینی‌شده."
    assert repository.entity("place", head.id).summary == public_summary

    repository.transition("place", copy.id, Status.IN_REVIEW, actor=actor, request_id=None)
    merged = repository.transition("place", copy.id, Status.PUBLISHED, actor=actor, request_id=None)
    assert merged.id == head.id, "approving a copy publishes the head record, not a second one"
    assert merged.summary == "متن بازبینی‌شده."
    assert merged.revision == head.revision + 1
    assert repository.entity("place", copy.id) is None or repository.pending_revision(
        "place", head.id
    ) is None, "the copy is gone once its content lives in the head"


@driver
def test_a_published_record_cannot_fork_twice(
    repository: EditorialRepository, actor: Actor
) -> None:
    head = repository.create_draft(make_draft(), actor=actor, request_id=None)
    repository.transition("place", head.id, Status.IN_REVIEW, actor=actor, request_id=None)
    repository.transition("place", head.id, Status.PUBLISHED, actor=actor, request_id=None)
    first = repository.begin_revision("place", head.id, actor=actor, request_id=None)
    second = repository.begin_revision("place", head.id, actor=actor, request_id=None)
    assert second.id == first.id, "one open revision per record, so two editors cannot diverge"
    with pytest.raises(NotFoundError):
        repository.begin_revision("place", "plc_missing", actor=actor, request_id=None)


# ------------------------------------------------------------------ reading the workflow


@driver
def test_the_queue_is_scoped_by_status(repository: EditorialRepository, actor: Actor) -> None:
    first = repository.create_draft(make_draft(), actor=actor, request_id=None)
    second = repository.create_draft(make_draft(), actor=actor, request_id=None)
    repository.transition("place", second.id, Status.IN_REVIEW, actor=actor, request_id=None)

    drafts = repository.queue(statuses=(Status.DRAFT,), limit=200)
    ids = {record.id for record in drafts}
    assert first.id in ids and second.id not in ids
    in_review = {record.id for record in repository.queue(statuses=(Status.IN_REVIEW,), limit=200)}
    assert second.id in in_review
    both = {
        record.id
        for record in repository.queue(
            statuses=(Status.DRAFT, Status.IN_REVIEW), entity_type="place", limit=200
        )
    }
    assert {first.id, second.id} <= both
    people = repository.queue(statuses=(Status.DRAFT,), entity_type="person", limit=200)
    assert all(record.entity_type is EntityType.PERSON for record in people), (
        "the queue honours its entity_type filter"
    )


@driver
def test_revisions_are_frozen_snapshots(repository: EditorialRepository, actor: Actor) -> None:
    record = repository.create_draft(make_draft(summary="نسخهٔ یک."), actor=actor, request_id=None)
    repository.update_draft(
        "place", record.id, make_draft(slug=record.slug, summary="نسخهٔ دو."),
        actor=actor,
        request_id=None,
    )
    repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
    history = repository.revisions("place", record.id)
    assert history, "every boundary leaves a snapshot"
    summaries = {str(item.payload.get("summary")) for item in history}
    assert "نسخهٔ یک." in summaries or "نسخهٔ دو." in summaries
    assert all(item.revision >= 1 for item in history)
    assert all(item.created_by for item in history)


@driver
def test_every_write_leaves_an_audit_trail(
    repository: EditorialRepository, actor: Actor
) -> None:
    record = repository.create_draft(make_draft(), actor=actor, request_id="req-audit")
    repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id="req-audit")
    entries = repository.audit(entity_id=record.id, limit=100)
    actions = {entry.action for entry in entries}
    assert "create" in actions and "submit" in actions
    assert all(entry.actor_id == actor.id for entry in entries)
    assert any(entry.request_id == "req-audit" for entry in entries)
    assert all(entry.created_at is not None for entry in entries)
    # A field-level diff is what makes an audit log useful (ADR-0010 rule 5).
    changed = [entry.payload.get("changed") for entry in entries if entry.action == "submit"]
    assert changed, "a transition records what changed"

    repository.record_audit(
        actor=actor,
        action=Action.MANAGE_USERS,
        entity_type="user",
        entity_id=actor.id,
        payload={"note": "direct entry"},
        request_id="req-direct",
    )
    assert any(
        entry.action == Action.MANAGE_USERS.value
        for entry in repository.audit(entity_type="user", entity_id=actor.id, limit=50)
    )


@driver
def test_lint_runs_are_recorded(repository: EditorialRepository, actor: Actor) -> None:
    run_id = repository.record_lint(
        scope="corpus",
        findings=[{"rule": "D4", "level": "error", "entity_id": "plc_x", "message_fa": "منبع ندارد"}],
        actor=actor,
    )
    assert isinstance(run_id, int) and run_id > 0


# ------------------------------------------------------------------ claims


@driver
def test_a_claim_can_be_reviewed(repository: EditorialRepository, actor: Actor) -> None:
    current = repository.assertion(SEEDED_ASSERTION)
    assert current is not None, "the seeded corpus carries reviewable claims"
    disputed = repository.review_assertion(
        SEEDED_ASSERTION, AssertionStatus.DISPUTED, actor=actor, request_id=None, note="دو روایت متفاوت است"
    )
    assert disputed.status is AssertionStatus.DISPUTED
    assert disputed.evidence, "evidence survives a review"
    accepted = repository.review_assertion(
        SEEDED_ASSERTION, AssertionStatus.ACCEPTED, actor=actor, request_id=None
    )
    assert accepted.status is AssertionStatus.ACCEPTED
    with pytest.raises(NotFoundError):
        repository.review_assertion("asn_missing", AssertionStatus.REJECTED, actor=actor, request_id=None)


@driver
def test_an_illegal_claim_transition_is_refused(
    repository: EditorialRepository, actor: Actor
) -> None:
    current = repository.assertion(SEEDED_ASSERTION)
    assert current is not None
    if current.status is AssertionStatus.ACCEPTED:
        # accepted -> accepted is not a move; accepted -> disputed -> rejected is.
        with pytest.raises(ConflictError):
            repository.review_assertion(
                SEEDED_ASSERTION, AssertionStatus.ACCEPTED, actor=actor, request_id=None
            )


# ------------------------------------------------------------------ cross-driver parity


def test_both_drivers_build_the_same_draft() -> None:
    """One builder, two storages: the same draft must come back the same way (ADR-0014)."""
    repositories = build_write_repositories()
    outcomes: list[tuple[Any, ...]] = []
    actors: list[Actor] = []
    slug = _unique("parity")
    for repository in repositories:
        actor = repository.create_user(
            email=f"parity-{uuid.uuid4().hex[:8]}@atlas.test",
            display_name="Parity",
            role=Role.ADMIN,
            password=PASSWORD,
        )
        actors.append(actor)
        record = repository.create_draft(
            make_draft(slug=slug, summary="یکسان در هر دو ذخیره‌ساز."),
            actor=actor,
            request_id=None,
        )
        published = repository.transition("place", record.id, Status.IN_REVIEW, actor=actor, request_id=None)
        published = repository.transition("place", published.id, Status.PUBLISHED, actor=actor, request_id=None)
        outcomes.append(
            (
                published.status,
                published.revision,
                published.slug,
                published.summary,
                tuple(sorted((name.form, name.lang) for name in published.names)),
                published.temporal.year_from if published.temporal else None,
                published.temporal.year_to if published.temporal else None,
                published.importance,
                published.certainty,
                len(published.geometries),
                published.has_geometry,
            )
        )
    for repository, actor in zip(repositories, actors, strict=True):
        _ACTORS[id(repository)] = actor
    try:
        assert len(set(outcomes)) == 1, f"drivers disagree: {outcomes}"
    finally:
        if _postgis_url():  # pragma: no cover - CI only
            for actor in actors:
                _purge(actor.id)
