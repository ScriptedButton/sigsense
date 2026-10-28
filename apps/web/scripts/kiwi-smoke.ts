import { kiwiModulation, kiwiBandpass, schemeFromKiwi } from "../src/lib/sdr/kiwi/modes";
import {
  kiwiZoomForSpanHz,
  kiwiZoomSpanHz,
  KIWI_MAX_FREQ_KHZ,
} from "../src/lib/sdr/kiwi/protocol";
import { decodeKiwiWaterfall } from "../src/lib/sdr/kiwi/spectrum";
import { ImaAdpcmDecoder } from "../src/lib/sdr/openwebrx/adpcm";
import {
  kiwiHttpOrigin,
  kiwiMixedContentMessage,
  kiwiWebSocketUrls,
} from "../src/lib/sdr/kiwi/url";

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

assert(
  kiwiHttpOrigin("21128.proxy2.kiwisdr.com:8073") ===
    "http://21128.proxy2.kiwisdr.com:8073",
  "bare host origin",
);
assert(
  kiwiHttpOrigin("http://21128.proxy2.kiwisdr.com:8073/") ===
    "http://21128.proxy2.kiwisdr.com:8073",
  "http origin strips path",
);
assert(
  kiwiHttpOrigin("https://kiwi.example.com:8073") ===
    "https://kiwi.example.com:8073",
  "https origin",
);

const modern = kiwiWebSocketUrls("http://kiwi.local:8073", 1_700_000_000_000, false);
assert(
  modern.snd === "ws://kiwi.local:8073/ws/kiwi/1700000000000/SND",
  `modern snd: ${modern.snd}`,
);
assert(
  modern.wf === "ws://kiwi.local:8073/ws/kiwi/1700000000000/W/F",
  `modern wf: ${modern.wf}`,
);

const legacy = kiwiWebSocketUrls("https://kiwi.local:8073", 42, true);
assert(
  legacy.snd === "wss://kiwi.local:8073/42/SND",
  `legacy snd: ${legacy.snd}`,
);
assert(legacy.wf === "wss://kiwi.local:8073/42/W/F", `legacy wf: ${legacy.wf}`);

let threw = false;
try {
  kiwiHttpOrigin("   ");
} catch {
  threw = true;
}
assert(threw, "blank address should fail");

assert(kiwiModulation("AM") === "am", "AM mod");
assert(kiwiModulation("USB") === "usb", "USB mod");
assert(schemeFromKiwi("nbfm") === "NBFM", "nbfm scheme");
const band = kiwiBandpass("USB", 2800);
assert(band.low_cut === 300 && band.high_cut === 2800, "usb bandpass");

const span8 = kiwiZoomSpanHz(8, KIWI_MAX_FREQ_KHZ);
assert(Math.abs(span8 - 117_187.5) < 1, `zoom 8 span: ${span8}`);
assert(kiwiZoomForSpanHz(120_000, KIWI_MAX_FREQ_KHZ) === 8, "zoom for 120 kHz");

const raw = new Uint8Array([10, 20, 30, 40]);
const bins = decodeKiwiWaterfall(raw, false, new ImaAdpcmDecoder());
assert(bins !== null && bins.length === 4, "raw wf length");
assert(bins![0] === -190 && bins![1] === -180, "raw wf scale");

// Mixed-content helper is a no-op outside the browser.
assert(kiwiMixedContentMessage("ws://x") === null, "no window → no mixed warning");

console.log("kiwi smoke ok");
