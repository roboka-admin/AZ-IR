"""Write side of the PostGIS driver: identity, drafts, the review loop, claims, audit (ADR-0010).

The rules this module lives by:

* **One transaction per write.** Content, workflow metadata, the audit row and the revision
  snapshot land together or not at all. A half-applied edit is worse than a rejected one.
* **The audit log is append-only.** Nothing here updates or deletes an audit row, and the payload
  carries the field-level before/after so a reviewer can see what actually moved.
* **``status`` lives on the entity row; ``editorial_state`` records who moved it and when**, plus
  the ``head_id`` link that implements the reviewed-copy pattern (rule 4): editing a published
  record creates a draft row, the public record stays untouched until the copy is approved.
* **Presentation columns are derived** by ``domain.semantic_zoom`` at write time, never typed.
* **Credentials never leave this module.** Passwords are hashed on the way in, sessions are stored
  as digests, and no query selects ``password_hash`` into a returned object.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.ids import new_id, slugify
from ...core.security import (
    constant_time_equals,
    hash_password,
    new_token,
    token_digest,
    verify_password,
)
from ...domain import text as textnorm
from ...domain.editorial import (
    Action,
    Role,
    action_for,
    allowed_targets,
    assertion_may,
    claim_is_evidenced,
    is_editable,
)
from ...domain.enums import AssertionStatus, Confidence, EntityType, Precision, Status
from ...domain.model import EntityRecord
from ...domain.semantic_zoom import presentation_for
from ...domain.temporal import TemporalInterval
from ..ports import (
    Actor,
    AssertionRecord,
    AuditEntry,
    EntityDraft,
    RevisionRecord,
    SessionTicket,
    WorkflowState,
)
from ..snapshots import (
    draft_extra,
    not_editable_error,
    snapshot_payload,
    temporal_columns,
)
from . import mappers, schema

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine

    from ...core.config import Settings

#: Reference data (sources, periods) is not editable through the panel yet: it is bibliography,
#: and changing it silently would rewrite the provenance of every claim that cites it.
EDITABLE_TYPES: frozenset[str] = frozenset(schema.ENTITY_TABLES)

#: Statuses that mean "somebody is working on this".
OPEN_STATUSES: tuple[str, ...] = ("draft", "in_review", "changes_requested", "imported_unverified")


def _table(entity_type: str) -> sa.Table:
    table = schema.ENTITY_TABLES.get(entity_type)
    if table is None:
        raise ValidationError(
            f"{entity_type!r} is not an editable entity type",
            entity_type=entity_type,
            editable=sorted(EDITABLE_TYPES),
        )
    return table


def _role(value: Any) -> Role:
    try:
        return Role(str(value))
    except ValueError:
        return Role.EDITOR


def _actor(row: Any) -> Actor:
    return Actor(
        id=str(row["id"]),
        email=str(row["email"]),
        display_name=str(row["display_name"]),
        role=_role(row["role"]),
        is_active=bool(row["is_active"]),
    )


def _copyable_columns(table: sa.Table) -> list[str]:
    """Columns a revision copy carries over. Identity, workflow and generated columns do not."""
    skip = {"id", "slug", "status", "revision", "created_at", "updated_at"}
    return [column.name for column in table.columns if column.name not in skip]


class PostgisEditorialMixin:
    """Write methods mixed into :class:`PostgisRepository` (it owns the engine and settings)."""

    if TYPE_CHECKING:  # pragma: no cover - declarations only; the subclass provides them
        _engine: Engine
        _settings: Settings

        def _load_records(
            self,
            ids: list[str],
            *,
            with_relationships: bool,
            connection: Connection | None = None,
        ) -> list[EntityRecord]:
            """Provided by :class:`PostgisRepository` (the read side of the same adapter)."""

    # ------------------------------------------------------------------ identity

    def authenticate(self, email: str, password: str) -> Actor | None:
        """Verify credentials. A wrong password and an unknown address look identical from here."""
        sql = text(
            "SELECT id, email, display_name, role, is_active, password_hash "
            "FROM app_user WHERE lower(email) = lower(:email)"
        )
        with self._engine.begin() as connection:
            row = connection.execute(sql, {"email": email.strip()}).mappings().first()
            if row is None or not bool(row["is_active"]):
                return None
            if not verify_password(_text(row["password_hash"]), password):
                return None
            connection.execute(
                text("UPDATE app_user SET last_login_at = now() WHERE id = :id"), {"id": row["id"]}
            )
            actor = _actor(row)
        return actor

    def user_by_id(self, user_id: str) -> Actor | None:
        sql = text(
            "SELECT id, email, display_name, role, is_active FROM app_user WHERE id = :id"
        )
        with self._engine.connect() as connection:
            row = connection.execute(sql, {"id": user_id}).mappings().first()
        return _actor(row) if row else None

    def create_user(self, *, email: str, display_name: str, role: Role, password: str) -> Actor:
        address = email.strip().lower()
        if not address or "@" not in address:
            raise ValidationError("a valid email is required", parameter="email")
        if len(password) < 10:
            raise ValidationError(
                "passwords must be at least 10 characters", parameter="password", minimum=10
            )
        user_id = new_id("user")
        with self._engine.begin() as connection:
            taken = connection.execute(
                text("SELECT id FROM app_user WHERE lower(email) = lower(:email)"),
                {"email": address},
            ).first()
            if taken is not None:
                raise ConflictError("that email already has an account", email=address)
            connection.execute(
                schema.app_user.insert().values(
                    id=user_id,
                    email=address,
                    display_name=display_name.strip() or address,
                    role=role.value,
                    is_active=True,
                    password_hash=hash_password(password),
                )
            )
        return Actor(id=user_id, email=address, display_name=display_name.strip() or address, role=role)

    def list_users(self) -> list[Actor]:
        sql = text(
            "SELECT id, email, display_name, role, is_active FROM app_user ORDER BY created_at, id"
        )
        with self._engine.connect() as connection:
            return [_actor(row) for row in connection.execute(sql).mappings().all()]

    def open_session(
        self, actor: Actor, *, ttl_seconds: int, user_agent: str | None = None
    ) -> SessionTicket:
        token = new_token(32)
        csrf = new_token(24)
        expires = datetime.now(UTC) + timedelta(seconds=max(60, ttl_seconds))
        with self._engine.begin() as connection:
            connection.execute(
                schema.app_session.insert().values(
                    id=token_digest(token),
                    user_id=actor.id,
                    csrf_hash=token_digest(csrf),
                    expires_at=expires,
                    user_agent=(user_agent or "")[:400] or None,
                )
            )
            connection.execute(
                text("DELETE FROM app_session WHERE expires_at < now() - interval '1 day'")
            )
        return SessionTicket(token=token, csrf_token=csrf, actor=actor, expires_at=expires)

    def session_actor(self, token: str) -> Actor | None:
        if not token:
            return None
        sql = text(
            "SELECT u.id, u.email, u.display_name, u.role, u.is_active "
            "FROM app_session s JOIN app_user u ON u.id = s.user_id "
            "WHERE s.id = :digest AND s.revoked_at IS NULL AND s.expires_at > now() AND u.is_active"
        )
        with self._engine.connect() as connection:
            row = connection.execute(sql, {"digest": token_digest(token)}).mappings().first()
        return _actor(row) if row else None

    def session_csrf_ok(self, token: str, csrf_token: str | None) -> bool:
        """Double-submit check: the header token must hash to what this session stored."""
        if not token or not csrf_token:
            return False
        sql = text(
            "SELECT csrf_hash FROM app_session "
            "WHERE id = :digest AND revoked_at IS NULL AND expires_at > now()"
        )
        with self._engine.connect() as connection:
            stored = connection.execute(sql, {"digest": token_digest(token)}).scalar()
        return constant_time_equals(_text(stored), token_digest(csrf_token))

    def close_session(self, token: str) -> None:
        if not token:
            return
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE app_session SET revoked_at = now() "
                    "WHERE id = :digest AND revoked_at IS NULL"
                ),
                {"digest": token_digest(token)},
            )

    # ------------------------------------------------------------------ content

    def create_draft(
        self, draft: EntityDraft, *, actor: Actor | None, request_id: str | None
    ) -> EntityRecord:
        entity_type = draft.entity_type.value
        table = _table(entity_type)
        entity_id = new_id(entity_type)
        presentation = presentation_for(
            importance=draft.importance,
            kind=draft.kind,
            entity_type=draft.entity_type,
            source_count=len(set(draft.source_ids)),
        )
        with self._engine.begin() as connection:
            slug = self._reserve_slug(connection, table, draft, entity_type, entity_id)
            row: dict[str, Any] = {
                "id": entity_id,
                "kind": draft.kind,
                "slug": slug,
                "status": Status.DRAFT.value,
                "revision": 1,
                "importance": float(draft.importance),
                "rank": presentation.rank,
                "layer": presentation.layer,
                "min_zoom": presentation.min_zoom,
                "max_zoom": presentation.max_zoom,
                **temporal_columns(draft.temporal),
                "summary_fa": draft.summary,
                "summary_en": draft.summary_en,
                "coverage_note_fa": draft.coverage_note,
                "coverage_note_en": draft.coverage_note_en,
                "certainty": draft.certainty,
                "sources": list(dict.fromkeys(draft.source_ids)),
                "extra": draft_extra(draft),
            }
            if draft.entity_type is EntityType.EVENT:
                row["attestation"] = draft.attestation.value if draft.attestation else None
            if draft.entity_type is EntityType.ARTICLE:
                row.update(
                    {
                        "title_fa": draft.title or _first_name(draft, "fa"),
                        "title_en": draft.title_en or _first_name(draft, "en"),
                        "body_fa_md": draft.body_md,
                        "body_en_md": draft.body_en_md,
                        "author": draft.author,
                        "lang": "fa",
                        "map_state": dict(draft.map_state) if draft.map_state else None,
                    }
                )
            connection.execute(table.insert().values(**row))
            self._write_facets(connection, draft, entity_type, entity_id)
            self._upsert_state(connection, entity_type, entity_id, revision=1)
            self._audit(
                connection,
                actor=actor,
                action=Action.CREATE,
                entity_type=entity_type,
                entity_id=entity_id,
                payload={"slug": slug, "status": Status.DRAFT.value, "kind": draft.kind},
                request_id=request_id,
            )
            record = self._load_records([entity_id], with_relationships=False, connection=connection)[0]
            self._snapshot(connection, record, actor=actor)
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
        table = _table(entity_type)
        with self._engine.begin() as connection:
            before = self._load_records([entity_id], with_relationships=False, connection=connection)
            if not before:
                raise NotFoundError(f"no {entity_type} with id {entity_id!r}")
            current = before[0]
            if not is_editable(current.status):
                raise not_editable_error(current.status, entity_id)
            presentation = presentation_for(
                importance=draft.importance,
                kind=draft.kind,
                entity_type=draft.entity_type,
                source_count=len(set(draft.source_ids)),
                assertion_count=current.counts.assertions,
                article_count=current.counts.articles,
                period_coverage=current.counts.periods,
            )
            values: dict[str, Any] = {
                "kind": draft.kind,
                "importance": float(draft.importance),
                "rank": presentation.rank,
                "layer": presentation.layer,
                "min_zoom": presentation.min_zoom,
                "max_zoom": presentation.max_zoom,
                **temporal_columns(draft.temporal),
                "summary_fa": draft.summary,
                "summary_en": draft.summary_en,
                "coverage_note_fa": draft.coverage_note,
                "coverage_note_en": draft.coverage_note_en,
                "certainty": draft.certainty,
                "sources": list(dict.fromkeys(draft.source_ids)),
                "extra": draft_extra(draft),
            }
            if draft.slug and draft.slug != current.slug:
                values["slug"] = self._reserve_slug(
                    connection, table, draft, entity_type, entity_id, allow=current.slug
                )
            if entity_type == EntityType.EVENT.value:
                values["attestation"] = draft.attestation.value if draft.attestation else None
            if entity_type == EntityType.ARTICLE.value:
                values.update(
                    {
                        "title_fa": draft.title,
                        "title_en": draft.title_en,
                        "body_fa_md": draft.body_md,
                        "body_en_md": draft.body_en_md,
                        "author": draft.author,
                        "map_state": dict(draft.map_state) if draft.map_state else None,
                    }
                )
            connection.execute(table.update().where(table.c.id == entity_id).values(**values))
            self._write_facets(connection, draft, entity_type, entity_id)
            self._upsert_state(connection, entity_type, entity_id, revision=current.revision)
            after = self._load_records([entity_id], with_relationships=False, connection=connection)[0]
            self._audit(
                connection,
                actor=actor,
                action=Action.UPDATE,
                entity_type=entity_type,
                entity_id=entity_id,
                payload={
                    "revision": after.revision,
                    "status": after.status.value,
                    "changed": _changed_fields(current, after),
                },
                request_id=request_id,
            )
            self._snapshot(connection, after, actor=actor)
        return after

    def begin_revision(
        self, entity_type: str, entity_id: str, *, actor: Actor | None, request_id: str | None
    ) -> EntityRecord:
        """Create the draft copy of a published record (the reviewed-copy pattern)."""
        table = _table(entity_type)
        with self._engine.begin() as connection:
            head = self._load_records([entity_id], with_relationships=False, connection=connection)
            if not head:
                raise NotFoundError(f"no {entity_type} with id {entity_id!r}")
            if head[0].status is not Status.PUBLISHED:
                raise ConflictError(
                    "only a published record needs a reviewed copy; edit the open draft instead",
                    entity_id=entity_id,
                    status=head[0].status.value,
                )
            existing = self._pending_id(connection, entity_type, entity_id)
            if existing:
                return self._load_records(
                    [existing], with_relationships=False, connection=connection
                )[0]

            draft_id = new_id(entity_type)
            revision = head[0].revision + 1
            columns = _copyable_columns(table)
            select_list = ", ".join(f"h.{column}" for column in columns)
            insert_list = ", ".join(columns)
            connection.execute(
                text(
                    f"INSERT INTO public.{table.name} (id, slug, status, revision, created_at, "
                    f"updated_at, {insert_list}) "
                    f"SELECT :draft_id, NULL, :status, :revision, now(), now(), {select_list} "
                    f"FROM public.{table.name} h WHERE h.id = :head_id"
                ),
                {
                    "draft_id": draft_id,
                    "status": Status.DRAFT.value,
                    "revision": revision,
                    "head_id": entity_id,
                },
            )
            # Facets follow the copy: names and geometries belong to the content, not to the graph.
            connection.execute(
                text(
                    "INSERT INTO name_variant (entity_type, entity_id, form, lang, script, kind, "
                    "transliteration, year_from, year_to, source_id, note, search_form) "
                    "SELECT entity_type, :draft_id, form, lang, script, kind, transliteration, "
                    "year_from, year_to, source_id, note, search_form "
                    "FROM name_variant WHERE entity_type = :entity_type AND entity_id = :head_id"
                ),
                {"draft_id": draft_id, "entity_type": entity_type, "head_id": entity_id},
            )
            connection.execute(
                text(
                    "INSERT INTO entity_geometry (entity_type, entity_id, kind, certainty, geom_json, "
                    "geom, year_from, year_to, lod_min_zoom, lod_max_zoom, source_id, note_fa, "
                    "note_en, needs_digitisation) "
                    "SELECT entity_type, :draft_id, kind, certainty, geom_json, geom, year_from, "
                    "year_to, lod_min_zoom, lod_max_zoom, source_id, note_fa, note_en, "
                    "needs_digitisation FROM entity_geometry "
                    "WHERE entity_type = :entity_type AND entity_id = :head_id"
                ),
                {"draft_id": draft_id, "entity_type": entity_type, "head_id": entity_id},
            )
            self._upsert_state(
                connection, entity_type, draft_id, revision=revision, head_id=entity_id
            )
            self._audit(
                connection,
                actor=actor,
                action=Action.BEGIN_REVISION,
                entity_type=entity_type,
                entity_id=entity_id,
                payload={"draft_id": draft_id, "revision": revision},
                request_id=request_id,
            )
            record = self._load_records([draft_id], with_relationships=False, connection=connection)[0]
            self._snapshot(connection, record, actor=actor)
        return record

    def pending_revision(self, entity_type: str, entity_id: str) -> EntityRecord | None:
        with self._engine.connect() as connection:
            draft_id = self._pending_id(connection, entity_type, entity_id)
            if not draft_id:
                return None
            records = self._load_records([draft_id], with_relationships=False, connection=connection)
        return records[0] if records else None

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
        """Move a record to ``target``, persisting the workflow metadata, audit row and snapshot."""
        table = _table(entity_type)
        with self._engine.begin() as connection:
            before = self._load_records([entity_id], with_relationships=False, connection=connection)
            if not before:
                raise NotFoundError(f"no {entity_type} with id {entity_id!r}")
            current = before[0]
            action = action_for(current.status, target)
            if action is None:
                raise ConflictError(
                    f"{current.status.value} -> {target.value} is not a legal transition",
                    entity_id=entity_id,
                    status=current.status.value,
                    target=target.value,
                    allowed=[status.value for status in allowed_targets(current.status)],
                )
            state = self._state_row(connection, entity_type, entity_id)
            head_id = _text(state["head_id"]) if state else None

            if target is Status.PUBLISHED and head_id:
                record = self._merge_into_head(
                    connection,
                    table=table,
                    entity_type=entity_type,
                    head_id=head_id,
                    draft_id=entity_id,
                    actor=actor,
                    request_id=request_id,
                    note=note,
                    revision=current.revision,
                )
            else:
                connection.execute(
                    table.update()
                    .where(table.c.id == entity_id)
                    .values(status=target.value)
                )
                updates: dict[str, Any] = {"updated_at": sa.func.now(), "revision": current.revision}
                if note is not None:
                    updates["review_note"] = note
                if actor is not None:
                    if target is Status.IN_REVIEW:
                        updates.update(submitted_by=actor.id, submitted_at=sa.func.now())
                    if target in (Status.PUBLISHED, Status.CHANGES_REQUESTED):
                        updates.update(reviewed_by=actor.id, reviewed_at=sa.func.now())
                    if target is Status.PUBLISHED:
                        updates.update(published_by=actor.id, published_at=sa.func.now())
                    if target is Status.ARCHIVED:
                        updates.update(archived_by=actor.id, archived_at=sa.func.now())
                    if target is Status.PUBLISHED and current.status is Status.ARCHIVED:
                        updates.update(published_by=actor.id, published_at=sa.func.now())
                self._upsert_state(connection, entity_type, entity_id, **updates)
                self._audit(
                    connection,
                    actor=actor,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    payload={
                        "from": current.status.value,
                        "to": target.value,
                        "revision": current.revision,
                        "note": note,
                    },
                    request_id=request_id,
                )
                record = self._load_records(
                    [entity_id], with_relationships=False, connection=connection
                )[0]
                self._snapshot(connection, record, actor=actor)
        return record

    # ------------------------------------------------------------------ queries

    def workflow_state(self, entity_type: str, entity_id: str) -> WorkflowState | None:
        with self._engine.connect() as connection:
            state = self._state_row(connection, entity_type, entity_id)
            if state is None:
                return None
            record = self._load_records([entity_id], with_relationships=False, connection=connection)
        status = record[0].status if record else Status.DRAFT
        revision = record[0].revision if record else int(state["revision"] or 1)
        return WorkflowState(
            entity_type=entity_type,
            entity_id=entity_id,
            status=status,
            revision=revision,
            head_id=_text(state["head_id"]),
            submitted_by=_text(state["submitted_by"]),
            submitted_at=_when(state["submitted_at"]),
            reviewed_by=_text(state["reviewed_by"]),
            reviewed_at=_when(state["reviewed_at"]),
            review_note=_text(state["review_note"]),
            published_by=_text(state["published_by"]),
            published_at=_when(state["published_at"]),
            archived_by=_text(state["archived_by"]),
            archived_at=_when(state["archived_at"]),
            updated_at=_when(state["updated_at"]),
        )

    def queue(
        self,
        *,
        statuses: tuple[Status, ...] = (Status.DRAFT, Status.IN_REVIEW, Status.CHANGES_REQUESTED),
        entity_type: str | None = None,
        limit: int = 50,
    ) -> list[EntityRecord]:
        sql = text(
            """
            SELECT erm.id AS id, erm.entity_type AS entity_type
            FROM entity_read_model erm
            LEFT JOIN editorial_state es
                   ON es.entity_id = erm.id AND es.entity_type = erm.entity_type
            WHERE erm.status = ANY(:statuses::text[])
              AND erm.entity_type = ANY(:narrative_types::text[])
              AND (:entity_type::text IS NULL OR erm.entity_type = :entity_type::text)
            ORDER BY es.updated_at DESC NULLS LAST, erm.id ASC
            LIMIT :limit
            """
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql,
                {
                    "statuses": [status.value for status in statuses],
                    "narrative_types": list(EDITABLE_TYPES),
                    "entity_type": entity_type,
                    "limit": max(1, min(limit, 500)),
                },
            ).all()
            ids = [str(row[0]) for row in rows]
            if not ids:
                return []
            return self._load_records(ids, with_relationships=False, connection=connection)

    def revisions(self, entity_type: str, entity_id: str) -> list[RevisionRecord]:
        sql = text(
            "SELECT entity_type, entity_id, revision, payload, created_by, created_at "
            "FROM revision_snapshot WHERE entity_type = :t AND entity_id = :id "
            "ORDER BY revision DESC, id DESC"
        )
        with self._engine.connect() as connection:
            rows = connection.execute(sql, {"t": entity_type, "id": entity_id}).mappings().all()
        return [
            RevisionRecord(
                entity_type=str(row["entity_type"]),
                entity_id=str(row["entity_id"]),
                revision=int(row["revision"]),
                payload=dict(row["payload"] or {}),
                created_by=_text(row["created_by"]),
                created_at=_when(row["created_at"]),
            )
            for row in rows
        ]

    def audit(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        sql = text(
            """
            SELECT a.id, a.actor_id, u.display_name AS actor_name, a.action, a.entity_type,
                   a.entity_id, a.payload, a.request_id, a.created_at
            FROM audit_log a LEFT JOIN app_user u ON u.id = a.actor_id
            WHERE (:entity_type::text IS NULL OR a.entity_type = :entity_type::text)
              AND (:entity_id::text IS NULL OR a.entity_id = :entity_id::text)
              AND (:action::text IS NULL OR a.action = :action::text)
            ORDER BY a.id DESC
            LIMIT :limit
            """
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                sql,
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "action": action,
                    "limit": max(1, min(limit, 500)),
                },
            ).mappings().all()
        return [_audit_entry(row) for row in rows]

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
        """For events with no content row of their own: logins, logouts, account changes."""
        with self._engine.begin() as connection:
            self._audit(
                connection,
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=payload or {},
                request_id=request_id,
            )

    # ------------------------------------------------------------------ claims

    def assertion(self, assertion_id: str) -> AssertionRecord | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM assertion WHERE id = :id"), {"id": assertion_id}
            ).mappings().first()
            if row is None:
                return None
            evidence = connection.execute(
                text(
                    "SELECT source_id, locator_type, locator, quote_original, quote_translation, "
                    "stance FROM evidence WHERE assertion_id = :id ORDER BY id"
                ),
                {"id": assertion_id},
            ).mappings().all()
        return _assertion_record(row, mappers.evidence_from([dict(item) for item in evidence]))

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
            # Rule D3 (docs/07): an accepted claim without supporting evidence is not a claim.
            raise ValidationError(
                "a claim cannot be accepted without evidence that carries it "
                "(stance=supports or qualifies, rule D3)",
                assertion_id=assertion_id,
                rule="D3",
            )
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE assertion SET status = :status, reviewed_by = :actor, "
                    "reviewed_at = now(), review_note = coalesce(:note, review_note), "
                    "topic_fa = coalesce(:topic_fa, topic_fa), "
                    "topic_en = coalesce(:topic_en, topic_en) "
                    "WHERE id = :id"
                ),
                {
                    "status": target.value,
                    "actor": actor.id if actor else None,
                    "note": note,
                    "topic_fa": topic_fa,
                    "topic_en": topic_en,
                    "id": assertion_id,
                },
            )
            self._audit(
                connection,
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
        reviewed = self.assertion(assertion_id)
        if reviewed is None:  # pragma: no cover - the row was just updated
            raise NotFoundError(f"no assertion with id {assertion_id!r}")
        return reviewed

    def record_lint(self, *, scope: str, findings: list[dict[str, Any]], actor: Actor | None) -> int:
        errors = sum(1 for item in findings if item.get("level") == "error")
        warnings = sum(1 for item in findings if item.get("level") == "warning")
        with self._engine.begin() as connection:
            result = connection.execute(
                schema.lint_run.insert()
                .values(
                    scope=scope,
                    actor_id=actor.id if actor else None,
                    errors=errors,
                    warnings=warnings,
                    findings=findings,
                )
                .returning(schema.lint_run.c.id)
            )
            return int(result.scalar_one())

    # ------------------------------------------------------------------ internals

    def _record(self, entity_id: str, connection: Connection | None = None) -> EntityRecord:
        if connection is not None:
            return self._load_records([entity_id], with_relationships=False, connection=connection)[0]
        with self._engine.connect() as fresh:
            return self._load_records([entity_id], with_relationships=False, connection=fresh)[0]

    def _reserve_slug(
        self,
        connection: Connection,
        table: sa.Table,
        draft: EntityDraft,
        entity_type: str,
        entity_id: str,
        *,
        allow: str | None = None,
    ) -> str | None:
        """Pick a slug that is free, or fail loudly. Slugs are public URLs (ADR-0008)."""
        candidate = (draft.slug or "").strip() or None
        if candidate is None:
            latin = _first_name(draft, "en") or draft.slug or ""
            candidate = slugify(latin, fallback=f"{entity_type}-{entity_id[-8:]}")
        taken = connection.execute(
            sa.select(table.c.id).where(table.c.slug == candidate, table.c.id != entity_id)
        ).scalar()
        if taken is not None and taken != allow:
            raise ConflictError(
                f"slug {candidate!r} is already used by {taken}", slug=candidate, entity_id=str(taken)
            )
        return candidate

    def _write_facets(
        self, connection: Connection, draft: EntityDraft, entity_type: str, entity_id: str
    ) -> None:
        """Replace names and geometries wholesale: they are a set, not a delta."""
        connection.execute(
            text("DELETE FROM name_variant WHERE entity_type = :t AND entity_id = :id"),
            {"t": entity_type, "id": entity_id},
        )
        if draft.names:
            connection.execute(
                schema.name_variant.insert(),
                [
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "form": name.form,
                        "lang": name.lang,
                        "script": name.script,
                        "kind": name.kind,
                        "transliteration": name.transliteration,
                        "year_from": name.year_from,
                        "year_to": name.year_to,
                        "source_id": name.source_id,
                        "note": name.note,
                        "search_form": textnorm.build_search_text(name.form),
                    }
                    for name in draft.names
                ],
            )
        connection.execute(
            text("DELETE FROM entity_geometry WHERE entity_type = :t AND entity_id = :id"),
            {"t": entity_type, "id": entity_id},
        )
        if draft.geometries:
            connection.execute(
                schema.entity_geometry.insert(),
                [
                    {
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "kind": geometry.kind.value,
                        "certainty": geometry.certainty.value,
                        "geom_json": dict(geometry.geojson),
                        "year_from": geometry.year_from,
                        "year_to": geometry.year_to,
                        "lod_min_zoom": geometry.lod_min_zoom,
                        "lod_max_zoom": geometry.lod_max_zoom,
                        "source_id": geometry.source_id,
                        "note_fa": geometry.note,
                        "note_en": geometry.note_en,
                        "needs_digitisation": geometry.needs_digitisation,
                    }
                    for geometry in draft.geometries
                ],
            )
            # `geom` is the authoritative spatial column and is always derived from the GeoJSON, so
            # a malformed shape fails the write instead of appearing later as a blank map.
            connection.execute(
                text(
                    "UPDATE entity_geometry "
                    "SET geom = ST_SetSRID(ST_GeomFromGeoJSON(geom_json::text), 4326) "
                    "WHERE entity_type = :t AND entity_id = :id AND geom IS NULL"
                ),
                {"t": entity_type, "id": entity_id},
            )

    def _pending_id(self, connection: Connection, entity_type: str, entity_id: str) -> str | None:
        sql = text(
            """
            SELECT es.entity_id FROM editorial_state es
            JOIN entity_read_model erm ON erm.id = es.entity_id
            WHERE es.head_id = :head AND es.entity_type = :t
              AND erm.status = ANY(:open::text[])
            ORDER BY es.revision DESC LIMIT 1
            """
        )
        row = connection.execute(
            sql, {"head": entity_id, "t": entity_type, "open": list(OPEN_STATUSES)}
        ).first()
        return str(row[0]) if row else None

    def _state_row(self, connection: Connection, entity_type: str, entity_id: str) -> Any:
        return connection.execute(
            sa.select(schema.editorial_state).where(
                schema.editorial_state.c.entity_type == entity_type,
                schema.editorial_state.c.entity_id == entity_id,
            )
        ).mappings().first()

    def _upsert_state(
        self, connection: Connection, entity_type: str, entity_id: str, **values: Any
    ) -> None:
        row = {"entity_type": entity_type, "entity_id": entity_id, "updated_at": sa.func.now()}
        row.update({key: value for key, value in values.items() if value is not None})
        statement = pg_insert(schema.editorial_state).values(**row)
        conflict = {key: value for key, value in row.items() if key not in ("entity_type", "entity_id")}
        statement = statement.on_conflict_do_update(
            index_elements=["entity_type", "entity_id"],
            set_=dict(conflict, updated_at=sa.func.now()),
        )
        connection.execute(statement)

    def _merge_into_head(
        self,
        connection: Connection,
        *,
        table: sa.Table,
        entity_type: str,
        head_id: str,
        draft_id: str,
        actor: Actor | None,
        request_id: str | None,
        note: str | None,
        revision: int,
    ) -> EntityRecord:
        """Publish a reviewed copy: the head takes the copy's content, the copy disappears.

        The public id and slug never change, so every link, citation and bookmark keeps working;
        only the content and the revision number move (ADR-0008, ADR-0010 rule 4).
        """
        head = self._load_records([head_id], with_relationships=False, connection=connection)
        if not head:  # pragma: no cover - the draft would not exist without its head
            raise NotFoundError(f"no {entity_type} with id {head_id!r}")
        columns = _copyable_columns(table)
        assignments = ", ".join(f"h.{column} = d.{column}" for column in columns)
        connection.execute(
            text(
                f"UPDATE public.{table.name} AS h SET {assignments}, "
                "h.status = :status, h.revision = :revision, h.updated_at = now() "
                f"FROM public.{table.name} AS d WHERE h.id = :head_id AND d.id = :draft_id"
            ),
            {
                "status": Status.PUBLISHED.value,
                "revision": revision,
                "head_id": head_id,
                "draft_id": draft_id,
            },
        )
        # Facets move with the content.
        connection.execute(
            text("DELETE FROM name_variant WHERE entity_type = :t AND entity_id = :head"),
            {"t": entity_type, "head": head_id},
        )
        connection.execute(
            text(
                "INSERT INTO name_variant (entity_type, entity_id, form, lang, script, kind, "
                "transliteration, year_from, year_to, source_id, note, search_form) "
                "SELECT entity_type, :head, form, lang, script, kind, transliteration, year_from, "
                "year_to, source_id, note, search_form FROM name_variant "
                "WHERE entity_type = :t AND entity_id = :draft"
            ),
            {"t": entity_type, "head": head_id, "draft": draft_id},
        )
        connection.execute(
            text("DELETE FROM entity_geometry WHERE entity_type = :t AND entity_id = :head"),
            {"t": entity_type, "head": head_id},
        )
        connection.execute(
            text(
                "INSERT INTO entity_geometry (entity_type, entity_id, kind, certainty, geom_json, "
                "geom, year_from, year_to, lod_min_zoom, lod_max_zoom, source_id, note_fa, note_en, "
                "needs_digitisation) SELECT entity_type, :head, kind, certainty, geom_json, geom, "
                "year_from, year_to, lod_min_zoom, lod_max_zoom, source_id, note_fa, note_en, "
                "needs_digitisation FROM entity_geometry WHERE entity_type = :t AND entity_id = :draft"
            ),
            {"t": entity_type, "head": head_id, "draft": draft_id},
        )
        # Retire the copy.
        connection.execute(
            text("DELETE FROM name_variant WHERE entity_type = :t AND entity_id = :draft"),
            {"t": entity_type, "draft": draft_id},
        )
        connection.execute(
            text("DELETE FROM entity_geometry WHERE entity_type = :t AND entity_id = :draft"),
            {"t": entity_type, "draft": draft_id},
        )
        connection.execute(
            text("DELETE FROM editorial_state WHERE entity_type = :t AND entity_id = :draft"),
            {"t": entity_type, "draft": draft_id},
        )
        connection.execute(table.delete().where(table.c.id == draft_id))
        updates: dict[str, Any] = {
            "revision": revision,
            "head_id": None,
            "review_note": note,
            "updated_at": sa.func.now(),
        }
        if actor is not None:
            updates.update(
                reviewed_by=actor.id,
                reviewed_at=sa.func.now(),
                published_by=actor.id,
                published_at=sa.func.now(),
            )
        self._upsert_state(connection, entity_type, head_id, **updates)
        self._audit(
            connection,
            actor=actor,
            action=Action.PUBLISH,
            entity_type=entity_type,
            entity_id=head_id,
            payload={
                "from": head[0].status.value,
                "to": Status.PUBLISHED.value,
                "revision": revision,
                "merged_draft_id": draft_id,
                "note": note,
                "changed": _changed_fields(head[0], self._record(head_id, connection)),
            },
            request_id=request_id,
        )
        record = self._load_records([head_id], with_relationships=False, connection=connection)[0]
        self._snapshot(connection, record, actor=actor)
        return record

    def _snapshot(self, connection: Connection, record: EntityRecord, *, actor: Actor | None) -> None:
        statement = pg_insert(schema.revision_snapshot).values(
            entity_type=record.entity_type.value,
            entity_id=record.id,
            revision=record.revision,
            payload=snapshot_payload(record),
            created_by=actor.id if actor else None,
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["entity_type", "entity_id", "revision"],
                set_={
                    "payload": statement.excluded.payload,
                    "created_by": statement.excluded.created_by,
                    "created_at": sa.func.now(),
                },
            )
        )

    def _audit(
        self,
        connection: Connection,
        *,
        actor: Actor | None,
        action: Action,
        entity_type: str | None,
        entity_id: str | None,
        payload: dict[str, Any],
        request_id: str | None,
    ) -> None:
        connection.execute(
            schema.audit_log.insert().values(
                actor_id=actor.id if actor else None,
                action=action.value,
                entity_type=entity_type,
                entity_id=entity_id,
                payload=_jsonable(payload),
                request_id=request_id,
            )
        )


# ------------------------------------------------------------------ module helpers


def _enum(kind: type[Any], value: Any, default: Any) -> Any:
    """Tolerant enum parse: a data-entry mistake must never take the panel down."""
    try:
        return kind(str(value))
    except (ValueError, TypeError):
        return default


def _first_name(draft: EntityDraft, lang: str) -> str | None:
    for name in draft.names:
        if name.lang == lang and name.kind == "preferred":
            return name.form
    for name in draft.names:
        if name.lang == lang:
            return name.form
    return None


def _changed_fields(before: EntityRecord, after: EntityRecord) -> dict[str, Any]:
    """Field-level before/after for the audit row (ADR-0010 rule 5).

    Full content lives in ``revision_snapshot``; this is the human-readable "what moved".
    """
    old = snapshot_payload(before)
    new = snapshot_payload(after)
    changed: dict[str, Any] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changed[key] = {"before": old.get(key), "after": new.get(key)}
    return changed


def _jsonable(payload: dict[str, Any]) -> dict[str, Any]:
    """Audit payloads are JSONB: convert enums and dates, drop what cannot be represented."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        out[key] = _json_value(value)
    return out


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, "value") and isinstance(value.value, str):
        return str(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _text(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def _audit_entry(row: Any) -> AuditEntry:
    return AuditEntry(
        id=int(row["id"]) if row["id"] is not None else None,
        actor_id=_text(row["actor_id"]),
        actor_name=_text(row["actor_name"]),
        action=str(row["action"]),
        entity_type=_text(row["entity_type"]),
        entity_id=_text(row["entity_id"]),
        payload=dict(row["payload"] or {}),
        request_id=_text(row["request_id"]),
        created_at=_when(row["created_at"]),
    )


def _temporal(row: Any) -> TemporalInterval | None:
    year_from, year_to = row["year_from"], row["year_to"]
    if year_from is None or year_to is None:
        return None
    return TemporalInterval(
        year_from=int(year_from),
        year_to=int(year_to),
        precision=_enum(Precision, row["precision"], Precision.UNKNOWN),
        confidence=_enum(Confidence, row["confidence"], Confidence.MEDIUM),
        display=_text(row["temporal_display_fa"]),
    )


def _assertion_record(row: Any, evidence: tuple[Any, ...]) -> AssertionRecord:
    return AssertionRecord(
        id=str(row["id"]),
        subject_type=str(row["subject_type"]),
        subject_id=str(row["subject_id"]),
        predicate=str(row["predicate"]),
        object_type=_text(row["object_type"]),
        object_id=_text(row["object_id"]),
        value=_text(row["value"]),
        status=_enum(AssertionStatus, row["status"], AssertionStatus.PROPOSED),
        topic_fa=_text(row["topic_fa"]),
        topic_en=_text(row["topic_en"]),
        note_fa=_text(row["note_fa"]),
        confidence=_enum(Confidence, row["confidence"], Confidence.MEDIUM),
        temporal=_temporal(row),
        evidence=evidence,
        created_by=_text(row["created_by"]),
        reviewed_by=_text(row["reviewed_by"]),
        reviewed_at=_when(row["reviewed_at"]),
        review_note=_text(row["review_note"]),
    )


__all__ = ["EDITABLE_TYPES", "OPEN_STATUSES", "PostgisEditorialMixin"]
