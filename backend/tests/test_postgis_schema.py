"""Static guarantees for the PostGIS layer.

These run *without* a database, which is the point: the schema mirror, the frozen migrations and
the SQL in the repository must agree before anyone ever connects. Behaviour against a real
PostgreSQL+PostGIS lives in ``test_postgis_contract.py`` (marker ``postgis``, run in CI).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import psycopg2 as pg_psycopg2
from sqlalchemy.schema import CreateTable

from azir.domain import text as textnorm
from azir.repositories.postgis import METADATA, schema
from azir.repositories.postgis.repository import (
    _DISAGREEMENT_SQL,
    _FEATURES_SQL,
    _GAPS_SQL,
    _RELATIONSHIP_SQL,
    _SEARCH_SQL,
)

BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "migrations" / "versions"
DIALECT = postgresql.dialect()
# psycopg2 is the driver production uses; its pyformat paramstyle is what makes `%` dangerous.
PSYCOPG2 = pg_psycopg2.dialect()
POSTGIS_PACKAGE = BACKEND / "src" / "azir" / "repositories" / "postgis"


def migration_text() -> str:
    return "\n".join(sorted(path.read_text(encoding="utf-8") for path in VERSIONS.glob("*.py")))


def created_tables(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE public\.(\w+)", sql))


def table_block(sql: str, name: str) -> str:
    match = re.search(rf"CREATE TABLE public\.{name} \((.*?)\n\);", sql, re.DOTALL)
    assert match, f"migration does not create public.{name}"
    return match.group(1)


def altered_columns(sql: str, name: str) -> set[str]:
    """Columns a later migration adds with ALTER TABLE (generated columns live in 0002+)."""
    return set(
        re.findall(rf"ALTER TABLE public\.{name}\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)", sql)
    )


def columns_of(name: str) -> set[str]:
    """Columns the Core mirror declares, excluding generated/virtual ones the DDL adds itself."""
    return {column.name for column in METADATA.tables[f"public.{name}"].columns}


# METADATA is schema-qualified, so keys look like "public.place"; the tests speak bare names.
REAL_TABLES = sorted(
    name.split(".")[-1] for name in schema.TABLES if name.split(".")[-1] != "entity_read_model"
)


# ------------------------------------------------------------------ drift


@pytest.mark.parametrize("name", REAL_TABLES)
def test_every_table_in_the_mirror_is_created_by_a_migration(name: str) -> None:
    assert name in created_tables(migration_text()), f"{name} is missing from the frozen DDL"


@pytest.mark.parametrize("name", REAL_TABLES)
def test_every_column_in_the_mirror_exists_in_the_ddl(name: str) -> None:
    """The mirror is what the repository queries; a missing column is a runtime explosion."""
    sql = migration_text()
    block = table_block(sql, name)
    declared = {line.strip().split(" ")[0] for line in block.splitlines() if line.strip()}
    declared |= altered_columns(sql, name)
    missing = columns_of(name) - declared
    assert not missing, f"public.{name}: migration DDL is missing {sorted(missing)}"


def test_no_column_is_declared_twice_across_migrations() -> None:
    """0001 must not pre-declare a column that a later migration adds (real-PostGIS CI caught this).

    ``name_variant.search_tsv`` is a generated column created by 0002; 0001 used to declare a plain
    column of the same name, so ``alembic upgrade head`` died with "column already exists" the
    moment it met a real server.
    """
    sql = migration_text()
    for name in REAL_TABLES:
        created = {
            line.strip().split(" ")[0]
            for line in table_block(sql, name).splitlines()
            if line.strip()
        }
        added_later = altered_columns(sql, name)
        twice = created & added_later
        assert not twice, f"public.{name}: {sorted(twice)} is both created and ALTERed in"


def test_no_table_exists_in_ddl_without_a_mirror_entry() -> None:
    """The other direction: undocumented tables are how schemas rot."""
    unknown = created_tables(migration_text()) - set(REAL_TABLES) - {"alembic_version"}
    assert not unknown, f"tables without a Core mirror entry: {sorted(unknown)}"


def test_read_model_view_covers_every_addressable_entity_type() -> None:
    sql = migration_text()
    view = re.search(r"CREATE VIEW public\.entity_read_model AS(.*?);\n", sql, re.DOTALL)
    assert view, "the read-model view is missing"
    body = view.group(1)
    for entity_type in ("place", "person", "event", "political_entity", "article", "source", "period"):
        assert f"'{entity_type}'" in body, f"{entity_type} is not part of the read model"
    for column in columns_of("entity_read_model"):
        assert column in body, f"read-model view does not expose {column}"


def test_spatial_column_is_postgis_typed_and_indexed() -> None:
    """AGENTS.md rule 11: spatial data lives in PostGIS, indexed, in SRID 4326."""
    sql = migration_text()
    assert "ADD COLUMN geom public.geometry(Geometry, 4326)" in sql
    assert "USING gist (geom)" in sql


def test_spatial_predicates_are_done_by_postgis_not_python() -> None:
    """The fixtures driver approximates in Python; this one must not."""
    source = (BACKEND / "src" / "azir" / "repositories" / "postgis" / "repository.py").read_text(
        encoding="utf-8"
    )
    for function in ("ST_Intersects", "ST_MakeEnvelope", "ST_DWithin", "ST_SimplifyPreserveTopology",
                     "ST_AsGeoJSON", "ST_PointOnSurface", "::geography"):
        assert function in source, f"{function} is missing: spatial work belongs to PostGIS"


def test_temporal_validity_is_a_range_with_an_index() -> None:
    sql = migration_text()
    assert sql.count("GENERATED ALWAYS AS (int4range(year_from, year_to, '[]')) STORED") >= 8
    assert "USING gist (validity)" in sql


def test_rank_ordering_is_indexed_for_the_map_query() -> None:
    sql = migration_text()
    for table in ("place", "person", "event", "political_entity", "article"):
        assert f"CREATE INDEX ix_{table}_rank ON public.{table} (rank DESC, id)" in sql


# ------------------------------------------------------------------ SQL sanity


@pytest.mark.parametrize(
    ("sql", "required"),
    [
        (_RELATIONSHIP_SQL, ("place_link", "event_place", "event_participant", "event_link",
                             "article_entity", "assertion", "predicate", "name_variant")),
        (_DISAGREEMENT_SQL, ("assertion", "evidence", "topic_fa")),
        (_GAPS_SQL, ("entity_read_model", "no-geometry")),
    ],
    ids=["relationships", "disagreements", "coverage-gaps"],
)
def test_repository_sql_touches_the_tables_it_needs(sql: str, required: tuple[str, ...]) -> None:
    for token in required:
        assert token in sql


def test_relationship_sql_projects_the_columns_the_mapper_reads() -> None:
    """A typo in the SELECT list would only show up against a live database."""
    from azir.repositories.postgis import mappers

    projection = re.search(r"SELECT e\.owner_id AS owner_id(.*?)FROM edges e", _RELATIONSHIP_SQL, re.DOTALL)
    assert projection, "the relationship query does not project from the edges CTE"
    projected = set(re.findall(r"AS (\w+)", projection.group(1))) | {"owner_id"}
    needed = {
        "owner_id", "predicate", "direction", "object_type", "object_id", "object_label",
        "object_slug", "object_value", "role", "side", "certainty", "year_from", "year_to",
        "precision", "confidence", "calendar", "temporal_display_fa", "status", "note_fa",
        "evidence",
    }
    assert needed <= projected, f"missing from the projection: {sorted(needed - projected)}"
    # And the mapper must actually understand every projected column it is given.
    row = dict.fromkeys(projected)
    row.update({"predicate": "born_in", "direction": "out", "object_id": "plc_ardabil"})
    relationship = mappers.relationship_from(row, labels={"born_in": ("زادگاه", "born in")})
    assert relationship.label_fa == "زادگاه"
    assert relationship.object_id == "plc_ardabil"


def test_metadata_compiles_against_the_postgresql_dialect() -> None:
    for name in REAL_TABLES:
        ddl = str(CreateTable(METADATA.tables[f"public.{name}"]).compile(dialect=DIALECT))
        assert f"public.{name}" in ddl


# ------------------------------------------------------------------ what actually reaches the server


def _percent_offenders(source: str) -> list[str]:
    """Every string literal in a module that contains `%%`."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "%%" in node.value:
            found.append(node.value.strip().splitlines()[0][:80])
    return found


@pytest.mark.parametrize("path", sorted(POSTGIS_PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_no_module_writes_a_double_percent(path: Path) -> None:
    """SQLAlchemy escapes `%` for the pyformat paramstyle itself; `%%` arrives as `%%` and is a
    syntax error on a real server (this is how the pg_trgm operator in search slipped through)."""
    offenders = _percent_offenders(path.read_text(encoding="utf-8"))
    assert not offenders, f"{path.name} contains %%: {offenders}"


ALL_SQL = {
    "features": _FEATURES_SQL,
    "search": _SEARCH_SQL,
    "gaps": _GAPS_SQL,
    "relationships": _RELATIONSHIP_SQL,
    "disagreements": _DISAGREEMENT_SQL,
}


def mogrify(sql: str) -> str:
    """The exact string PostgreSQL receives: SQLAlchemy compiles it, psycopg2 substitutes params."""
    compiled = text(sql).compile(dialect=PSYCOPG2)
    return str(compiled) % dict.fromkeys(compiled.params, "'x'")


@pytest.mark.parametrize("name", sorted(ALL_SQL))
def test_sql_survives_the_paramstyle_round_trip(name: str) -> None:
    sent = mogrify(ALL_SQL[name])
    assert "%%" not in sent
    assert " %(" not in sent, "an unbound parameter survived compilation"


def test_the_trgm_similarity_operator_reaches_postgres_intact() -> None:
    assert "nv.search_form % 'x'" in mogrify(_SEARCH_SQL)


def test_textual_sql_does_not_put_postgres_casts_directly_after_bind_names() -> None:
    """``text(':value::type')`` escapes the colon instead of creating a bind parameter.

    PostgreSQL then receives the literal ``:value`` token.  ``CAST(:value AS type)`` is unambiguous
    to both SQLAlchemy's textual-SQL parser and PostgreSQL.
    """
    pattern = re.compile(r":[A-Za-z_][A-Za-z0-9_]*::[A-Za-z_]")
    offenders = {
        path.name: sorted(set(pattern.findall(path.read_text(encoding="utf-8"))))
        for path in POSTGIS_PACKAGE.glob("*.py")
    }
    offenders = {name: matches for name, matches in offenders.items() if matches}
    assert not offenders, f"ambiguous bind/cast syntax: {offenders}"


def test_radius_parameters_are_lon_then_lat() -> None:
    """The codebase speaks (lon, lat) everywhere -- GeoJSON order, `?near=lon,lat`, `point=`.

    A swapped pair compiles fine and silently queries the wrong hemisphere, so pin the binding.
    """
    source = (POSTGIS_PACKAGE / "repository.py").read_text(encoding="utf-8")
    assert '"near_lon": query.near[0]' in source
    assert '"near_lat": query.near[1]' in source
    assert '"lon": point[0]' in source
    assert '"lat": point[1]' in source


# ------------------------------------------------------------------ search parity (ADR-0007)


def test_sql_folding_mirrors_the_domain_code_points() -> None:
    """The SQL mirror must fold exactly what the Python implementation folds."""
    sql = migration_text()
    function = re.search(r"FUNCTION public\.azir_normalize_fa.*?\$fn\$;", sql, re.DOTALL)
    assert function, "azir_normalize_fa is missing"
    body = function.group(0)
    # ZWNJ / ZWJ, the tatweel, Persian yeh and keheh, Persian and Arabic-Indic digits.
    for code_point in (8204, 8205, 1600, 1740, 1705, 1776, 1632, 1611, 1626, 1648):
        assert f"chr({code_point})" in body, f"code point {code_point} is not folded in SQL"
    assert "normalize(input, NFKC)" in body
    assert "IMMUTABLE" in body, "the folding function must be immutable to be indexable"


def test_domain_folding_still_does_what_the_sql_claims() -> None:
    """Guard the Python side of the same contract with real strings."""
    cases = {
        "صفی\u200cالدین اردبیلی": "صفی الدین اردبیلی",
        "تبريز": "تبریز",
        "اردبیلٔ": "اردبیل",
        "۱۵۱۴": "1514",
        "الصفوی": "الصفوی",
    }
    for raw, expected in cases.items():
        assert textnorm.normalize_fa(raw) == expected
    # Half-space variants are indexed too, so every spelling a reader might type matches:
    # with the half-space as a space, glued together, and with the definite article dropped.
    blob = textnorm.build_search_text("صفی\u200cالدین")
    assert "صفیالدین" in blob, blob
    assert "صفی دین" in blob, blob
    assert textnorm.score("صفی‌الدین", blob) > 0
    assert textnorm.score("صفی الدین", blob) > 0
