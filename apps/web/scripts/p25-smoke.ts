/**
 * Offline smoke test: sync+NID+TSBK dibits → framer + grant parse.
 * Run: pnpm exec tsx scripts/p25-smoke.ts
 */
import { findFrameSync, encodeSyncAndNid, P25Framer } from "../src/lib/sdr/p25/framer";
import {
  encodeTsbkGrantDibits,
  parseTsbkBytes,
  tsbkDibitsToPayload,
} from "../src/lib/sdr/p25/tsbk";
import { DEMO_P25_FREQUENCY_HZ } from "../src/lib/sdr/p25/types";

const NAC = 0x293;
const TGID = 1001;
const SOURCE = 0x1a2b3c;
const CHANNEL = 0x012c;

const syncNid = encodeSyncAndNid(NAC, 0x7);
const sync = findFrameSync(syncNid);
if (!sync || sync.errors > 0) {
  console.error("FAIL: sync not found on encoded pattern", sync);
  process.exit(1);
}
console.log("sync OK at", sync.index, "errors", sync.errors);

const grantDibits = encodeTsbkGrantDibits({
  tgid: TGID,
  sourceId: SOURCE,
  channel: CHANNEL,
});
const bytes = tsbkDibitsToPayload(grantDibits);
if (!bytes) {
  console.error("FAIL: could not pack TSBK bytes");
  process.exit(1);
}
const channelIds = new Map();
channelIds.set(0, {
  identifier: 0,
  bandwidthKhz: 12.5,
  txOffsetMhz: 0,
  channelSpacingKhz: 6.25,
  baseFreqMhz: DEMO_P25_FREQUENCY_HZ / 1e6 - (CHANNEL & 0xfff) * 0.00625,
});
const parsed = parseTsbkBytes(bytes, channelIds);
console.log("tsbk:", parsed?.type, parsed?.summary);
if (!parsed || parsed.tgid !== TGID || parsed.sourceId !== SOURCE) {
  console.error("FAIL: grant parse mismatch", parsed);
  process.exit(1);
}

const stream = [
  ...syncNid,
  ...new Array(24).fill(1),
  ...grantDibits,
  ...new Array(60).fill(1),
  ...syncNid,
  ...new Array(24).fill(1),
  ...grantDibits,
];

const framer = new P25Framer();
const frames = framer.push(stream);
console.log("frames:", frames.length, frames.map((f) => f.duidLabel));
if (frames.length < 1 || frames[0]!.nac !== NAC || frames[0]!.duid !== 0x7) {
  console.error("FAIL: framer did not emit TSBK", frames[0]);
  process.exit(1);
}

console.log("OK");
