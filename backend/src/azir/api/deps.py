"""FastAPI dependencies: the only place where services are constructed."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..core.config import Settings, get_settings
from ..core.errors import ValidationError
from ..core.i18n import negotiate_locale
from ..repositories.ports import AtlasRepository
from ..services.atlas import AtlasService
from ..services.entity import ArticleService, EntityService
from ..services.registry import get_repository
from ..services.search import SearchService

SettingsDep = Annotated[Settings, Depends(get_settings)]
RepositoryDep = Annotated[AtlasRepository, Depends(get_repository)]


def get_atlas_service(repo: RepositoryDep, settings: SettingsDep) -> AtlasService:
    return AtlasService(repo, settings)


def get_entity_service(repo: RepositoryDep, settings: SettingsDep) -> EntityService:
    return EntityService(repo, settings)


def get_article_service(repo: RepositoryDep, settings: SettingsDep) -> ArticleService:
    return ArticleService(repo, settings)


def get_search_service(repo: RepositoryDep, settings: SettingsDep) -> SearchService:
    return SearchService(repo, settings)


AtlasDep = Annotated[AtlasService, Depends(get_atlas_service)]
EntityDep = Annotated[EntityService, Depends(get_entity_service)]
ArticleDep = Annotated[ArticleService, Depends(get_article_service)]
SearchDep = Annotated[SearchService, Depends(get_search_service)]


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
