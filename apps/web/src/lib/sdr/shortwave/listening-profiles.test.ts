import { test } from "node:test";
import assert from "node:assert/strict";
import { AM_LISTENING_PROFILES, cleanupEnabled } from "./listening-profiles";
test("listening profiles narrow progressively without silently enabling neural isolation", () => {
  assert.deepEqual(AM_LISTENING_PROFILES.map((p) => p.bandwidthHz), [9000, 6000, 4000]);
  assert.ok(AM_LISTENING_PROFILES.every((p) => !p.enhancement.voiceIsolation));
  assert.equal(cleanupEnabled(AM_LISTENING_PROFILES[0].enhancement), false);
  assert.equal(cleanupEnabled(AM_LISTENING_PROFILES[1].enhancement), true);
  assert.equal(cleanupEnabled({ noiseReduction: "off", voiceIsolation: false, speechFilter: true }), true);
});
