"""PostGIS adapter: the production read model (ADR-0002, ADR-0014).

Rules this file lives by:

* Spatial predicates belong to PostGIS (``ST_Intersects``, ``ST_DWithin`` on ``geography``,
  ``ST_SimplifyPreserveTopology`` for LOD). The fixtures driver approximates them in Python; this
  one does them properly (AGENTS.md rule 11).
* Temporal predicates are range predicates over the stored normalized window. Precision and
  confidence travel with every row, so an approximate date is never served as an exact one.
* Rank, layer and zoom bands are *domain* outputs written by the seeder; SQL only sorts and filters
  by them, which is what keeps both drivers in agreement.
* Search narrows with the SQL index and is then scored by the same domain scorer the fixtures
  driver uses, so relevance ordering is identical across drivers (ADR-0007).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from ...core.config import Settings, get_settings
from ...core.errors import RepositoryError
from ...domain import text as textnorm
from ...domain.enums import EntityType
from ...domain.geo import BBox
from ...domain.model import Disagreement, EntityCounts, EntityRecord, NameVariant, Relationship
from ...domain.temporal import TimeWindow
from ..ports import AtlasQuery, FeaturePage, SearchHit, TimelineBucket
from . import mappers
from .mappers import Row

#: Reference data is addressable through the same read model but never drawn on the map.
NARRATIVE_TYPES: tuple[str, ...] = ("place", "person", "event", "political_entity", "article")

def _pair(
    rows: Sequence[Row], page_rows: Sequence[tuple[float, str]]
) -> list[tuple[Row, tuple[float, str]]]:
    """Zip the ordered SQL rows with the page slice taken from them (same order, same length)."""
    return list(zip(rows[: len(page_rows)], page_rows, strict=True))


class PostgisRepository:
    """Read-only PostGIS implementation of :class:`AtlasRepository`."""

    driver_name = "postgis"

    def __init__(self, db_url: str, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._engine: Engine = create_engine(
            db_url,
            future=True,
            pool_pre_ping=True,
            pool_size=self._settings.db_pool_size,
            max_overflow=self._settings.db_max_overflow,
            connect_args={"options": f"-c statement_timeout={self._settings.db_statement_timeout_ms}"},
        )
        self._labels: dict[str, tuple[str, str]] | None = None

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        self._engine.dispose()

    @property
    def engine(self) -> Engine:
        return self._engine

    def healthcheck(self) -> None:
        """Used by ``/readyz`` and ``azir doctor``: proves the database answers and PostGIS is on."""
        with self._engine.connect() as connection:
            version = connection.execute(text("SELECT postgis_lib_version()")).scalar()
            if not version:  # pragma: no cover - the migration would have failed earlier
                raise RepositoryError("postgis extension is not available")

    # ------------------------------------------------------------------ vocabulary

    def labels(self, connection: Connection) -> dict[str, tuple[str, str]]:
        """Predicate labels, cached per instance: vocabulary changes only with a deploy."""
        if self._labels is None:
            rows = connection.execute(text("SELECT code, label_fa, label_en FROM predicate")).all()
            self._labels = {str(row[0]): (str(row[1]), str(row[2])) for row in rows}
        return self._labels

    # ------------------------------------------------------------------ map

    def features(self, query: AtlasQuery) -> FeaturePage:
        bbox = query.bbox
        window = query.window
        params = self._common_params(query)
        params.update(
            {
                "west": bbox.min_lon,
                "south": bbox.min_lat,
                "east": bbox.max_lon,
                "north": bbox.max_lat,
                "near_lon": query.near[0] if query.near else None,
                "near_lat": query.near[1] if query.near else None,
                "radius_m": (query.radius_km or 0.0) * 1000.0,
                "tolerance": query.lod_tolerance,
                "rep_year": window.representative_year,
                "limit_plus_one": query.limit + 1,
                "cursor_rank": query.cursor.rank if query.cursor else None,
                "cursor_id": query.cursor.id if query.cursor else None,
            }
        )
        sql = text(_FEATURES_SQL)
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, params).mappings().all()]
            gaps = connection.execute(text(_GAPS_SQL), self._common_params(query)).scalars().all()

        total = int(rows[0]["total"]) if rows else 0
        page_rows = [(float(row["rank"]), str(row["id"])) for row in rows[: query.limit]]
        ordered_ids = [ident for _, ident in page_rows]
        geometries = {ident: row["geojson"] for row, (rank, ident) in _pair(rows, page_rows)}
        entities = {
            record.id: record
            for record in self._load_records(ordered_ids, with_relationships=False)
        }
        return FeaturePage(
            rows=page_rows,
            entities=entities,
            geometries=geometries,
            total_estimate=total or len(page_rows),
            coverage_gaps=sorted(str(gap) for gap in gaps)[:20],
        )

    def timeline(
        self, bbox: BBox, window: TimeWindow, bucket: int, layers: tuple[str, ...], locale: str
    ) -> list[TimelineBucket]:
        size = max(1, bucket)
        start = (window.year_from // size) * size
        end = ((window.year_to // size) + 1) * size
        params = {
            "start": start,
            "end": end,
            "size": size,
            "layers": list(layers),
            "west": bbox.min_lon,
            "south": bbox.min_lat,
            "east": bbox.max_lon,
            "north": bbox.max_lat,
        }
        sql = text(
            """
            SELECT b.bucket_from AS bucket_from,
                   erm.id AS id, erm.entity_type AS entity_type, erm.layer AS layer,
                   coalesce(erm.kind, erm.entity_type) AS kind,
                   erm.rank::float AS rank,
                   erm.temporal_display_fa AS temporal_display_fa,
                   erm.year_from AS year_from, erm.year_to AS year_to
            FROM generate_series(:start::int, :end::int - :size::int, :size::int) AS b(bucket_from)
            JOIN entity_read_model erm
              ON erm.year_from IS NOT NULL AND erm.year_to IS NOT NULL
             AND erm.year_from <= b.bucket_from + :size::int - 1
             AND erm.year_to >= b.bucket_from
            WHERE erm.status = 'published'
              AND erm.entity_type = ANY(:narrative_types::text[])
              AND erm.layer = ANY(:layers::text[])
              AND EXISTS (
                    SELECT 1 FROM entity_geometry g
                    WHERE g.entity_id = erm.id
                      AND ST_Intersects(g.geom, ST_MakeEnvelope(
                            :west::float, :south::float, :east::float, :north::float, 4326))
                      AND (g.validity @> (b.bucket_from + (:size::int / 2))
                           OR NOT EXISTS (
                                SELECT 1 FROM entity_geometry g2
                                WHERE g2.entity_id = erm.id
                                  AND g2.validity @> (b.bucket_from + (:size::int / 2))))
                  )
            ORDER BY b.bucket_from, erm.rank DESC, erm.id
            """
        )
        params["narrative_types"] = list(NARRATIVE_TYPES)
        with self._engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(sql, params).mappings().all()]
            names = self._preferred_names(connection, {str(row["id"]) for row in rows}, locale)

        by_bucket: dict[int, list[Row]] = defaultdict(list)
        for row in rows:
            by_bucket[int(row["bucket_from"])].append(row)

        buckets: list[TimelineBucket] = []
        for cursor in range(start, end, size):
            counts: dict[str, int] = defaultdict(int)
            kinds: dict[str, int] = defaultdict(int)
            notable: list[tuple[float, dict[str, Any]]] = []
            for bucket_row in by_bucket.get(cursor, []):
                counts[str(bucket_row["layer"])] += 1
                kinds[str(bucket_row["kind"])] += 1
                rank = float(bucket_row["rank"])
                if bucket_row["entity_type"] == "event" and rank >= 40:
                    year_from = bucket_row["year_from"]
                    year_to = bucket_row["year_to"]
                    midpoint = (
                        (int(year_from) + int(year_to)) / 2
                        if year_from is not None and year_to is not None
                        else None
                    )
                    notable.append(
                        (
                            rank,
                            {
                                "id": str(bucket_row["id"]),
                                "label": names.get(str(bucket_row["id"]), str(bucket_row["id"])),
                                "year": midpoint,
                                "kind": str(bucket_row["kind"]),
                            },
                        )
                    )
            notable.sort(key=lambda item: -item[0])
            buckets.append(
                TimelineBucket(
                    year_from=cursor,
                    year_to=cursor + size - 1,
                    counts=dict(counts),
                    total=sum(counts.values()),
                    top_kinds=[key for key, _ in sorted(kinds.items(), key=lambda kv: -kv[1])[:3]],
                    notable=[payload for _, payload in notable[:3]],
                )
            )
        return buckets

    # ------------------------------------------------------------------ single entity

    def entity(self, entity_type: str, id_or_slug: str) -> EntityRecord | None:
        sql = text(
            """
            SELECT * FROM entity_read_model
            WHERE id = :key OR slug = :key
            ORDER BY (id = :key) DESC
            LIMIT 1
            """
        )
        with self._engine.connect() as connection:
            found = connection.execute(sql, {"key": id_or_slug}).mappings().first()
            if found is None:
                return None
            row: Row = dict(found)
            records = self._load_records([str(row["id"])], with_relationships=True, connection=connection)
        return records[0] if records else None

    def related(
        self, entity_type: str, entity_id: str, *, depth: int = 1, limit: int = 50
    ) -> list[Relationship]:
        record = self.entity(entity_type, entity_id)
        if record is None:
            return []
        return list(record.relationships[:limit])

    # ------------------------------------------------------------------ lists

    def list_entities(
        self,
        entity_type: EntityType,
        *,
        status: str | None = "published",
        locale: str = "fa",
        limit: int = 50,
        window: TimeWindow | None = None,
    ) -> list[EntityRecord]:
        params: dict[str, Any] = {
            "entity_type": entity_type.value,
            "status": status,
            "limit": limit,
            "temporal_off": window is None,
            "temporal_mode": window.mode if window else "overlaps",
            "year_from": window.year_from if window else 0,
            "year_to": window.year_to if window else 0,
        }
        sql = text(
            """
            SELECT id FROM entity_read_model erm
            WHERE erm.entity_type = :entity_type
              AND (:status::text IS NULL OR erm.status = :status::text)
              AND (:temporal_off::boolean
                   OR erm.year_from IS NULL OR erm.year_to IS NULL
                   OR (:temporal_mode = 'during'
                       AND erm.year_from >= :year_from AND erm.year_to <= :year_to)
                   OR (:temporal_mode <> 'during'
                       AND erm.year_from <= :year_to AND erm.year_to >= :year_from))
            ORDER BY erm.rank DESC, erm.id ASC
            LIMIT :limit
            """
        )
        with self._engine.connect() as connection:
            ids = [str(row[0]) for row in connection.execute(sql, params).all()]
            if not ids:
                return []
            records = self._load_records(ids, with_relationships=False, connection=connection)
        order = {ident: index for index, ident in enumerate(ids)}
        records.sort(key=lambda record: order.get(record.id, len(order)))
        return records

    def articles(
        self, *, locale: str = "fa", limit: int = 50, entity_id: str | None = None
    ) -> list[EntityRecord]:
        if not entity_id:
            return self.list_entities(EntityType.ARTICLE, locale=locale, limit=limit)
        sql = text(
            """
            SELECT erm.id FROM article_entity ae
            JOIN entity_read_model erm ON erm.id = ae.article_id
            WHERE ae.entity_id = :entity_id AND erm.status = 'published'
            ORDER BY erm.rank DESC, erm.id
            LIMIT :limit
            """
        )
        with self._engine.connect() as connection:
            ids = [
                str(row[0])
                for row in connection.execute(sql, {"entity_id": entity_id, "limit": limit}).all()
            ]
            if not ids:
                return []
            records = self._load_records(ids, with_relationships=False, connection=connection)
        order = {ident: index for index, ident in enumerate(ids)}
        records.sort(key=lambda record: order.get(record.id, len(order)))
        return records

    def all_published(self) -> list[EntityRecord]:
        with self._engine.connect() as connection:
            ids = [
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT id FROM entity_read_model WHERE status = 'published' "
                        "ORDER BY rank DESC, id"
                    )
                ).all()
            ]
            if not ids:
                return []
            return self._load_records(ids, with_relationships=False, connection=connection)

    # ------------------------------------------------------------------ search

    def search(
        self,
        term: str,
        *,
        types: tuple[str, ...],
        locale: str = "fa",
        limit: int = 20,
        near: tuple[float, float] | None = None,
        radius_km: float | None = None,
        window: TimeWindow | None = None,
    ) -> list[SearchHit]:
        folded = textnorm.build_search_text(term)
        if not folded.strip():
            # Same answer the fixtures driver gives, and no sequential scan for an empty query.
            return []
        allowed = list(types) if types else [item.value for item in EntityType]
        params: dict[str, Any] = {
            "folded": folded,
            "raw": term.strip().lower(),
            "types": allowed,
            "temporal_off": window is None,
            "temporal_mode": window.mode if window else "overlaps",
            "year_from": window.year_from if window else 0,
            "year_to": window.year_to if window else 0,
            "near_lon": near[0] if near else None,
            "near_lat": near[1] if near else None,
            "radius_m": (radius_km or 0.0) * 1000.0,
            "candidate_limit": 500,
        }
        sql = text(_SEARCH_SQL)
        with self._engine.connect() as connection:
            ids = [str(row[0]) for row in connection.execute(sql, params).all()]
            if not ids:
                return []
            records = self._load_records(ids, with_relationships=False, connection=connection)

        # The index narrows; the domain scorer decides relevance, so both drivers rank identically.
        hits: list[SearchHit] = []
        for record in records:
            score = 0.0
            matched_on = "name"
            for name in record.names:
                score = max(score, textnorm.score(term, name.form))
            body_score = textnorm.score(term, record.summary or "")
            if body_score > score:
                score, matched_on = body_score, "summary"
            title_score = textnorm.score(term, str(record.extra.get("title_fa", "")))
            if title_score > score:
                score, matched_on = title_score, "title"
            if score <= 0:
                continue
            snippet = textnorm.snippet(record.summary or record.display_name(locale), term)
            hits.append(
                SearchHit(
                    entity=record,
                    score=round(score * (0.5 + record.rank / 200), 4),
                    matched_on=matched_on,
                    snippet=snippet,
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.entity.id))
        return hits[:limit]

    # ------------------------------------------------------------------ context

    def context(
        self,
        *,
        place_id: str | None,
        point: tuple[float, float] | None,
        radius_km: float,
        locale: str,
        limit: int,
    ) -> list[Relationship]:
        out: list[Relationship] = []
        seen: set[tuple[str, str]] = set()
        with self._engine.connect() as connection:
            labels = self.labels(connection)
            if place_id:
                record = self.entity("place", place_id)
                if record is None:
                    return []
                for relation in record.relationships:
                    if relation.object_id and (relation.predicate, relation.object_id) not in seen:
                        seen.add((relation.predicate, relation.object_id))
                        out.append(relation)
            if point:
                sql = text(
                    """
                    SELECT erm.id, erm.entity_type, erm.year_from, erm.year_to, erm.precision,
                           erm.confidence, erm.calendar, erm.temporal_display_fa
                    FROM entity_read_model erm
                    WHERE erm.status = 'published'
                      AND erm.entity_type = ANY(:narrative_types::text[])
                      AND EXISTS (
                          SELECT 1 FROM entity_geometry g
                          WHERE g.entity_id = erm.id
                            AND ST_DWithin(
                                ST_PointOnSurface(g.geom)::geography,
                                ST_SetSRID(ST_MakePoint(:lon::float, :lat::float), 4326)::geography,
                                :radius_m::float))
                    ORDER BY erm.rank DESC, erm.id
                    """
                )
                near_label = labels.get("located_near", ("در نزدیکی", "located near"))
                rows = [
                    dict(item)
                    for item in connection.execute(
                        sql,
                        {
                            "narrative_types": list(NARRATIVE_TYPES),
                            "lon": point[0],
                            "lat": point[1],
                            "radius_m": radius_km * 1000.0,
                        },
                    ).mappings().all()
                ]
                for row in rows:
                    out.append(
                        Relationship(
                            predicate="located_near",
                            label_fa=near_label[0],
                            label_en=near_label[1],
                            object_type=EntityType(str(row["entity_type"])),
                            object_id=str(row["id"]),
                            temporal=mappers.temporal_from(row),
                        )
                    )
        out.sort(key=lambda relation: relation.temporal.year_from if relation.temporal else 0)
        return out[:limit]

    # ------------------------------------------------------------------ stats

    def provisional_geometry_count(self) -> int:
        with self._engine.connect() as connection:
            return int(
                connection.execute(
                    text("SELECT count(*) FROM entity_geometry WHERE needs_digitisation")
                ).scalar_one()
            )

    def stats(self) -> dict[str, Any]:
        with self._engine.connect() as connection:
            by_type = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    text(
                        "SELECT entity_type, count(*) FROM entity_read_model "
                        "WHERE status = 'published' AND entity_type = ANY(:types::text[]) "
                        "GROUP BY entity_type"
                    ),
                    {"types": list(NARRATIVE_TYPES)},
                ).all()
            }
            sources = int(connection.execute(text("SELECT count(*) FROM source")).scalar_one())
            periods = int(connection.execute(text("SELECT count(*) FROM period")).scalar_one())
            disputed = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM ("
                        "  SELECT 1 FROM assertion WHERE status = 'disputed' "
                        "  GROUP BY subject_id, coalesce(topic_fa, predicate)) AS topics"
                    )
                ).scalar_one()
            )
            provisional = int(
                connection.execute(
                    text("SELECT count(*) FROM entity_geometry WHERE needs_digitisation")
                ).scalar_one()
            )
        return {
            **by_type,
            "sources": sources,
            "periods": periods,
            "disputed_assertions": disputed,
            "provisional_geometries": provisional,
        }

    # ------------------------------------------------------------------ record assembly

    def _common_params(self, query: AtlasQuery) -> dict[str, Any]:
        return {
            "narrative_types": list(NARRATIVE_TYPES),
            "include_unpublished": query.include_unpublished,
            "layers": list(query.layers),
            "kinds": list(query.kinds),
            "min_rank": query.min_rank,
            "temporal_mode": query.window.mode,
            "year_from": query.window.year_from,
            "year_to": query.window.year_to,
        }

    def _load_records(
        self,
        ids: list[str],
        *,
        with_relationships: bool,
        connection: Connection | None = None,
    ) -> list[EntityRecord]:
        """Batch-load full records: one query per facet, never one per entity."""
        if not ids:
            return []
        owns_connection = connection is None
        connection = connection or self._engine.connect()
        try:
            rows = [
                dict(row)
                for row in connection.execute(
                    text("SELECT * FROM entity_read_model WHERE id = ANY(:ids::text[])"),
                    {"ids": ids},
                ).mappings().all()
            ]
            if not rows:
                return []
            names = self._names_by_entity(connection, ids)
            geometries = self._geometries_by_entity(connection, ids)
            counts = self._counts_by_entity(connection, rows)
            article_ids = self._article_ids_by_entity(connection, ids)
            disagreements = self._disagreements_by_entity(connection, ids)
            relationships = (
                self._relationships_by_entity(connection, ids) if with_relationships else {}
            )
            records = [
                mappers.entity_from(
                    row,
                    names=names.get(str(row["id"]), ()),
                    geometries=geometries.get(str(row["id"]), ()),
                    relationships=relationships.get(str(row["id"]), ()),
                    disagreements=disagreements.get(str(row["id"]), ()),
                    counts=counts.get(str(row["id"]), EntityCounts()),
                    article_ids=article_ids.get(str(row["id"]), ()),
                )
                for row in rows
            ]
        finally:
            if owns_connection:
                connection.close()
        order = {ident: index for index, ident in enumerate(ids)}
        records.sort(key=lambda record: order.get(record.id, len(order)))
        return records

    def _names_by_entity(
        self, connection: Connection, ids: list[str]
    ) -> dict[str, tuple[NameVariant, ...]]:
        name_rows: list[Row] = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT entity_id, form, lang, script, kind, transliteration, year_from, "
                    "year_to, source_id, note FROM name_variant "
                    "WHERE entity_id = ANY(:ids::text[]) ORDER BY entity_id, id"
                ),
                {"ids": ids},
            ).mappings().all()
        ]
        grouped: dict[str, list[NameVariant]] = defaultdict(list)
        for row in name_rows:
            grouped[str(row["entity_id"])].append(mappers.name_from(row))
        return {key: tuple(value) for key, value in grouped.items()}

    def _preferred_names(
        self, connection: Connection, ids: set[str], locale: str
    ) -> dict[str, str]:
        if not ids:
            return {}
        rows = connection.execute(
            text(
                "SELECT DISTINCT ON (entity_id) entity_id, form FROM name_variant "
                "WHERE entity_id = ANY(:ids::text[]) "
                "ORDER BY entity_id, (lang = :locale) DESC, (kind = 'preferred') DESC, id"
            ),
            {"ids": list(ids), "locale": locale},
        ).all()
        return {str(row[0]): str(row[1]) for row in rows}

    def _geometries_by_entity(self, connection: Connection, ids: list[str]) -> dict[str, tuple[Any, ...]]:
        geometry_rows: list[Row] = [
            dict(row)
            for row in connection.execute(
                text(
                    "SELECT entity_id, ST_AsGeoJSON(geom)::json AS geojson, kind, certainty, "
                    "year_from, year_to, lod_min_zoom, lod_max_zoom, source_id, note_fa, note_en, "
                    "needs_digitisation FROM entity_geometry "
                    "WHERE entity_id = ANY(:ids::text[]) ORDER BY entity_id, id"
                ),
                {"ids": ids},
            ).mappings().all()
        ]
        grouped: dict[str, list[Any]] = defaultdict(list)
        for row in geometry_rows:
            grouped[str(row["entity_id"])].append(mappers.geometry_from(row))
        return {key: tuple(value) for key, value in grouped.items()}

    def _counts_by_entity(
        self, connection: Connection, rows: Sequence[Row]
    ) -> dict[str, EntityCounts]:
        ids = [str(row["id"]) for row in rows]
        article_counts = {
            str(count_row[0]): int(count_row[1])
            for count_row in connection.execute(
                text(
                    "SELECT entity_id, count(*) FROM article_entity "
                    "WHERE entity_id = ANY(:ids::text[]) GROUP BY entity_id"
                ),
                {"ids": ids},
            ).all()
        }
        assertion_counts: dict[str, int] = defaultdict(int)
        for count_row in connection.execute(
                text(
                    "SELECT subject_id AS id, count(*) FROM assertion "
                    "WHERE status <> 'rejected' AND subject_id = ANY(:ids::text[]) "
                    "GROUP BY subject_id "
                    "UNION ALL "
                    "SELECT object_id AS id, count(*) FROM assertion "
                    "WHERE status <> 'rejected' AND object_id = ANY(:ids::text[]) "
                    "GROUP BY object_id"
                ),
                {"ids": ids},
            ).all():
            assertion_counts[str(count_row[0])] += int(count_row[1])
        period_counts = {
            str(count_row[0]): int(count_row[1])
            for count_row in connection.execute(
                text(
                    "SELECT erm.id, count(p.id) FROM entity_read_model erm "
                    "LEFT JOIN period p ON erm.year_from IS NOT NULL AND erm.year_to IS NOT NULL "
                    "  AND p.year_from IS NOT NULL AND p.year_to IS NOT NULL "
                    "  AND p.year_from <= erm.year_to AND p.year_to >= erm.year_from "
                    "WHERE erm.id = ANY(:ids::text[]) GROUP BY erm.id"
                ),
                {"ids": ids},
            ).all()
        }
        merged: dict[str, EntityCounts] = {}
        for entity_row in rows:
            ident = str(entity_row["id"])
            source_ids = entity_row.get("sources") or []
            merged[ident] = EntityCounts(
                sources=len(set(source_ids)),
                articles=article_counts.get(ident, 0),
                assertions=assertion_counts.get(ident, 0),
                periods=period_counts.get(ident, 0),
            )
        return merged

    def _article_ids_by_entity(self, connection: Connection, ids: list[str]) -> dict[str, tuple[str, ...]]:
        rows = connection.execute(
            text(
                "SELECT entity_id, article_id FROM article_entity "
                "WHERE entity_id = ANY(:ids::text[]) ORDER BY entity_id, id"
            ),
            {"ids": ids},
        ).mappings().all()
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            grouped[str(row["entity_id"])].append(str(row["article_id"]))
        return {key: tuple(value) for key, value in grouped.items()}

    def _disagreements_by_entity(
        self, connection: Connection, ids: list[str]
    ) -> dict[str, tuple[Disagreement, ...]]:
        rows = [
            dict(row)
            for row in connection.execute(text(_DISAGREEMENT_SQL), {"ids": ids}).mappings().all()
        ]
        labels = self.labels(connection)
        grouped: dict[str, list[Disagreement]] = defaultdict(list)
        by_subject: dict[str, list[Row]] = defaultdict(list)
        for row in rows:
            by_subject[str(row["subject_id"])].append(row)
        for subject, subject_rows in by_subject.items():
            grouped[subject] = mappers.group_disagreements(subject_rows, labels=labels)
        return {key: tuple(value) for key, value in grouped.items()}

    def _relationships_by_entity(
        self, connection: Connection, ids: list[str]
    ) -> dict[str, tuple[Relationship, ...]]:
        rows = [
            dict(row)
            for row in connection.execute(text(_RELATIONSHIP_SQL), {"ids": ids}).mappings().all()
        ]
        labels = self.labels(connection)
        grouped: dict[str, list[Relationship]] = defaultdict(list)
        for row in rows:
            grouped[str(row["owner_id"])].append(mappers.relationship_from(row, labels=labels))
        return {key: tuple(value) for key, value in grouped.items()}


_FEATURES_SQL = """
            WITH candidate AS (
                SELECT erm.id AS id, erm.entity_type AS entity_type, erm.rank AS rank,
                       (
                         SELECT g.geom
                         FROM entity_geometry g
                         WHERE g.entity_id = erm.id
                         ORDER BY (g.validity @> :rep_year::int)::int DESC,
                                  CASE g.kind
                                      WHEN 'footprint' THEN 0
                                      WHEN 'extent_reconstructed' THEN 1
                                      WHEN 'point' THEN 2
                                      WHEN 'route_alignment' THEN 3
                                      ELSE 4
                                  END,
                                  g.id
                         LIMIT 1
                       ) AS geom
                FROM entity_read_model erm
                WHERE erm.entity_type = ANY(:narrative_types::text[])
                  AND (:include_unpublished::boolean OR erm.status = 'published')
                  AND erm.layer = ANY(:layers::text[])
                  AND (cardinality(:kinds::text[]) = 0 OR erm.kind = ANY(:kinds::text[]))
                  AND erm.rank >= :min_rank::numeric
                  AND (erm.year_from IS NULL OR erm.year_to IS NULL
                       OR (:temporal_mode = 'during'
                           AND erm.year_from >= :year_from AND erm.year_to <= :year_to)
                       OR (:temporal_mode <> 'during'
                           AND erm.year_from <= :year_to AND erm.year_to >= :year_from))
            ),
            located AS (
                SELECT c.id, c.entity_type, c.rank, c.geom, count(*) OVER () AS total
                FROM candidate c
                WHERE c.geom IS NOT NULL
                  AND ST_Intersects(c.geom, ST_MakeEnvelope(
                        :west::float, :south::float, :east::float, :north::float, 4326))
                  AND (:near_lon::float IS NULL OR ST_DWithin(
                        c.geom::geography,
                        ST_SetSRID(ST_MakePoint(:near_lon::float, :near_lat::float), 4326)::geography,
                        :radius_m::float))
            )
            SELECT id, entity_type, rank::float AS rank,
                   ST_AsGeoJSON(CASE
                       WHEN :tolerance::float > 0
                            AND GeometryType(geom) NOT IN ('POINT', 'MULTIPOINT')
                       THEN ST_SimplifyPreserveTopology(geom, :tolerance::float)
                       ELSE geom
                   END)::json AS geojson,
                   total
            FROM located
            WHERE (:cursor_rank::numeric IS NULL
                   OR rank < :cursor_rank::numeric
                   OR (rank = :cursor_rank::numeric AND id > :cursor_id::text))
            ORDER BY rank DESC, id ASC
            LIMIT :limit_plus_one
"""

_SEARCH_SQL = """
            SELECT DISTINCT erm.id AS id
            FROM entity_read_model erm
            JOIN name_variant nv ON nv.entity_id = erm.id AND nv.entity_type = erm.entity_type
            WHERE erm.status = 'published'
              AND erm.entity_type = ANY(:types::text[])
              AND (nv.search_form % :folded
                   OR strpos(nv.search_form, :folded) > 0
                   OR strpos(nv.search_form, :raw) > 0
                   OR nv.search_tsv @@ plainto_tsquery('simple', :folded)
                   OR strpos(public.azir_search_form(coalesce(erm.summary_fa, '')), :folded) > 0
                   OR strpos(public.azir_search_form(coalesce(erm.extra ->> 'title_fa', '')), :folded) > 0)
              AND (:temporal_off::boolean
                   OR erm.year_from IS NULL OR erm.year_to IS NULL
                   OR (:temporal_mode = 'during'
                       AND erm.year_from >= :year_from AND erm.year_to <= :year_to)
                   OR (:temporal_mode <> 'during'
                       AND erm.year_from <= :year_to AND erm.year_to >= :year_from))
              AND (:near_lon::float IS NULL OR EXISTS (
                    SELECT 1 FROM entity_geometry g
                    WHERE g.entity_id = erm.id
                      AND ST_DWithin(
                            ST_PointOnSurface(g.geom)::geography,
                            ST_SetSRID(ST_MakePoint(:near_lon::float, :near_lat::float), 4326)::geography,
                            :radius_m::float)))
            ORDER BY erm.id
            LIMIT :candidate_limit
"""

_GAPS_SQL = """
SELECT erm.entity_type || ':' || erm.id || ':no-geometry' AS gap
FROM entity_read_model erm
WHERE erm.entity_type = ANY(:narrative_types::text[])
  AND (:include_unpublished::boolean OR erm.status = 'published')
  AND erm.layer = ANY(:layers::text[])
  AND (cardinality(:kinds::text[]) = 0 OR erm.kind = ANY(:kinds::text[]))
  AND erm.rank >= :min_rank::numeric
  AND (erm.year_from IS NULL OR erm.year_to IS NULL
       OR (:temporal_mode = 'during' AND erm.year_from >= :year_from AND erm.year_to <= :year_to)
       OR (:temporal_mode <> 'during' AND erm.year_from <= :year_to AND erm.year_to >= :year_from))
  AND NOT EXISTS (SELECT 1 FROM entity_geometry g WHERE g.entity_id = erm.id)
ORDER BY gap
LIMIT 20
"""

_EVIDENCE_AGG = """
    coalesce((
        SELECT json_agg(json_build_object(
                   'source_id', e.source_id,
                   'locator_type', e.locator_type,
                   'locator', e.locator,
                   'quote_original', e.quote_original,
                   'quote_translation', e.quote_translation,
                   'stance', e.stance) ORDER BY e.id)
        FROM evidence e WHERE e.assertion_id = a.id
    ), '[]'::json) AS evidence
"""

_DISAGREEMENT_SQL = f"""
SELECT a.id AS id, a.subject_id AS subject_id, a.predicate AS predicate,
       a.object_type AS object_type, a.object_id AS object_id,
       (SELECT coalesce(
           (SELECT nv.form FROM name_variant nv
             WHERE nv.entity_id = a.object_id AND nv.lang = 'fa' AND nv.kind = 'preferred'
             ORDER BY nv.id LIMIT 1),
           (SELECT nv.form FROM name_variant nv
             WHERE nv.entity_id = a.object_id AND nv.lang = 'fa' ORDER BY nv.id LIMIT 1))
        ) AS object_label,
       (SELECT obj.slug FROM entity_read_model obj WHERE obj.id = a.object_id LIMIT 1) AS object_slug,
       a.value AS object_value, a.role AS role, NULL::text AS side, NULL::text AS certainty,
       a.year_from AS year_from, a.year_to AS year_to, a.precision AS precision,
       a.confidence AS confidence, a.calendar AS calendar,
       a.temporal_display_fa AS temporal_display_fa, a.status AS status,
       a.topic_fa AS topic_fa, a.topic_en AS topic_en, a.note_fa AS note_fa,
       'out' AS direction,
       {_EVIDENCE_AGG}
FROM assertion a
WHERE a.status = 'disputed' AND a.subject_id = ANY(:ids::text[])
ORDER BY a.subject_id, a.topic_fa, a.id
"""

#: One query for the whole graph around a set of entities. `owner_id` is the entity the edge is
#: attached to; `direction` says whether it points out of or into that entity. Inverse edges are
#: produced by the same query, exactly as the fixtures driver produces them at load time.
_RELATIONSHIP_SQL = f"""
WITH edges AS (
    -- 0: place hierarchy (structural containment)
    SELECT 0 AS sort_key, pl.id AS edge_id, pl.parent_id AS owner_id, 'out' AS direction,
           pl.kind AS predicate, 'place' AS object_type, pl.child_id AS object_id,
           NULL::text AS object_value, NULL::text AS role, NULL::text AS side,
           pl.certainty AS certainty, pl.year_from, pl.year_to, pl.precision, pl.confidence,
           'gregorian_proleptic'::text AS calendar, NULL::text AS temporal_display_fa,
           'accepted'::text AS status, NULL::text AS topic_fa, pl.note_fa,
           '[]'::json AS evidence
    FROM place_link pl WHERE pl.parent_id = ANY(:ids::text[])
    UNION ALL
    SELECT 0, pl.id, pl.child_id, 'in', pl.kind, 'place', pl.parent_id,
           NULL, NULL, NULL, pl.certainty, pl.year_from, pl.year_to, pl.precision, pl.confidence,
           'gregorian_proleptic', NULL, 'accepted', NULL, pl.note_fa, '[]'::json
    FROM place_link pl WHERE pl.child_id = ANY(:ids::text[])
    UNION ALL
    -- 1: event <-> place
    SELECT 1, 0, ep.event_id, 'out', ep.role, 'place', ep.place_id,
           NULL, ep.role, NULL, ep.certainty, ep.year_from, ep.year_to,
           'range', 'high', 'gregorian_proleptic', NULL, 'accepted', NULL, NULL, '[]'::json
    FROM event_place ep WHERE ep.event_id = ANY(:ids::text[])
    UNION ALL
    SELECT 1, 0, ep.place_id, 'in', 'hosted', 'event', ep.event_id,
           NULL, ep.role, NULL, ep.certainty, ep.year_from, ep.year_to,
           'range', 'high', 'gregorian_proleptic', NULL, 'accepted', NULL, NULL, '[]'::json
    FROM event_place ep WHERE ep.place_id = ANY(:ids::text[])
    UNION ALL
    -- 2: event <-> participant
    SELECT 2, epn.id, epn.event_id, 'out', 'participated_in', epn.participant_type,
           epn.participant_id, NULL, epn.role, epn.side, NULL, NULL, NULL,
           'range', 'high', 'gregorian_proleptic', NULL, 'accepted', NULL, NULL, '[]'::json
    FROM event_participant epn WHERE epn.event_id = ANY(:ids::text[])
    UNION ALL
    SELECT 2, epn.id, epn.participant_id, 'in', 'participated_in', 'event', epn.event_id,
           NULL, epn.role, epn.side, NULL, NULL, NULL,
           'range', 'high', 'gregorian_proleptic', NULL, 'accepted', NULL, NULL, '[]'::json
    FROM event_participant epn WHERE epn.participant_id = ANY(:ids::text[])
    UNION ALL
    -- 3: event <-> event
    SELECT 3, el.id, el.event_id, 'out', el.relation, 'event', el.other_event_id,
           NULL, NULL, NULL, NULL, NULL, NULL, 'range', 'high', 'gregorian_proleptic', NULL,
           'accepted', NULL, NULL, '[]'::json
    FROM event_link el WHERE el.event_id = ANY(:ids::text[])
    UNION ALL
    SELECT 3, el.id, el.other_event_id, 'in',
           CASE el.relation WHEN 'part_of' THEN 'has_part' ELSE el.relation END,
           'event', el.event_id, NULL, NULL, NULL, NULL, NULL, NULL, 'range', 'high',
           'gregorian_proleptic', NULL, 'accepted', NULL, NULL, '[]'::json
    FROM event_link el WHERE el.other_event_id = ANY(:ids::text[])
    UNION ALL
    -- 4: article <-> entity
    SELECT 4, ae.id, ae.entity_id, 'out', 'article:' || ae.relation, 'article', ae.article_id,
           NULL, ae.relation, NULL, NULL, NULL, NULL, 'range', 'high', 'gregorian_proleptic',
           NULL, 'accepted', NULL, NULL, '[]'::json
    FROM article_entity ae WHERE ae.entity_id = ANY(:ids::text[])
    UNION ALL
    SELECT 4, ae.id, ae.article_id, 'in', 'article:' || ae.relation, erm.entity_type, erm.id,
           NULL, ae.relation, NULL, NULL, NULL, NULL, 'range', 'high', 'gregorian_proleptic',
           NULL, 'accepted', NULL, NULL, '[]'::json
    FROM article_entity ae JOIN entity_read_model erm ON erm.id = ae.entity_id
    WHERE ae.article_id = ANY(:ids::text[])
    UNION ALL
    -- 5: assertions, outgoing
    SELECT 5, 0, a.subject_id, 'out', a.predicate, a.object_type, a.object_id,
           a.value, a.role, NULL::text, NULL::text, a.year_from, a.year_to, a.precision,
           a.confidence, a.calendar, a.temporal_display_fa, a.status, a.topic_fa, a.note_fa,
           {_EVIDENCE_AGG}
    FROM assertion a WHERE a.subject_id = ANY(:ids::text[]) AND a.status <> 'rejected'
    UNION ALL
    -- 5: assertions, incoming (predicate inverted, exactly as the domain does)
    SELECT 5, 0, a.object_id, 'in',
           coalesce(p.inverse_code, 'is_' || a.predicate || '_of'),
           a.subject_type, a.subject_id,
           a.value, a.role, NULL, NULL, a.year_from, a.year_to, a.precision,
           a.confidence, a.calendar, a.temporal_display_fa, a.status, a.topic_fa, a.note_fa,
           {_EVIDENCE_AGG}
    FROM assertion a LEFT JOIN predicate p ON p.code = a.predicate
    WHERE a.object_id = ANY(:ids::text[]) AND a.status <> 'rejected'
)
SELECT e.owner_id AS owner_id, e.sort_key AS sort_key, e.edge_id AS edge_id,
       e.predicate AS predicate, e.direction AS direction, e.object_type AS object_type,
       e.object_id AS object_id,
       (SELECT coalesce(
            (SELECT nv.form FROM name_variant nv
              WHERE nv.entity_id = e.object_id AND nv.lang = 'fa' AND nv.kind = 'preferred'
              ORDER BY nv.id LIMIT 1),
            (SELECT nv.form FROM name_variant nv
              WHERE nv.entity_id = e.object_id AND nv.lang = 'fa' ORDER BY nv.id LIMIT 1))
       ) AS object_label,
       (SELECT obj.slug FROM entity_read_model obj WHERE obj.id = e.object_id LIMIT 1) AS object_slug,
       e.object_value AS object_value, e.role AS role, e.side AS side, e.certainty AS certainty,
       e.year_from AS year_from, e.year_to AS year_to, e.precision AS precision,
       e.confidence AS confidence, e.calendar AS calendar,
       e.temporal_display_fa AS temporal_display_fa, e.status AS status, e.topic_fa AS topic_fa,
       e.note_fa AS note_fa, e.evidence AS evidence
FROM edges e
ORDER BY e.owner_id, e.sort_key, e.edge_id, e.object_id
"""

__all__ = ["NARRATIVE_TYPES", "PostgisRepository"]
