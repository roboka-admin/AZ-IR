/**
 * Where the map's tiles come from.
 *
 * Three possibilities, in the order they are preferred, and the API -- not this file -- decides
 * which one is available (`GET /api/v1/tiles/index.json`):
 *
 *   1. a built **PMTiles archive**: one static file, read over HTTP range requests. This is the
 *      production path (ADR-0011): it scales to a whole pyramid without a tile server, and it can
 *      live on a CDN.
 *   2. the API's **dynamic render endpoint**: no build step, always current, one request per tile.
 *      Right for development and small deployments.
 *   3. nothing: the caller falls back to the GeoJSON viewport endpoint, which always works.
 *
 * Two rules are enforced here rather than in components: the archive URL is rewritten to a
 * same-origin path when it points at this API (so Next.js rewrites it and no CORS preflight
 * happens), and the `pmtiles` protocol is registered exactly once, lazily, on the client.
 */

import type { TilesIndex, TilesDelivery, TilesPreference, VectorSourceSpec } from "./types";

const API_MARKER = "/api/v1/";

/** Registered once per page: MapLibre keeps a global protocol table. */
let protocolRegistered = false;

/**
 * Point a URL at this origin when it is one of ours, and leave a CDN URL alone.
 *
 * The backend reports its own `public_base_url` (http://localhost:8000 in development). Fetching
 * that from the browser would skip the Next.js rewrite and trip CORS, so the path is what matters.
 * A configured `AZIR_TILES_PUBLIC_BASE_URL` has no `/api/v1/` in it and is used verbatim.
 */
export function tileUrl(raw: string): string {
  const marker = raw.indexOf(API_MARKER);
  if (marker >= 0) return raw.slice(marker);
  return raw;
}

export async function registerPmtilesProtocol(): Promise<void> {
  if (protocolRegistered || typeof window === "undefined") return;
  const [maplibre, pmtiles] = await Promise.all([import("maplibre-gl"), import("pmtiles")]);
  const protocol = new pmtiles.Protocol();
  // Bound because MapLibre calls it detached from the instance.
  maplibre.default.addProtocol("pmtiles", protocol.tile.bind(protocol));
  protocolRegistered = true;
}

export interface ResolvedTiles {
  /** null means "no tile source; use GeoJSON". */
  spec: VectorSourceSpec | null;
  /** The MVT source-layers the style has to bind to, in paint order. */
  sourceLayers: string[];
  mode: TilesDelivery;
  /** Archive facts worth showing the reader: revision, generated time, tile count. */
  archive: TilesIndex["archive"];
  tilesetVersion: string;
}

/**
 * Turn the index into something MapLibre can be given, or `null` when tiles are unavailable.
 *
 * Failure is not fatal: a missing archive, a blocked CDN or an old browser all end up here, and the
 * map still renders from GeoJSON. Tiles are an optimisation, never a precondition.
 */
export async function resolveTiles(
  index: TilesIndex | null,
  preference: TilesPreference = "auto",
): Promise<ResolvedTiles> {
  const fallback: ResolvedTiles = {
    spec: null,
    sourceLayers: [],
    mode: "geojson",
    archive: index?.archive ?? null,
    tilesetVersion: index?.tileset_version ?? "",
  };
  if (!index || preference === "geojson") return fallback;

  const archiveUrl = index.archive?.url;
  const hasArchive = typeof archiveUrl === "string" && archiveUrl.length > 0;
  // "auto" only upgrades to tiles when an archive exists: that is the operator having run a build,
  // not the API merely being able to render. The default reader experience must not change because
  // a development server happens to have the render endpoint switched on.
  const wantsDynamic = preference === "tiles";

  if (hasArchive && (preference === "auto" || wantsDynamic)) {
    try {
      await registerPmtilesProtocol();
    } catch (cause) {
      console.warn("pmtiles protocol unavailable, falling back", cause);
      return fallback;
    }
    return {
      ...fallback,
      mode: "pmtiles",
      sourceLayers: index.layers,
      spec: { type: "vector", url: `pmtiles://${tileUrl(archiveUrl as string)}` },
    };
  }

  if (wantsDynamic && index.dynamic_enabled && index.dynamic_template) {
    return {
      ...fallback,
      mode: "dynamic",
      sourceLayers: index.layers,
      spec: {
        type: "vector",
        tiles: [tileUrl(index.dynamic_template)],
        minzoom: index.min_zoom,
        maxzoom: index.max_zoom,
      },
    };
  }
  return fallback;
}
