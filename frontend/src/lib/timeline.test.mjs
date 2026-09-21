import assert from "node:assert/strict";
import test from "node:test";

import { parseAtlasState, serializeAtlasState } from "../../.test-build/atlasState.js";
import {
  centerTimelineWindow,
  moveRangeToYear,
  normalizeYearRange,
  rangeMidpoint,
  uniqueById,
} from "../../.test-build/timeline.js";

test("normalizes reversed and out-of-bounds range input", () => {
  assert.deepEqual(normalizeYearRange([2200, -900], -800, 2100), [-800, 2100]);
  assert.deepEqual(normalizeYearRange([1524, 1501], -800, 2100), [1501, 1524]);
});

test("centers the visible window and preserves its width at the edges", () => {
  assert.deepEqual(centerTimelineWindow(1514, 600, -800, 2100), [1214, 1814]);
  assert.deepEqual(centerTimelineWindow(-790, 600, -800, 2100), [-800, -200]);
  assert.deepEqual(centerTimelineWindow(2090, 600, -800, 2100), [1500, 2100]);
});

test("range cursor is stable across URL reload and map scrubbing", () => {
  const reign = [1501, 1524];
  assert.equal(rangeMidpoint(reign), 1513);
  assert.deepEqual(moveRangeToYear(reign, 1600, -800, 2100), [1588, 1611]);
});

test("moving a range at the timeline boundary never changes its duration", () => {
  const moved = moveRangeToYear([1500, 1550], 2100, -800, 2100);
  assert.deepEqual(moved, [2050, 2100]);
  assert.equal(moved[1] - moved[0], 50);
});

test("point URLs cannot retain an interval-only matching mode", () => {
  const point = parseAtlasState(new URLSearchParams("t=1514&mode=during"), null);
  assert.equal(point.mode, "at");
  assert.equal(new URLSearchParams(serializeAtlasState(point, null)).has("mode"), false);

  const span = parseAtlasState(new URLSearchParams("from=1501&to=1524&mode=during"), null);
  assert.equal(span.mode, "during");
  assert.match(serializeAtlasState(span, null), /mode=during/);
});

test("timeline filtering is opt-in and survives URL round trips", () => {
  const overview = parseAtlasState(new URLSearchParams("t=1514"), null);
  assert.equal(overview.timelineActive, false);
  assert.equal(new URLSearchParams(serializeAtlasState(overview, null)).has("time"), false);

  const filtered = parseAtlasState(new URLSearchParams("t=1514&time=1"), null);
  assert.equal(filtered.timelineActive, true);
  assert.equal(new URLSearchParams(serializeAtlasState(filtered, null)).get("time"), "1");
});

test("an event overlapping several timeline buckets is rendered once", () => {
  const events = [
    { id: "evt_babak_revolt", year: 816, label: "Babak" },
    { id: "evt_babak_revolt", year: 816, label: "Babak" },
    { id: "evt_chaldiran", year: 1514, label: "Chaldiran" },
  ];

  assert.deepEqual(uniqueById(events), [events[0], events[2]]);
});

