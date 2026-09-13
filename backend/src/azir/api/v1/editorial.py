"""The editorial panel API: the queue, drafts, the review loop, claims, users and lint.

Every route here is authenticated (session cookie) and, when it changes state, CSRF-checked. The
routers hold no policy: they translate HTTP into a service call and the service's answer back into
JSON (AGENTS.md rule 2). Read-only public endpoints live in the other routers and stay anonymous.

Route order matters: the fixed paths (``queue``, ``audit``, ``lint``, ``users``, ``assertions``)
are declared before the parameterised ``{entity_type}`` ones so they cannot be shadowed.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from ...core.errors import NotFoundError, ValidationError
from ...domain.editorial import Action, Role
from ...domain.enums import EntityType, Status
from ...schemas.editorial import (
    AssertionReviewIn,
    EntityDraftIn,
    LoginIn,  # noqa: F401  (re-exported for the OpenAPI schema of the auth router)
    TransitionIn,
    UserIn,
)
from ..deps import ActorDep, CsrfDep, EditorialDep, LocaleDep, RequestDep

router = APIRouter(prefix="/editorial", tags=["editorial"])

EDITABLE = ("place", "person", "event", "political_entity", "article")
_STATUS_VALUES = tuple(status.value for status in Status)


def _guard_type(entity_type: str) -> None:
    if entity_type not in EDITABLE:
        raise NotFoundError(
            f"unknown editable entity type {entity_type!r}", known=list(EDITABLE)
        )


def _parse_statuses(raw: str | None) -> tuple[Status, ...]:
    if not raw:
        return (Status.DRAFT, Status.IN_REVIEW, Status.CHANGES_REQUESTED)
    wanted: list[Status] = []
    for part in (item.strip() for item in raw.split(",")):
        if not part:
            continue
        if part not in _STATUS_VALUES:
            raise ValidationError(
                f"unknown status {part!r}", parameter="status", known=list(_STATUS_VALUES)
            )
        wanted.append(Status(part))
    return tuple(wanted) or (Status.DRAFT,)


# ------------------------------------------------------------------ fixed paths (declared first)


@router.get("/queue", summary="What is waiting for the team")
def queue(
    service: EditorialDep,
    actor: ActorDep,
    locale: LocaleDep,
    status: Annotated[str | None, Query(description="comma separated statuses")] = None,
    entity_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    if entity_type is not None:
        _guard_type(entity_type)
    return service.queue(
        actor,
        statuses=_parse_statuses(status),
        entity_type=entity_type,
        limit=limit,
        locale=locale,
    )


@router.get("/audit", summary="The immutable trail of every write")
def audit(
    service: EditorialDep,
    actor: ActorDep,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    return service.trail(
        actor, entity_type=entity_type, entity_id=entity_id, action=action, limit=limit
    )


@router.get("/lint", summary="Data-quality report (docs/07 §3)")
def lint(
    service: EditorialDep,
    actor: ActorDep,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return service.lint(actor, entity_type=entity_type, entity_id=entity_id)


@router.post("/lint", summary="Run the linter and persist the report")
def lint_and_record(
    service: EditorialDep,
    actor: ActorDep,
    csrf: CsrfDep,
    request_id: RequestDep,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    report = service.lint(
        actor, entity_type=entity_type, entity_id=entity_id, persist=True
    )
    report["meta"]["request_id"] = request_id
    return report


@router.get("/users", summary="The team (admin only)")
def users(service: EditorialDep, actor: ActorDep) -> dict[str, Any]:
    return service.users(actor)


@router.post("/users", summary="Add a team member (admin only)")
def add_user(
    payload: UserIn,
    service: EditorialDep,
    actor: ActorDep,
    csrf: CsrfDep,
    request_id: RequestDep,
) -> dict[str, Any]:
    created = service.add_user(
        actor,
        email=payload.email,
        display_name=payload.display_name,
        role=Role(payload.role),
        password=payload.password,
        request_id=request_id,
    )
    return {
        "data": {
            "id": created.id,
            "email": created.email,
            "display_name": created.display_name,
            "role": created.role.value,
            "is_active": created.is_active,
        },
        "meta": {"created_by": actor.id, "request_id": request_id},
    }


@router.post("/assertions/{assertion_id}/review", summary="Accept, reject or dispute a claim")
def review_assertion(
    assertion_id: str,
    payload: AssertionReviewIn,
    service: EditorialDep,
    actor: ActorDep,
    csrf: CsrfDep,
    request_id: RequestDep,
) -> dict[str, Any]:
    reviewed = service.review_assertion(
        actor,
        assertion_id,
        payload.status,
        note=payload.note,
        topic_fa=payload.topic_fa,
        topic_en=payload.topic_en,
        request_id=request_id,
    )
    return {
        "data": {
            "id": reviewed.id,
            "status": reviewed.status.value,
            "predicate": reviewed.predicate,
            "subject_id": reviewed.subject_id,
            "object_id": reviewed.object_id,
            "value": reviewed.value,
            "topic_fa": reviewed.topic_fa,
            "topic_en": reviewed.topic_en,
            "review_note": reviewed.review_note,
            "reviewed_by": reviewed.reviewed_by,
            "reviewed_at": reviewed.reviewed_at.isoformat() if reviewed.reviewed_at else None,
            "evidence": [
                {
                    "source_id": item.source_id,
                    "locator_type": item.locator_type,
                    "locator": item.locator,
                    "stance": item.stance,
                    "quote_original": item.quote_original,
                    "quote_translation": item.quote_translation,
                }
                for item in reviewed.evidence
            ],
        },
        "meta": {"reviewed_by": actor.id, "request_id": request_id},
    }


# ------------------------------------------------------------------ per-record paths


@router.get("/{entity_type}/{id_or_slug}", summary="One record as the panel sees it")
def detail(
    entity_type: str,
    id_or_slug: str,
    service: EditorialDep,
    actor: ActorDep,
    locale: LocaleDep,
) -> dict[str, Any]:
    _guard_type(entity_type)
    return service.detail(actor, entity_type, id_or_slug, locale=locale)


@router.get("/{entity_type}/{id_or_slug}/state", summary="Where this record is in the loop")
def state(
    entity_type: str,
    id_or_slug: str,
    service: EditorialDep,
    actor: ActorDep,
    locale: LocaleDep,
) -> dict[str, Any]:
    _guard_type(entity_type)
    payload = service.detail(actor, entity_type, id_or_slug, locale=locale)["data"]
    return {
        "data": {
            "id": payload["id"],
            "status": payload["status"],
            "revision": payload["revision"],
            "workflow": payload["workflow"],
            "pending_revision": payload["pending_revision"],
            "available_actions": payload["available_actions"],
        },
        "meta": {"driver": service.driver_name},
    }


@router.get("/{entity_type}/{id_or_slug}/revisions", summary="Frozen snapshots of every revision")
def revisions(
    entity_type: str,
    id_or_slug: str,
    service: EditorialDep,
    actor: ActorDep,
) -> dict[str, Any]:
    _guard_type(entity_type)
    record = service.record_for(actor, entity_type, id_or_slug)
    return service.history(actor, entity_type, record.id)


@router.post("/{entity_type}", summary="Create a draft record", status_code=201)
def create(
    entity_type: str,
    payload: EntityDraftIn,
    service: EditorialDep,
    actor: ActorDep,
    csrf: CsrfDep,
    request_id: RequestDep,
    locale: LocaleDep,
) -> dict[str, Any]:
    _guard_type(entity_type)
    if payload.entity_type is not None and payload.entity_type.value != entity_type:
        raise ValidationError(
            f"the body says {payload.entity_type.value!r} but the path says {entity_type!r}",
            parameter="entity_type",
        )
    record = service.create(
        actor, payload.to_draft(EntityType(entity_type)), request_id=request_id
    )
    body = service.detail(actor, entity_type, record.id, locale=locale)
    return {
        "data": body["data"],
        "meta": {**body["meta"], "created": True, "request_id": request_id},
    }


@router.patch("/{entity_type}/{id_or_slug}", summary="Save an edit (published records fork)")
def revise(
    entity_type: str,
    id_or_slug: str,
    payload: EntityDraftIn,
    service: EditorialDep,
    actor: ActorDep,
    csrf: CsrfDep,
    request_id: RequestDep,
    locale: LocaleDep,
) -> dict[str, Any]:
    """Editing a published record returns the *draft copy*, not the public record.

    That is the reviewed-copy pattern (ADR-0010 rule 4) showing up in the API: the response says
    which record the editor is now working on, and the public one keeps serving until approval.
    """
    _guard_type(entity_type)
    record = service.revise(
        actor,
        entity_type,
        id_or_slug,
        service.merged_draft(actor, entity_type, id_or_slug, payload.to_patch()),
        request_id=request_id,
    )
    body = service.detail(actor, record.entity_type.value, record.id, locale=locale)
    return {
        "data": body["data"],
        "meta": {
            **body["meta"],
            "edited": id_or_slug,
            "working_copy": record.id,
            "forked": record.id != id_or_slug,
            "request_id": request_id,
        },
    }


@router.post("/{entity_type}/{id_or_slug}/transition", summary="Move a record through the loop")
def transition(
    entity_type: str,
    id_or_slug: str,
    payload: TransitionIn,
    service: EditorialDep,
    actor: ActorDep,
    csrf: CsrfDep,
    request_id: RequestDep,
    locale: LocaleDep,
) -> dict[str, Any]:
    _guard_type(entity_type)
    record = service.transition(
        actor,
        entity_type,
        id_or_slug,
        Action(payload.action),
        note=payload.note,
        request_id=request_id,
    )
    body = service.detail(actor, record.entity_type.value, record.id, locale=locale)
    return {
        "data": body["data"],
        "meta": {
            **body["meta"],
            "action": payload.action,
            "note": payload.note,
            "request_id": request_id,
        },
    }
