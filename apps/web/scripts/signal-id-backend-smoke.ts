/**
 * Routing smoke test: browser spectrum -> backend CNN client, HackRF tracks,
 * and the offline fallback. The backend is mocked; nothing touches the network.
 *
 *   pnpm tsx scripts/signal-id-backend-smoke.ts
 */
import { displayClassFor, mapBackendTrack } from "../src/lib/sdr/signal-id/backend-tracks";
import { HybridSignalId } from "../src/lib/sdr/signal-id/hybrid";

let failed = 0;
function check(name: string, ok: boolean, detail = ""): void {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failed++;
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const track = {
  id: "rf:abc",
  label: "NFM",
  confidence: 0.82,
  alternatives: [{ label: "FSK", confidence: 0.1 }],
  band_plan: { label: "GMRS channel 1 (462.5625 MHz)", service: "GMRS", channel: 1 },
  center_freq_hz: 462_562_500,
  bandwidth_hz: 12_500,
  power_dbfs: -50,
  snr_db: 24,
  evidence: "CNN on spectrum rows · on GMRS channel 1 (462.5625 MHz)",
  identity_type: "rf_occupancy",
};

// 1. class mapping and track mapping
check("NFM maps to NBFM", displayClassFor("NFM") === "NBFM");
check("unknown maps to UNKNOWN", displayClassFor("unknown") === "UNKNOWN");
check("GFSK4 maps to FSK", displayClassFor("GFSK4") === "FSK");
check("LoRa maps to DIGITAL", displayClassFor("LoRa") === "DIGITAL");
const mapped = mapBackendTrack(track, { centerHz: 462_600_000, noiseFloorDb: -80, ageSec: 0 });
check("offset from centre", Math.round(mapped.offsetHz) === -37_500, String(mapped.offsetHz));
check("backend basis + label kept", mapped.basis === "backend-cnn" && mapped.backendLabel === "NFM");
check("band plan note carried", mapped.bandPlanNote === "GMRS channel 1 (462.5625 MHz)");
check("GMRS channel outranks loose wiki matches", /GMRS/i.test(mapped.identities[0]?.name ?? ""), mapped.identities[0]?.name);
const unknown = mapBackendTrack({ ...track, label: "unknown", confidence: 0.3 }, {
  centerHz: 462_600_000, noiseFloorDb: -80, ageSec: 4,
});
check("unknown stays unknown and stale after 4 s", unknown.unknown === true && unknown.stale === true);

// 2. browser source -> backend (mock fetch)
const requests: { path: string; body: Record<string, unknown> }[] = [];
let backendUp = true;
globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  if (!backendUp) throw new Error("connect ECONNREFUSED");
  requests.push({ path: String(input), body: JSON.parse(String(init?.body ?? "{}")) });
  return new Response(
    JSON.stringify({ noise_floor_db: -80, tracks: [{ ...track, age_s: 0 }], approximation: "mock" }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}) as typeof fetch;

const frame = () => {
  const row = new Float32Array(2048);
  for (let i = 0; i < row.length; i++) row[i] = -80 + Math.random();
  return row;
};
async function feed(id: HybridSignalId, rows: number): Promise<void> {
  for (let i = 0; i < rows; i++) {
    id.pushSpectrum(frame(), 462_600_000, 1_024_000, 462_600_000);
    await sleep(65);
  }
}

async function main(): Promise<void> {
  const id = new HybridSignalId();
  id.setEnabled(true);
  await feed(id, 14);
  await sleep(100);
  check("request sent to the spectrum-bands endpoint", requests.some((r) => r.path.endsWith("/analyze/spectrum-bands")));
  const body = requests[0]?.body ?? {};
  check("payload carries rows/bins/session", typeof body.session === "string" && Number(body.rows) >= 8 && body.bins === 2048, JSON.stringify({ rows: body.rows, bins: body.bins }));
  const state = id.getState();
  check("signals come from the backend", state.source === "backend-cnn" && state.signals.length === 1);
  check("only one request in flight at a time", requests.length <= 2, `${requests.length} requests`);

  // 3. backend goes away -> offline fallback, labelled
  backendUp = false;
  await sleep(950);
  await feed(id, 40);
  await sleep(1100);
  await feed(id, 20);
  check("falls back to the on-device model", id.getState().source === "offline-fallback", String(id.getState().source));
  check("fallback says why", /unreachable/i.test(id.getState().detail ?? ""));

  // 4. HackRF: backend tracks shown, browser classifier off
  const hackrf = new HybridSignalId();
  hackrf.setExternalMode(true);
  hackrf.setExternalTracks([{ ...track, last_seen: 100, first_seen: 90 }], {
    centerHz: 462_600_000, noiseFloorDb: -80, serverNow: 101,
  });
  const hs = hackrf.getState();
  check("HackRF mode is always enabled", hs.enabled === true && hs.source === "backend-cnn");
  check("HackRF track listed with its class", hs.signals[0]?.backendLabel === "NFM");
  hackrf.setExternalTracks([{ ...track, last_seen: 80 }], {
    centerHz: 462_600_000, noiseFloorDb: -80, serverNow: 101,
  });
  check("tracks older than the hold are dropped", hackrf.getState().signals.length === 0);
  const before = requests.length;
  hackrf.pushSpectrum(frame(), 462_600_000, 1_024_000, 462_600_000);
  check("HackRF mode never posts browser rows", requests.length === before);
  hackrf.setExternalMode(false);
  check("leaving HackRF mode clears its tracks", hackrf.getState().signals.length === 0);

  console.log(failed ? `\n${failed} failed` : "\nsignal-id backend smoke passed");
  process.exit(failed ? 1 : 0);
}
void main();
