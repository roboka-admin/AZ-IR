"""Authentication for the internal editorial panel (ADR-0010).

The public atlas API stays read-only and anonymous; these three endpoints are the door to the write
side. Sessions are cookies, not bearer tokens: the panel is a same-site web app, and a cookie the
script cannot read (HttpOnly) is not exfiltratable by an XSS in the map bundle.

CSRF is the double-submit pattern: the session cookie is HttpOnly, its partner ``azir_csrf`` cookie
is readable by the frontend, and every state-changing request must echo it in ``X-CSRF-Token``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from ...core.config import Settings
from ...domain.editorial import Action
from ...repositories.ports import Actor, SessionTicket
from ...schemas.editorial import LoginIn
from ..deps import ActorDep, EditorialDep, RequestDep, SettingsDep, session_token

_ALL_ACTIONS = tuple(Action)

router = APIRouter(prefix="/auth", tags=["editorial"])


def _actor_dict(actor: Actor) -> dict[str, Any]:
    return {
        "id": actor.id,
        "email": actor.email,
        "display_name": actor.display_name,
        "role": actor.role.value,
        "is_active": actor.is_active,
    }


def _set_session_cookies(response: Response, settings: Settings, ticket: SessionTicket) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=ticket.token,
        max_age=settings.session_ttl_seconds,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    # Readable by script on purpose: this is the half of the double-submit pair the frontend echoes
    # back in a header. It grants nothing on its own.
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=ticket.csrf_token,
        max_age=settings.session_ttl_seconds,
        path="/",
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.session_cookie_samesite,
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(key=name, path="/")


@router.post("/login", summary="Exchange credentials for a session cookie")
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    service: EditorialDep,
    settings: SettingsDep,
    request_id: RequestDep,
) -> dict[str, Any]:
    ticket = service.login(
        payload.email,
        payload.password,
        request_id=request_id,
        user_agent=request.headers.get("user-agent"),
        client_ip=request.client.host if request.client else None,
    )
    _set_session_cookies(response, settings, ticket)
    return {
        "data": {
            "actor": _actor_dict(ticket.actor),
            "csrf_token": ticket.csrf_token,
            "csrf_header": settings.csrf_header_name,
            "expires_at": ticket.expires_at.isoformat(),
        },
        "meta": {"driver": service.driver_name, "editorial": settings.editorial_on},
    }


@router.post("/logout", summary="Revoke the current session")
def logout(
    request: Request,
    response: Response,
    service: EditorialDep,
    settings: SettingsDep,
    request_id: RequestDep,
) -> dict[str, Any]:
    service.logout(session_token(request), request_id=request_id)
    _clear_session_cookies(response, settings)
    return {"data": {"logged_out": True}, "meta": {}}


@router.get("/me", summary="Who am I, and what may I do?")
def me(
    service: EditorialDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> dict[str, Any]:
    return {
        "data": {
            "actor": _actor_dict(actor),
            "permissions": sorted(action.value for action in _ALL_ACTIONS if actor.may(action)),
            "csrf_header": settings.csrf_header_name,
        },
        "meta": {"driver": service.driver_name, "editorial": settings.editorial_on},
    }
