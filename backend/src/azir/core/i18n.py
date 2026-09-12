"""Locale negotiation and display-name resolution (ADR-0006).

Deliberately tiny: no framework, no extra dependency. The UI reads all copy from
``frontend/messages/*.json``; the backend only resolves *data* names and labels.
"""

from __future__ import annotations

from typing import Final

from ..domain.enums import DEFAULT_LOCALE, LOCALE_DIRECTION, Locale
from ..domain.model import EntityRecord

SUPPORTED: Final[tuple[str, ...]] = (Locale.FA, Locale.EN)


def negotiate_locale(param: str | None, accept_language: str | None, default: str) -> str:
    """Resolve a locale from ``?locale=`` then ``Accept-Language`` then the app default."""
    if param:
        candidate = param.strip().lower()
        if candidate in SUPPORTED:
            return candidate
        base = candidate.split("-")[0]
        if base in SUPPORTED:
            return base
    if accept_language:
        for chunk in accept_language.split(","):
            tag = chunk.split(";")[0].strip().lower()
            if tag in SUPPORTED:
                return tag
            base = tag.split("-")[0]
            if base in SUPPORTED:
                return base
    return default if default in SUPPORTED else DEFAULT_LOCALE


def direction(locale: str) -> str:
    return LOCALE_DIRECTION.get(locale, "rtl" if locale == Locale.FA else "ltr")


def display_name(entity: EntityRecord, locale: str, at_year: int | None = None) -> str:
    return entity.display_name(locale, at_year)


def display_name_secondary(entity: EntityRecord, locale: str) -> str | None:
    return entity.secondary_name(locale)
