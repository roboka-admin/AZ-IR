"""FastAPI dependencies: the only place where services are constructed."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..core.config import Settings, get_settings
from ..core.errors import ValidationError
from ..core.i18n import negotiate_locale
from ..repositories.ports import Actor, AtlasRepository, EditorialRepository
from ..services.atlas import AtlasService
from ..services.editorial import EditorialService
from ..services.entity import ArticleService, EntityService
from ..services.lint import DataLinter
from ..services.registry import get_editorial_repository, get_repository
from ..services.search import SearchService


def request_settings(request: Request) -> Settings:
    """The settings *this app* was built with.

    ``create_app(settings)`` accepts an explicit object, and ``app.state.settings`` is what the rest
    of the app (cookies, middleware) already reads. Falling back to the process-wide cache keeps
    scripts and the CLI working. Reading two different settings objects in one request is how a
    deployment ends up with a cookie name nobody recognises.
    """
    stored = getattr(request.app.state, "settings", None)
    return stored if isinstance(stored, Settings) else get_settings()


SettingsDep = Annotated[Settings, Depends(request_settings)]
RepositoryDep = Annotated[AtlasRepository, Depends(get_repository)]
EditorialRepositoryDep = Annotated[EditorialRepository, Depends(get_editorial_repository)]

#: Methods that never change state, and therefore never need a CSRF token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_atlas_service(repo: RepositoryDep, settings: SettingsDep) -> AtlasService:
    return AtlasService(repo, settings)


def get_entity_service(repo: RepositoryDep, settings: SettingsDep) -> EntityService:
    return EntityService(repo, settings)


def get_article_service(repo: RepositoryDep, settings: SettingsDep) -> ArticleService:
    return ArticleService(repo, settings)


def get_search_service(repo: RepositoryDep, settings: SettingsDep) -> SearchService:
    return SearchService(repo, settings)


def get_editorial_service(
    editorial: EditorialRepositoryDep, repo: RepositoryDep, settings: SettingsDep
) -> EditorialService:
    """Write-side orchestration. Reads and writes share one repository object per driver."""
    return EditorialService(editorial, repo, settings)


def get_linter(repo: RepositoryDep, settings: SettingsDep) -> DataLinter:
    return DataLinter(repo, settings)


AtlasDep = Annotated[AtlasService, Depends(get_atlas_service)]
EntityDep = Annotated[EntityService, Depends(get_entity_service)]
ArticleDep = Annotated[ArticleService, Depends(get_article_service)]
SearchDep = Annotated[SearchService, Depends(get_search_service)]
EditorialDep = Annotated[EditorialService, Depends(get_editorial_service)]
LinterDep = Annotated[DataLinter, Depends(get_linter)]


def get_locale(request: Request, locale: str | None = None) -> str:
    """``?locale=`` wins, then ``Accept-Language``, then the app default (ADR-0006).

    An explicit ``?locale=`` is a programming contract, so an unsupported value is a 422 rather
    than a silent fallback: a typo in the frontend must surface, not quietly serve Persian to an
    English reader. ``Accept-Language`` stays lenient, because browsers send whatever they like.
    """
    settings: Settings = request.app.state.settings
    if locale is not None and locale not in settings.supported_locales:
        raise ValidationError(
            f"unsupported locale {locale!r}",
            parameter="locale",
            supported=list(settings.supported_locales),
        )
    return negotiate_locale(locale, request.headers.get("accept-language"), settings.default_locale)


LocaleDep = Annotated[str, Depends(get_locale)]


# ------------------------------------------------------------------ editorial session
#
# The public API is anonymous. Everything below serves the write side only, and it is the single
# place where a cookie becomes an actor: no router ever reads cookies itself.


def session_token(request: Request) -> str | None:
    settings: Settings = request.app.state.settings
    return request.cookies.get(settings.session_cookie_name)


def request_id_of(request: Request) -> str | None:
    """The id the logging middleware minted; every audit row carries it (ADR-0010 rule 5)."""
    return getattr(request.state, "request_id", None)


def current_actor(request: Request, service: EditorialDep) -> Actor:
    return service.actor_for(session_token(request))


def csrf_guard(request: Request, service: EditorialDep) -> None:
    """Double-submit CSRF: unsafe methods must echo the readable cookie in a header."""
    if request.method in SAFE_METHODS:
        return
    settings: Settings = request.app.state.settings
    service.check_csrf(
        session_token(request), request.headers.get(settings.csrf_header_name)
    )


ActorDep = Annotated[Actor, Depends(current_actor)]
RequestDep = Annotated[str | None, Depends(request_id_of)]
CsrfDep = Annotated[None, Depends(csrf_guard)]
