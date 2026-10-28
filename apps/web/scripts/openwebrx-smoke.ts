import { openWebRxWebSocketUrl } from "../src/lib/sdr/openwebrx/url";
import { unshiftSpectrum } from "../src/lib/sdr/openwebrx/spectrum";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

const cases: Array<[string, string]> = [
  ["localhost:8073", "ws://localhost:8073/ws/"],
  ["http://localhost:8073", "ws://localhost:8073/ws/"],
  ["http://localhost:8073/", "ws://localhost:8073/ws/"],
  ["https://radio.local:8073", "wss://radio.local:8073/ws/"],
  ["ws://127.0.0.1:8073/ws/", "ws://127.0.0.1:8073/ws/"],
  ["http://192.168.1.20:8073/owrx", "ws://192.168.1.20:8073/owrx/ws/"],
];

for (const [input, expected] of cases) {
  const actual = openWebRxWebSocketUrl(input);
  assert(actual === expected, `${input} -> ${actual}, expected ${expected}`);
}

let threw = false;
try {
  openWebRxWebSocketUrl("   ");
} catch {
  threw = true;
}
assert(threw, "blank address should fail");

const shifted = new Float32Array([10, 20, 30, 40]);
const raw = unshiftSpectrum(shifted);
assert(
  raw[0] === 30 && raw[1] === 40 && raw[2] === 10 && raw[3] === 20,
  `unshift mismatch: ${Array.from(raw).join(",")}`,
);
const roundTrip = unshiftSpectrum(raw);
assert(
  roundTrip[0] === 10 &&
    roundTrip[1] === 20 &&
    roundTrip[2] === 30 &&
    roundTrip[3] === 40,
  "fftshift should be its own inverse",
);

console.log("openwebrx smoke ok");
