import type { MetaResponse } from "./types";

export type YearRange = [number, number];

/** Keep every timeline window ordered and inside the API's normalized bounds. */
export function normalizeYearRange(range: YearRange, floor: number, ceil: number): YearRange {
  const first = clampYear(Math.round(range[0]), floor, ceil);
  const second = clampYear(Math.round(range[1]), floor, ceil);
  return first <= second ? [first, second] : [second, first];
}

/** Build a viewport around a year without changing its requested width at either boundary. */
export function centerTimelineWindow(year: number, width: number, floor: number, ceil: number): YearRange {
  const available = Math.max(0, ceil - floor);
  const span = Math.max(0, Math.min(Math.round(width), available));
  const center = clampYear(Math.round(year), floor, ceil);
  // Floor keeps odd-width ranges anchored to the requested integer cursor after midpoint rounding.
  let from = Math.floor(center - span / 2);
  from = Math.max(floor, Math.min(from, ceil - span));
  return [from, from + span];
}

/** A range selection has one representative cursor: its midpoint. */
export function rangeMidpoint(range: YearRange): number {
  return Math.round((range[0] + range[1]) / 2);
}

/** Move a selected range with the scrubber/playback while preserving its duration. */
export function moveRangeToYear(range: YearRange, year: number, floor: number, ceil: number): YearRange {
  return centerTimelineWindow(year, range[1] - range[0], floor, ceil);
}

/** Clamp a year using the public metadata contract. */
export function clampTimelineYear(year: number, meta: MetaResponse): number {
  return clampYear(Math.round(year), meta.timeline.floor, meta.timeline.ceil);
}

/** Timeline events can legitimately overlap several histogram buckets; render each entity once. */
export function uniqueById<T extends { id: string }>(items: readonly T[]): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function clampYear(year: number, floor: number, ceil: number): number {
  return Math.min(ceil, Math.max(floor, year));
}
