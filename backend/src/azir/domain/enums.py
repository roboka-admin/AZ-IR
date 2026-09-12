"""Controlled vocabularies for the historical domain.

Every value here must exist in the ``taxonomy`` table (ADR-0012). The Python enums are the
typed mirror used by the pure domain layer; the database is the authoritative registry so that
new values can be added by an editor through a reviewed data migration.
"""

from __future__ import annotations

from enum import StrEnum


class EntityType(StrEnum):
    """Polymorphic subject/object of assertions and links."""

    PLACE = "place"
    PERSON = "person"
    EVENT = "event"
    POLITICAL_ENTITY = "political_entity"
    ARTICLE = "article"
    SOURCE = "source"
    PERIOD = "period"


class Status(StrEnum):
    """Editorial lifecycle (ADR-0010)."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    # bulk imports that nobody has verified yet -- never shown as published truth
    IMPORTED_UNVERIFIED = "imported_unverified"


PUBLIC_STATUSES: frozenset[Status] = frozenset({Status.PUBLISHED})


class Calendar(StrEnum):
    GREGORIAN_PROLEPTIC = "gregorian_proleptic"
    JULIAN = "julian"
    ISLAMIC_LUNAR = "islamic_lunar"
    PERSIAN_SOLAR = "persian_solar"
    UNKNOWN = "unknown"


class Precision(StrEnum):
    """How precisely a date is known -- never silently upgraded to ``exact`` (ADR-0005)."""

    EXACT_DAY = "exact_day"
    EXACT_MONTH = "exact_month"
    EXACT_YEAR = "exact_year"
    CIRCA_YEAR = "circa_year"
    DECADE = "decade"
    QUARTER_CENTURY = "quarter_century"
    CENTURY = "century"
    HALF_MILLENNIUM = "half_millennium"
    MILLENNIUM = "millennium"
    BEFORE = "before"
    AFTER = "after"
    RANGE = "range"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DISPUTED = "disputed"


class AssertionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DISPUTED = "disputed"


class Stance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"


class Certainty(StrEnum):
    """Spatial certainty -- drives the visual language (ADR-0013)."""

    EXACT = "exact"
    APPROXIMATE = "approximate"
    UNCERTAIN = "uncertain"
    RECONSTRUCTED = "reconstructed"


class GeometryKind(StrEnum):
    POINT = "point"
    FOOTPRINT = "footprint"
    EXTENT_RECONSTRUCTED = "extent_reconstructed"
    MODERN_ADMIN = "modern_admin"
    ROUTE_ALIGNMENT = "route_alignment"
    UNCERTAIN_LOCUS = "uncertain_locus"


class Attestation(StrEnum):
    """How well an event is attested in the sources."""

    WELL_ATTESTED = "well_attested"
    SINGLE_SOURCE = "single_source"
    TRADITIONAL = "traditional"
    LEGENDARY = "legendary"
    UNKNOWN = "unknown"


class Locale(StrEnum):
    FA = "fa"
    EN = "en"


LOCALE_DIRECTION: dict[str, str] = {Locale.FA: "rtl", Locale.EN: "ltr"}
LOCALE_FALLBACK: dict[str, str] = {Locale.FA: "fa", Locale.EN: "fa"}
DEFAULT_LOCALE: Locale = Locale.FA
