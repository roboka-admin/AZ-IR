/**
 * URL-synced atlas state.
 *
 * The map+time+layers+selection state lives in the query string so every view is shareable and
 * reloadable (`/?c=38.24,48.29&z=12&t=1514&l=places,buildings&entity=plc_ardabil`). Nothing
 * historical is stored here -- only view state (AGENTS.md rule 15).
 */

import type { CalendarCode, MetaResponse, TemporalMode } from "./types";

export interface AtlasState {
  center: [number, number]; // [lat, lon] -- MapLibre order used by the UI
  zoom: number;
  year: number;
  calendar: CalendarCode;
  mode: TemporalMode;
  span: [number, number] | null;
  layers: string[] | null; // null = use the server defaults from /meta
  entity: string | null;
  query: string | null;
  playing: boolean;
}

const CALENDARS: CalendarCode[] = ["gregorian_proleptic", "julian", "islamic_lunar", "persian_solar"];
const MODES: TemporalMode[] = ["at", "during", "overlaps"];

function pair(raw: string | null): [number, number] | null {
  if (!raw) return null;
  const parts = raw.split(",").map(Number);
  if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n))) return null;
  return [parts[0] as number, parts[1] as number];
}

function integer(raw: string | null): number | null {
  if (!raw) return null;
  const value = Number.parseInt(raw, 10);
  return Number.isFinite(value) ? value : null;
}

export function parseAtlasState(search: URLSearchParams, meta: MetaResponse | null): AtlasState {
  const center = pair(search.get("c")) ?? (meta ? [meta.study_area.center[1], meta.study_area.center[0]] : [38.0, 47.0]);
  const zoom = Number(search.get("z")) || meta?.study_area.default_zoom || 6.4;
  const calendarParam = search.get("cal") as CalendarCode | null;
  const calendar = calendarParam && CALENDARS.includes(calendarParam) ? calendarParam : "gregorian_proleptic";
  const modeParam = search.get("mode") as TemporalMode | null;
  const mode = modeParam && MODES.includes(modeParam) ? modeParam : "at";
  const from = integer(search.get("from"));
  const to = integer(search.get("to"));
  const year =
    integer(search.get("t")) ?? (from !== null ? from : meta?.timeline.default_year ?? 1500);
  const layersRaw = search.get("l");
  const knownLayers = new Set((meta?.layers ?? []).map((layer) => layer.id));

  return {
    center: [clampLat(center[0]), clampLon(center[1])],
    zoom: clampZoom(zoom),
    year: clampYear(year, meta),
    calendar,
    mode,
    span: from !== null && to !== null && to >= from ? [from, to] : null,
    layers: layersRaw
      ? layersRaw.split(",").filter((id) => knownLayers.size === 0 || knownLayers.has(id))
      : null,
    entity: search.get("entity"),
    query: search.get("q"),
    playing: search.get("play") === "1",
  };
}

export function serializeAtlasState(state: AtlasState, meta: MetaResponse | null): string {
  const params = new URLSearchParams();
  params.set("c", `${round(state.center[0], 4)},${round(state.center[1], 4)}`);
  params.set("z", String(round(state.zoom, 2)));
  if (state.span) {
    params.set("from", String(state.span[0]));
    params.set("to", String(state.span[1]));
    if (state.mode !== "overlaps") params.set("mode", state.mode);
  } else {
    params.set("t", String(state.year));
  }
  if (state.calendar !== "gregorian_proleptic") params.set("cal", state.calendar);
  if (state.layers) params.set("l", state.layers.join(","));
  if (state.entity) params.set("entity", state.entity);
  if (state.query) params.set("q", state.query);
  if (state.playing) params.set("play", "1");
  void meta;
  return params.toString();
}

/** The deep link the backend suggests (`/?entity=...&t=...`) parsed into a patch. */
export function parseDeepLink(link: string, meta: MetaResponse | null): Partial<AtlasState> {
  const query = link.includes("?") ? link.slice(link.indexOf("?") + 1) : link;
  return parseAtlasState(new URLSearchParams(query), meta);
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function clampLat(lat: number): number {
  return Math.min(85, Math.max(-85, lat));
}

function clampLon(lon: number): number {
  return Math.min(180, Math.max(-180, lon));
}

function clampZoom(zoom: number): number {
  return Math.min(22, Math.max(0, zoom));
}

function clampYear(year: number, meta: MetaResponse | null): number {
  const floor = meta?.timeline.floor ?? -800;
  const ceil = meta?.timeline.ceil ?? 2100;
  return Math.min(ceil, Math.max(floor, year));
}
