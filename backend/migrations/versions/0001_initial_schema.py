"""Initial schema: entities, time-varying geometry, claims, editorial machinery.

Frozen DDL (ADR-0002, ADR-0003, ADR-0004, ADR-0010). This migration is the source of truth for
the database; ``azir.repositories.postgis.schema`` mirrors it in SQLAlchemy Core and
``tests/test_postgis_schema.py`` fails if the two drift apart.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-12
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

EXTENSIONS = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
"""

TABLES = """
CREATE TABLE public.entity_kind (
	code TEXT NOT NULL, 
	category TEXT NOT NULL, 
	label_fa TEXT NOT NULL, 
	label_en TEXT NOT NULL, 
	weight NUMERIC(4, 3) DEFAULT '0.5' NOT NULL, 
	is_active BOOLEAN DEFAULT true NOT NULL, 
	CONSTRAINT pk_entity_kind PRIMARY KEY (code)
);

CREATE TABLE public.predicate (
	code TEXT NOT NULL, 
	label_fa TEXT NOT NULL, 
	label_en TEXT NOT NULL, 
	inverse_code TEXT, 
	applies_to JSONB DEFAULT '[]' NOT NULL, 
	is_active BOOLEAN DEFAULT true NOT NULL, 
	CONSTRAINT pk_predicate PRIMARY KEY (code)
);

CREATE TABLE public.period_scheme (
	id TEXT NOT NULL, 
	code TEXT NOT NULL, 
	name_fa TEXT NOT NULL, 
	name_en TEXT NOT NULL, 
	description_fa TEXT, 
	owner TEXT, 
	is_default BOOLEAN DEFAULT false NOT NULL, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	CONSTRAINT pk_period_scheme PRIMARY KEY (id), 
	CONSTRAINT uq_period_scheme_code UNIQUE (code)
);

CREATE TABLE public.period (
	id TEXT NOT NULL, 
	scheme_id TEXT NOT NULL, 
	code TEXT NOT NULL, 
	label_fa TEXT NOT NULL, 
	label_en TEXT NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'range' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	note_fa TEXT, 
	note_en TEXT, 
	sort_order INTEGER DEFAULT '0' NOT NULL, 
	status TEXT DEFAULT 'published' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'sources' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	certainty TEXT, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	CONSTRAINT pk_period PRIMARY KEY (id), 
	CONSTRAINT fk_period_scheme_id_period_scheme FOREIGN KEY(scheme_id) REFERENCES public.period_scheme (id) ON DELETE CASCADE
);

CREATE TABLE public.source (
	id TEXT NOT NULL, 
	kind TEXT, 
	title TEXT NOT NULL, 
	title_fa TEXT, 
	title_en TEXT, 
	author TEXT, 
	author_fa TEXT, 
	origin_year INTEGER, 
	origin_temporal JSONB, 
	reliability TEXT DEFAULT 'secondary' NOT NULL, 
	language TEXT, 
	publisher TEXT, 
	citation TEXT, 
	url TEXT, 
	license TEXT, 
	needs_review BOOLEAN DEFAULT false NOT NULL, 
	external_ids JSONB DEFAULT '{}' NOT NULL, 
	status TEXT DEFAULT 'published' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'sources' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	certainty TEXT, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	CONSTRAINT pk_source PRIMARY KEY (id)
);

CREATE TABLE public.place (
	id TEXT NOT NULL, 
	kind TEXT, 
	slug TEXT, 
	status TEXT DEFAULT 'draft' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	importance NUMERIC(4, 3) DEFAULT '0.5' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'places' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	coverage_note_fa TEXT, 
	coverage_note_en TEXT, 
	certainty TEXT, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_place PRIMARY KEY (id), 
	CONSTRAINT fk_place_kind_entity_kind FOREIGN KEY(kind) REFERENCES public.entity_kind (code), 
	CONSTRAINT uq_place_slug UNIQUE (slug)
);

CREATE TABLE public.person (
	id TEXT NOT NULL, 
	kind TEXT, 
	slug TEXT, 
	status TEXT DEFAULT 'draft' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	importance NUMERIC(4, 3) DEFAULT '0.5' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'places' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	coverage_note_fa TEXT, 
	coverage_note_en TEXT, 
	certainty TEXT, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_person PRIMARY KEY (id), 
	CONSTRAINT fk_person_kind_entity_kind FOREIGN KEY(kind) REFERENCES public.entity_kind (code), 
	CONSTRAINT uq_person_slug UNIQUE (slug)
);

CREATE TABLE public.event (
	id TEXT NOT NULL, 
	kind TEXT, 
	slug TEXT, 
	status TEXT DEFAULT 'draft' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	importance NUMERIC(4, 3) DEFAULT '0.5' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'places' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	coverage_note_fa TEXT, 
	coverage_note_en TEXT, 
	certainty TEXT, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	attestation TEXT, 
	CONSTRAINT pk_event PRIMARY KEY (id), 
	CONSTRAINT fk_event_kind_entity_kind FOREIGN KEY(kind) REFERENCES public.entity_kind (code), 
	CONSTRAINT uq_event_slug UNIQUE (slug)
);

CREATE TABLE public.political_entity (
	id TEXT NOT NULL, 
	kind TEXT, 
	slug TEXT, 
	status TEXT DEFAULT 'draft' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	importance NUMERIC(4, 3) DEFAULT '0.5' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'places' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	coverage_note_fa TEXT, 
	coverage_note_en TEXT, 
	certainty TEXT, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_political_entity PRIMARY KEY (id), 
	CONSTRAINT fk_political_entity_kind_entity_kind FOREIGN KEY(kind) REFERENCES public.entity_kind (code), 
	CONSTRAINT uq_political_entity_slug UNIQUE (slug)
);

CREATE TABLE public.article (
	id TEXT NOT NULL, 
	kind TEXT, 
	slug TEXT, 
	status TEXT DEFAULT 'draft' NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	importance NUMERIC(4, 3) DEFAULT '0.5' NOT NULL, 
	rank NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	layer TEXT DEFAULT 'places' NOT NULL, 
	min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	temporal_display_en TEXT, 
	summary_fa TEXT, 
	summary_en TEXT, 
	coverage_note_fa TEXT, 
	coverage_note_en TEXT, 
	certainty TEXT, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	extra JSONB DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	title_fa TEXT, 
	title_en TEXT, 
	body_fa_md TEXT, 
	body_en_md TEXT, 
	author TEXT, 
	published_at DATE, 
	reading_time_min INTEGER, 
	lang TEXT DEFAULT 'fa', 
	map_state JSONB, 
	CONSTRAINT pk_article PRIMARY KEY (id), 
	CONSTRAINT fk_article_kind_entity_kind FOREIGN KEY(kind) REFERENCES public.entity_kind (code), 
	CONSTRAINT uq_article_slug UNIQUE (slug)
);

CREATE TABLE public.name_variant (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	entity_type TEXT NOT NULL, 
	entity_id TEXT NOT NULL, 
	form TEXT NOT NULL, 
	lang TEXT DEFAULT 'fa' NOT NULL, 
	script TEXT DEFAULT 'Arab' NOT NULL, 
	kind TEXT DEFAULT 'preferred' NOT NULL, 
	transliteration TEXT, 
	year_from INTEGER, 
	year_to INTEGER, 
	source_id TEXT, 
	 note TEXT, 
	 search_form TEXT DEFAULT '' NOT NULL, 
	 CONSTRAINT pk_name_variant PRIMARY KEY (id), 
	CONSTRAINT fk_name_variant_source_id_source FOREIGN KEY(source_id) REFERENCES public.source (id)
);

CREATE TABLE public.entity_geometry (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	entity_type TEXT NOT NULL, 
	entity_id TEXT NOT NULL, 
	kind TEXT DEFAULT 'point' NOT NULL, 
	certainty TEXT DEFAULT 'exact' NOT NULL, 
	geom_json JSONB NOT NULL, 
	year_from INTEGER, 
	year_to INTEGER, 
	lod_min_zoom NUMERIC(4, 2) DEFAULT '0' NOT NULL, 
	lod_max_zoom NUMERIC(4, 2) DEFAULT '22' NOT NULL, 
	source_id TEXT, 
	note_fa TEXT, 
	note_en TEXT, 
	needs_digitisation BOOLEAN DEFAULT false NOT NULL, 
	CONSTRAINT pk_entity_geometry PRIMARY KEY (id), 
	CONSTRAINT fk_entity_geometry_source_id_source FOREIGN KEY(source_id) REFERENCES public.source (id)
);

CREATE TABLE public.place_link (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	parent_id TEXT NOT NULL, 
	child_id TEXT NOT NULL, 
	kind TEXT, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'range' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	certainty TEXT, 
	sources JSONB DEFAULT '[]' NOT NULL, 
	note_fa TEXT, 
	note_en TEXT, 
	CONSTRAINT pk_place_link PRIMARY KEY (id), 
	CONSTRAINT uq_place_link_edge UNIQUE (parent_id, child_id, kind, year_from), 
	CONSTRAINT fk_place_link_parent_id_place FOREIGN KEY(parent_id) REFERENCES public.place (id) ON DELETE CASCADE, 
	CONSTRAINT fk_place_link_child_id_place FOREIGN KEY(child_id) REFERENCES public.place (id) ON DELETE CASCADE, 
	CONSTRAINT fk_place_link_kind_predicate FOREIGN KEY(kind) REFERENCES public.predicate (code)
);

CREATE TABLE public.event_place (
	event_id TEXT NOT NULL, 
	place_id TEXT NOT NULL, 
	role TEXT DEFAULT 'site_of' NOT NULL, 
	certainty TEXT, 
	year_from INTEGER, 
	year_to INTEGER, 
	CONSTRAINT pk_event_place PRIMARY KEY (event_id, place_id), 
	CONSTRAINT fk_event_place_event_id_event FOREIGN KEY(event_id) REFERENCES public.event (id) ON DELETE CASCADE, 
	CONSTRAINT fk_event_place_place_id_place FOREIGN KEY(place_id) REFERENCES public.place (id) ON DELETE CASCADE
);

CREATE TABLE public.event_participant (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	event_id TEXT NOT NULL, 
	participant_type TEXT NOT NULL, 
	participant_id TEXT NOT NULL, 
	role TEXT, 
	side TEXT, 
	CONSTRAINT pk_event_participant PRIMARY KEY (id), 
	CONSTRAINT fk_event_participant_event_id_event FOREIGN KEY(event_id) REFERENCES public.event (id) ON DELETE CASCADE
);

CREATE TABLE public.event_link (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	event_id TEXT NOT NULL, 
	relation TEXT NOT NULL, 
	other_event_id TEXT NOT NULL, 
	CONSTRAINT pk_event_link PRIMARY KEY (id), 
	CONSTRAINT fk_event_link_event_id_event FOREIGN KEY(event_id) REFERENCES public.event (id) ON DELETE CASCADE, 
	CONSTRAINT fk_event_link_other_event_id_event FOREIGN KEY(other_event_id) REFERENCES public.event (id) ON DELETE CASCADE
);

CREATE TABLE public.article_entity (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	article_id TEXT NOT NULL, 
	entity_type TEXT NOT NULL, 
	entity_id TEXT NOT NULL, 
	relation TEXT DEFAULT 'mentions' NOT NULL, 
	CONSTRAINT pk_article_entity PRIMARY KEY (id), 
	CONSTRAINT uq_article_entity_link UNIQUE (article_id, entity_type, entity_id, relation), 
	CONSTRAINT fk_article_entity_article_id_article FOREIGN KEY(article_id) REFERENCES public.article (id) ON DELETE CASCADE
);

CREATE TABLE public.assertion (
	id TEXT NOT NULL, 
	subject_type TEXT NOT NULL, 
	subject_id TEXT NOT NULL, 
	predicate TEXT NOT NULL, 
	object_type TEXT, 
	object_id TEXT, 
	value TEXT, 
	year_from INTEGER, 
	year_to INTEGER, 
	precision TEXT DEFAULT 'unknown' NOT NULL, 
	confidence TEXT DEFAULT 'medium' NOT NULL, 
	calendar TEXT DEFAULT 'gregorian_proleptic' NOT NULL, 
	temporal_display_fa TEXT, 
	status TEXT DEFAULT 'proposed' NOT NULL, 
	topic_fa TEXT, 
	topic_en TEXT, 
	note_fa TEXT, 
	note_en TEXT, 
	role TEXT, 
	created_by TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_assertion PRIMARY KEY (id), 
	CONSTRAINT fk_assertion_predicate_predicate FOREIGN KEY(predicate) REFERENCES public.predicate (code)
);

CREATE TABLE public.evidence (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	assertion_id TEXT NOT NULL, 
	source_id TEXT NOT NULL, 
	locator_type TEXT DEFAULT 'page' NOT NULL, 
	locator TEXT, 
	quote_original TEXT, 
	quote_translation TEXT, 
	stance TEXT DEFAULT 'supports' NOT NULL, 
	CONSTRAINT pk_evidence PRIMARY KEY (id), 
	CONSTRAINT fk_evidence_assertion_id_assertion FOREIGN KEY(assertion_id) REFERENCES public.assertion (id) ON DELETE CASCADE, 
	CONSTRAINT fk_evidence_source_id_source FOREIGN KEY(source_id) REFERENCES public.source (id)
);

CREATE TABLE public.app_user (
	id TEXT NOT NULL, 
	email TEXT NOT NULL, 
	display_name TEXT NOT NULL, 
	role TEXT DEFAULT 'editor' NOT NULL, 
	is_active BOOLEAN DEFAULT true NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_app_user PRIMARY KEY (id), 
	CONSTRAINT uq_app_user_email UNIQUE (email)
);

CREATE TABLE public.audit_log (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	actor_id TEXT, 
	action TEXT NOT NULL, 
	entity_type TEXT, 
	entity_id TEXT, 
	payload JSONB DEFAULT '{}' NOT NULL, 
	request_id TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_audit_log PRIMARY KEY (id), 
	CONSTRAINT fk_audit_log_actor_id_app_user FOREIGN KEY(actor_id) REFERENCES public.app_user (id)
);

CREATE TABLE public.revision_snapshot (
	id BIGINT GENERATED BY DEFAULT AS IDENTITY, 
	entity_type TEXT NOT NULL, 
	entity_id TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	payload JSONB NOT NULL, 
	created_by TEXT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_revision_snapshot PRIMARY KEY (id), 
	CONSTRAINT uq_revision_snapshot_entity UNIQUE (entity_type, entity_id, revision), 
	CONSTRAINT fk_revision_snapshot_created_by_app_user FOREIGN KEY(created_by) REFERENCES public.app_user (id)
);

CREATE TABLE public.slug_redirect (
	entity_type TEXT NOT NULL, 
	old_slug TEXT NOT NULL, 
	new_slug TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_slug_redirect PRIMARY KEY (entity_type, old_slug)
);
"""

POSTGIS_COLUMNS = """
-- The authoritative spatial column. `geom_json` (declared in the Core mirror) is a cached
-- projection for payload assembly; `geom` is what every spatial predicate uses.
ALTER TABLE public.entity_geometry
    ADD COLUMN geom public.geometry(Geometry, 4326);
"""

GENERATED_COLUMNS = """
ALTER TABLE public.place
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.person
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.event
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.political_entity
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.article
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.source
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.period
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.entity_geometry
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.assertion
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
ALTER TABLE public.place_link
    ADD COLUMN validity int4range GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED;
"""

INDEXES = """
-- Map queries: ORDER BY rank DESC, id ASC with a cursor (ADR-0009).
CREATE INDEX ix_place_rank ON public.place (rank DESC, id);
CREATE INDEX ix_person_rank ON public.person (rank DESC, id);
CREATE INDEX ix_event_rank ON public.event (rank DESC, id);
CREATE INDEX ix_political_entity_rank ON public.political_entity (rank DESC, id);
CREATE INDEX ix_article_rank ON public.article (rank DESC, id);

-- Layer + status is the hottest filter on the map path.
CREATE INDEX ix_place_layer_status ON public.place (layer, status);
CREATE INDEX ix_person_layer_status ON public.person (layer, status);
CREATE INDEX ix_event_layer_status ON public.event (layer, status);
CREATE INDEX ix_political_entity_layer_status ON public.political_entity (layer, status);
CREATE INDEX ix_article_layer_status ON public.article (layer, status);

-- Temporal windows are ranges, so overlap/containment uses GiST, never a scan.
CREATE INDEX ix_place_validity ON public.place USING gist (validity);
CREATE INDEX ix_person_validity ON public.person USING gist (validity);
CREATE INDEX ix_event_validity ON public.event USING gist (validity);
CREATE INDEX ix_political_entity_validity ON public.political_entity USING gist (validity);
CREATE INDEX ix_article_validity ON public.article USING gist (validity);
CREATE INDEX ix_source_validity ON public.source USING gist (validity);
CREATE INDEX ix_period_validity ON public.period USING gist (validity);
CREATE INDEX ix_assertion_validity ON public.assertion USING gist (validity);
CREATE INDEX ix_place_link_validity ON public.place_link USING gist (validity);

-- Spatial: everything the map draws is one GiST index away (AGENTS.md rule 11).
CREATE INDEX ix_entity_geometry_geom ON public.entity_geometry USING gist (geom);
CREATE INDEX ix_entity_geometry_entity ON public.entity_geometry (entity_type, entity_id);
CREATE INDEX ix_entity_geometry_validity ON public.entity_geometry USING gist (validity);

-- Graph traversal.
CREATE INDEX ix_assertion_subject ON public.assertion (subject_type, subject_id);
CREATE INDEX ix_assertion_object ON public.assertion (object_type, object_id);
CREATE INDEX ix_assertion_topic ON public.assertion (subject_id, topic_fa);
CREATE INDEX ix_evidence_assertion ON public.evidence (assertion_id);
CREATE INDEX ix_place_link_parent ON public.place_link (parent_id);
CREATE INDEX ix_place_link_child ON public.place_link (child_id);
CREATE INDEX ix_event_place_event ON public.event_place (event_id);
CREATE INDEX ix_event_place_place ON public.event_place (place_id);
CREATE INDEX ix_event_participant_event ON public.event_participant (event_id);
CREATE INDEX ix_event_participant_actor ON public.event_participant (participant_type, participant_id);
CREATE INDEX ix_article_entity_entity ON public.article_entity (entity_type, entity_id);
CREATE INDEX ix_article_entity_article ON public.article_entity (article_id);

-- Names are the join every read path makes.
CREATE INDEX ix_name_variant_entity ON public.name_variant (entity_type, entity_id);
CREATE INDEX ix_name_variant_search ON public.name_variant (search_form);

-- Editorial machinery (ADR-0010).
CREATE INDEX ix_audit_log_entity ON public.audit_log (entity_type, entity_id, created_at DESC);
CREATE INDEX ix_revision_snapshot_entity ON public.revision_snapshot (entity_type, entity_id, revision DESC);
"""

READ_MODEL = """
CREATE VIEW public.entity_read_model AS
SELECT
    id,
    'place'::text AS entity_type,
    kind, slug, status, importance, rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    coverage_note_fa, coverage_note_en, certainty,
    NULL::text AS attestation, NULL::text AS body_fa_md, NULL::text AS body_en_md,
    NULL::jsonb AS map_state, sources, extra
FROM public.place
UNION ALL
SELECT id, 'person', kind, slug, status, importance, rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    coverage_note_fa, coverage_note_en, certainty,
    NULL, NULL, NULL, NULL, sources, extra
FROM public.person
UNION ALL
SELECT id, 'event', kind, slug, status, importance, rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    coverage_note_fa, coverage_note_en, certainty,
    attestation, NULL, NULL, NULL, sources, extra
FROM public.event
UNION ALL
SELECT id, 'political_entity', kind, slug, status, importance, rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    coverage_note_fa, coverage_note_en, certainty,
    NULL, NULL, NULL, NULL, sources, extra
FROM public.political_entity
UNION ALL
SELECT id, 'article', kind, slug, status, importance, rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    coverage_note_fa, coverage_note_en, certainty,
    NULL, body_fa_md, body_en_md, map_state, sources, extra
FROM public.article
UNION ALL
SELECT id, 'source', kind, NULL, status, 0.5::numeric(4,3), rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    NULL, NULL, certainty,
    NULL, NULL, NULL, NULL, '[]'::jsonb, extra
FROM public.source
UNION ALL
SELECT id, 'period', NULL::text AS kind, NULL, status, 0.5::numeric(4,3), rank, layer, min_zoom, max_zoom,
    year_from, year_to, precision, confidence, calendar,
    temporal_display_fa, temporal_display_en, summary_fa, summary_en,
    note_fa, note_en, certainty,
    NULL, NULL, NULL, NULL, '[]'::jsonb, extra
FROM public.period;
"""


def upgrade() -> None:
    for statement in (EXTENSIONS, TABLES, POSTGIS_COLUMNS, GENERATED_COLUMNS, INDEXES, READ_MODEL):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS public.entity_read_model;")
    for table in (
        "slug_redirect", "revision_snapshot", "audit_log", "app_user", "evidence", "assertion",
        "article_entity", "event_link", "event_participant", "event_place", "place_link",
        "entity_geometry", "name_variant", "article", "political_entity", "event", "person",
        "place", "source", "period", "period_scheme", "predicate", "entity_kind",
    ):
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE;")
