"""SQLAlchemy Core mirror of the PostGIS schema.

The migrations in ``backend/migrations`` are the frozen source of truth for DDL; this metadata is
the *typed* view of the same schema used by the repository (queries, inserts in the seeder) and by
``tests/test_postgis_schema.py``, which fails if the two drift apart.

Design notes
------------
* Spatial data lives in PostGIS, in SRID 4326, one row per geometry with its own validity window
  (AGENTS.md rule 11, ADR-0004). An entity may therefore have several geometries: a dynasty's
  extent in 1510 is not its extent in 1700.
* Rank / min_zoom / max_zoom / layer are *domain* outputs, computed in Python at write time and
  stored as columns. SQL only ever sorts and filters by them, so both drivers agree (ADR-0014).
* Certainty, precision, confidence and status are columns, never implied by presentation.
* The polymorphic ``(entity_type, entity_id)`` pair keeps the read model unified: one name table,
  one geometry table, one assertion table for every entity kind.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

METADATA = sa.MetaData(schema="public", naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})

# ------------------------------------------------------------------ vocabulary

entity_kind = sa.Table(
    "entity_kind", METADATA,
    sa.Column("code", sa.Text, primary_key=True),
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("label_fa", sa.Text, nullable=False),
    sa.Column("label_en", sa.Text, nullable=False),
    sa.Column("weight", sa.Numeric(4, 3), nullable=False, server_default="0.5"),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
)

predicate = sa.Table(
    "predicate", METADATA,
    sa.Column("code", sa.Text, primary_key=True),
    sa.Column("label_fa", sa.Text, nullable=False),
    sa.Column("label_en", sa.Text, nullable=False),
    sa.Column("inverse_code", sa.Text),
    sa.Column("applies_to", JSONB, nullable=False, server_default="[]"),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
)

period_scheme = sa.Table(
    "period_scheme", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("code", sa.Text, nullable=False, unique=True),
    sa.Column("name_fa", sa.Text, nullable=False),
    sa.Column("name_en", sa.Text, nullable=False),
    sa.Column("description_fa", sa.Text),
    sa.Column("owner", sa.Text),
    sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("sources", JSONB, nullable=False, server_default="[]"),
)

# Reference data (sources, periods) is addressable through the same read model as narrative
# entities, so it carries the same presentation spine. Values are computed in Python by the domain
# at write time -- SQL never re-derives rank or zoom bands (ADR-0014).
def _ref_spine() -> list[sa.Column[Any]]:
    """Fresh Column objects: SQLAlchemy forbids sharing one instance between tables."""
    return [
        sa.Column("status", sa.Text, nullable=False, server_default="published"),
        sa.Column("rank", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("layer", sa.Text, nullable=False, server_default="sources"),
        sa.Column("min_zoom", sa.Numeric(4, 2), nullable=False, server_default="0"),
        sa.Column("max_zoom", sa.Numeric(4, 2), nullable=False, server_default="22"),
        sa.Column("year_from", sa.Integer),
        sa.Column("year_to", sa.Integer),
        sa.Column("precision", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.Text, nullable=False, server_default="medium"),
        sa.Column("calendar", sa.Text, nullable=False, server_default="gregorian_proleptic"),
        sa.Column("temporal_display_fa", sa.Text),
        sa.Column("temporal_display_en", sa.Text),
        sa.Column("summary_fa", sa.Text),
        sa.Column("summary_en", sa.Text),
        sa.Column("certainty", sa.Text),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
    ]


def _period_spine() -> list[sa.Column[Any]]:
    """`period` already declares year_from/year_to/precision/confidence of its own."""
    skip = {"year_from", "year_to", "precision", "confidence"}
    return [column for column in _ref_spine() if column.name not in skip]


period = sa.Table(
    "period", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("scheme_id", sa.Text, sa.ForeignKey("period_scheme.id", ondelete="CASCADE"), nullable=False),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("label_fa", sa.Text, nullable=False),
    sa.Column("label_en", sa.Text, nullable=False),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
    sa.Column("precision", sa.Text, nullable=False, server_default="range"),
    sa.Column("confidence", sa.Text, nullable=False, server_default="medium"),
    sa.Column("note_fa", sa.Text),
    sa.Column("note_en", sa.Text),
    sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
    *_period_spine(),
)


source = sa.Table(
    "source", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("title_fa", sa.Text),
    sa.Column("title_en", sa.Text),
    sa.Column("author", sa.Text),
    sa.Column("author_fa", sa.Text),
    sa.Column("origin_year", sa.Integer),
    sa.Column("origin_temporal", JSONB),
    sa.Column("reliability", sa.Text, nullable=False, server_default="secondary"),
    sa.Column("language", sa.Text),
    sa.Column("publisher", sa.Text),
    sa.Column("citation", sa.Text),
    sa.Column("url", sa.Text),
    sa.Column("license", sa.Text),
    sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("external_ids", JSONB, nullable=False, server_default="{}"),
    *_ref_spine(),
)

# ------------------------------------------------------------------ entities
#
# Every narrative entity carries the same temporal + editorial spine. The columns are repeated
# per table (instead of one polymorphic table) because each kind has genuinely different fields
# and because partitioning by kind keeps the hot map queries narrow.

def _entity_common() -> list[sa.Column[Any]]:
    """Fresh Column objects per table (SQLAlchemy forbids sharing instances)."""
    return [
        sa.Column("id", sa.Text, primary_key=True),
        # Nullable: articles carry no kind, and reference data keeps its own (source.kind).
        sa.Column("kind", sa.Text, sa.ForeignKey("entity_kind.code")),
        sa.Column("slug", sa.Text, unique=True),
        sa.Column("status", sa.Text, nullable=False, server_default="draft"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("importance", sa.Numeric(4, 3), nullable=False, server_default="0.5"),
        # Domain-computed presentation aids (never recomputed in SQL):
        sa.Column("rank", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("layer", sa.Text, nullable=False, server_default="places"),
        sa.Column("min_zoom", sa.Numeric(4, 2), nullable=False, server_default="0"),
        sa.Column("max_zoom", sa.Numeric(4, 2), nullable=False, server_default="22"),
        # Normalized (astronomical) validity window; `precision` keeps the human claim honest.
        sa.Column("year_from", sa.Integer),
        sa.Column("year_to", sa.Integer),
        sa.Column("precision", sa.Text, nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.Text, nullable=False, server_default="medium"),
        sa.Column("calendar", sa.Text, nullable=False, server_default="gregorian_proleptic"),
        sa.Column("temporal_display_fa", sa.Text),
        sa.Column("temporal_display_en", sa.Text),
        sa.Column("summary_fa", sa.Text),
        sa.Column("summary_en", sa.Text),
        sa.Column("coverage_note_fa", sa.Text),
        sa.Column("coverage_note_en", sa.Text),
        sa.Column("certainty", sa.Text),
        sa.Column("sources", JSONB, nullable=False, server_default="[]"),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _entity_table(name: str, *extra: sa.Column[Any]) -> sa.Table:
    return sa.Table(name, METADATA, *_entity_common(), *extra)


place = _entity_table("place")
person = _entity_table("person")
political_entity = _entity_table("political_entity")
event = _entity_table(
    "event",
    sa.Column("attestation", sa.Text),
)
article = _entity_table(
    "article",
    sa.Column("title_fa", sa.Text),
    sa.Column("title_en", sa.Text),
    sa.Column("body_fa_md", sa.Text),
    sa.Column("body_en_md", sa.Text),
    sa.Column("author", sa.Text),
    sa.Column("published_at", sa.Date),
    sa.Column("reading_time_min", sa.Integer),
    sa.Column("lang", sa.Text, server_default="fa"),
    sa.Column("map_state", JSONB),
)

ENTITY_TABLES = {
    "place": place,
    "person": person,
    "event": event,
    "political_entity": political_entity,
    "article": article,
}

# ------------------------------------------------------------------ shared entity facets

name_variant = sa.Table(
    "name_variant", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("entity_id", sa.Text, nullable=False),
    sa.Column("form", sa.Text, nullable=False),
    sa.Column("lang", sa.Text, nullable=False, server_default="fa"),
    sa.Column("script", sa.Text, nullable=False, server_default="Arab"),
    sa.Column("kind", sa.Text, nullable=False, server_default="preferred"),
    sa.Column("transliteration", sa.Text),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
    sa.Column("source_id", sa.Text, sa.ForeignKey("source.id")),
    sa.Column("note", sa.Text),
    # Normalized search form, produced by the same folding rules as the domain (ADR-0007).
    sa.Column("search_form", sa.Text, nullable=False, server_default=""),
    sa.Column("search_tsv", TSVECTOR),
)

# geometry() columns are declared in the migration DDL (PostGIS type); the mirror keeps them as
# opaque JSON so that importing this module never requires GeoAlchemy2 to be installed.
entity_geometry = sa.Table(
    "entity_geometry", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("entity_id", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False, server_default="point"),
    sa.Column("certainty", sa.Text, nullable=False, server_default="exact"),
    sa.Column("geom_json", JSONB, nullable=False),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
    sa.Column("lod_min_zoom", sa.Numeric(4, 2), nullable=False, server_default="0"),
    sa.Column("lod_max_zoom", sa.Numeric(4, 2), nullable=False, server_default="22"),
    sa.Column("source_id", sa.Text, sa.ForeignKey("source.id")),
    sa.Column("note_fa", sa.Text),
    sa.Column("note_en", sa.Text),
    sa.Column("needs_digitisation", sa.Boolean, nullable=False, server_default=sa.false()),
)

place_link = sa.Table(
    "place_link", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("parent_id", sa.Text, sa.ForeignKey("place.id", ondelete="CASCADE"), nullable=False),
    sa.Column("child_id", sa.Text, sa.ForeignKey("place.id", ondelete="CASCADE"), nullable=False),
    sa.Column("kind", sa.Text, sa.ForeignKey("predicate.code"), nullable=False),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
    sa.Column("precision", sa.Text, nullable=False, server_default="range"),
    sa.Column("confidence", sa.Text, nullable=False, server_default="medium"),
    sa.Column("certainty", sa.Text),
    sa.Column("sources", JSONB, nullable=False, server_default="[]"),
    sa.Column("note_fa", sa.Text),
    sa.Column("note_en", sa.Text),
    sa.UniqueConstraint("parent_id", "child_id", "kind", "year_from", name="uq_place_link_edge"),
)

event_place = sa.Table(
    "event_place", METADATA,
    sa.Column("event_id", sa.Text, sa.ForeignKey("event.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("place_id", sa.Text, sa.ForeignKey("place.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("role", sa.Text, nullable=False, server_default="site_of"),
    sa.Column("certainty", sa.Text),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
)

event_participant = sa.Table(
    "event_participant", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("event_id", sa.Text, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
    sa.Column("participant_type", sa.Text, nullable=False),
    sa.Column("participant_id", sa.Text, nullable=False),
    sa.Column("role", sa.Text),
    sa.Column("side", sa.Text),
)

event_link = sa.Table(
    "event_link", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("event_id", sa.Text, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
    sa.Column("relation", sa.Text, nullable=False),
    sa.Column("other_event_id", sa.Text, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
)

article_entity = sa.Table(
    "article_entity", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("article_id", sa.Text, sa.ForeignKey("article.id", ondelete="CASCADE"), nullable=False),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("entity_id", sa.Text, nullable=False),
    sa.Column("relation", sa.Text, nullable=False, server_default="mentions"),
    sa.UniqueConstraint("article_id", "entity_type", "entity_id", "relation", name="uq_article_entity_link"),
)

# ------------------------------------------------------------------ claims (the graph)

assertion = sa.Table(
    "assertion", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("subject_type", sa.Text, nullable=False),
    sa.Column("subject_id", sa.Text, nullable=False),
    sa.Column("predicate", sa.Text, sa.ForeignKey("predicate.code"), nullable=False),
    sa.Column("object_type", sa.Text),
    sa.Column("object_id", sa.Text),
    sa.Column("value", sa.Text),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
    sa.Column("precision", sa.Text, nullable=False, server_default="unknown"),
    sa.Column("confidence", sa.Text, nullable=False, server_default="medium"),
    sa.Column("calendar", sa.Text, nullable=False, server_default="gregorian_proleptic"),
    sa.Column("temporal_display_fa", sa.Text),
    sa.Column("status", sa.Text, nullable=False, server_default="proposed"),
    sa.Column("topic_fa", sa.Text),
    sa.Column("topic_en", sa.Text),
    sa.Column("note_fa", sa.Text),
    sa.Column("note_en", sa.Text),
    sa.Column("role", sa.Text),
    sa.Column("created_by", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

evidence = sa.Table(
    "evidence", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("assertion_id", sa.Text, sa.ForeignKey("assertion.id", ondelete="CASCADE"), nullable=False),
    sa.Column("source_id", sa.Text, sa.ForeignKey("source.id"), nullable=False),
    sa.Column("locator_type", sa.Text, nullable=False, server_default="page"),
    sa.Column("locator", sa.Text),
    sa.Column("quote_original", sa.Text),
    sa.Column("quote_translation", sa.Text),
    sa.Column("stance", sa.Text, nullable=False, server_default="supports"),
)

# ------------------------------------------------------------------ editorial machinery (ADR-0010)

app_user = sa.Table(
    "app_user", METADATA,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("email", sa.Text, nullable=False, unique=True),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("role", sa.Text, nullable=False, server_default="editor"),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

audit_log = sa.Table(
    "audit_log", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("app_user.id")),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("entity_type", sa.Text),
    sa.Column("entity_id", sa.Text),
    sa.Column("payload", JSONB, nullable=False, server_default="{}"),
    sa.Column("request_id", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

revision_snapshot = sa.Table(
    "revision_snapshot", METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("entity_id", sa.Text, nullable=False),
    sa.Column("revision", sa.Integer, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("created_by", sa.Text, sa.ForeignKey("app_user.id")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("entity_type", "entity_id", "revision", name="uq_revision_snapshot_entity"),
)

slug_redirect = sa.Table(
    "slug_redirect", METADATA,
    sa.Column("entity_type", sa.Text, primary_key=True),
    sa.Column("old_slug", sa.Text, primary_key=True),
    sa.Column("new_slug", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

TABLES: tuple[str, ...] = tuple(sorted(METADATA.tables))

# The unified read model used by map, search and timeline queries. Created by the migration as a
# VIEW; declared here so the repository can query it through the same metadata.
entity_read_model = sa.Table(
    "entity_read_model", METADATA,
    sa.Column("id", sa.Text),
    sa.Column("entity_type", sa.Text),
    sa.Column("kind", sa.Text),
    sa.Column("slug", sa.Text),
    sa.Column("status", sa.Text),
    sa.Column("importance", sa.Numeric(4, 3)),
    sa.Column("rank", sa.Numeric(5, 2)),
    sa.Column("layer", sa.Text),
    sa.Column("min_zoom", sa.Numeric(4, 2)),
    sa.Column("max_zoom", sa.Numeric(4, 2)),
    sa.Column("year_from", sa.Integer),
    sa.Column("year_to", sa.Integer),
    sa.Column("precision", sa.Text),
    sa.Column("confidence", sa.Text),
    sa.Column("calendar", sa.Text),
    sa.Column("temporal_display_fa", sa.Text),
    sa.Column("temporal_display_en", sa.Text),
    sa.Column("summary_fa", sa.Text),
    sa.Column("summary_en", sa.Text),
    sa.Column("coverage_note_fa", sa.Text),
    sa.Column("coverage_note_en", sa.Text),
    sa.Column("certainty", sa.Text),
    sa.Column("attestation", sa.Text),
    sa.Column("body_fa_md", sa.Text),
    sa.Column("body_en_md", sa.Text),
    sa.Column("map_state", JSONB),
    sa.Column("sources", JSONB),
    sa.Column("extra", JSONB),
    extend_existing=True,
)

__all__ = [
    "ENTITY_TABLES",
    "METADATA",
    "TABLES",
    "article",
    "article_entity",
    "assertion",
    "audit_log",
    "entity_geometry",
    "entity_kind",
    "entity_read_model",
    "event",
    "event_link",
    "event_participant",
    "event_place",
    "evidence",
    "name_variant",
    "period",
    "period_scheme",
    "person",
    "place",
    "place_link",
    "political_entity",
    "predicate",
    "revision_snapshot",
    "slug_redirect",
    "source",
]
