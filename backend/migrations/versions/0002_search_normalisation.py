"""Persian/Arabic search normalisation mirrored in SQL (ADR-0007).

``azir.domain.text.normalize_fa`` is the contract; this migration implements the same folding in
the database so that an index built in SQL and a query folded in Python agree. ``tests/
test_postgis_contract.py`` asserts the two produce identical output for a corpus of real strings
(names with half-spaces, Arabic letter variants, Persian digits, tatweel, diacritics).

Order is part of the contract: NFKC -> drop diacritics/tatweel -> unify ZWJ/ZWNJ -> fold letters
-> fold digits -> punctuation to space -> collapse whitespace -> lowercase.

Revision ID: 0002_search_normalisation
Revises: 0001_initial_schema
Create Date: 2026-09-12
"""

from __future__ import annotations

from alembic import op

revision = "0002_search_normalisation"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

# Code points are spelled with chr() so the migration file stays ASCII and unambiguous.
NORMALIZE_FA = """
CREATE OR REPLACE FUNCTION public.azir_normalize_fa(input text, half_space text DEFAULT 'space')
RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
AS $fn$
DECLARE
    out text;
    joiner text := CASE WHEN half_space = 'join' THEN '' ELSE ' ' END;
BEGIN
    out := normalize(input, NFKC);

    -- Diacritics U+064B..U+0652, superscript alef U+0670, U+0653..U+0655 and the tatweel U+0640.
    out := regexp_replace(
        out,
        '[' || chr(1611) || '-' || chr(1626) || chr(1648) || chr(1635) || '-' || chr(1637)
            || chr(1600) || ']',
        '',
        'g'
    );

    -- Half-space (ZWNJ) and ZWJ become a space, or nothing when the user typed them away.
    out := replace(replace(out, chr(8204), joiner), chr(8205), joiner);

    -- Letter variants: Arabic yeh/kaf/teh-marbuta/hamzated forms -> their Persian equivalents.
    out := translate(
        out,
        chr(1610) || chr(1609) || chr(1603) || chr(1577) || chr(1728) || chr(1571) || chr(1573)
            || chr(1570) || chr(1649) || chr(1572) || chr(1574) || chr(1726) || chr(1749)
            || chr(1735) || chr(1734) || chr(1736),
        chr(1740) || chr(1740) || chr(1705) || chr(1607) || chr(1607) || chr(1575) || chr(1575)
            || chr(1575) || chr(1575) || chr(1608) || chr(1740) || chr(1607) || chr(1607)
            || chr(1608) || chr(1608) || chr(1608)
    );

    -- Persian (U+06F0..) and Arabic-Indic (U+0660..) digits -> ASCII.
    out := translate(
        out,
        chr(1776) || chr(1777) || chr(1778) || chr(1779) || chr(1780) || chr(1781) || chr(1782)
            || chr(1783) || chr(1784) || chr(1785) || chr(1632) || chr(1633) || chr(1634)
            || chr(1635) || chr(1636) || chr(1637) || chr(1638) || chr(1639) || chr(1640)
            || chr(1641),
        '01234567890123456789'
    );

    -- Punctuation to space (same class as the domain implementation).
    out := regexp_replace(
        out,
        '[' || chr(171) || chr(187) || '"' || chr(39) || '`(){}\\[\\].,:;!' || chr(1567) || '?'
            || chr(1548) || '\\-_/\\\\|+=*&^%$#@~' || chr(8226) || chr(183) || ']',
        ' ',
        'g'
    );

    out := btrim(regexp_replace(out, '\\s+', ' ', 'g'));
    RETURN lower(out);
END;
$fn$;

COMMENT ON FUNCTION public.azir_normalize_fa(text, text) IS
    'SQL mirror of azir.domain.text.normalize_fa (ADR-0007). Keep the two in lockstep.';
"""

NORMALIZE_ANY = """
CREATE OR REPLACE FUNCTION public.azir_normalize_any(input text)
RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
AS $fn$
DECLARE
    out text;
BEGIN
    -- Non-Persian scripts: decompose, drop the common combining marks, fold digits/punctuation.
    out := normalize(input, NFKD);
    out := regexp_replace(out, '[' || chr(768) || '-' || chr(879) || ']', '', 'g');
    out := translate(
        out,
        chr(1776) || chr(1777) || chr(1778) || chr(1779) || chr(1780) || chr(1781) || chr(1782)
            || chr(1783) || chr(1784) || chr(1785) || chr(1632) || chr(1633) || chr(1634)
            || chr(1635) || chr(1636) || chr(1637) || chr(1638) || chr(1639) || chr(1640)
            || chr(1641),
        '01234567890123456789'
    );
    out := regexp_replace(
        out,
        '[' || chr(171) || chr(187) || '"' || chr(39) || '`(){}\\[\\].,:;!' || chr(1567) || '?'
            || chr(1548) || '\\-_/\\\\|+=*&^%$#@~' || chr(8226) || chr(183) || ']',
        ' ',
        'g'
    );
    RETURN lower(btrim(regexp_replace(out, '\\s+', ' ', 'g')));
END;
$fn$;
"""

SEARCH_FORM = """
CREATE OR REPLACE FUNCTION public.azir_search_form(input text)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
    SELECT coalesce(
        nullif(public.azir_normalize_fa(coalesce(input, '')), ''),
        public.azir_normalize_any(coalesce(input, '')),
        ''
    );
$fn$;

COMMENT ON FUNCTION public.azir_search_form(text) IS
    'Fold any script into one searchable form: Persian rules first, generic rules as fallback.';
"""

GENERATED_TSV = """
ALTER TABLE public.name_variant
    ADD COLUMN search_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, coalesce(search_form, ''))) STORED;
"""

INDEXES = """
CREATE INDEX ix_name_variant_trgm ON public.name_variant
    USING gin (search_form public.gin_trgm_ops);
CREATE INDEX ix_name_variant_tsv ON public.name_variant USING gin (search_tsv);
CREATE INDEX ix_place_slug ON public.place (slug);
CREATE INDEX ix_person_slug ON public.person (slug);
CREATE INDEX ix_event_slug ON public.event (slug);
CREATE INDEX ix_political_entity_slug ON public.political_entity (slug);
CREATE INDEX ix_article_slug ON public.article (slug);
"""


def upgrade() -> None:
    for statement in (NORMALIZE_FA, NORMALIZE_ANY, SEARCH_FORM, GENERATED_TSV, INDEXES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_name_variant_tsv;")
    op.execute("DROP INDEX IF EXISTS public.ix_name_variant_trgm;")
    op.execute("ALTER TABLE public.name_variant DROP COLUMN IF EXISTS search_tsv;")
    op.execute("DROP FUNCTION IF EXISTS public.azir_search_form(text);")
    op.execute("DROP FUNCTION IF EXISTS public.azir_normalize_any(text);")
    op.execute("DROP FUNCTION IF EXISTS public.azir_normalize_fa(text, text);")
