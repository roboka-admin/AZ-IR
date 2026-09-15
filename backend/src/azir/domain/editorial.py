"""Editorial workflow rules: who may do what, and in which order (ADR-0010, docs/07).

Pure domain: no I/O, no HTTP, no database. The service layer asks these functions whether a
transition is legal and who is allowed to make it; the repository layer only persists the answer.
Keeping the rules here is what makes them enforceable in *both* drivers and testable without a
server -- a rule that lives in a router is a rule that eventually gets bypassed.

The state machine, for entities and articles alike::

    draft ──submit──► in_review ──approve──► published ──archive──► archived
      ▲                   │                      │                     │
      │  changes_requested│                      └── new revision ─────┘
      └───────────────────┘                          (draft copy, head stays live)

Assertions run their own, shorter loop: ``proposed → accepted | rejected | disputed``.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from .enums import AssertionStatus, Status


class Role(StrEnum):
    """The four hats a 2-5 person research team actually needs (ADR-0010)."""

    ADMIN = "admin"
    REVIEWER = "reviewer"
    EDITOR = "editor"
    CONTRIBUTOR = "contributor"


#: Ordering only exists to make "at least an editor" readable; authority comes from ACTION_ROLES.
ROLE_RANK: dict[Role, int] = {
    Role.CONTRIBUTOR: 0,
    Role.EDITOR: 1,
    Role.REVIEWER: 2,
    Role.ADMIN: 3,
}


class Action(StrEnum):
    """Verbs. They are also the ``action`` values written to the audit log."""

    CREATE = "create"
    UPDATE = "update"
    SUBMIT = "submit"
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    PUBLISH = "publish"
    ARCHIVE = "archive"
    RESTORE = "restore"
    BEGIN_REVISION = "begin_revision"
    REVIEW_ASSERTION = "review_assertion"
    MANAGE_USERS = "manage_users"
    READ_QUEUE = "read_queue"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"


#: Who may perform each action. Anything not listed is denied by default.
ACTION_ROLES: dict[Action, frozenset[Role]] = {
    # A contributor may propose, but never push their own proposal towards publication.
    Action.CREATE: frozenset({Role.ADMIN, Role.REVIEWER, Role.EDITOR, Role.CONTRIBUTOR}),
    Action.UPDATE: frozenset({Role.ADMIN, Role.REVIEWER, Role.EDITOR, Role.CONTRIBUTOR}),
    Action.SUBMIT: frozenset({Role.ADMIN, Role.REVIEWER, Role.EDITOR}),
    Action.BEGIN_REVISION: frozenset({Role.ADMIN, Role.REVIEWER, Role.EDITOR}),
    # Review is the separation-of-duties step: it needs a reviewer or an admin (rule 3).
    Action.APPROVE: frozenset({Role.ADMIN, Role.REVIEWER}),
    Action.PUBLISH: frozenset({Role.ADMIN, Role.REVIEWER}),
    Action.REQUEST_CHANGES: frozenset({Role.ADMIN, Role.REVIEWER}),
    Action.REVIEW_ASSERTION: frozenset({Role.ADMIN, Role.REVIEWER}),
    # Archiving removes public truth; only an admin does that.
    Action.ARCHIVE: frozenset({Role.ADMIN}),
    Action.RESTORE: frozenset({Role.ADMIN}),
    Action.MANAGE_USERS: frozenset({Role.ADMIN}),
    Action.READ_QUEUE: frozenset({Role.ADMIN, Role.REVIEWER, Role.EDITOR, Role.CONTRIBUTOR}),
}

#: Which status change each action performs. ``None`` means "no status change" (a content edit).
ACTION_TARGET: dict[Action, Status | None] = {
    Action.CREATE: Status.DRAFT,
    Action.UPDATE: None,
    Action.SUBMIT: Status.IN_REVIEW,
    Action.APPROVE: Status.PUBLISHED,
    Action.PUBLISH: Status.PUBLISHED,
    Action.REQUEST_CHANGES: Status.CHANGES_REQUESTED,
    Action.ARCHIVE: Status.ARCHIVED,
    Action.RESTORE: Status.PUBLISHED,
    Action.BEGIN_REVISION: Status.DRAFT,
    Action.REVIEW_ASSERTION: None,
    Action.MANAGE_USERS: None,
    Action.READ_QUEUE: None,
}

#: Legal edges of the content state machine (ADR-0010).
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.DRAFT: frozenset({Status.IN_REVIEW, Status.ARCHIVED}),
    Status.IMPORTED_UNVERIFIED: frozenset({Status.DRAFT, Status.IN_REVIEW, Status.ARCHIVED}),
    Status.IN_REVIEW: frozenset({Status.PUBLISHED, Status.CHANGES_REQUESTED, Status.DRAFT}),
    Status.CHANGES_REQUESTED: frozenset({Status.DRAFT, Status.IN_REVIEW, Status.ARCHIVED}),
    Status.PUBLISHED: frozenset({Status.ARCHIVED}),
    Status.ARCHIVED: frozenset({Status.PUBLISHED}),
}

#: Evidence stances that may carry an *accepted* claim (rule D3, docs/07 §3).
#:
#: ``supports`` is the plain case. ``qualifies`` is a source that affirms the claim with a
#: limitation -- Maragheh *was* an Ilkhanid capital, for part of the period -- and refusing it would
#: push honest, nuanced research out of the corpus and towards overstated claims, which is the
#: opposite of what the rule is for. Only ``contradicts`` (and no evidence at all) is insufficient.
SUFFICIENT_STANCES: frozenset[str] = frozenset({"supports", "qualifies"})


def claim_is_evidenced(evidence: Iterable[Any]) -> bool:
    """May a claim carrying this evidence be presented as accepted?

    One function, used by the linter and by both repository adapters' accept gate, so a claim is
    never acceptable in one place and unacceptable in another.
    """
    return any(str(getattr(item, "stance", item)) in SUFFICIENT_STANCES for item in evidence)


#: Statuses whose content an editor may change in place.
#:
#: A record that is *in review* is frozen: a reviewer must never approve text that moved underneath
#: them. To keep editing, the reviewer sends it back (``request_changes``) or the editor withdraws
#: it. ``published`` is frozen for a different reason -- its content is what the public sees, so it
#: is edited through a reviewed copy (ADR-0010 rule 4) -- and ``archived`` is a record kept for its
#: history, not for change.
EDITABLE_STATUSES: frozenset[Status] = frozenset(
    {Status.DRAFT, Status.CHANGES_REQUESTED, Status.IMPORTED_UNVERIFIED}
)


def is_editable(status: Status) -> bool:
    """May the content of a record in this status be changed in place?"""
    return status in EDITABLE_STATUSES


#: The action that carries a given edge, so a caller can ask "how do I get from A to B?".
TRANSITION_ACTION: dict[tuple[Status, Status], Action] = {
    (Status.DRAFT, Status.IN_REVIEW): Action.SUBMIT,
    (Status.IMPORTED_UNVERIFIED, Status.IN_REVIEW): Action.SUBMIT,
    (Status.IMPORTED_UNVERIFIED, Status.DRAFT): Action.UPDATE,
    (Status.IN_REVIEW, Status.PUBLISHED): Action.APPROVE,
    (Status.IN_REVIEW, Status.CHANGES_REQUESTED): Action.REQUEST_CHANGES,
    (Status.IN_REVIEW, Status.DRAFT): Action.REQUEST_CHANGES,
    (Status.CHANGES_REQUESTED, Status.DRAFT): Action.UPDATE,
    (Status.CHANGES_REQUESTED, Status.IN_REVIEW): Action.SUBMIT,
    (Status.PUBLISHED, Status.ARCHIVED): Action.ARCHIVE,
    (Status.ARCHIVED, Status.PUBLISHED): Action.RESTORE,
    (Status.DRAFT, Status.ARCHIVED): Action.ARCHIVE,
    (Status.CHANGES_REQUESTED, Status.ARCHIVED): Action.ARCHIVE,
}

#: Assertion review loop. ``disputed`` is a first-class outcome, never a dead end (AGENTS rule 20).
ASSERTION_TRANSITIONS: dict[AssertionStatus, frozenset[AssertionStatus]] = {
    AssertionStatus.PROPOSED: frozenset(
        {AssertionStatus.ACCEPTED, AssertionStatus.REJECTED, AssertionStatus.DISPUTED}
    ),
    AssertionStatus.ACCEPTED: frozenset({AssertionStatus.DISPUTED, AssertionStatus.REJECTED}),
    AssertionStatus.REJECTED: frozenset({AssertionStatus.PROPOSED, AssertionStatus.DISPUTED}),
    AssertionStatus.DISPUTED: frozenset({AssertionStatus.ACCEPTED, AssertionStatus.REJECTED}),
}

#: Lint rules (docs/07 §3) that block publication. Warnings are shown, never enforced.
BLOCKING_RULES: frozenset[str] = frozenset({"D2", "D3", "D4", "D5", "D11"})


def may(role: Role, action: Action) -> bool:
    return role in ACTION_ROLES.get(action, frozenset())


def may_review(role: Role) -> bool:
    return may(role, Action.APPROVE)


def can_publish(role: Role) -> bool:
    return may(role, Action.PUBLISH)


def allowed_targets(status: Status) -> tuple[Status, ...]:
    """Where a record in ``status`` may legally go next, in a stable order."""
    order = [
        Status.DRAFT,
        Status.IN_REVIEW,
        Status.CHANGES_REQUESTED,
        Status.PUBLISHED,
        Status.ARCHIVED,
    ]
    allowed = TRANSITIONS.get(status, frozenset())
    return tuple(target for target in order if target in allowed)


def action_for(current: Status, target: Status) -> Action | None:
    """The action that performs ``current -> target``, or None when the edge does not exist."""
    if target not in TRANSITIONS.get(current, frozenset()):
        return None
    return TRANSITION_ACTION.get((current, target))


def action_for_status(target: Status) -> Action:
    """The action a reviewer means when they ask for a target status."""
    return {
        Status.IN_REVIEW: Action.SUBMIT,
        Status.PUBLISHED: Action.APPROVE,
        Status.CHANGES_REQUESTED: Action.REQUEST_CHANGES,
        Status.ARCHIVED: Action.ARCHIVE,
        Status.DRAFT: Action.UPDATE,
    }.get(target, Action.UPDATE)


def requires_review_before_publish(status: Status) -> bool:
    """Rule 3: ``published`` is reachable only from ``in_review``.

    ``archived -> published`` is the admin restore path, which is why it is exempt: the record was
    reviewed and published before, and only an admin can put it back.
    """
    return status is not Status.ARCHIVED


def assertion_may(current: AssertionStatus, target: AssertionStatus) -> bool:
    return target in ASSERTION_TRANSITIONS.get(current, frozenset())


__all__ = [
    "ACTION_ROLES",
    "ACTION_TARGET",
    "ASSERTION_TRANSITIONS",
    "BLOCKING_RULES",
    "EDITABLE_STATUSES",
    "ROLE_RANK",
    "SUFFICIENT_STANCES",
    "TRANSITIONS",
    "TRANSITION_ACTION",
    "Action",
    "Role",
    "action_for",
    "action_for_status",
    "allowed_targets",
    "assertion_may",
    "can_publish",
    "claim_is_evidenced",
    "is_editable",
    "may",
    "may_review",
    "requires_review_before_publish",
]
