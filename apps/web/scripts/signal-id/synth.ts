import { DEFAULT_SAMPLE_RATE, FFT_SIZE } from "../../src/lib/sdr/types";
import type { SignalClass } from "../../src/lib/sdr/signal-id/types";
import { makeBlackmanWindow, spectrumDb } from "./fft";

export const SAMPLE_RATE = DEFAULT_SAMPLE_RATE;
export const BIN_HZ = SAMPLE_RATE / FFT_SIZE;

/** Deterministic PRNG so training runs are reproducible. */
export class Rng {
  private state: number;

  constructor(seed: number) {
    this.state = seed >>> 0;
  }

  next(): number {
    this.state = (this.state + 0x6d2b79f5) >>> 0;
    let t = this.state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  range(lo: number, hi: number): number {
    return lo + this.next() * (hi - lo);
  }

  /** Log-uniform, for quantities spanning orders of magnitude. */
  logRange(lo: number, hi: number): number {
    return Math.exp(this.range(Math.log(lo), Math.log(hi)));
  }

  int(lo: number, hi: number): number {
    return Math.floor(this.range(lo, hi + 1 - 1e-9));
  }

  pick<T>(items: readonly T[]): T {
    return items[this.int(0, items.length - 1)];
  }

  /** Standard normal via Box-Muller. */
  normal(): number {
    const u = Math.max(this.next(), 1e-12);
    const v = this.next();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }
}

type SignalSource = {
  /** Occupied bandwidth, used to calibrate noise for a target SNR. */
  bandwidthHz: number;
  /** Time-domain RMS of one component of the complex envelope. */
  rms: number;
  render(t0: number, re: Float32Array, im: Float32Array): void;
};

/**
 * A sum of complex exponentials advanced by phasor recurrence rather than a
 * trig call per tone per sample. That makes it affordable to use enough tones
 * (24+) for a voice-like *continuous* spectrum, which matters because the
 * gentle roll-off away from the carrier is the only thing distinguishing upper
 * from lower sideband once a signal is only a handful of FFT bins wide.
 */
class ToneBank {
  private readonly count: number;
  private readonly freqs: Float64Array;
  private readonly amps: Float64Array;
  private readonly phases: Float64Array;
  private readonly stepRe: Float64Array;
  private readonly stepIm: Float64Array;
  private readonly curRe: Float64Array;
  private readonly curIm: Float64Array;

  constructor(freqs: number[], amps: number[], phases: number[]) {
    this.count = freqs.length;
    this.freqs = Float64Array.from(freqs);
    this.amps = Float64Array.from(amps);
    this.phases = Float64Array.from(phases);
    this.stepRe = new Float64Array(this.count);
    this.stepIm = new Float64Array(this.count);
    this.curRe = new Float64Array(this.count);
    this.curIm = new Float64Array(this.count);
    for (let k = 0; k < this.count; k++) {
      const w = (2 * Math.PI * this.freqs[k]) / SAMPLE_RATE;
      this.stepRe[k] = Math.cos(w);
      this.stepIm[k] = Math.sin(w);
    }
  }

  /** Frames are gapped in time, so each one re-seeds from absolute time. */
  reset(t0: number): void {
    for (let k = 0; k < this.count; k++) {
      const ph = 2 * Math.PI * this.freqs[k] * t0 + this.phases[k];
      this.curRe[k] = Math.cos(ph);
      this.curIm[k] = Math.sin(ph);
    }
  }

  /** Advances one sample and accumulates the weighted sum into `out`. */
  next(out: { re: number; im: number }): void {
    let sr = 0;
    let si = 0;
    for (let k = 0; k < this.count; k++) {
      const re = this.curRe[k];
      const im = this.curIm[k];
      sr += this.amps[k] * re;
      si += this.amps[k] * im;
      this.curRe[k] = re * this.stepRe[k] - im * this.stepIm[k];
      this.curIm[k] = re * this.stepIm[k] + im * this.stepRe[k];
    }
    out.re = sr;
    out.im = si;
  }
}

/** Voice-like tone set: dense across 180 Hz–`maxHz` with a speech roll-off. */
function voiceTones(
  rng: Rng,
  maxHz: number,
  count: number,
  sign: 1 | -1,
): { bank: ToneBank; rmsNorm: number } {
  const freqs: number[] = [];
  const amps: number[] = [];
  const phases: number[] = [];
  let power = 0;
  for (let k = 0; k < count; k++) {
    // Spread log-uniformly so the low end, where speech energy lives, is
    // resolved more finely than the sparse high end.
    const f = Math.exp(rng.range(Math.log(180), Math.log(maxHz)));
    const a = (1 / (1 + Math.pow(f / 420, 1.3))) * rng.range(0.6, 1.4);
    freqs.push(sign * f);
    amps.push(a);
    phases.push(rng.range(0, Math.PI * 2));
    power += a * a;
  }
  // Normalising by RMS rather than by the sum of amplitudes is what keeps the
  // modulation depth physically meaningful: summing N random-phase tones and
  // dividing by their total would attenuate the result by roughly sqrt(N),
  // pushing AM sidebands far below where they belong.
  return { bank: new ToneBank(freqs, amps, phases), rmsNorm: Math.sqrt(power / 2) };
}

export type SynthSample = {
  /** Raw FFT-order dB spectra, oldest first. */
  frames: Float32Array[];
  offsetHz: number;
  bandwidthHz: number;
};

/** A real-valued modulating signal that has to be advanced sample by sample. */
type Modulator = {
  reset(t0: number): void;
  next(t: number): number;
};

/** Voice-like modulating signal: dense tones under a syllabic envelope. */
function makeVoice(rng: Rng, maxHz: number): Modulator {
  const { bank, rmsNorm } = voiceTones(rng, maxHz, 20, 1);
  const syllRate = rng.range(1.5, 5);
  const syllPhase = rng.range(0, Math.PI * 2);
  const floorLevel = rng.range(0.15, 0.45);
  const scratch = { re: 0, im: 0 };

  return {
    reset(t0) {
      bank.reset(t0);
    },
    next(t) {
      bank.next(scratch);
      const env =
        floorLevel +
        (1 - floorLevel) * (0.5 + 0.5 * Math.sin(2 * Math.PI * syllRate * t + syllPhase));
      // 0.45 RMS puts peaks near unity with a speech-like crest factor; the
      // clamp stands in for the limiting any real transmitter applies.
      const v = (scratch.im / rmsNorm) * 0.45 * env;
      return v > 1 ? 1 : v < -1 ? -1 : v;
    },
  };
}

function carrierSource(rng: Rng, offsetHz: number): SignalSource {
  const phase0 = rng.range(0, Math.PI * 2);
  return {
    bandwidthHz: BIN_HZ,
    rms: 1,
    render(t0, re, im) {
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const ph = 2 * Math.PI * offsetHz * t + phase0;
        re[i] = Math.cos(ph);
        im[i] = Math.sin(ph);
      }
    },
  };
}

/** Builds a Morse-like on/off schedule long enough to cover the whole capture. */
function makeKeying(rng: Rng, totalSeconds: number): (t: number) => number {
  const wpm = rng.range(12, 28);
  const dot = 1.2 / wpm;
  const edges: number[] = [];
  const states: number[] = [];
  let t = 0;
  let on = false;
  while (t < totalSeconds + 2) {
    edges.push(t);
    states.push(on ? 1 : 0);
    if (on) {
      t += rng.next() < 0.65 ? dot : dot * 3;
    } else {
      // Long inter-word gaps are kept rare: one spanning the whole ~0.7 s
      // observation window would leave nothing but a steady carrier to see.
      const r = rng.next();
      t += r < 0.7 ? dot : r < 0.95 ? dot * 3 : dot * 5;
    }
    on = !on;
  }
  const ramp = dot * 0.12;

  return (time: number) => {
    let lo = 0;
    let hi = edges.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (edges[mid] <= time) lo = mid;
      else hi = mid - 1;
    }
    const target = states[lo];
    // Raised-cosine edges keep the keying clicks out of the spectrum.
    const since = time - edges[lo];
    if (since < ramp) {
      const prev = lo > 0 ? states[lo - 1] : 1 - target;
      const x = 0.5 - 0.5 * Math.cos((Math.PI * since) / ramp);
      return prev + (target - prev) * x;
    }
    return target;
  };
}

function cwSource(rng: Rng, offsetHz: number, totalSeconds: number): SignalSource {
  const phase0 = rng.range(0, Math.PI * 2);
  const key = makeKeying(rng, totalSeconds);
  return {
    bandwidthHz: BIN_HZ * 2,
    rms: 0.6,
    render(t0, re, im) {
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const a = key(t);
        const ph = 2 * Math.PI * offsetHz * t + phase0;
        re[i] = a * Math.cos(ph);
        im[i] = a * Math.sin(ph);
      }
    },
  };
}

function amSource(rng: Rng, offsetHz: number): SignalSource {
  const phase0 = rng.range(0, Math.PI * 2);
  const audioMax = rng.range(2500, 5000);
  // Shallow modulation buries the sidebands under the detection threshold and
  // is genuinely indistinguishable from a bare carrier, so keep it audible.
  const depth = rng.range(0.45, 0.95);
  const voice = makeVoice(rng, audioMax);
  return {
    bandwidthHz: audioMax * 2,
    rms: 1,
    render(t0, re, im) {
      voice.reset(t0);
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const env = 1 + depth * voice.next(t);
        const ph = 2 * Math.PI * offsetHz * t + phase0;
        re[i] = env * Math.cos(ph);
        im[i] = env * Math.sin(ph);
      }
    },
  };
}

/**
 * Suppressed-carrier single sideband. Energy sits entirely on one side of the
 * (absent) carrier and tapers away from it, so the band's spectral skew points
 * back towards where the carrier would be — the cue that separates USB in LSB.
 */
function ssbSource(rng: Rng, offsetHz: number, upper: boolean): SignalSource {
  const audioMax = rng.range(2400, 3100);
  const sign: 1 | -1 = upper ? 1 : -1;
  const { bank, rmsNorm } = voiceTones(rng, audioMax, 28, sign);
  const syllRate = rng.range(1.5, 5);
  const syllPhase = rng.range(0, Math.PI * 2);
  const scratch = { re: 0, im: 0 };

  return {
    bandwidthHz: audioMax,
    rms: 0.65,
    render(t0, re, im) {
      bank.reset(t0);
      const carrierStep = (2 * Math.PI * offsetHz) / SAMPLE_RATE;
      let cPhase = 2 * Math.PI * offsetHz * t0;
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const env =
          0.3 + 0.7 * (0.5 + 0.5 * Math.sin(2 * Math.PI * syllRate * t + syllPhase));
        bank.next(scratch);
        const ar = (scratch.re / rmsNorm) * env;
        const ai = (scratch.im / rmsNorm) * env;
        const c = Math.cos(cPhase);
        const s = Math.sin(cPhase);
        re[i] = ar * c - ai * s;
        im[i] = ar * s + ai * c;
        cPhase += carrierStep;
      }
    },
  };
}

function fmSource(
  rng: Rng,
  offsetHz: number,
  deviationHz: number,
  audioMax: number,
  stereo: boolean,
): SignalSource {
  const voice = makeVoice(rng, audioMax);
  const pilotLevel = stereo ? rng.range(0.06, 0.12) : 0;
  const subLevel = stereo ? rng.range(0.2, 0.4) : 0;
  const sub = stereo ? makeVoice(rng, audioMax) : null;

  return {
    // Carson's rule.
    bandwidthHz: 2 * (deviationHz + audioMax),
    rms: 1,
    render(t0, re, im) {
      voice.reset(t0);
      sub?.reset(t0);
      let phase = 0;
      const step = (2 * Math.PI) / SAMPLE_RATE;
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        let m = voice.next(t);
        if (sub) {
          m =
            m * 0.5 +
            pilotLevel * Math.sin(2 * Math.PI * 19000 * t) +
            subLevel * sub.next(t) * Math.sin(2 * Math.PI * 38000 * t);
        }
        phase += step * (offsetHz + deviationHz * m);
        re[i] = Math.cos(phase);
        im[i] = Math.sin(phase);
      }
    },
  };
}

/** Stable pseudo-random symbol value for a given index. */
function symbolAt(index: number, seed: number, levels: number): number {
  let h = Math.imul(index ^ seed, 0x9e3779b1);
  h ^= h >>> 15;
  h = Math.imul(h, 0x85ebca6b);
  h ^= h >>> 13;
  return (h >>> 0) % levels;
}

function fskSource(rng: Rng, offsetHz: number): SignalSource {
  const levels = rng.next() < 0.75 ? 2 : 4;
  // A shift narrower than a couple of FFT bins is unresolvable at this
  // resolution and would only teach the model to guess between FSK and CW.
  const shiftHz = rng.logRange(900, 8000);
  const baud = rng.logRange(45, 1200);
  const seed = rng.int(1, 1 << 20);

  return {
    bandwidthHz: shiftHz + baud * 2,
    rms: 1,
    render(t0, re, im) {
      let phase = 0;
      const step = (2 * Math.PI) / SAMPLE_RATE;
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const sym = symbolAt(Math.floor(t * baud), seed, levels);
        const norm = levels === 2 ? sym * 2 - 1 : (sym / (levels - 1)) * 2 - 1;
        phase += step * (offsetHz + (norm * shiftHz) / 2);
        re[i] = Math.cos(phase);
        im[i] = Math.sin(phase);
      }
    },
  };
}

/**
 * Band-limited complex noise, which is what most digital modes look like on a
 * spectrum analyser: flat top, steep skirts, no internal structure.
 */
function digitalSource(rng: Rng, offsetHz: number, bandwidthHz: number): SignalSource {
  const taps = Math.max(2, Math.round((0.9 * SAMPLE_RATE) / bandwidthHz));
  const stages = 3;
  const seed = rng.int(1, 1 << 24);
  const effectiveBw = SAMPLE_RATE / taps;

  return {
    bandwidthHz: effectiveBw,
    rms: 1,
    render(t0, re, im) {
      const n = re.length;
      const warmup = taps * stages;
      const total = n + warmup;
      const local = new Rng(seed ^ Math.round(t0 * 1000));
      let bufRe: Float32Array = new Float32Array(total);
      let bufIm: Float32Array = new Float32Array(total);
      for (let i = 0; i < total; i++) {
        bufRe[i] = local.normal();
        bufIm[i] = local.normal();
      }
      for (let s = 0; s < stages; s++) {
        bufRe = movingAverage(bufRe, taps);
        bufIm = movingAverage(bufIm, taps);
      }

      // Normalise then shift to the requested offset.
      let power = 0;
      for (let i = warmup; i < total; i++) {
        power += bufRe[i] * bufRe[i] + bufIm[i] * bufIm[i];
      }
      const scale = Math.sqrt(n / Math.max(power, 1e-12));

      for (let i = 0; i < n; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const ph = 2 * Math.PI * offsetHz * t;
        const c = Math.cos(ph);
        const s = Math.sin(ph);
        const xr = bufRe[i + warmup] * scale;
        const xi = bufIm[i + warmup] * scale;
        re[i] = xr * c - xi * s;
        im[i] = xr * s + xi * c;
      }
    },
  };
}

function movingAverage(input: Float32Array, taps: number): Float32Array {
  const out = new Float32Array(input.length);
  let sum = 0;
  for (let i = 0; i < input.length; i++) {
    sum += input[i];
    if (i >= taps) sum -= input[i - taps];
    out[i] = sum / Math.min(i + 1, taps);
  }
  return out;
}

/**
 * A narrow carrier that drifts unpredictably — the classic receiver spur.
 *
 * Frequency instability alone defines this class. Gating it on and off would
 * make it Morse by any reasonable reading, so it stays keyed up throughout.
 */
function spurSource(rng: Rng, offsetHz: number): SignalSource {
  const wander = rng.range(1200, 9000);
  const wanderRate = rng.range(0.8, 7);
  const phase0 = rng.range(0, Math.PI * 2);
  return {
    bandwidthHz: BIN_HZ * 3,
    rms: 0.5,
    render(t0, re, im) {
      let phase = phase0;
      const step = (2 * Math.PI) / SAMPLE_RATE;
      for (let i = 0; i < re.length; i++) {
        const t = t0 + i / SAMPLE_RATE;
        const drift = wander * Math.sin(2 * Math.PI * wanderRate * t);
        phase += step * (offsetHz + drift);
        re[i] = Math.cos(phase);
        im[i] = Math.sin(phase);
      }
    },
  };
}

function buildSource(
  cls: SignalClass,
  rng: Rng,
  offsetHz: number,
  totalSeconds: number,
): SignalSource {
  switch (cls) {
    case "CARRIER":
      return carrierSource(rng, offsetHz);
    case "CW":
      return cwSource(rng, offsetHz, totalSeconds);
    case "AM":
      return amSource(rng, offsetHz);
    case "USB":
      return ssbSource(rng, offsetHz, true);
    case "LSB":
      return ssbSource(rng, offsetHz, false);
    case "NBFM":
      return fmSource(rng, offsetHz, rng.range(2000, 5500), rng.range(2500, 3400), false);
    case "WBFM":
      return fmSource(
        rng,
        offsetHz,
        rng.range(50000, 78000),
        rng.range(12000, 15500),
        rng.next() < 0.7,
      );
    case "FSK":
      return fskSource(rng, offsetHz);
    case "DIGITAL":
      return digitalSource(rng, offsetHz, rng.logRange(6000, 180000));
    case "NOISE":
      // Only drifting spurs. Reusing the digital generator here would label
      // identical waveforms DIGITAL or NOISE purely by their strength, which
      // teaches the model nothing but confusion between the two.
      return spurSource(rng, offsetHz);
  }
}

/**
 * Peak SNR ranges per class, in dB. NOISE overlaps the others deliberately:
 * it should be recognised by its drifting, structureless character rather than
 * by being weak, otherwise every faint real signal gets dismissed as noise.
 */
function snrRange(cls: SignalClass, rng: Rng): number {
  if (cls === "NOISE") return rng.range(3, 20);
  return rng.range(9, 46);
}

const window = makeBlackmanWindow(FFT_SIZE);

/**
 * Produces a run of consecutive spectrum frames for one synthetic emitter,
 * including the multi-frame time gaps a live receiver would have between
 * spectrum updates.
 */
export function synthesize(cls: SignalClass, rng: Rng, frameCount: number): SynthSample {
  // Keep signals clear of the band edges so their skirts stay visible.
  const maxOffset = SAMPLE_RATE * 0.36;
  let offsetHz = rng.range(-maxOffset, maxOffset);
  // Avoid the centre bin, where a real receiver's DC spike would sit.
  if (Math.abs(offsetHz) < 4 * BIN_HZ) offsetHz += 8 * BIN_HZ * Math.sign(offsetHz || 1);

  // The receiver emits 20 spectrum frames a second; allow some spread so the
  // model tolerates a host that falls behind.
  const frameGap = rng.range(0.04, 0.13);
  const totalSeconds = frameGap * frameCount + 1;
  const source = buildSource(cls, rng, offsetHz, totalSeconds);

  const spreadBins = Math.max(1, source.bandwidthHz / BIN_HZ);
  const targetSnrDb = snrRange(cls, rng);
  // Expected peak bin level for a signal of this RMS spread over `spreadBins`,
  // against the noise floor a given sigma produces. Inverting gives sigma.
  const coherentGainDb = 20 * Math.log10(source.rms * 0.42 * FFT_SIZE);
  const spreadLossDb = 10 * Math.log10(spreadBins);
  const noiseGainDb = 20 * Math.log10(Math.sqrt(FFT_SIZE * 0.3046));
  const sigmaDb = coherentGainDb - spreadLossDb - targetSnrDb - noiseGainDb;
  const sigma = Math.pow(10, sigmaDb / 20);

  const re = new Float32Array(FFT_SIZE);
  const im = new Float32Array(FFT_SIZE);
  const spectrum = new Float32Array(FFT_SIZE);
  const frames: Float32Array[] = [];

  let t = rng.range(0, 5);
  for (let f = 0; f < frameCount; f++) {
    source.render(t, re, im);
    for (let i = 0; i < FFT_SIZE; i++) {
      re[i] += sigma * rng.normal();
      im[i] += sigma * rng.normal();
    }
    spectrumDb(re, im, window, spectrum);
    frames.push(spectrum.slice());
    t += frameGap;
  }

  return { frames, offsetHz, bandwidthHz: source.bandwidthHz };
}

export type SceneSpec = {
  cls: SignalClass;
  offsetHz: number;
  snrDb: number;
};

/**
 * Renders several emitters into one shared spectrum, the way a real band looks.
 * Used by the verification harness to exercise detection and tracking together.
 */
export function synthesizeScene(
  specs: SceneSpec[],
  rng: Rng,
  frameCount: number,
): { frames: Float32Array[]; frameGapMs: number } {
  const frameGap = 0.05;
  const totalSeconds = frameGap * frameCount + 1;
  const sigma = 1;
  const noiseGainDb = 20 * Math.log10(Math.sqrt(FFT_SIZE * 0.3046));
  const floorDb = 20 * Math.log10(sigma) + noiseGainDb;

  const built = specs.map((spec) => {
    const source = buildSource(spec.cls, rng, spec.offsetHz, totalSeconds);
    const spreadBins = Math.max(1, source.bandwidthHz / BIN_HZ);
    const gainDb =
      spec.snrDb +
      floorDb -
      20 * Math.log10(source.rms * 0.42 * FFT_SIZE) +
      10 * Math.log10(spreadBins);
    return { source, amplitude: Math.pow(10, gainDb / 20) };
  });

  const re = new Float32Array(FFT_SIZE);
  const im = new Float32Array(FFT_SIZE);
  const partRe = new Float32Array(FFT_SIZE);
  const partIm = new Float32Array(FFT_SIZE);
  const spectrum = new Float32Array(FFT_SIZE);
  const frames: Float32Array[] = [];

  let t = rng.range(0, 3);
  for (let f = 0; f < frameCount; f++) {
    re.fill(0);
    im.fill(0);
    for (const { source, amplitude } of built) {
      source.render(t, partRe, partIm);
      for (let i = 0; i < FFT_SIZE; i++) {
        re[i] += amplitude * partRe[i];
        im[i] += amplitude * partIm[i];
      }
    }
    for (let i = 0; i < FFT_SIZE; i++) {
      re[i] += sigma * rng.normal();
      im[i] += sigma * rng.normal();
    }
    spectrumDb(re, im, window, spectrum);
    frames.push(spectrum.slice());
    t += frameGap;
  }

  return { frames, frameGapMs: frameGap * 1000 };
}

/** A frame of pure receiver noise, used to teach the detector's false positives. */
export function synthesizeNoiseFloor(rng: Rng, frameCount: number): SynthSample {
  const sigma = rng.logRange(0.2, 6);
  const re = new Float32Array(FFT_SIZE);
  const im = new Float32Array(FFT_SIZE);
  const spectrum = new Float32Array(FFT_SIZE);
  const frames: Float32Array[] = [];
  for (let f = 0; f < frameCount; f++) {
    for (let i = 0; i < FFT_SIZE; i++) {
      re[i] = sigma * rng.normal();
      im[i] = sigma * rng.normal();
    }
    spectrumDb(re, im, window, spectrum);
    frames.push(spectrum.slice());
  }
  return { frames, offsetHz: 0, bandwidthHz: BIN_HZ };
}
