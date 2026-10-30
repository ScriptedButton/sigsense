import { ConfigAM, type ModeAM } from "@jtarrio/signals/demod/demod-am.js";
import type { Demod, Demodulated } from "@jtarrio/signals/demod/modes.js";
import { makeLowPassKernel } from "@jtarrio/signals/dsp/coefficients.js";
import { AGC, DcBlocker, FrequencyShifter, IqFFTFilter } from "@jtarrio/signals/dsp/filters.js";
import { getPower } from "@jtarrio/signals/dsp/power.js";
import { getIqResampler, type IqResampler } from "@jtarrio/signals/dsp/resamplers.js";

/** Streaming AM receiver designed for 1–2.4 MS/s direct-sampling HF input. */
export class HfAmDemod implements Demod<ModeAM> {
  private mode: ModeAM;
  private readonly outRate: number;
  private readonly shifter: FrequencyShifter;
  private readonly downsampler: IqResampler;
  private readonly filter: IqFFTFilter;
  private readonly dcBlocker: DcBlocker;
  private readonly agc: AGC;
  private readonly fadeSamples: number;
  private samplesSeen = 0;
  private carrierAmplitude: number | null = null;

  constructor(inRate: number, outRate: number, mode: ModeAM) {
    this.mode = mode;
    this.outRate = outRate;
    this.shifter = new FrequencyShifter(inRate);
    // Output-rate taps maintain anti-alias rejection at both RTL capture rates;
    // the stock AM path's legacy taps shrink with increasing input rate.
    this.downsampler = getIqResampler(inRate, outRate, {
      taps: 127,
      lowPassFrequency: Math.min(20_000, outRate * 0.45),
    });
    this.filter = new IqFFTFilter(makeLowPassKernel(outRate, mode.bandwidth / 2, 511));
    this.dcBlocker = new DcBlocker(outRate);
    this.agc = new AGC(outRate, 0.12, 8);
    this.fadeSamples = Math.max(1, Math.round(outRate * 0.025));
  }

  getMode() { return this.mode; }

  setMode(mode: ModeAM) {
    this.mode = mode;
    this.filter.setCoefficients(makeLowPassKernel(this.outRate, mode.bandwidth / 2, 511));
  }

  demodulate(samplesI: Float32Array, samplesQ: Float32Array, freqOffset: number): Demodulated {
    this.shifter.inPlace(samplesI, samplesQ, -freqOffset);
    const [I, Q] = this.downsampler.resample(samplesI, samplesQ);
    const allPower = getPower(I, Q);
    this.filter.inPlace(I, Q);
    const signalPower = (getPower(I, Q) * this.outRate) / this.mode.bandwidth;
    const audio = new Float32Array(I.length);
    let carrier = this.carrierAmplitude;
    if (carrier === null) {
      let sum = 0;
      let peak = 0;
      let count = 0;
      for (let index = 0; index < I.length; index += 1) {
        const amplitude = Math.hypot(I[index], Q[index]);
        if (amplitude > 1e-7) {
          sum += amplitude;
          peak = Math.max(peak, amplitude);
          count += 1;
        }
      }
      // FIR/FFT startup contains leading zeroes. Seed from meaningful samples
      // in the whole block, with a peak-derived floor, before feeding the AGC.
      carrier = Math.max(count > 0 ? sum / count : 0, peak * 0.1, 1e-6);
    }
    const warmingUp = this.samplesSeen < this.outRate * 0.1;
    const carrierAlpha = 1 - Math.exp(-1 / (this.outRate * (warmingUp ? 0.02 : 0.35)));
    for (let index = 0; index < audio.length; index += 1) {
      const amplitude = Math.hypot(I[index], Q[index]);
      carrier += carrierAlpha * (amplitude - carrier);
      // Bound before AGC so a filter-start transient cannot poison its
      // one-second peak memory and suppress otherwise usable speech.
      audio[index] = Math.max(-3, Math.min(3, amplitude / Math.max(carrier, 1e-6) - 1));
    }
    this.carrierAmplitude = carrier;
    this.dcBlocker.inPlace(audio);
    this.agc.inPlace(audio);
    for (let index = 0; index < audio.length; index += 1) {
      const fade = Math.min(1, (this.samplesSeen + index) / this.fadeSamples);
      const sample = Number.isFinite(audio[index]) ? audio[index] : 0;
      audio[index] = Math.max(-1, Math.min(1, sample)) * fade;
    }
    this.samplesSeen += audio.length;
    return {
      left: audio,
      right: new Float32Array(audio),
      stereo: false,
      snr: allPower > 0 ? signalPower / allPower : 0,
    };
  }
}

export { ConfigAM as HfAmConfig };
