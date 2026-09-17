import assert from "node:assert/strict";
import test from "node:test";

import {
  centerTimelineWindow,
  moveRangeToYear,
  normalizeYearRange,
  rangeMidpoint,
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

