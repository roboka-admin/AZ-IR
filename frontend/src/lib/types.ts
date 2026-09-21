/**
 * Types for the public API contract (docs/05-api-contract.md, ADR-0009).
 *
 * Everything the UI knows about history arrives through these shapes. Nothing here may be
 * hard-coded into a component (AGENTS.md rule 19): layers, zoom levels, periods, calendars and
 * kind weights all come from `/api/v1/meta`.
 */

export type Locale = "fa" | "en";
export type Direction = "rtl" | "ltr";
export type Certainty = "exact" | "approximate" | "uncertain" | "reconstructed";
export type CalendarCode =
  | "gregorian_proleptic"
  | "julian"
  | "islamic_lunar"
  | "persian_solar"
  | "unknown";
export type TemporalMode = "at" | "during" | "overlaps";
export type EntityType = "place" | "person" | "event" | "political_entity" | "article" | "source" | "period";

/* ------------------------------------------------------------------ /api/v1/meta */

export interface LocaleInfo {
  code: Locale;
  dir: Direction;
  default: boolean;
}

export interface StudyArea {
  name: string;
  name_fa: string;
  name_en: string;
  bbox: [number, number, number, number];
  center: [number, number];
  default_zoom: number;
}

export interface TimelineConfig {
  floor: number;
  ceil: number;
  default_year: number;
  buckets: number[];
  calendars: CalendarCode[];
  precisions: string[];
  default_calendar: CalendarCode;
}

export interface LayerInfo {
  id: string;
  label: string;
  label_fa: string;
  label_en: string;
  default_on: boolean;
  style_token: string;
  count: number;
  min_zoom: number;
  max_zoom: number;
}

export interface ZoomLevelInfo {
  level: string;
  label: string;
  min_zoom: number;
  max_zoom: number;
  description?: string;
  min_rank?: number;
  lod_tolerance?: number;
}

export interface PeriodInfo {
  id: string;
  code: string;
  label: string;
  year_from: number | null;
  year_to: number | null;
}

export interface CoverageStats {
  place?: number;
  person?: number;
  event?: number;
  political_entity?: number;
  article?: number;
  sources?: number;
  periods?: number;
  disputed_assertions?: number;
  provisional_geometries?: number;
  [key: string]: number | undefined;
}

export interface MetaResponse {
  locales: LocaleInfo[];
  locale: Locale;
  dir: Direction;
  study_area: StudyArea;
  timeline: TimelineConfig;
  layers: LayerInfo[];
  zoom_levels: ZoomLevelInfo[];
  periods: PeriodInfo[];
  political_entities: {
    id: string;
    label: string;
    color: string;
    t_display: string | null;
    certainty: Certainty | null;
  }[];
  coverage: CoverageStats;
  disclaimer: { borders: string };
  kind_weights: Record<string, number>;
  limits: { max_limit: number; default_limit: number; payload_budget_bytes: number };
  license: { code: string; data: string };
  api_version?: string;
  driver?: string;
}

/* ------------------------------------------------------------------ GeoJSON */

export interface FeatureProperties {
  id: string;
  entity_type: EntityType;
  kind: string | null;
  layer: string;
  rank: number;
  min_zoom: number;
  max_zoom: number;
  label: string;
  label_anchor?: boolean;
  style_color?: string | null;
  dir: Direction;
  status: string;
  certainty: Certainty | null;
  geometry_kind: string | null;
  href: string;
  slug?: string | null;
  label_secondary?: string | null;
  t_from?: number | null;
  t_to?: number | null;
  t_display?: string | null;
  t_precision?: string | null;
  confidence?: string | null;
  article_count?: number;
  source_count?: number;
  assertion_count?: number;
  has_disagreements?: boolean;
  importance?: number;
  /** Tile-only: which geometry variant this feature is, and the years that variant covers. */
  g_index?: number;
  g_from?: number;
  g_to?: number;
  g_lod_min_zoom?: number;
  g_lod_max_zoom?: number;
  summary?: string | null;
  certainty_note?: string | null;
  needs_digitisation?: boolean;
  attestation?: string | null;
  alternates?: string[];
}

export interface AtlasFeature {
  type: "Feature";
  id: string;
  geometry: GeoJSON.Geometry;
  properties: FeatureProperties;
}

export interface FeaturesMeta {
  driver: string;
  zoom_level: string;
  zoom_level_label: string;
  min_rank: number;
  lod_tolerance: number;
  time: { mode: TemporalMode; from: number; to: number; calendar: CalendarCode };
  fields: "min" | "default" | "full";
  requested: number;
  returned: number;
  total_estimate: number;
  truncated: boolean;
  payload_bytes: number;
  next_cursor: string | null;
  coverage_gaps: string[];
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: AtlasFeature[];
  meta: FeaturesMeta;
}

/* ------------------------------------------------------------------ timeline */

export interface TimelineBucket {
  from: number;
  to: number;
  display_from?: number;
  display_to?: number;
  counts: Record<string, number>;
  total: number;
  top_kinds: string[];
  notable: { id: string; label: string; year: number; display_year?: number; kind: string | null }[];
}

export interface TimelineResponse {
  data: TimelineBucket[];
  meta: { bucket: number; mode: TemporalMode; driver: string; calendar: CalendarCode };
}

/* ------------------------------------------------------------------ tiles (ADR-0018) */

/** What the API says about how to fetch tiles. `archive` is null until one has been built. */
export interface TilesArchivePointer {
  url: string;
  filename: string;
  tileset_version: string;
  data_revision: string;
  generated_at: string;
  bytes: number;
  tiles: number;
  min_zoom: number;
  max_zoom: number;
  bounds: number[];
  center: number[];
  driver: string;
  locale: string;
}

export interface TilesIndex {
  mode: "pmtiles" | "dynamic";
  dynamic_enabled: boolean;
  dynamic_template: string | null;
  archive: TilesArchivePointer | null;
  /**
   * Locales that have a built archive. Labels are baked into tiles at build time, so an archive
   * belongs to one language: this list is why the map may fall back to GeoJSON in *this* language.
   */
  archive_locales: string[];
  tileset_version: string;
  /** MVT source-layers, in paint order. */
  layers: string[];
  /** Tile property names and their TileJSON types: the contract the style is written against. */
  properties: Record<string, string>;
  min_zoom: number;
  max_zoom: number;
  locale: string;
  bounds: number[];
  center: number[];
}

export type VectorSourceSpec =
  | { type: "vector"; url: string; minzoom?: number; maxzoom?: number }
  | { type: "vector"; tiles: string[]; minzoom?: number; maxzoom?: number };

/** The timeline as a filter. `from`/`to` null means "every period". */
export interface TemporalFilter {
  from: number | null;
  to: number | null;
  mode: TemporalMode;
}

/** What `/api/v1/atlas/window` answers: the years a calendar selection really means. */
export interface NormalizedWindow {
  from: number;
  to: number;
  mode: TemporalMode;
  calendar: CalendarCode;
  normalized_calendar: CalendarCode;
}

/**
 * The reader's choice of delivery.
 *
 * `auto` is the default and means "PMTiles if an archive has been built, otherwise GeoJSON": a built
 * archive is a deliberate operator action, so it changes the default; the on-demand render endpoint
 * stays an explicit choice because it costs a request per tile.
 */
export type TilesPreference = "auto" | "tiles" | "geojson";

/** Which delivery the map ended up using -- shown in the status bar, because it is not cosmetic:
 *  in tiles mode the timeline filters locally and the coverage panel has no viewport payload. */
export type TilesDelivery = "pmtiles" | "dynamic" | "geojson";

/* ------------------------------------------------------------------ entities */

export interface NameVariant {
  form: string;
  lang: string;
  script: string;
  kind: string;
  year_from: number | null;
  year_to: number | null;
  note: string | null;
}

export interface GeometryPayload {
  geojson: GeoJSON.Geometry | null;
  kind: string | null;
  certainty: Certainty | null;
  note: string | null;
  note_fa: string | null;
  note_en: string | null;
  needs_digitisation: boolean;
  source_id: string | null;
  year_from: number | null;
  year_to: number | null;
  center: [number, number] | null;
  other_geometries: {
    kind: string;
    certainty: Certainty;
    year_from: number | null;
    year_to: number | null;
    note: string | null;
  }[];
}

export interface Evidence {
  source_id: string;
  stance: string;
  locator: string | null;
  quote?: string | null;
}

export interface Relationship {
  predicate: string;
  label: string;
  direction: "out" | "in";
  object_type?: EntityType | null;
  object_id?: string | null;
  object_label?: string | null;
  object_slug?: string | null;
  object_value?: string | null;
  role?: string | null;
  certainty?: string | null;
  confidence: string;
  status: string;
  t_display?: string | null;
  t_from?: number | null;
  t_to?: number | null;
  evidence?: Evidence[];
  note?: string | null;
}

export interface Disagreement {
  topic: string;
  topic_fa: string;
  topic_en: string;
  positions: Relationship[];
}

export interface EntityBrief {
  id: string;
  entity_type: EntityType;
  kind: string | null;
  kind_label: string | null;
  slug: string | null;
  label: string;
  label_secondary: string | null;
  t_display: string | null;
  t_from: number | null;
  t_to: number | null;
  rank: number;
  layer: string;
  center: [number, number] | null;
  has_disagreements: boolean;
  status: string;
  href: string;
  relation?: string;
}

export interface SourceRecord {
  id: string;
  kind: string;
  title: string;
  title_en?: string | null;
  author?: string | null;
  year?: number | null;
  publisher?: string | null;
  reliability?: string | null;
  citation?: string | null;
  url?: string | null;
  needs_review?: boolean;
}

export interface EntityDetail {
  id: string;
  entity_type: EntityType;
  kind: string | null;
  kind_label: string | null;
  slug: string | null;
  status: string;
  revision: number;
  rank: number;
  min_zoom: number;
  max_zoom: number;
  layer: string;
  names: { display: string; display_secondary: string | null; alternatives: NameVariant[] };
  temporal: {
    year_from: number | null;
    year_to: number | null;
    precision: string;
    calendar: CalendarCode;
    confidence: string;
    display: string | null;
  } | null;
  geometry: GeometryPayload;
  summary: string | null;
  body_md: string | null;
  attestation: string | null;
  counts: { sources: number; articles: number; assertions: number; periods: number };
  relationships: Relationship[];
  disagreements: Disagreement[];
  articles: EntityBrief[];
  sources: SourceRecord[];
  coverage_note: string | null;
  links: { self: string; related: string; map: string };
  article?: ArticleBlock;
}

export interface MapState {
  center: [number, number];
  zoom: number;
  time: { from?: number; to?: number; year?: number; mode?: TemporalMode };
  layers?: string[];
}

export interface ArticleBlock {
  title: string;
  title_fa: string;
  title_en: string;
  title_secondary: string | null;
  body_md: string | null;
  body_fa_md: string | null;
  body_en_md: string | null;
  author?: string | null;
  published_at?: string | null;
  reading_time_min?: number | null;
  lang?: string;
  map_state: MapState;
  entities: EntityBrief[];
}

/* ------------------------------------------------------------------ search */

export interface SearchHit extends EntityBrief {
  score: number;
  matched_on: string;
  snippet: string | null;
}

export interface SearchResponse {
  data: SearchHit[];
  page: { limit: number; next_cursor: string | null; total_estimate: number };
  meta: { normalized_query: string; driver: string; locale: Locale };
}

export interface ListResponse<T> {
  data: T[];
  page: { limit: number; next_cursor: string | null; total_estimate: number };
  meta: Record<string, unknown>;
}

/* ------------------------------------------------------------------ errors */

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  request_id?: string | null;
  errors?: { field: string; message: string }[];
  supported?: string[];
  known?: string[];
}
