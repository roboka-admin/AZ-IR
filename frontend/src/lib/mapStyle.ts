/**
 * MapLibre style construction.
 *
 * The basemap is decoration; every historical mark on the map comes from the API. Two rules shape
 * this file:
 *   - the map contains no historical logic (AGENTS.md rule 5): styling keys are `layer`,
 *     `certainty`, `rank` and `geometry_kind`, all of which the API supplies;
 *   - certainty drives the visual language (ADR-0013): a reconstructed extent is dashed and
 *     transparent, a surveyed footprint is solid. The reader must be able to tell them apart
 *     without opening a popup.
 */

import type { FeatureCollection as GeoJSONCollection } from "geojson";
import type { GeoJSONSource, Map as MaplibreMap, StyleSpecification } from "maplibre-gl";
import type { FeatureCollection } from "./types";

/** Free, keyless basemap; probed at runtime and replaced by `fallbackStyle()` if unreachable. */
export const BASEMAP_STYLE_URL = "https://tiles.openfreemap.io/styles/liberty";

export const ATLAS_SOURCE = "azir-atlas";
export const GRATICULE_SOURCE = "azir-graticule";
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
  return ["match", ["get", "layer"],
    "political_entities", PALETTE.political_entities,
    "modern_borders", PALETTE.modern_borders,
    "archaeology", PALETTE.archaeology,
    "places", PALETTE.places,
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

function atlasLayers(font: string[]): LayerSpec[] {
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
        "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 10, 2],
        "line-opacity": 0.92,
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
      filter: ["==", ["geometry-type"], "Point"],
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
      filter: ["==", ["geometry-type"], "Point"],
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
      // No geometry filter: points get offset labels, polygons get a centred one.
      filter: ["all"],
      layout: {
        "text-field": ["get", "label"],
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

export function ensureAtlasLayers(map: MaplibreMap, font: string[] = LIBERTY_FONT): void {
  if (!map.getSource(ATLAS_SOURCE)) {
    map.addSource(ATLAS_SOURCE, {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
      // The API already clips and simplifies; a small buffer keeps labels near the edge stable.
      buffer: 64,
      maxzoom: 16,
    } as never);
  }
  for (const spec of atlasLayers(font)) {
    if (map.getLayer(spec.id)) continue;
    map.addLayer({
      id: spec.id,
      type: spec.type,
      source: ATLAS_SOURCE,
      filter: spec.filter,
      layout: spec.layout,
      paint: spec.paint,
      minzoom: spec.minzoom,
    } as never);
  }
}

/** Last payload, so a basemap swap can replay it without another round-trip. */
let lastCollection: FeatureCollection | null = null;

export function updateAtlasData(map: MaplibreMap, collection: FeatureCollection): void {
  lastCollection = collection;
  const source = map.getSource(ATLAS_SOURCE) as GeoJSONSource | undefined;
  source?.setData({ type: "FeatureCollection", features: collection.features } as never);
}

export function replayAtlasData(map: MaplibreMap): void {
  if (lastCollection) updateAtlasData(map, lastCollection);
}

export function setSelectedFeature(map: MaplibreMap | null, id: string | null): void {
  if (!map || !map.getLayer("azir-selected")) return;
  map.setFilter("azir-selected", [
    "all",
    ["==", ["geometry-type"], "Point"],
    ["==", ["get", "id"], id ?? "__none__"],
  ] as never);
}

export function setLayerVisibility(map: MaplibreMap, visibleLayers: string[]): void {
  const allowed = [...new Set(visibleLayers)];
  for (const spec of atlasLayers(LIBERTY_FONT)) {
    if (spec.id === "azir-selected" || !map.getLayer(spec.id)) continue;
    map.setFilter(spec.id, ["all", spec.filter, ["in", ["get", "layer"], ["literal", allowed]]] as never);
  }
}
