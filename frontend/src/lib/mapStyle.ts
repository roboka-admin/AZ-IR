/**
 * MapLibre style construction.
 *
 * The basemap is decoration; every historical mark on the map comes from the API. Three rules shape
 * this file:
 *   - the map contains no historical logic (AGENTS.md rule 5): styling keys are `layer`,
 *     `certainty`, `rank` and `geometry_kind`, all of which the API supplies;
 *   - certainty drives the visual language (ADR-0013): a reconstructed extent is dashed and
 *     transparent, a surveyed footprint is solid. The reader must be able to tell them apart
 *     without opening a popup;
 *   - one styling vocabulary for both deliveries. Whether the features arrive as a GeoJSON viewport
 *     or as vector tiles, the *same* layer specs draw them, because the tile carries the same
 *     properties (ADR-0018). The only difference is where the features come from: a single GeoJSON
 *     source, or one style layer per MVT source-layer.
 */

import type { FeatureCollection as GeoJSONCollection } from "geojson";
import type { GeoJSONSource, Map as MaplibreMap, StyleSpecification } from "maplibre-gl";
import type { FeatureCollection, TemporalFilter, VectorSourceSpec } from "./types";

/** Free, keyless basemap; probed at runtime and replaced by `fallbackStyle()` if unreachable. */
export const BASEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

export const ATLAS_SOURCE = "azir-atlas";
export const GRATICULE_SOURCE = "azir-graticule";
/** The paint stack, bottom to top. Ids on the map are these, or `<id>@<source-layer>` for tiles. */
export const DATA_LAYERS = ["azir-fill", "azir-fill-outline", "azir-line", "azir-point-halo", "azir-point", "azir-label"];

/** Font stacks differ per basemap; both are presentation choices, not data. */
export const LIBERTY_FONT = ["Noto Sans Regular"];
export const FALLBACK_FONT = ["Open Sans Regular"];

const FALLBACK_GLYPHS = "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf";

/** Colour per layer id. Unknown layers fall back to `DEFAULT_COLOR`. */
export const PALETTE: Record<string, string> = {
  places: "#b0562c",
  buildings: "#a1762a",
  archaeology: "#7c6a4b",
  political_entities: "#6d4fa3",
  events: "#c0392b",
  battles: "#8e1b1b",
  people: "#1f6f78",
  routes: "#3f6ea5",
  modern_borders: "#5c6672",
  articles: "#2f7d32",
};

const DEFAULT_COLOR = "#3d4a5c";

export function colorForLayer(layer: string): string {
  return PALETTE[layer] ?? DEFAULT_COLOR;
}

/**
 * One-degree graticule for the offline fallback: without a basemap the reader still needs a sense
 * of scale and position. Generated from the study area the API reports, never hard-coded.
 */
export function graticuleCollection(
  bbox: [number, number, number, number],
  step = 1,
): GeoJSONCollection {
  const [west, south, east, north] = bbox;
  const features: GeoJSONCollection["features"] = [];
  for (let lon = Math.ceil(west / step) * step; lon <= east; lon += step) {
    features.push({
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: [[lon, south], [lon, north]] },
    });
  }
  for (let lat = Math.ceil(south / step) * step; lat <= north; lat += step) {
    features.push({
      type: "Feature",
      properties: {},
      geometry: { type: "LineString", coordinates: [[west, lat], [east, lat]] },
    });
  }
  return { type: "FeatureCollection", features };
}

/** Adds the graticule underneath the data layers. Only used with the local fallback style. */
export function ensureFallbackLayers(map: MaplibreMap, bbox: [number, number, number, number]): void {
  if (!map.getSource(GRATICULE_SOURCE)) {
    map.addSource(GRATICULE_SOURCE, { type: "geojson", data: graticuleCollection(bbox) } as never);
  }
  if (!map.getLayer("azir-graticule")) {
    map.addLayer({
      id: "azir-graticule",
      type: "line",
      source: GRATICULE_SOURCE,
      paint: { "line-color": "#c9c0ac", "line-width": 0.6, "line-opacity": 0.9 },
    } as never);
  }
}

export function fallbackStyle(): StyleSpecification {
  return {
    version: 8,
    name: "azir-fallback",
    glyphs: FALLBACK_GLYPHS,
    sources: {},
    layers: [{ id: "background", type: "background", paint: { "background-color": "#e9e4d8" } }],
  };
}

/* ------------------------------------------------------------------ data layers */

/** Certainty decides how confident a mark looks. */
const FILL_OPACITY = [
  "match", ["get", "certainty"],
  "exact", 0.3,
  "approximate", 0.24,
  "uncertain", 0.15,
  "reconstructed", 0.14,
  0.2,
];

const DASH = ["match", ["get", "certainty"], "reconstructed", [2, 2], "uncertain", [1.5, 1.5], [0]];

function fillColour(): unknown[] {
  return ["case",
    ["==", ["get", "layer"], "political_entities"], ["coalesce", ["get", "style_color"], PALETTE.political_entities],
    ["==", ["get", "layer"], "modern_borders"], PALETTE.modern_borders,
    ["==", ["get", "layer"], "archaeology"], PALETTE.archaeology,
    ["==", ["get", "layer"], "places"], PALETTE.places,
    DEFAULT_COLOR];
}

function pointColour(): unknown[] {
  return ["match", ["get", "layer"],
    "places", PALETTE.places,
    "buildings", PALETTE.buildings,
    "archaeology", PALETTE.archaeology,
    "events", PALETTE.events,
    "battles", PALETTE.battles,
    "people", PALETTE.people,
    "political_entities", PALETTE.political_entities,
    "routes", PALETTE.routes,
    DEFAULT_COLOR];
}

interface LayerSpec {
  id: string;
  type: "fill" | "line" | "circle" | "symbol";
  filter: unknown[];
  layout?: Record<string, unknown>;
  paint?: Record<string, unknown>;
  minzoom?: number;
}

function atlasLayerSpecs(font: string[]): LayerSpec[] {
  return [
    {
      id: "azir-fill",
      type: "fill",
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: { "fill-color": fillColour(), "fill-opacity": FILL_OPACITY },
    },
    {
      id: "azir-fill-outline",
      type: "line",
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: {
        "line-color": fillColour(),
        "line-width": ["case",
          ["==", ["get", "certainty"], "exact"], ["interpolate", ["linear"], ["zoom"], 3, 1.6, 10, 3],
          ["interpolate", ["linear"], ["zoom"], 3, 1.1, 10, 2.2],
        ],
        "line-opacity": ["match", ["get", "certainty"], "exact", 1, "approximate", 0.72, "uncertain", 0.82, "reconstructed", 0.9, 0.85],
        "line-dasharray": DASH,
      },
    },
    {
      id: "azir-line",
      type: "line",
      filter: ["==", ["geometry-type"], "LineString"],
      paint: {
        "line-color": ["match", ["get", "layer"], "routes", PALETTE.routes, PALETTE.places],
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.9, 12, 2.6],
        "line-opacity": 0.85,
        "line-dasharray": DASH,
      },
    },
    {
      id: "azir-point-halo",
      type: "circle",
      filter: ["all", ["==", ["geometry-type"], "Point"], ["!=", ["get", "label_anchor"], true]],
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["get", "rank"], 0, 5, 100, 15],
        "circle-color": "#ffffff",
        "circle-opacity": 0.5,
        "circle-stroke-width": 0,
      },
    },
    {
      id: "azir-point",
      type: "circle",
      filter: ["all", ["==", ["geometry-type"], "Point"], ["!=", ["get", "label_anchor"], true]],
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["get", "rank"], 0, 2.5, 40, 4.5, 70, 6.5, 100, 9],
        "circle-color": pointColour(),
        // A derived locus is drawn hollow: we know *near where*, not *where*.
        "circle-opacity": ["match", ["get", "geometry_kind"], "uncertain_locus", 0.22, 0.92],
        "circle-stroke-color": ["match", ["get", "certainty"],
          "reconstructed", "#c58b1a", "uncertain", "#c58b1a", "#ffffff"],
        "circle-stroke-width": ["match", ["get", "geometry_kind"], "uncertain_locus", 2.2, 1.4],
        "circle-stroke-opacity": 0.95,
      },
    },
    {
      id: "azir-selected",
      type: "circle",
      filter: ["all", ["==", ["geometry-type"], "Point"], ["==", ["get", "id"], "__none__"]],
      paint: {
        "circle-radius": 15,
        "circle-color": "#00000000",
        "circle-stroke-color": "#1b6ef3",
        "circle-stroke-width": 3,
      },
    },
    {
      id: "azir-label",
      type: "symbol",
      minzoom: 2,
      // The API marks exactly one presentation anchor per entity. In vector tiles that anchor is a
      // dedicated point, avoiding one polity label on every clipped polygon/tile fragment.
      filter: ["all", ["==", ["get", "label_anchor"], true], ["!=", ["get", "layer"], "political_entities"]],
      layout: {
        // A data label must read as an atlas annotation rather than a basemap place-name.
        // The marker is presentation only; label content and temporal naming still come from API.
        "text-field": ["concat", "◆ ", ["get", "label"]],
        "text-font": font,
        "text-size": ["interpolate", ["linear"], ["zoom"], 3, 10, 8, 12.5, 14, 14],
        "text-anchor": "top",
        "text-offset": [0, 0.9],
        "text-max-width": 8,
        "text-allow-overlap": false,
        "text-optional": true,
        "symbol-sort-key": ["-", 100, ["get", "rank"]],
      },
      paint: {
        "text-color": "#1d2430",
        "text-halo-color": "#ffffff",
        "text-halo-width": 1.4,
        "text-opacity": ["case", ["<=", ["get", "rank"], 20], 0.72, 1],
      },
    },
  ];
}

/** What a vector source needs: the MapLibre source spec plus the MVT layers it contains. */
export interface VectorBinding {
  spec: VectorSourceSpec;
  sourceLayers: string[];
}

interface Registration {
  /** The id on the map: `azir-fill` for GeoJSON, `azir-fill@places` for a vector source-layer. */
  id: string;
  /** The spec id, so callers can find "all the label layers" without parsing strings. */
  specId: string;
  base: unknown[];
  /** null for the GeoJSON source, where every layer shares one source. */
  sourceLayer: string | null;
}

/** Everything this module has put on the map, so filters can be recomposed instead of guessed. */
let registry: Registration[] = [];
let activeLayers: string[] | null = null;
let activeWindow: TemporalFilter = { from: null, to: null, mode: "overlaps" };
let lastFont: string[] = LIBERTY_FONT;
let lastBinding: VectorBinding | null = null;

/**
 * Add the atlas source and its layers.
 *
 * `binding` decides the delivery: omit it for the GeoJSON viewport (one source, filters by
 * property), pass it for tiles (one style layer per MVT source-layer, visibility by source-layer).
 * Calling it again after a basemap swap replays the same choice -- MapLibre drops custom layers
 * whenever the style is replaced.
 */
export function ensureAtlasLayers(
  map: MaplibreMap,
  font: string[] = LIBERTY_FONT,
  binding: VectorBinding | null = null,
): void {
  lastFont = font;
  lastBinding = binding;
  if (!map.getSource(ATLAS_SOURCE)) {
    if (binding) {
      map.addSource(ATLAS_SOURCE, binding.spec as never);
    } else {
      map.addSource(ATLAS_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
        // The API already clips and simplifies; a small buffer keeps labels near the edge stable.
        buffer: 64,
        maxzoom: 16,
      } as never);
    }
  }

  registry = [];
  const specs = atlasLayerSpecs(font);
  const sourceLayers = binding ? binding.sourceLayers : [null];
  for (const spec of specs) {
    // MapLibre paints in the order layers were added, so the outer loop is the paint stack (areas,
    // then lines, then points, then labels) and the inner loop is the tile's own layer order, which
    // the archive was written in: `modern_borders` underneath, `places` and `buildings` above it.
    for (const sourceLayer of sourceLayers) {
      const id = sourceLayer ? `${spec.id}@${sourceLayer}` : spec.id;
      if (!map.getLayer(id)) {
        map.addLayer({
          id,
          type: spec.type,
          source: ATLAS_SOURCE,
          ...(sourceLayer ? { "source-layer": sourceLayer } : {}),
          filter: spec.filter,
          layout: spec.layout,
          paint: spec.paint,
          minzoom: spec.minzoom,
        } as never);
      }
      registry.push({ id, specId: spec.id, base: spec.filter, sourceLayer });
    }
  }
  reapplyFilters(map);
}

/** Ids of the data layers actually on the map -- what clicks and queries should look at. */
export function dataLayerIds(map: MaplibreMap | null): string[] {
  if (!map) return [];
  return registry
    .filter((entry) => entry.specId !== "azir-selected" && Boolean(map.getLayer(entry.id)))
    .map((entry) => entry.id);
}

/** Which of the atlas layers a rendered feature came from, for the "hide this layer" affordance. */
export function layerKeyOf(layerId: string): string {
  const at = layerId.indexOf("@");
  return at >= 0 ? layerId.slice(at + 1) : "";
}

export function setLayerVisibility(map: MaplibreMap, visibleLayers: string[]): void {
  activeLayers = [...new Set(visibleLayers)];
  reapplyFilters(map);
}

/**
 * The timeline, applied as a filter.
 *
 * With tiles this *is* the temporal mechanism: the archive holds every period at once and the
 * client narrows it, which is why scrubbing the timeline costs no request (ADR-0018). With GeoJSON
 * it is a no-op safety net -- the server has already filtered, and the client predicate is the same
 * one, so nothing that was legitimately returned can disappear.
 *
 * A feature with no years at all is always shown: absence means "we do not know when", not "never"
 * (AGENTS.md rule 5 -- no fake precision, in either direction).
 */
export function setTemporalWindow(map: MaplibreMap | null, window: TemporalFilter): void {
  activeWindow = window;
  if (map) reapplyFilters(map);
}

/**
 * The temporal predicate: `g_from`/`g_to` (this geometry variant's own years) win over
 * `t_from`/`t_to` (the entity's), because a boundary can be narrower in time than the thing it
 * belongs to. `coalesce` handles the absent case, which MVT cannot represent as null.
 */
function temporalFilter(window: TemporalFilter): unknown[] | null {
  if (window.from === null && window.to === null) return null;
  const from = ["coalesce", ["get", "g_from"], ["get", "t_from"], -ALWAYS];
  const to = ["coalesce", ["get", "g_to"], ["get", "t_to"], ALWAYS];
  const low = window.from ?? (window.to as number);
  const high = window.to ?? (window.from as number);
  if (window.mode === "during") {
    // Contained in the window: the reader asked for "only what fits inside these years".
    return ["all", [">=", from, low], ["<=", to, high]];
  }
  return ["all", ["<=", from, high], [">=", to, low]];
}

/** A year count larger than any date in the corpus, standing in for "unknown" in comparisons. */
const ALWAYS = 1_000_000;

function reapplyFilters(map: MaplibreMap): void {
  const temporal = temporalFilter(activeWindow);
  for (const entry of registry) {
    if (entry.specId === "azir-selected" || !map.getLayer(entry.id)) continue;
    if (entry.sourceLayer) {
      // Vector mode: one style layer per source-layer, so hiding a layer is a layout property.
      const visible = activeLayers === null || activeLayers.includes(entry.sourceLayer);
      map.setLayoutProperty(entry.id, "visibility", visible ? "visible" : "none");
      if (!visible) continue;
      map.setFilter(entry.id, combine(entry.base, temporal));
      continue;
    }
    const clauses: unknown[] = [entry.base];
    if (activeLayers && activeLayers.length > 0) {
      clauses.push(["in", ["get", "layer"], ["literal", activeLayers]]);
    }
    if (temporal) clauses.push(temporal);
    map.setFilter(entry.id, ["all", ...clauses] as never);
  }
}

function combine(base: unknown[], temporal: unknown[] | null): never {
  return (temporal ? ["all", base, temporal] : ["all", base]) as never;
}

/** Last payload, so a basemap swap can replay it without another round-trip (GeoJSON mode only). */
let lastCollection: FeatureCollection | null = null;

export function updateAtlasData(map: MaplibreMap, collection: FeatureCollection): void {
  lastCollection = collection;
  const source = map.getSource(ATLAS_SOURCE) as GeoJSONSource | undefined;
  source?.setData({ type: "FeatureCollection", features: collection.features } as never);
}

export function replayAtlasData(map: MaplibreMap): void {
  if (lastCollection && !lastBinding) updateAtlasData(map, lastCollection);
}

/** Re-add everything after a style swap, in the delivery mode that was chosen. */
export function restoreAtlasLayers(map: MaplibreMap, font: string[] = lastFont): void {
  ensureAtlasLayers(map, font, lastBinding);
  replayAtlasData(map);
}

/**
 * Change the delivery between GeoJSON and tiles on a live map.
 *
 * MapLibre will not let a source change type, so the switch is: take the layers off, take the source
 * off, put them back the other way. It costs one re-render, not a new map -- which is what makes the
 * reader-facing "data source" switch cheap enough to offer at all.
 */
export function setDelivery(
  map: MaplibreMap,
  binding: VectorBinding | null,
  font: string[] = lastFont,
): void {
  if (sameDelivery(binding, lastBinding)) {
    ensureAtlasLayers(map, font, binding);
    return;
  }
  for (const entry of [...registry].reverse()) {
    if (map.getLayer(entry.id)) map.removeLayer(entry.id);
  }
  if (map.getSource(ATLAS_SOURCE)) map.removeSource(ATLAS_SOURCE);
  registry = [];
  ensureAtlasLayers(map, font, binding);
  replayAtlasData(map); // a no-op in tiles mode, and the reason switching back is instant
}

function sameDelivery(a: VectorBinding | null, b: VectorBinding | null): boolean {
  if (a === null || b === null) return a === b;
  return JSON.stringify(a.spec) === JSON.stringify(b.spec);
}

export function setSelectedFeature(map: MaplibreMap | null, id: string | null): void {
  if (!map) return;
  const filter = [
    "all",
    ["==", ["geometry-type"], "Point"],
    ["==", ["get", "id"], id ?? "__none__"],
  ] as never;
  for (const entry of registry) {
    if (entry.specId !== "azir-selected" || !map.getLayer(entry.id)) continue;
    map.setFilter(entry.id, filter);
  }
  // A map whose layers were added before this module tracked them (older code paths, tests).
  if (registry.length === 0 && map.getLayer("azir-selected")) {
    map.setFilter("azir-selected", filter);
  }
}
