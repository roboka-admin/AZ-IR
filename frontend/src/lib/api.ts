/**
 * The only module in the frontend that talks to the backend (AGENTS.md rules 1, 12).
 *
 * All requests are same-origin `/api/v1/...`; Next.js rewrites them to FastAPI, so the browser
 * never learns where the database lives and no CORS preflight is needed in production.
 */

import type {
  EntityDetail,
  FeatureCollection,
  ListResponse,
  MetaResponse,
  NormalizedWindow,
  ProblemDetails,
  SearchResponse,
  SourceRecord,
  TimelineResponse,
  TilesIndex,
  Locale,
  Relationship,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetails;

  constructor(problem: ProblemDetails) {
    super(`${problem.title}: ${problem.detail}`);
    this.name = "ApiError";
    this.status = problem.status;
    this.problem = problem;
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

function toQuery(params?: Params): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

/** Small memo cache for bootstrap payloads that never change during a session. */
const memo = new Map<string, unknown>();

async function request<T>(path: string, params?: Params, init?: RequestInit & { memoize?: boolean }): Promise<T> {
  const url = `${BASE}${path}${toQuery(params)}`;
  if (init?.memoize && memo.has(url)) return memo.get(url) as T;

  const response = await fetch(url, {
    ...init,
    headers: { Accept: "application/json, application/geo+json", ...(init?.headers ?? {}) },
    // The atlas is read-only; letting the browser cache features keeps scrubbing smooth.
    cache: init?.cache ?? "default",
  });

  if (!response.ok) {
    let problem: ProblemDetails;
    try {
      problem = (await response.json()) as ProblemDetails;
    } catch {
      problem = {
        type: "https://errors.azir.dev/http",
        title: response.statusText || "Request failed",
        status: response.status,
        detail: `${response.status} ${response.statusText}`,
      };
    }
    throw new ApiError(problem);
  }

  const body = (await response.json()) as T;
  if (init?.memoize) memo.set(url, body);
  return body;
}

/* ------------------------------------------------------------------ bootstrap */

export function getMeta(locale: Locale, signal?: AbortSignal): Promise<MetaResponse> {
  return request<MetaResponse>("/meta", { locale }, { signal, memoize: true });
}

export function getLayers(locale: Locale): Promise<ListResponse<MetaResponse["layers"][number]>> {
  return request("/atlas/layers", { locale }, { memoize: true });
}

/* ------------------------------------------------------------------ atlas */

export interface FeaturesParams {
  bbox?: string;
  zoom: number;
  t?: number;
  from?: number;
  to?: number;
  mode?: "at" | "during" | "overlaps";
  cal?: string;
  layers?: string;
  kinds?: string;
  fields?: "min" | "default" | "full";
  limit?: number;
  cursor?: string;
  locale?: Locale;
  period?: string;
}

export function getFeatures(params: FeaturesParams, signal?: AbortSignal): Promise<FeatureCollection> {
  return request<FeatureCollection>("/atlas/features", { ...params, zoom: roundZoom(params.zoom) }, { signal });
}

export function getTimeline(
  params: { from: number; to: number; bucket?: number; bbox?: string; layers?: string; locale?: Locale; cal?: string },
  signal?: AbortSignal,
): Promise<TimelineResponse> {
  return request<TimelineResponse>("/atlas/timeline", params, { signal });
}

export function getCalendarYear(
  year: number,
  calendar: string,
  signal?: AbortSignal,
): Promise<{ data: { year: number; calendar: string; normalized_year: number } }> {
  return request("/atlas/calendar-year", { t: year, cal: calendar }, { signal, memoize: true });
}

export function getContext(
  params: { place_id?: string; lat?: number; lon?: number; radius_km?: number; locale?: Locale },
  signal?: AbortSignal,
): Promise<ListResponse<Relationship>> {
  return request<ListResponse<Relationship>>("/atlas/context", params, { signal });
}

/**
 * The normalized years behind a calendar selection.
 *
 * Tiles carry normalized years and are filtered in the browser, so the conversion has to come from
 * the server: doing it here would put calendar arithmetic -- historical logic -- in the frontend
 * (AGENTS.md rule 5). Memoized because it is a pure function of its parameters.
 */
export function getWindow(
  params: { t?: number; from?: number; to?: number; mode?: string; cal?: string },
  signal?: AbortSignal,
): Promise<{ data: NormalizedWindow; meta: Record<string, unknown> }> {
  return request("/atlas/window", params, { signal, memoize: true });
}

/* ------------------------------------------------------------------ tiles */

/** How the map should get its tiles: archive, dynamic render, or nothing (GeoJSON viewport). */
export function getTilesIndex(
  locale: Locale,
  signal?: AbortSignal,
): Promise<{ data: TilesIndex; meta: { driver: string } }> {
  return request("/tiles/index.json", { locale }, { signal, memoize: true });
}

/** Half-a-zoom rounding keeps responses cacheable while the user pinch-zooms (ADR-0009). */
export function roundZoom(zoom: number): number {
  return Math.round(zoom * 2) / 2;
}

/* ------------------------------------------------------------------ catalog */

export function getEntity(
  type: string,
  idOrSlug: string,
  locale: Locale,
  signal?: AbortSignal,
): Promise<EntityDetail> {
  return request<EntityDetail>(`/entities/${type}/${encodeURIComponent(idOrSlug)}`, { locale }, { signal });
}

export function getArticle(slug: string, locale: Locale, signal?: AbortSignal): Promise<EntityDetail> {
  return request<EntityDetail>(`/articles/${encodeURIComponent(slug)}`, { locale }, { signal });
}

export function listArticles(locale: Locale, limit = 50): Promise<ListResponse<EntityDetail["articles"][number]>> {
  return request("/articles", { locale, limit });
}

export function listSources(locale: Locale, limit = 100): Promise<ListResponse<SourceRecord>> {
  return request("/sources", { locale, limit });
}

export function search(
  q: string,
  params: { locale: Locale; types?: string; limit?: number; t?: number; near?: string; radius_km?: number },
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return request<SearchResponse>("/search", { q, ...params }, { signal });
}
