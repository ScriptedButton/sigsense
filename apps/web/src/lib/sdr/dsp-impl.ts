/**
 * Everything that touches the @jtarrio/signals WebAssembly (filters, FFT,
 * generators). Importing any of these compiles relaxed-SIMD WASM at load time,
 * which iOS Safari rejects. Keep this file out of the static import graph and
 * reach it only through loadDsp() so unsupported browsers still load the app.
 */
import { Demodulator } from "@jtarrio/signals/demod/demodulator.js";
import { registerDemod } from "@jtarrio/signals/demod/modes.js";
import { SampleCounter } from "@jtarrio/signals/demod/sample-counter.js";
import { StreamingSpectrum as Spectrum } from "./streaming-spectrum";
import { CompositeReceiver } from "@jtarrio/signals/radio/sample_receiver.js";
import {
  modulateAM,
  modulateFM,
  noise,
  sum,
  tone,
  wbfmSignal,
} from "@jtarrio/signals/sources/generators.js";
import { FakeRtlDeviceProvider } from "@jtarrio/webrtlsdr/fakertl/fakertl.js";
import { Radio, RtlProvider } from "@jtarrio/webrtlsdr/radio.js";
import { DirectSampling } from "@jtarrio/webrtlsdr/rtlsdr.js";

import { createDemoFskGenerator } from "./fsk/demo-signal";
import { createDemoFt8Generator } from "./ft8/demo-signal";
import { createDemoMorseGenerator } from "./morse/demo-signal";
import { createDemoP25Generator } from "./p25/demo-signal";
import { createDemoWefaxBank } from "./wefax/demo-signal";
import { wefaxDemoCarriersHz } from "./wefax/stations";
import { DEMO_WEFAX_FREQUENCY_HZ } from "./wefax/types";
import { HfAmConfig, HfAmDemod } from "./hf-am-demod";

// Override the package AM implementation after demodulator.js registers its
// defaults. Existing Demodulator instances then construct the HF-safe receiver.
registerDemod("AM", HfAmDemod, HfAmConfig);

export {
  CompositeReceiver,
  Demodulator,
  DirectSampling,
  FakeRtlDeviceProvider,
  Radio,
  RtlProvider,
  SampleCounter,
  Spectrum,
};

export function createDemoGenerator() {
  const wefaxCarriers = [DEMO_WEFAX_FREQUENCY_HZ, ...wefaxDemoCarriersHz()];
  return sum(
    modulateFM(93.9e6, 75_000, 0.1, wbfmSignal(tone(600, 0.5), tone(400, 0.5))),
    modulateFM(88.5e6, 75_000, 0.08, wbfmSignal(tone(440, 0.5))),
    modulateAM(810e3, 0.1, tone(900, 0.5)),
    tone(14_225_750, 0.01),
    createDemoFskGenerator(0.09),
    createDemoFt8Generator(0.05),
    createDemoMorseGenerator(0.06),
    createDemoP25Generator(0.08),
    createDemoWefaxBank(wefaxCarriers, 0.06),
    noise(0.03),
  );
}
