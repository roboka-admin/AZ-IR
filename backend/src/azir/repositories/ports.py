"""Repository ports.

These protocols are the *only* thing services may depend on. Two adapters implement them
(``postgis`` and ``fixtures``, ADR-0014) and the same contract test-suite runs against both.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ..core.pagination import Cursor
from ..domain.editorial import Action, Role
from ..domain.editorial import may as may_perform
from ..domain.editorial import may_review as can_review
from ..domain.enums import AssertionStatus, Attestation, Confidence, EntityType, Status
from ..domain.geo import BBox, GeometryRecord
from ..domain.model import EntityRecord, EvidenceRef, NameVariant, Relationship
from ..domain.temporal import TemporalInterval, TimeWindow


@dataclass(frozen=True, slots=True)
class AtlasQuery:
    """A fully-resolved map query. Routers never build this; the service does."""

    bbox: BBox
    zoom: float
    window: TimeWindow
    layers: tuple[str, ...]
    kinds: tuple[str, ...] = ()
    locale: str = "fa"
    fields: str = "default"
    limit: int = 800
    cursor: Cursor | None = None
    min_rank: float = 0.0
    lod_tolerance: float = 0.0
    include_unpublished: bool = False
    near: tuple[float, float] | None = None
    radius_km: float | None = None
    period_code: str | None = None

    def cache_key(self) -> str:
        b = self.bbox
        return "|".join(
            [
                f"{b.min_lon:.2f},{b.min_lat:.2f},{b.max_lon:.2f},{b.max_lat:.2f}",
                f"z{round(self.zoom * 2) / 2:.1f}",
                f"t{self.window.year_from}:{self.window.year_to}:{self.window.mode}",
                ",".join(self.layers) or "-",
                ",".join(self.kinds) or "-",
                self.locale,
                self.fields,
            ]
        )


@dataclass(frozen=True, slots=True)
class FeaturePage:
    rows: list[tuple[float, str]]           # (rank, id) ordered pairs used for pagination
    entities: dict[str, EntityRecord]
    geometries: dict[str, Any]               # id -> GeoJSON geometry (already simplified)
    total_estimate: int
    coverage_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchHit:
    entity: EntityRecord
    score: float
    matched_on: str = "name"
    snippet: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineBucket:
    year_from: int
    year_to: int
    counts: dict[str, int]
    total: int
    top_kinds: list[str]
    notable: list[dict[str, Any]]


@runtime_checkable
class AtlasRepository(Protocol):
    """Data access contract for the whole atlas read-model."""

    driver_name: str

    def features(self, query: AtlasQuery) -> FeaturePage: ...

    def timeline(
        self, bbox: BBox, window: TimeWindow, bucket: int, layers: tuple[str, ...], locale: str
    ) -> list[TimelineBucket]: ...

    def entity(self, entity_type: str, id_or_slug: str) -> EntityRecord | None: ...

    def related(
        self, entity_type: str, entity_id: str, *, depth: int = 1, limit: int = 50
    ) -> list[Relationship]: ...

    def list_entities(
        self,
        entity_type: EntityType,
        *,
        status: str | None = "published",
        locale: str = "fa",
        limit: int = 50,
        window: TimeWindow | None = None,
    ) -> list[EntityRecord]: ...

    def articles(self, *, locale: str = "fa", limit: int = 50, entity_id: str | None = None) -> list[EntityRecord]: ...

    def search(
        self,
        term: str,
        *,
        types: tuple[str, ...],
        locale: str = "fa",
        limit: int = 20,
        near: tuple[float, float] | None = None,
        radius_km: float | None = None,
        window: TimeWindow | None = None,
    ) -> list[SearchHit]: ...

    def context(
        self, *, place_id: str | None, point: tuple[float, float] | None, radius_km: float,
        locale: str, limit: int,
    ) -> list[Relationship]: ...

    def stats(self) -> dict[str, Any]: ...

    def all_published(self) -> list[EntityRecord]: ...


# ------------------------------------------------------------------ write side (ADR-0010)
#
# The read port above stays exactly as it was: the public API is read-only and unauthenticated.
# Everything below serves the internal editorial panel, and it is a *separate* protocol so that a
# service which only reads can never accidentally be handed write access.


@dataclass(frozen=True, slots=True)
class Actor:
    """An authenticated member of the research team."""

    id: str
    email: str
    display_name: str
    role: Role
    is_active: bool = True

    def may(self, action: Action) -> bool:
        return self.is_active and may_perform(self.role, action)

    @property
    def may_review(self) -> bool:
        return self.is_active and can_review(self.role)


@dataclass(frozen=True, slots=True)
class SessionTicket:
    """What a successful login hands back: an opaque cookie value plus its CSRF partner."""

    token: str
    csrf_token: str
    actor: Actor
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class EntityDraft:
    """A complete, editable view of one entity.

    Repositories always receive the *whole* draft; merging a partial request into the current
    record is the service's job (business logic never lives in an adapter, AGENTS.md rule 4).
    """

    entity_type: EntityType
    kind: str | None = None
    slug: str | None = None
    names: tuple[NameVariant, ...] = ()
    temporal: TemporalInterval | None = None
    geometries: tuple[GeometryRecord, ...] = ()
    summary: str | None = None
    summary_en: str | None = None
    coverage_note: str | None = None
    coverage_note_en: str | None = None
    source_ids: tuple[str, ...] = ()
    importance: float = 0.5
    certainty: str | None = None
    attestation: Attestation | None = None
    # article-only fields; ignored for every other type
    title: str | None = None
    title_en: str | None = None
    body_md: str | None = None
    body_en_md: str | None = None
    author: str | None = None
    map_state: Mapping[str, Any] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowState:
    """Where a record sits in the review loop and who moved it (ADR-0010 rule 5)."""

    entity_type: str
    entity_id: str
    status: Status
    revision: int = 1
    head_id: str | None = None
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    published_by: str | None = None
    published_at: datetime | None = None
    archived_by: str | None = None
    archived_at: datetime | None = None
    #: Last time anything about this record moved; the review queue is ordered by it.
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One immutable line of the research trail. Nothing is ever updated or deleted here."""

    actor_id: str | None
    actor_name: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    payload: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    created_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    """A frozen snapshot of an entity at one revision, kept even after the draft is merged."""

    entity_type: str
    entity_id: str
    revision: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AssertionRecord:
    """A claim with its evidence, as the review panel needs to see it."""

    id: str
    subject_type: str
    subject_id: str
    predicate: str
    object_type: str | None = None
    object_id: str | None = None
    value: str | None = None
    status: AssertionStatus = AssertionStatus.PROPOSED
    topic_fa: str | None = None
    topic_en: str | None = None
    note_fa: str | None = None
    confidence: Confidence = Confidence.MEDIUM
    temporal: TemporalInterval | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    created_by: str | None = None
    #: Who reviewed this claim, when, and what they said. A claim without a reviewer is a rumour
    #: with a citation, so the panel always shows these three.
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None

    @property
    def supporting_evidence(self) -> tuple[EvidenceRef, ...]:
        return tuple(item for item in self.evidence if item.stance == "supports")


@runtime_checkable
class EditorialRepository(Protocol):
    """Write side of the atlas: identity, drafts, the review loop, claims and the audit trail."""

    driver_name: str

    # --- identity ---------------------------------------------------------
    def authenticate(self, email: str, password: str) -> Actor | None: ...

    def user_by_id(self, user_id: str) -> Actor | None: ...

    def create_user(self, *, email: str, display_name: str, role: Role, password: str) -> Actor: ...

    def list_users(self) -> list[Actor]: ...

    def open_session(
        self, actor: Actor, *, ttl_seconds: int, user_agent: str | None = None
    ) -> SessionTicket: ...

    def session_actor(self, token: str) -> Actor | None: ...

    def session_csrf_ok(self, token: str, csrf_token: str | None) -> bool:
        """Double-submit CSRF check for state-changing requests."""
        ...

    def close_session(self, token: str) -> None: ...

    # --- content ----------------------------------------------------------
    def create_draft(
        self, draft: EntityDraft, *, actor: Actor | None, request_id: str | None
    ) -> EntityRecord: ...

    def update_draft(
        self,
        entity_type: str,
        entity_id: str,
        draft: EntityDraft,
        *,
        actor: Actor | None,
        request_id: str | None,
    ) -> EntityRecord: ...

    def begin_revision(
        self, entity_type: str, entity_id: str, *, actor: Actor | None, request_id: str | None
    ) -> EntityRecord: ...

    def pending_revision(self, entity_type: str, entity_id: str) -> EntityRecord | None: ...

    def transition(
        self,
        entity_type: str,
        entity_id: str,
        target: Status,
        *,
        actor: Actor | None,
        request_id: str | None,
        note: str | None = None,
    ) -> EntityRecord: ...

    def workflow_state(self, entity_type: str, entity_id: str) -> WorkflowState | None: ...

    def queue(
        self,
        *,
        statuses: tuple[Status, ...] = (Status.DRAFT, Status.IN_REVIEW, Status.CHANGES_REQUESTED),
        entity_type: str | None = None,
        limit: int = 50,
    ) -> list[EntityRecord]: ...

    def revisions(self, entity_type: str, entity_id: str) -> list[RevisionRecord]: ...

    def audit(
        self,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]: ...

    # --- claims -----------------------------------------------------------
    def assertion(self, assertion_id: str) -> AssertionRecord | None: ...

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
        """Move a claim through its own loop.

        ``topic_fa``/``topic_en`` name *what* is being disagreed about, which a disputed claim
        cannot do without (rule D13): two positions with no topic are just noise side by side.
        """
        ...

    # --- data quality ------------------------------------------------------
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
        ...

    def record_lint(
        self, *, scope: str, findings: list[dict[str, Any]], actor: Actor | None
    ) -> int:
        """Persist a data-lint report (docs/07 §3) and return its id."""
        ...
