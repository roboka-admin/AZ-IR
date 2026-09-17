"""The editorial workflow: who may do what, in which order, and what must be true first.

Routers call this; repositories never decide policy (AGENTS.md rules 2-4). Every entry point here
answers three questions in the same order:

1. **Is the panel on?** ``AZIR_EDITORIAL_ENABLED`` is auto-off in production (ADR-0010).
2. **May this actor do this?** Roles and the state machine live in ``domain.editorial``.
3. **Is the content ready?** Data lint gates publication; a claim needs supporting evidence.

Everything that happens is written to the audit log by the repository, in the same transaction as
the change itself -- an action that cannot be attributed did not happen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..core.config import Settings
from ..core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    TooManyRequestsError,
    UnauthorizedError,
    ValidationError,
)
from ..core.security import LoginThrottle
from ..domain.editorial import (
    ACTION_TARGET,
    Action,
    Role,
    action_for,
    allowed_targets,
    assertion_may,
)
from ..domain.enums import AssertionStatus, Status
from ..domain.model import EntityRecord
from ..repositories.ports import (
    Actor,
    AssertionRecord,
    AtlasRepository,
    AuditEntry,
    EditorialRepository,
    EntityDraft,
    RevisionRecord,
    SessionTicket,
    WorkflowState,
)
from ..repositories.snapshots import draft_from_record
from .lint import DataLinter

#: Login throttling has to outlive a request, and services are built per request. One process, one
#: throttle. A multi-worker deployment should move this into the cache (documented in docs/07).
_THROTTLE = LoginThrottle()

#: Editable through the panel. Sources and periods are bibliography and chronology: changing them
#: silently would rewrite the provenance of every claim that cites them, so they stay in migrations.
EDITABLE_TYPES: tuple[str, ...] = ("place", "person", "event", "political_entity", "article")


def reset_throttle() -> None:
    """Tests only: forget every recorded login failure."""
    _THROTTLE._events.clear()


class EditorialService:
    """Write-side orchestration: sessions, drafts, the review loop, claims, users, lint."""

    def __init__(
        self,
        editorial: EditorialRepository,
        atlas: AtlasRepository,
        settings: Settings,
        linter: DataLinter | None = None,
    ) -> None:
        self._editorial = editorial
        self._atlas = atlas
        self._settings = settings
        self._linter = linter or DataLinter(atlas, settings)
        _THROTTLE.max_attempts = settings.login_max_attempts
        _THROTTLE.window_seconds = float(settings.login_window_seconds)

    @property
    def driver_name(self) -> str:
        return self._editorial.driver_name

    # ------------------------------------------------------------------ session

    def login(
        self,
        email: str,
        password: str,
        *,
        request_id: str | None = None,
        user_agent: str | None = None,
        client_ip: str | None = None,
    ) -> SessionTicket:
        self._require_enabled()
        key = f"{email.strip().lower()}|{client_ip or '-'}"
        wait = _THROTTLE.wait_seconds(key)
        if wait > 0:
            raise TooManyRequestsError(
                "too many failed logins; wait before trying again",
                retry_after_seconds=int(wait) + 1,
            )
        actor = self._editorial.authenticate(email, password)
        if actor is None:
            _THROTTLE.register_failure(key)
            self._editorial.record_audit(
                actor=None,
                action=Action.LOGIN_FAILED,
                entity_type="user",
                payload={"email": email.strip().lower()},
                request_id=request_id,
            )
            # One message for "no such account" and "wrong password": the difference is only
            # useful to somebody enumerating addresses.
            raise UnauthorizedError("email or password is incorrect")
        _THROTTLE.register_success(key)
        ticket = self._editorial.open_session(
            actor, ttl_seconds=self._settings.session_ttl_seconds, user_agent=user_agent
        )
        self._editorial.record_audit(
            actor=actor,
            action=Action.LOGIN,
            entity_type="user",
            entity_id=actor.id,
            payload={"role": actor.role.value},
            request_id=request_id,
        )
        return ticket

    def logout(self, token: str | None, *, request_id: str | None = None) -> None:
        if not token:
            return
        actor = self._editorial.session_actor(token)
        self._editorial.close_session(token)
        if actor is not None:
            self._editorial.record_audit(
                actor=actor, action=Action.LOGOUT, entity_type="user", entity_id=actor.id,
                request_id=request_id,
            )

    def actor_for(self, token: str | None) -> Actor:
        """The actor behind a session cookie, or 401."""
        self._require_enabled()
        if not token:
            raise UnauthorizedError("this endpoint needs an editorial session")
        actor = self._editorial.session_actor(token)
        if actor is None:
            raise UnauthorizedError("the session expired or was revoked; sign in again")
        return actor

    def authorize(self, actor: Actor, action: Action) -> None:
        if not actor.may(action):
            raise PermissionDeniedError(
                f"role {actor.role.value!r} may not {action.value}",
                role=actor.role.value,
                action=action.value,
                requires=sorted(role.value for role in _roles_for(action)),
            )

    def check_csrf(self, token: str | None, csrf_token: str | None) -> None:
        """Double-submit check for every state-changing request."""
        if not self._editorial.session_csrf_ok(token or "", csrf_token):
            raise PermissionDeniedError(
                "the CSRF token is missing or does not match this session",
                header=self._settings.csrf_header_name,
            )

    # ------------------------------------------------------------------ content

    def create(
        self, actor: Actor, draft: EntityDraft, *, request_id: str | None = None
    ) -> EntityRecord:
        self._require_enabled()
        self.authorize(actor, Action.CREATE)
        self._validate_draft(draft)
        return self._editorial.create_draft(draft, actor=actor, request_id=request_id)

    def revise(
        self,
        actor: Actor,
        entity_type: str,
        entity_id: str,
        draft: EntityDraft,
        *,
        request_id: str | None = None,
    ) -> EntityRecord:
        """Save an edit.

        Editing a *published* record does not touch it: a draft copy at the next revision is
        created and edited instead, so the public version stays intact until a reviewer approves
        the copy (ADR-0010 rule 4, the reviewed-copy pattern).
        """
        self._require_enabled()
        self.authorize(actor, Action.UPDATE)
        self._validate_draft(draft)
        current = self._require_record(entity_type, entity_id)
        if current.status is Status.PUBLISHED:
            self.authorize(actor, Action.BEGIN_REVISION)
            copy = self._editorial.begin_revision(
                entity_type, entity_id, actor=actor, request_id=request_id
            )
            # The head owns the public slug; a copy must not shadow it in search or URLs.
            return self._editorial.update_draft(
                entity_type, copy.id, replace(draft, slug=None), actor=actor, request_id=request_id
            )
        return self._editorial.update_draft(
            entity_type, entity_id, draft, actor=actor, request_id=request_id
        )

    def record_for(self, actor: Actor, entity_type: str, entity_id: str) -> EntityRecord:
        """An editable record by id or slug, authorized for panel reading."""
        self._require_enabled()
        self.authorize(actor, Action.READ_QUEUE)
        return self._require_record(entity_type, entity_id)

    def merged_draft(
        self, actor: Actor, entity_type: str, entity_id: str, patch: Mapping[str, Any]
    ) -> EntityDraft:
        """Turn a partial PATCH into the complete draft the repository requires.

        Merging happens here -- not in the router and not in the repository -- because it needs both
        the current content (a read) and the write permission (policy).
        """
        self._require_enabled()
        self.authorize(actor, Action.UPDATE)
        record = self._require_record(entity_type, entity_id)
        merged = replace(draft_from_record(record), **dict(patch))
        self._validate_draft(merged)
        return merged

    def transition(
        self,
        actor: Actor,
        entity_type: str,
        entity_id: str,
        action: Action,
        *,
        note: str | None = None,
        request_id: str | None = None,
    ) -> EntityRecord:
        self._require_enabled()
        self.authorize(actor, action)
        target = ACTION_TARGET.get(action)
        if target is None:
            raise ValidationError(
                f"{action.value!r} is not a workflow transition",
                action=action.value,
                transitions=[item.value for item in _TRANSITION_ACTIONS],
            )
        record = self._require_record(entity_type, entity_id)
        legal = action_for(record.status, target)
        if legal is None:
            raise ConflictError(
                f"{record.status.value} -> {target.value} is not a legal transition",
                entity_id=entity_id,
                status=record.status.value,
                target=target.value,
                allowed=[status.value for status in allowed_targets(record.status)],
            )
        if legal is not action and action is not Action.PUBLISH:
            raise ConflictError(
                f"moving {record.status.value} -> {target.value} is the action {legal.value!r}",
                entity_id=entity_id,
                action=action.value,
                expected=legal.value,
            )
        if target is Status.PUBLISHED:
            self._gate_publish(record, actor, action)
        return self._editorial.transition(
            entity_type, entity_id, target, actor=actor, request_id=request_id, note=note
        )

    def _gate_publish(self, record: EntityRecord, actor: Actor, action: Action) -> None:
        """Rule 3 (only from review) plus the data-lint gates, before anything is written."""
        if action is not Action.RESTORE and record.status is not Status.IN_REVIEW:
            raise ConflictError(
                "only a record that is in review can be published (ADR-0010 rule 3)",
                entity_id=record.id,
                status=record.status.value,
                required=Status.IN_REVIEW.value,
            )
        findings = self._linter.gates(record)
        if findings:
            raise ValidationError(
                "this record cannot be published yet: data lint reports blocking problems",
                entity_id=record.id,
                rules=sorted({finding.rule for finding in findings}),
                findings=[finding.as_dict() for finding in findings],
            )

    def review_assertion(
        self,
        actor: Actor,
        assertion_id: str,
        target: AssertionStatus,
        *,
        note: str | None = None,
        topic_fa: str | None = None,
        topic_en: str | None = None,
        request_id: str | None = None,
    ) -> AssertionRecord:
        self._require_enabled()
        self.authorize(actor, Action.REVIEW_ASSERTION)
        current = self._editorial.assertion(assertion_id)
        if current is None:
            raise NotFoundError(f"no assertion with id {assertion_id!r}")
        if not assertion_may(current.status, target):
            raise ConflictError(
                f"{current.status.value} -> {target.value} is not a legal claim transition",
                assertion_id=assertion_id,
                status=current.status.value,
                target=target.value,
            )
        if target is AssertionStatus.DISPUTED and not (
            topic_fa or topic_en or current.topic_fa or current.topic_en
        ):
            # A disagreement with no topic cannot be presented as two positions (rule D13): the
            # reader would see competing values and no way to tell what they compete about.
            raise ValidationError(
                "a disputed claim needs a topic so competing positions can be shown side by side",
                assertion_id=assertion_id,
                rule="D13",
                parameter="topic_fa",
            )
        return self._editorial.review_assertion(
            assertion_id,
            target,
            actor=actor,
            request_id=request_id,
            note=note,
            topic_fa=topic_fa,
            topic_en=topic_en,
        )

    # ------------------------------------------------------------------ reading the workflow

    def queue(
        self,
        actor: Actor,
        *,
        statuses: tuple[Status, ...] = (Status.DRAFT, Status.IN_REVIEW, Status.CHANGES_REQUESTED),
        entity_type: str | None = None,
        limit: int = 50,
        locale: str = "fa",
    ) -> dict[str, Any]:
        self._require_enabled()
        self.authorize(actor, Action.READ_QUEUE)
        records = self._editorial.queue(statuses=statuses, entity_type=entity_type, limit=limit)
        return {
            "data": [self._queue_item(record, locale) for record in records],
            "meta": {
                "count": len(records),
                "statuses": [status.value for status in statuses],
                "entity_type": entity_type,
                "driver": self._editorial.driver_name,
            },
        }

    def _queue_item(self, record: EntityRecord, locale: str) -> dict[str, Any]:
        state = self._editorial.workflow_state(record.entity_type.value, record.id)
        return {
            "id": record.id,
            "entity_type": record.entity_type.value,
            "kind": record.kind,
            "slug": record.slug,
            "status": record.status.value,
            "revision": record.revision,
            "title": record.display_name(locale),
            "title_secondary": record.secondary_name(locale),
            "summary": record.summary if locale == "fa" else (record.summary_en or record.summary),
            "temporal": record.temporal_display(locale),
            "has_geometry": record.has_geometry,
            "head_id": state.head_id if state else None,
            "available_actions": self.available_actions(actor=None, record=record, state=state),
            "blocking_findings": [
                finding.as_dict() for finding in self._linter.gates(record)
            ],
            "updated_at": state.updated_at.isoformat() if state and state.updated_at else None,
        }

    def detail(
        self, actor: Actor, entity_type: str, entity_id: str, *, locale: str = "fa"
    ) -> dict[str, Any]:
        """The panel's view of one record: content, workflow state, history and lint."""
        self._require_enabled()
        self.authorize(actor, Action.READ_QUEUE)
        record = self._require_record(entity_type, entity_id)
        state = self._editorial.workflow_state(entity_type, record.id)
        findings = self._lint_for_editor(record)
        pending = self._editorial.pending_revision(entity_type, record.id)
        return {
            "data": {
                "entity_type": entity_type,
                "id": record.id,
                "slug": record.slug,
                "kind": record.kind,
                "status": record.status.value,
                "revision": record.revision,
                "title": record.display_name(locale),
                "names": [
                    {
                        "form": name.form,
                        "lang": name.lang,
                        "kind": name.kind,
                        "script": name.script,
                        "transliteration": name.transliteration,
                    }
                    for name in record.names
                ],
                "temporal": (
                    {
                        "year_from": record.temporal.year_from,
                        "year_to": record.temporal.year_to,
                        "precision": record.temporal.precision.value,
                        "calendar": record.temporal.calendar.value,
                        "confidence": record.temporal.confidence.value,
                        "display": record.temporal.display_text(locale),
                    }
                    if record.temporal
                    else None
                ),
                "geometries": [
                    {
                        "geojson": geometry.geojson,
                        "kind": geometry.kind.value,
                        "certainty": geometry.certainty.value,
                        "year_from": geometry.year_from,
                        "year_to": geometry.year_to,
                        "needs_digitisation": geometry.needs_digitisation,
                    }
                    for geometry in record.geometries
                ],
                "summary": record.summary,
                "summary_en": record.summary_en,
                "source_ids": list(record.source_ids),
                "importance": record.importance,
                "certainty": record.certainty,
                "attestation": record.attestation.value if record.attestation else None,
                "body_md": record.body_md,
                "extra": dict(record.extra),
                "workflow": _state_dict(state),
                "pending_revision": pending.id if pending else None,
                "available_actions": self.available_actions(actor=actor, record=record, state=state),
                "lint": [finding.as_dict() for finding in findings],
            },
            "meta": {
                "driver": self._editorial.driver_name,
                "actor": {"id": actor.id, "role": actor.role.value},
            },
        }

    def _lint_for_editor(self, record: EntityRecord) -> list[Any]:
        """What the panel shows: today's problems, plus what would block publication.

        A draft without a source is not an error *yet* -- but it is the one thing the editor most
        needs to see while writing, because it will stop the record at the gate. Reporting it early
        is the difference between a lint that teaches and a lint that only rejects.
        """
        findings = list(self._linter.record(record))
        if record.status is not Status.PUBLISHED:
            known = {(item.rule, item.entity_id) for item in findings}
            findings.extend(
                item
                for item in self._linter.gates(record)
                if (item.rule, item.entity_id) not in known
            )
        findings.sort(key=lambda item: (item.level != "error", item.rule))
        return findings

    def available_actions(
        self, *, actor: Actor | None, record: EntityRecord, state: WorkflowState | None
    ) -> list[dict[str, Any]]:
        """What the UI may offer for this record, given the state machine and the actor's role."""
        out: list[dict[str, Any]] = []
        for target in allowed_targets(record.status):
            action = action_for(record.status, target)
            if action is None:
                continue
            permitted = actor is None or actor.may(action)
            blocked: list[str] = []
            if target is Status.PUBLISHED and actor is not None:
                blocked = [
                    finding.rule for finding in self._linter.gates(record)
                ] if action is not Action.RESTORE else []
            out.append(
                {
                    "action": action.value,
                    "target": target.value,
                    "permitted": permitted and not blocked,
                    "blocked_by": blocked,
                    "requires": sorted(role.value for role in _roles_for(action)),
                }
            )
        if record.status is Status.PUBLISHED and actor is not None:
            out.append(
                {
                    "action": Action.BEGIN_REVISION.value,
                    "target": Status.DRAFT.value,
                    "permitted": actor.may(Action.BEGIN_REVISION),
                    "blocked_by": [],
                    "requires": sorted(role.value for role in _roles_for(Action.BEGIN_REVISION)),
                }
            )
        _ = state  # reserved: a future "you already submitted this" hint
        return out

    def history(self, actor: Actor, entity_type: str, entity_id: str) -> dict[str, Any]:
        self._require_enabled()
        self.authorize(actor, Action.READ_QUEUE)
        revisions: list[RevisionRecord] = self._editorial.revisions(entity_type, entity_id)
        return {
            "data": [
                {
                    "revision": item.revision,
                    "created_by": item.created_by,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "status": item.payload.get("status"),
                    "title": _title_of(item.payload),
                    "payload": dict(item.payload),
                }
                for item in revisions
            ],
            "meta": {"count": len(revisions), "entity_id": entity_id},
        }

    def trail(
        self,
        actor: Actor,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._require_enabled()
        self.authorize(actor, Action.READ_QUEUE)
        entries: list[AuditEntry] = self._editorial.audit(
            entity_type=entity_type, entity_id=entity_id, action=action, limit=limit
        )
        return {
            "data": [_audit_dict(entry) for entry in entries],
            "meta": {"count": len(entries), "filters": {"entity_type": entity_type,
                                                         "entity_id": entity_id, "action": action}},
        }

    def lint(
        self,
        actor: Actor | None,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        persist: bool = False,
    ) -> dict[str, Any]:
        self._require_enabled()
        if actor is not None:
            self.authorize(actor, Action.READ_QUEUE)
        if entity_id:
            record = self._require_record(entity_type or _guess_type(entity_id), entity_id)
            findings = self._linter.record(record)
            scope = f"{record.entity_type.value}:{record.id}"
        else:
            findings = self._linter.corpus()
            scope = "corpus"
        report = self._linter.report(findings, scope=scope)
        if persist:
            report["data"]["lint_run_id"] = self._editorial.record_lint(
                scope=scope, findings=[item.as_dict() for item in findings], actor=actor
            )
        return report

    # ------------------------------------------------------------------ users

    def users(self, actor: Actor) -> dict[str, Any]:
        self._require_enabled()
        self.authorize(actor, Action.MANAGE_USERS)
        people = self._editorial.list_users()
        return {
            "data": [
                {
                    "id": person.id,
                    "email": person.email,
                    "display_name": person.display_name,
                    "role": person.role.value,
                    "is_active": person.is_active,
                }
                for person in people
            ],
            "meta": {"count": len(people)},
        }

    def add_user(
        self,
        actor: Actor,
        *,
        email: str,
        display_name: str,
        role: Role,
        password: str,
        request_id: str | None = None,
    ) -> Actor:
        self._require_enabled()
        self.authorize(actor, Action.MANAGE_USERS)
        if role is Role.ADMIN and actor.role is not Role.ADMIN:  # pragma: no cover - defensive
            raise PermissionDeniedError("only an admin may create an admin")
        created = self._editorial.create_user(
            email=email, display_name=display_name, role=role, password=password
        )
        self._editorial.record_audit(
            actor=actor,
            action=Action.MANAGE_USERS,
            entity_type="user",
            entity_id=created.id,
            payload={"created": created.email, "role": created.role.value},
            request_id=request_id,
        )
        return created

    # ------------------------------------------------------------------ internals

    def _require_enabled(self) -> None:
        if not self._settings.editorial_on:
            raise PermissionDeniedError(
                "the editorial API is disabled in this environment "
                "(set AZIR_EDITORIAL_ENABLED=1 to turn it on)",
                env=self._settings.env,
            )

    def _require_record(self, entity_type: str, entity_id: str) -> EntityRecord:
        if entity_type not in EDITABLE_TYPES:
            raise ValidationError(
                f"{entity_type!r} is not an editable entity type",
                entity_type=entity_type,
                editable=list(EDITABLE_TYPES),
            )
        record = self._atlas.entity(entity_type, entity_id)
        if record is None:
            raise NotFoundError(f"no {entity_type} with id or slug {entity_id!r}")
        return record

    def _validate_draft(self, draft: EntityDraft) -> None:
        if draft.entity_type.value not in EDITABLE_TYPES:
            raise ValidationError(
                f"{draft.entity_type.value!r} is not an editable entity type",
                entity_type=draft.entity_type.value,
                editable=list(EDITABLE_TYPES),
            )
        if not draft.names:
            raise ValidationError(
                "a record needs at least one name", parameter="names"
            )
        for name in draft.names:
            if not name.form.strip():
                raise ValidationError("a name variant cannot be empty", parameter="names")
        if not any(name.lang == "fa" for name in draft.names):
            raise ValidationError(
                "a Persian name variant is required (the atlas is Persian-first)",
                parameter="names",
                rule="D5",
            )
        if not 0.0 <= float(draft.importance) <= 1.0:
            raise ValidationError(
                "importance must be between 0 and 1", parameter="importance"
            )
        if draft.temporal is not None:
            try:  # the interval enforces its own invariants (no fake precision, D8)
                _ = draft.temporal.midpoint
            except ValueError as exc:
                raise ValidationError(str(exc), parameter="temporal") from exc
        unknown = [
            source_id
            for source_id in draft.source_ids
            if self._atlas.entity("source", source_id) is None
        ]
        if unknown:
            raise ValidationError(
                "unknown source id(s); a citation must point at a source that exists",
                parameter="source_ids",
                unknown=unknown,
            )


_TRANSITION_ACTIONS: tuple[Action, ...] = (
    Action.SUBMIT,
    Action.APPROVE,
    Action.REQUEST_CHANGES,
    Action.ARCHIVE,
    Action.RESTORE,
)


def _roles_for(action: Action) -> frozenset[Role]:
    from ..domain.editorial import ACTION_ROLES

    return ACTION_ROLES.get(action, frozenset())


def _guess_type(entity_id: str) -> str:
    from ..core.ids import PREFIX

    prefix = entity_id.split("_", 1)[0] if "_" in entity_id else ""
    for entity_type, short in PREFIX.items():
        if short == prefix and entity_type in EDITABLE_TYPES:
            return str(entity_type)
    return "place"


def _state_dict(state: WorkflowState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "status": state.status.value,
        "revision": state.revision,
        "head_id": state.head_id,
        "submitted_by": state.submitted_by,
        "submitted_at": state.submitted_at.isoformat() if state.submitted_at else None,
        "reviewed_by": state.reviewed_by,
        "reviewed_at": state.reviewed_at.isoformat() if state.reviewed_at else None,
        "review_note": state.review_note,
        "published_by": state.published_by,
        "published_at": state.published_at.isoformat() if state.published_at else None,
        "archived_by": state.archived_by,
        "archived_at": state.archived_at.isoformat() if state.archived_at else None,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }


def _audit_dict(entry: AuditEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "action": entry.action,
        "actor_id": entry.actor_id,
        "actor": entry.actor_name,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "payload": dict(entry.payload),
        "request_id": entry.request_id,
        "at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _title_of(payload: Mapping[str, Any]) -> str | None:
    for name in payload.get("names") or ():
        if isinstance(name, dict) and name.get("lang") == "fa" and name.get("kind") == "preferred":
            return str(name.get("form"))
    for name in payload.get("names") or ():
        if isinstance(name, dict) and name.get("form"):
            return str(name.get("form"))
    return None


__all__ = ["EDITABLE_TYPES", "EditorialService", "reset_throttle"]
