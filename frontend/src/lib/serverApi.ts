/**
 * Server-side data access for Next.js (RSC) pages.
 *
 * Inside the server we must use an absolute URL, because the `/api` rewrite only exists for
 * browser requests. This is still the same read-only API: no database driver, no ORM, no
 * environment secret beyond the backend's address (AGENTS.md rule 1).
 */

import type { EntityDetail, ListResponse, Locale, MetaResponse, SourceRecord } from "./types";

const BACKEND = process.env.AZIR_BACKEND_URL ?? "http://127.0.0.1:8000";

async function serverGet<T>(path: string, params: Record<string, string | number | undefined>): Promise<T | null> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const query = search.toString();
  try {
    const response = await fetch(`${BACKEND}/api/v1${path}${query ? `?${query}` : ""}`, {
      // Editorial content changes often during research; never serve a stale article from cache.
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export function serverMeta(locale: Locale) {
  return serverGet<MetaResponse>("/meta", { locale });
}

export function serverEntity(type: string, idOrSlug: string, locale: Locale) {
  return serverGet<EntityDetail>(`/entities/${type}/${encodeURIComponent(idOrSlug)}`, { locale });
}

export function serverArticle(slug: string, locale: Locale) {
  return serverGet<EntityDetail>(`/articles/${encodeURIComponent(slug)}`, { locale });
}

export function serverArticles(locale: Locale, limit = 50) {
  return serverGet<ListResponse<EntityDetail["articles"][number]>>("/articles", { locale, limit });
}

export function serverSources(locale: Locale, limit = 200) {
  return serverGet<ListResponse<SourceRecord>>("/sources", { locale, limit });
}
