"""Write side of the fixtures driver (ADR-0014).

The fixtures driver is the development driver: everything it writes lives in this process's memory
and is gone on restart. That is deliberate. It lets the whole editorial workflow -- draft, submit,
review, publish, audit -- be exercised, tested and demoed with no database at all, and it is why
``Settings.validate_consistency`` refuses to run this driver in production.

Behaviour is intentionally identical to the PostGIS adapter: the same domain rules, the same
revision snapshots, the same audit entries, the same reviewed-copy pattern. One contract suite
(``tests/test_editorial_contract.py``) runs against both, so a difference is a build failure.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.ids import new_id, slugify
from ..core.security import (
    constant_time_equals,
    hash_password,
    new_token,
    token_digest,
    verify_password,
)
from ..domain.editorial import (
    Action,
    Role,
    action_for,
    allowed_targets,
    assertion_may,
    claim_is_evidenced,
    is_editable,
)
from ..domain.enums import AssertionStatus, EntityType, Status
from ..domain.model import EntityRecord, Relationship
from .ports import (
    Actor,
    AssertionRecord,
    AuditEntry,
    EntityDraft,
    RevisionRecord,
    SessionTicket,
    WorkflowState,
)
from .snapshots import (
    draft_from_record,
    not_editable_error,
    record_from_draft,
    snapshot_payload,
)

if TYPE_CHECKING:  # pragma: no cover
    from .fixtures import FixturesRepository  # noqa: F401

#: Reference data is bibliography: not editable through the panel (same rule as the PostGIS driver).
EDITABLE_TYPES: frozenset[str] = frozenset(
    {
        EntityType.PLACE.value,
        EntityType.PERSON.value,
        EntityType.EVENT.value,
        EntityType.POLITICAL_ENTITY.value,
        EntityType.ARTICLE.value,
    }
)

OPEN_STATUSES: tuple[str, ...] = ("draft", "in_review", "changes_requested", "imported_unverified")

#: The fixtures driver's demo identities take their password from the environment, so no credential
#: is ever committed. It is a development convenience, not a default that can reach production.
DEV_PASSWORD_ENV = "AZIR_DEV_PASSWORD"
DEV_PASSWORD_DEFAULT = "atlas-dev-password"


def _now() -> datetime:
    return datetime.now(UTC)


class FixturesEditorialMixin:
    """In-memory implementation of :class:`azir.repositories.ports.EditorialRepository`."""

    if TYPE_CHECKING:  # pragma: no cover - provided by FixturesRepository
        _entities: dict[str, EntityRecord]
        _by_slug: dict[tuple[str, str], str]
        _docs: dict[str, list[Any]]
        _relations: dict[str, list[Relationship]]

        def _compute_rankings(self) -> None: ...

    # ------------------------------------------------------------------ bootstrap

    def _editorial_init(self) -> None:
        self._users: dict[str, Actor] = {}
        self._by_email: dict[str, str] = {}
        self._passwords: dict[str, str] = {}
        self._sessions: dict[str, tuple[str, str, datetime]] = {}
        self._audit: list[AuditEntry] = []
        self._audit_seq = 0
        self._snapshots: dict[tuple[str, str, int], RevisionRecord] = {}
        self._workflow: dict[tuple[str, str], WorkflowState] = {}
        self._assertions: dict[str, AssertionRecord] = {}
        self._lint_runs: list[dict[str, Any]] = []
        self._load_demo_users()
        self._index_workflow()

    def _load_demo_users(self) -> None:
        """Identities from ``10-users.yaml`` so the panel is usable without a database."""
        password = os.environ.get(DEV_PASSWORD_ENV) or DEV_PASSWORD_DEFAULT
        for item in self._docs.get("users", []):
            if not isinstance(item, dict):  # pragma: no cover - defensive
                continue
            email = str(item.get("email", "")).strip().lower()
            if not email:
                continue
            actor = Actor(
                id=str(item.get("id") or new_id("user")),
                email=email,
                display_name=str(item.get("display_name") or email),
                role=_role(item.get("role")),
                is_active=bool(item.get("is_active", True)),
            )
            self._users[actor.id] = actor
            self._by_email[actor.email] = actor.id
            self._passwords[actor.id] = hash_password(password, fast=True)

    def _index_workflow(self) -> None:
        """Seeded records are already where the fixtures say they are; record the state."""
        for record in list(self._entities.values()):
            key = (record.entity_type.value, record.id)
            published = record.status is Status.PUBLISHED
            self._workflow[key] = WorkflowState(
                entity_type=record.entity_type.value,
                entity_id=record.id,
                status=record.status,
                revision=record.revision,
                published_at=_now() if published else None,
                updated_at=_now(),
            )

    # ------------------------------------------------------------------ identity

    def authenticate(self, email: str, password: str) -> Actor | None:
        user_id = self._by_email.get(email.strip().lower())
        if user_id is None:
            return None
        actor = self._users[user_id]
        if not actor.is_active:
            return None
        if not verify_password(self._passwords.get(user_id), password, fast=True):
            return None
        return actor

    def user_by_id(self, user_id: str) -> Actor | None:
        return self._users.get(user_id)

    def create_user(self, *, email: str, display_name: str, role: Role, password: str) -> Actor:
        address = email.strip().lower()
        if not address or "@" not in address:
            raise ValidationError("a valid email is required", parameter="email")
        if len(password) < 10:
            raise ValidationError(
                "passwords must be at least 10 characters", parameter="password", minimum=10
            )
        if address in self._by_email:
            raise ConflictError("that email already has an account", email=address)
        actor = Actor(
            id=new_id("user"),
            email=address,
            display_name=display_name.strip() or address,
            role=role,
            is_active=True,
        )
        self._users[actor.id] = actor
        self._by_email[actor.email] = actor.id
        self._passwords[actor.id] = hash_password(password, fast=True)
        return actor

    def list_users(self) -> list[Actor]:
        return sorted(self._users.values(), key=lambda actor: actor.email)

    def open_session(
        self, actor: Actor, *, ttl_seconds: int, user_agent: str | None = None
    ) -> SessionTicket:
        token = new_token(32)
        csrf = new_token(24)
        expires = _now() + timedelta(seconds=max(60, ttl_seconds))
        self._sessions[token_digest(token)] = (actor.id, token_digest(csrf), expires)
        _ = user_agent  # kept for parity with the PostGIS adapter, which stores it
        return SessionTicket(token=token, csrf_token=csrf, actor=actor, expires_at=expires)

    def session_actor(self, token: str) -> Actor | None:
        entry = self._live_session(token)
        if entry is None:
            return None
        actor = self._users.get(entry[0])
        return actor if actor and actor.is_active else None

    def session_csrf_ok(self, token: str, csrf_token: str | None) -> bool:
        """Double-submit check. Like the PostGIS query, it only accepts a *live* session: an
        expired or closed one must never validate a CSRF token."""
        if not csrf_token:
            return False
        entry = self._live_session(token)
        if entry is None:
            return False
        return constant_time_equals(entry[1], token_digest(csrf_token))

    def _live_session(self, token: str) -> tuple[str, str, datetime] | None:
        """The session if it exists and has not expired; expired rows are dropped on sight."""
        entry = self._sessions.get(token_digest(token)) if token else None
        if entry is None:
            return None
        if entry[2] <= _now():
            self._sessions.pop(token_digest(token), None)
            return None
        return entry

    def close_session(self, token: str) -> None:
        if token:
            self._sessions.pop(token_digest(token), None)

    # ------------------------------------------------------------------ content

    def create_draft(
        self, draft: EntityDraft, *, actor: Actor | None, request_id: str | None
    ) -> EntityRecord:
        entity_type = draft.entity_type.value
        _require_editable(entity_type)
        entity_id = new_id(entity_type)
        slug = self._reserve_slug(draft, entity_type, entity_id)
        record = record_from_draft(
            draft, entity_id=entity_id, slug=slug, status=Status.DRAFT, revision=1
        )
        self._put(record)
        self._compute_rankings()
        record = self._entities[entity_id]
        self._workflow[(entity_type, entity_id)] = WorkflowState(
            entity_type=entity_type,
            entity_id=entity_id,
            status=Status.DRAFT,
            revision=1,
            updated_at=_now(),
        )
        self._write_audit(
            actor=actor,
            action=Action.CREATE,
            entity_type=entity_type,
            entity_id=entity_id,
            payload={"slug": slug, "status": Status.DRAFT.value, "kind": draft.kind},
            request_id=request_id,
        )
        self._write_snapshot(record, actor=actor)
        return record

    def update_draft(
        self,
        entity_type: str,
        entity_id: str,
        draft: EntityDraft,
        *,
        actor: Actor | None,
        request_id: str | None,
    ) -> EntityRecord:
        _require_editable(entity_type)
        before = self._entities.get(entity_id)
        if before is None:
            raise NotFoundError(f"no {entity_type} with id {entity_id!r}")
        if not is_editable(before.status):
            raise not_editable_error(before.status, entity_id)
        slug = before.slug
        if draft.slug and draft.slug != before.slug:
            slug = self._reserve_slug(draft, entity_type, entity_id, allow=before.slug)
        record = record_from_draft(
            draft,
            entity_id=entity_id,
            slug=slug,
            status=before.status,
            revision=before.revision,
            relationships=before.relationships,
            article_ids=before.article_ids,
        )
        self._put(record)
        self._compute_rankings()
        after = self._entities[entity_id]
        state = self._workflow.get((entity_type, entity_id))
        self._workflow[(entity_type, entity_id)] = WorkflowState(
            entity_type=entity_type,
            entity_id=entity_id,
            status=after.status,
            revision=after.revision,
            head_id=state.head_id if state else None,
            submitted_by=state.submitted_by if state else None,
            submitted_at=state.submitted_at if state else None,
            review_note=state.review_note if state else None,
        )
        self._write_audit(
            actor=actor,
            action=Action.UPDATE,
            entity_type=entity_type,
            entity_id=entity_id,
            payload={
                "revision": after.revision,
                "status": after.status.value,
                "changed": _changed_fields(before, after),
            },
            request_id=request_id,
        )
        self._write_snapshot(after, actor=actor)
        return after

    def begin_revision(
        self, entity_type: str, entity_id: str, *, actor: Actor | None, request_id: str | None
    ) -> EntityRecord:
        _require_editable(entity_type)
        head = self._entities.get(entity_id)
        if head is None:
            raise NotFoundError(f"no {entity_type} with id {entity_id!r}")
        if head.status is not Status.PUBLISHED:
            raise ConflictError(
                "only a published record needs a reviewed copy; edit the open draft instead",
                entity_id=entity_id,
                status=head.status.value,
            )
        existing = self._pending_id(entity_type, entity_id)
        if existing:
            return self._entities[existing]
        draft_id = new_id(entity_type)
        revision = head.revision + 1
        copy = record_from_draft(
            draft_from_record(head),
            entity_id=draft_id,
            slug=None,  # the head owns the public slug; a copy must not shadow it
            status=Status.DRAFT,
            revision=revision,
        )
        self._put(copy)
        self._compute_rankings()
        record = self._entities[draft_id]
        self._workflow[(entity_type, draft_id)] = WorkflowState(
            entity_type=entity_type,
            entity_id=draft_id,
            status=Status.DRAFT,
            revision=revision,
            head_id=entity_id,
            updated_at=_now(),
        )
        self._write_audit(
            actor=actor,
            action=Action.BEGIN_REVISION,
            entity_type=entity_type,
            entity_id=entity_id,
            payload={"draft_id": draft_id, "revision": revision},
            request_id=request_id,
        )
        self._write_snapshot(record, actor=actor)
        return record

    def pending_revision(self, entity_type: str, entity_id: str) -> EntityRecord | None:
        draft_id = self._pending_id(entity_type, entity_id)
        return self._entities.get(draft_id) if draft_id else None

    def transition(
        self,
        entity_type: str,
        entity_id: str,
        target: Status,
        *,
        actor: Actor | None,
        request_id: str | None,
        note: str | None = None,
    ) -> EntityRecord:
        _require_editable(entity_type)
        current = self._entities.get(entity_id)
        if current is None:
            raise NotFoundError(f"no {entity_type} with id {entity_id!r}")
        action = action_for(current.status, target)
        if action is None:
            raise ConflictError(
                f"{current.status.value} -> {target.value} is not a legal transition",
                entity_id=entity_id,
                status=current.status.value,
                target=target.value,
                allowed=[status.value for status in allowed_targets(current.status)],
            )
        state = self._workflow.get((entity_type, entity_id))
        head_id = state.head_id if state else None

        if target is Status.PUBLISHED and head_id:
            record = self._merge_into_head(
                entity_type=entity_type,
                head_id=head_id,
                draft_id=entity_id,
                actor=actor,
                request_id=request_id,
                note=note,
            )
        else:
            record = replace(current, status=target)
            self._put(record)
            self._compute_rankings()
            record = self._entities[entity_id]
            now = _now()
            updates: dict[str, Any] = {
                "status": target,
                "revision": record.revision,
                "review_note": note if note is not None else (state.review_note if state else None),
                "updated_at": now,
            }
            if actor is not None:
                if target is Status.IN_REVIEW:
                    updates.update(submitted_by=actor.id, submitted_at=now)
                if target in (Status.PUBLISHED, Status.CHANGES_REQUESTED):
                    updates.update(reviewed_by=actor.id, reviewed_at=now)
                if target is Status.PUBLISHED:
                    updates.update(published_by=actor.id, published_at=now)
                if target is Status.ARCHIVED:
                    updates.update(archived_by=actor.id, archived_at=now)
            self._workflow[(entity_type, entity_id)] = _state(entity_type, entity_id, state, updates)
            self._write_audit(
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                payload={
                    "from": current.status.value,
                    "to": target.value,
                    "revision": record.revision,
                    "note": note,
                },
                request_id=request_id,
            )
            self._write_snapshot(record, actor=actor)
        return record

    def _merge_into_head(
        self,
        *,
        entity_type: str,
        head_id: str,
        draft_id: str,
        actor: Actor | None,
        request_id: str | None,
        note: str | None,
    ) -> EntityRecord:
        head = self._entities.get(head_id)
        copy = self._entities.get(draft_id)
        if head is None or copy is None:  # pragma: no cover - guarded by the caller
            raise NotFoundError(f"no {entity_type} with id {head_id!r}")
        draft = draft_from_record(copy)
        merged = record_from_draft(
            draft,
            entity_id=head_id,
            slug=head.slug,  # the public id and slug never change (ADR-0008)
            status=Status.PUBLISHED,
            revision=copy.revision,
            relationships=head.relationships,
            article_ids=head.article_ids,
        )
        self._forget(draft_id, entity_type)
        self._put(merged)
        self._compute_rankings()
        record = self._entities[head_id]
        self._workflow.pop((entity_type, draft_id), None)
        now = _now()
        updates: dict[str, Any] = {
            "status": Status.PUBLISHED,
            "revision": copy.revision,
            "head_id": None,
            "review_note": note,
            "updated_at": now,
        }
        if actor is not None:
            updates.update(
                reviewed_by=actor.id, reviewed_at=now, published_by=actor.id, published_at=now
            )
        self._workflow[(entity_type, head_id)] = _state(
            entity_type, head_id, self._workflow.get((entity_type, head_id)), updates
        )
        self._write_audit(
            actor=actor,
            action=Action.PUBLISH,
            entity_type=entity_type,
            entity_id=head_id,
            payload={
                "from": head.status.value,
                "to": Status.PUBLISHED.value,
                "revision": copy.revision,
                "merged_draft_id": draft_id,
                "note": note,
                "changed": _changed_fields(head, record),
            },
            request_id=request_id,
        )
        self._write_snapshot(record, actor=actor)
        return record

    # ------------------------------------------------------------------ queries

    def workflow_state(self, entity_type: str, entity_id: str) -> WorkflowState | None:
        state = self._workflow.get((entity_type, entity_id))
        record = self._entities.get(entity_id)
        if state is None and record is None:
            return None
        if record is not None:
            state = _state(
                entity_type,
                entity_id,
                state,
                {"status": record.status, "revision": record.revision},
            )
            self._workflow[(entity_type, entity_id)] = state
        return state

    def queue(
        self,
        *,
        statuses: tuple[Status, ...] = (Status.DRAFT, Status.IN_REVIEW, Status.CHANGES_REQUESTED),
        entity_type: str | None = None,
        limit: int = 50,
    ) -> list[EntityRecord]:
        wanted = {status.value for status in statuses}
        out = [
            record
            for record in self._entities.values()
            if record.status.value in wanted
            and record.entity_type.value in EDITABLE_TYPES
            and (entity_type is None or record.entity_type.value == entity_type)
        ]

        epoch = datetime.fromtimestamp(0, tz=UTC)

        def touched(record: EntityRecord) -> datetime:
            state = self._workflow.get((record.entity_type.value, record.id))
            return (state.updated_at if state else None) or epoch

        out.sort(key=lambda record: (touched(record), record.id), reverse=True)
        return out[: max(1, min(limit, 500))]

    def revisions(self, entity_type: str, entity_id: str) -> list[RevisionRecord]:
        found = [
            snapshot
            for key, snapshot in self._snapshots.items()
            if key[0] == entity_type and key[1] == entity_id
        ]
        found.sort(key=lambda item: item.revision, reverse=True)
        return found

    def audit(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        out = [
            entry
            for entry in reversed(self._audit)
            if (entity_type is None or entry.entity_type == entity_type)
            and (entity_id is None or entry.entity_id == entity_id)
            and (action is None or entry.action == action)
        ]
        return out[: max(1, min(limit, 500))]

    def record_audit(
        self,
        *,
        actor: Actor | None,
        action: Action,
        entity_type: str | None = None,
        entity_id: str | None = None,
        payload: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self._write_audit(
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            request_id=request_id,
        )

    # ------------------------------------------------------------------ claims

    def assertions(self) -> dict[str, AssertionRecord]:
        """The claims the fixtures loaded, indexed for review (built on first use)."""
        if not self._assertions:
            self._assertions = _assertions_from_docs(self._docs)
        return self._assertions

    def assertion(self, assertion_id: str) -> AssertionRecord | None:
        return self.assertions().get(assertion_id)

    def review_assertion(
        self,
        assertion_id: str,
        target: AssertionStatus,
        *,
        actor: Actor | None,
        request_id: str | None,
        note: str | None = None,
        topic_fa: str | None = None,
        topic_en: str | None = None,
    ) -> AssertionRecord:
        current = self.assertion(assertion_id)
        if current is None:
            raise NotFoundError(f"no assertion with id {assertion_id!r}")
        if not assertion_may(current.status, target):
            raise ConflictError(
                f"{current.status.value} -> {target.value} is not a legal claim transition",
                assertion_id=assertion_id,
            )
        if target is AssertionStatus.ACCEPTED and not claim_is_evidenced(current.evidence):
            raise ValidationError(
                "a claim cannot be accepted without evidence that carries it "
                "(stance=supports or qualifies, rule D3)",
                assertion_id=assertion_id,
                rule="D3",
            )
        reviewed = replace(
            current,
            status=target,
            topic_fa=topic_fa or current.topic_fa,
            topic_en=topic_en or current.topic_en,
            reviewed_by=actor.id if actor else current.reviewed_by,
            reviewed_at=_now(),
            review_note=note or current.review_note,
        )
        self.assertions()[assertion_id] = reviewed
        self._write_audit(
            actor=actor,
            action=Action.REVIEW_ASSERTION,
            entity_type="assertion",
            entity_id=assertion_id,
            payload={
                "from": current.status.value,
                "to": target.value,
                "note": note,
                "topic_fa": topic_fa,
                "topic_en": topic_en,
            },
            request_id=request_id,
        )
        return reviewed

    def record_lint(self, *, scope: str, findings: list[dict[str, Any]], actor: Actor | None) -> int:
        errors = sum(1 for item in findings if item.get("level") == "error")
        warnings = sum(1 for item in findings if item.get("level") == "warning")
        self._lint_runs.append(
            {
                "id": len(self._lint_runs) + 1,
                "scope": scope,
                "errors": errors,
                "warnings": warnings,
                "findings": findings,
                "actor_id": actor.id if actor else None,
                "created_at": _now().isoformat(),
            }
        )
        return int(self._lint_runs[-1]["id"])

    # ------------------------------------------------------------------ internals

    def _put(self, record: EntityRecord) -> None:
        self._entities[record.id] = record
        if record.slug:
            self._by_slug[(record.entity_type.value, record.slug)] = record.id

    def _forget(self, entity_id: str, entity_type: str) -> None:
        record = self._entities.pop(entity_id, None)
        if record and record.slug:
            self._by_slug.pop((entity_type, record.slug), None)

    def _reserve_slug(
        self, draft: EntityDraft, entity_type: str, entity_id: str, *, allow: str | None = None
    ) -> str | None:
        candidate = (draft.slug or "").strip() or None
        if candidate is None:
            latin = _first_name(draft, "en") or ""
            candidate = slugify(latin, fallback=f"{entity_type}-{entity_id[-8:]}")
        owner = self._by_slug.get((entity_type, candidate))
        if owner is not None and owner != entity_id and owner != allow:
            raise ConflictError(
                f"slug {candidate!r} is already used by {owner}", slug=candidate, entity_id=owner
            )
        return candidate

    def _pending_id(self, entity_type: str, entity_id: str) -> str | None:
        candidates = [
            (key[1], state.revision)
            for key, state in self._workflow.items()
            if key[0] == entity_type and state.head_id == entity_id
        ]
        open_ones = [
            ident
            for ident, _revision in candidates
            if self._entities.get(ident) is not None
            and self._entities[ident].status.value in OPEN_STATUSES
        ]
        if not open_ones:
            return None
        return sorted(open_ones, key=lambda ident: -self._entities[ident].revision)[0]

    def _write_snapshot(self, record: EntityRecord, *, actor: Actor | None) -> None:
        key = (record.entity_type.value, record.id, record.revision)
        self._snapshots[key] = RevisionRecord(
            entity_type=record.entity_type.value,
            entity_id=record.id,
            revision=record.revision,
            payload=snapshot_payload(record),
            created_by=actor.id if actor else None,
            created_at=_now(),
        )

    def _write_audit(
        self,
        *,
        actor: Actor | None,
        action: Action,
        entity_type: str | None,
        entity_id: str | None,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> None:
        self._audit_seq += 1
        self._audit.append(
            AuditEntry(
                id=self._audit_seq,
                actor_id=actor.id if actor else None,
                actor_name=actor.display_name if actor else None,
                action=action.value,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=_jsonable(payload),
                request_id=request_id,
                created_at=_now(),
            )
        )


# ------------------------------------------------------------------ module helpers


def _require_editable(entity_type: str) -> None:
    if entity_type not in EDITABLE_TYPES:
        raise ValidationError(
            f"{entity_type!r} is not an editable entity type",
            entity_type=entity_type,
            editable=sorted(EDITABLE_TYPES),
        )


def _role(value: Any) -> Role:
    try:
        return Role(str(value))
    except ValueError:
        return Role.EDITOR


def _state(
    entity_type: str, entity_id: str, previous: WorkflowState | None, updates: dict[str, Any]
) -> WorkflowState:
    base = previous or WorkflowState(
        entity_type=entity_type, entity_id=entity_id, status=Status.DRAFT, revision=1
    )
    return replace(base, **updates)


def _first_name(draft: EntityDraft, lang: str) -> str | None:
    for name in draft.names:
        if name.lang == lang and name.kind == "preferred":
            return name.form
    for name in draft.names:
        if name.lang == lang:
            return name.form
    return None


def _changed_fields(before: EntityRecord, after: EntityRecord) -> dict[str, Any]:
    old = snapshot_payload(before)
    new = snapshot_payload(after)
    return {
        key: {"before": old.get(key), "after": new.get(key)}
        for key in sorted(set(old) | set(new))
        if old.get(key) != new.get(key)
    }


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        out[key] = _json_value(value)
    return out


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value") and isinstance(value.value, str):
        return str(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _assertions_from_docs(docs: dict[str, list[Any]]) -> dict[str, AssertionRecord]:
    """Claims as the fixtures declare them, so the review loop has something to review."""
    from ..domain.temporal import TemporalInterval
    from .fixtures import build_temporal

    out: dict[str, AssertionRecord] = {}
    for item in docs.get("assertions", []):
        if not isinstance(item, dict):  # pragma: no cover - defensive
            continue
        assertion_id = str(item.get("id") or "")
        if not assertion_id:
            continue
        subject = dict(item.get("subject") or {})
        target = dict(item.get("object") or {})
        temporal = build_temporal(item.get("temporal"))
        evidence = tuple(
            _evidence(entry) for entry in (item.get("evidence") or []) if isinstance(entry, dict)
        )
        out[assertion_id] = AssertionRecord(
            id=assertion_id,
            subject_type=str(subject.get("type") or "place"),
            subject_id=str(subject.get("id") or ""),
            predicate=str(item.get("predicate") or "related_to"),
            object_type=_str(target.get("type")),
            object_id=_str(target.get("id")),
            value=_str(item.get("value")),
            status=_assertion_status(item.get("status")),
            topic_fa=_str(item.get("topic_fa") or item.get("topic")),
            topic_en=_str(item.get("topic_en") or item.get("topic")),
            note_fa=_str(item.get("note_fa") or item.get("note")),
            confidence=_confidence(temporal),
            temporal=temporal if isinstance(temporal, TemporalInterval) else None,
            evidence=evidence,
            created_by=_str(item.get("created_by")),
        )
    return out


def _evidence(entry: dict[str, Any]) -> Any:
    from ..domain.model import EvidenceRef

    return EvidenceRef(
        source_id=str(entry.get("source") or ""),
        locator_type=str(entry.get("locator_type") or "page"),
        locator=_str(entry.get("locator")),
        quote_original=_str(entry.get("quote_original")),
        quote_translation=_str(entry.get("quote_translation")),
        stance=str(entry.get("stance") or "supports"),
    )


def _assertion_status(value: Any) -> AssertionStatus:
    try:
        return AssertionStatus(str(value or "accepted"))
    except ValueError:
        return AssertionStatus.ACCEPTED


def _confidence(temporal: Any) -> Any:
    from ..domain.enums import Confidence

    return getattr(temporal, "confidence", Confidence.MEDIUM)


def _str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


__all__ = ["DEV_PASSWORD_ENV", "EDITABLE_TYPES", "OPEN_STATUSES", "FixturesEditorialMixin"]
