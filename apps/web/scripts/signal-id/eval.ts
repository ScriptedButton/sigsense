/**
 * Reproducible evaluation of the signal identification pipeline under receiver
 * impairments. Held-out synthetic emitters (seed differs from training) are
 * degraded the way real captures differ from the clean generator, then run
 * through the real SignalIdEngine under several configurations.
 *
 *   pnpm tsx scripts/signal-id/eval.ts [--per-class=24] [--seed=424242]
 *                                       [--configs=baseline,new] [--json=out.json]
 *
 * This measures robustness to *modelled* impairments. It is not accuracy on
 * real over-the-air signals, which needs recorded captures.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { SignalIdEngine, type SignalIdOptions } from "../../src/lib/sdr/signal-id/engine";
import type { ModelWeights } from "../../src/lib/sdr/signal-id/mlp";
import { BUNDLED_MODEL } from "../../src/lib/sdr/signal-id/classifier";
import { catalogEntries } from "../../src/lib/sdr/signal-id/catalog/match";
import { classPriorFactors } from "../../src/lib/sdr/signal-id/priors";
import { SIGNAL_CLASSES, type SignalClass } from "../../src/lib/sdr/signal-id/types";
import { addDcSpike, addGain, applyDuty, coarsen } from "./impair";
import {
  Rng,
  SAMPLE_RATE,
  synthesize,
  synthesizeNoiseFloor,
  synthesizeScene,
} from "./synth";

const HERE = dirname(fileURLToPath(import.meta.url));
const PREV_PATH = resolve(HERE, "../../src/lib/sdr/signal-id/model-weights.prev.json");
const FRAMES = 18;

function arg(name: string, fallback: string): string {
  const flag = `--${name}=`;
  const found = process.argv.find((a) => a.startsWith(flag));
  return found ? found.slice(flag.length) : fallback;
}

type Frequency = "none" | "informative" | "misleading";
type Case = {
  cls: SignalClass;
  frames: Float32Array[];
  offsetHz: number;
  bandwidthHz: number;
  centerHz: number;
  frequency: Frequency;
};

type Scenario = {
  id: string;
  label: string;
  build(cls: SignalClass, rng: Rng, per: number): Case;
};

const PREV = JSON.parse(readFileSync(PREV_PATH, "utf8")) as ModelWeights;

// --weights=path evaluates a candidate weight file instead of the bundled one.
const CANDIDATE: ModelWeights = process.argv.find((a) => a.startsWith("--weights="))
  ? (JSON.parse(
      readFileSync(process.argv.find((a) => a.startsWith("--weights="))!.slice(10), "utf8"),
    ) as ModelWeights)
  : BUNDLED_MODEL;

const CONFIGS: Record<string, SignalIdOptions> = {
  // What shipped before this work: old weights, uncalibrated, no priors.
  baseline: {
    model: PREV,
    temperature: 1,
    priors: false,
    smoothing: "ema",
    detectionFrames: 1,
  },
  // Ablations, each adding one thing.
  "new-model": { model: CANDIDATE, priors: false, smoothing: "ema" },
  "new-model+log": { model: CANDIDATE, priors: false, smoothing: "log" },
  new: { model: CANDIDATE, priors: true, smoothing: "ema" },
  // What the app now does by default.
  shipped: {},
  "new+log": { model: CANDIDATE, priors: true, smoothing: "log" },
  "new+log b.5": { model: CANDIDATE, priors: true, priorBeta: 0.5, smoothing: "log" },
  "new+log b.25": { model: CANDIDATE, priors: true, priorBeta: 0.25, smoothing: "log" },
};

// --- frequency assignment (for priors) ---------------------------------------

const COVERED = new Map<SignalClass, { lo: number; hi: number }[]>();
const MISLEADING: { lo: number; hi: number; classes: string[] }[] = [];
for (const entry of catalogEntries()) {
  for (const r of entry.frequencies) {
    if (r.hi - r.lo > 50e6 || r.hi - r.lo < 1) continue;
    for (const c of entry.classes) {
      const list = COVERED.get(c as SignalClass) ?? [];
      list.push({ lo: r.lo, hi: r.hi });
      COVERED.set(c as SignalClass, list);
    }
    MISLEADING.push({ lo: r.lo, hi: r.hi, classes: entry.classes });
  }
}

function pickFrequency(cls: SignalClass, mode: Frequency, rng: Rng, bw: number) {
  if (mode === "informative") {
    const ranges = COVERED.get(cls);
    if (ranges?.length) {
      const r = rng.pick(ranges);
      return { hz: rng.range(r.lo, r.hi), mode };
    }
  }
  if (mode === "misleading") {
    for (let t = 0; t < 200; t++) {
      const r = rng.pick(MISLEADING);
      if (!r.classes.includes(cls)) return { hz: rng.range(r.lo, r.hi), mode };
    }
  }
  // Uninformative: a frequency no catalog entry covers.
  for (let t = 0; t < 400; t++) {
    const hz = rng.logRange(5e6, 3e9);
    if (!classPriorFactors(hz, bw)) return { hz, mode: "none" as Frequency };
  }
  return { hz: 1.7e9, mode: "none" as Frequency };
}

function base(cls: SignalClass, rng: Rng) {
  if (cls === "NOISE") {
    const s = synthesizeNoiseFloor(rng, FRAMES);
    return { frames: s.frames, offsetHz: 0, bandwidthHz: s.bandwidthHz };
  }
  const s = synthesize(cls, rng, FRAMES);
  return { frames: s.frames, offsetHz: s.offsetHz, bandwidthHz: s.bandwidthHz };
}

function make(
  cls: SignalClass,
  rng: Rng,
  frames: Float32Array[],
  offsetHz: number,
  bandwidthHz: number,
  freqMode: Frequency = "none",
): Case {
  const f = pickFrequency(cls, freqMode, rng, bandwidthHz);
  return {
    cls,
    frames,
    offsetHz,
    bandwidthHz,
    centerHz: f.hz - offsetHz,
    frequency: f.mode,
  };
}

function coarseScenario(k: number): Scenario {
  return {
    id: `res-${(0.5 * k).toFixed(k >= 2 ? 0 : 1)}k`,
    label: `${(0.5 * k).toFixed(k >= 2 ? 0 : 1)} kHz bins`,
    build(cls, rng) {
      const b = base(cls, rng);
      return make(cls, rng, coarsen(b.frames, k), b.offsetHz, b.bandwidthHz);
    },
  };
}

const SCENARIOS: Scenario[] = [
  {
    id: "clean",
    label: "clean (training conditions)",
    build(cls, rng) {
      const b = base(cls, rng);
      return make(cls, rng, b.frames, b.offsetHz, b.bandwidthHz);
    },
  },
  coarseScenario(4),
  coarseScenario(16),
  coarseScenario(32),
  {
    id: "dc",
    label: "DC spike + gain offset",
    build(cls, rng) {
      const b = base(cls, rng);
      const f = addGain(addDcSpike(b.frames, rng), rng.range(-20, 15));
      return make(cls, rng, f, b.offsetHz, b.bandwidthHz);
    },
  },
  {
    id: "burst",
    label: "bursty (PTT duty 35-75%)",
    build(cls, rng) {
      const b = base(cls, rng);
      return make(cls, rng, applyDuty(b.frames, rng), b.offsetHz, b.bandwidthHz);
    },
  },
  {
    id: "interferer",
    label: "adjacent interferer",
    build(cls, rng) {
      if (cls === "NOISE") {
        const b = base(cls, rng);
        return make(cls, rng, b.frames, b.offsetHz, b.bandwidthHz);
      }
      const offset = rng.range(-0.3, 0.3) * SAMPLE_RATE;
      const gap = rng.range(60e3, 180e3) * (rng.next() < 0.5 ? -1 : 1);
      const others = SIGNAL_CLASSES.filter((c) => c !== "NOISE" && c !== cls);
      const scene = synthesizeScene(
        [
          { cls, offsetHz: offset, snrDb: rng.range(14, 28) },
          { cls: rng.pick(others), offsetHz: offset + gap, snrDb: rng.range(12, 26) },
        ],
        rng,
        FRAMES,
      );
      return make(cls, rng, scene.frames, offset, 15e3);
    },
  },
  {
    id: "hard",
    label: "combined: 4 kHz bins + DC + bursty + gain",
    build(cls, rng) {
      const b = base(cls, rng);
      let f = coarsen(b.frames, 8);
      f = addDcSpike(f, rng);
      f = applyDuty(f, rng);
      f = addGain(f, rng.range(-15, 10));
      return make(cls, rng, f, b.offsetHz, b.bandwidthHz);
    },
  },
  ...(["informative", "none", "misleading"] as const).map(
    (mode): Scenario => ({
      id: `prior-${mode}`,
      label: `4 kHz bins, catalog prior ${mode}`,
      build(cls, rng) {
        const b = base(cls, rng);
        return make(cls, rng, coarsen(b.frames, 8), b.offsetHz, b.bandwidthHz, mode);
      },
    }),
  ),
];

// --- running ------------------------------------------------------------------

type Outcome = {
  detected: boolean;
  correct: boolean;
  confidence: number;
  /** Non-NOISE tracks reported on a pure-noise case. */
  falseTracks?: number;
};

function runEngine(options: SignalIdOptions, c: Case): Outcome {
  const engine = new SignalIdEngine(options);
  engine.setEnabled(true);
  let now = 1_000_000;
  const realNow = Date.now;
  Date.now = () => now;
  try {
    for (const frame of c.frames) {
      engine.pushSpectrum(frame, c.centerHz, SAMPLE_RATE, c.centerHz);
      now += 500;
    }
  } finally {
    Date.now = realNow;
  }
  const signals = engine.getState().signals;
  if (c.cls === "NOISE") {
    // Nothing confidently identified is the right answer for pure noise.
    const wrong = signals.filter((s) => s.label !== "NOISE");
    return {
      detected: true,
      correct: wrong.length === 0,
      confidence: wrong[0]?.confidence ?? 1,
      falseTracks: wrong.length,
    };
  }
  const tolerance = Math.max(c.bandwidthHz, 20e3);
  let best: (typeof signals)[number] | undefined;
  for (const s of signals) {
    if (Math.abs(s.offsetHz - c.offsetHz) > tolerance) continue;
    if (!best || s.snrDb > best.snrDb) best = s;
  }
  if (!best) return { detected: false, correct: false, confidence: 0 };
  const label = best.label === "SSB" && (c.cls === "USB" || c.cls === "LSB") ? c.cls : best.label;
  return { detected: true, correct: label === c.cls, confidence: best.confidence };
}

type Stats = {
  n: number;
  detected: number;
  correct: number;
  ece: number;
  wrongConf: number;
};

function summarise(outcomes: Outcome[]): Stats {
  const n = outcomes.length;
  const det = outcomes.filter((o) => o.detected);
  const bins = Array.from({ length: 10 }, () => ({ n: 0, conf: 0, acc: 0 }));
  for (const o of det) {
    const b = bins[Math.min(9, Math.floor(o.confidence * 10))];
    b.n++;
    b.conf += o.confidence;
    b.acc += o.correct ? 1 : 0;
  }
  let ece = 0;
  for (const b of bins) if (b.n) ece += (b.n / det.length) * Math.abs(b.conf / b.n - b.acc / b.n);
  const wrong = det.filter((o) => !o.correct);
  return {
    n,
    detected: det.length / n,
    correct: outcomes.filter((o) => o.correct).length / n,
    ece: det.length ? ece : 0,
    wrongConf: wrong.length ? wrong.reduce((s, o) => s + o.confidence, 0) / wrong.length : 0,
  };
}

function main(): void {
  const per = Number(arg("per-class", "24"));
  const seed = Number(arg("seed", "424242"));
  const names = arg("configs", Object.keys(CONFIGS).join(",")).split(",");
  const jsonPath = arg("json", "");
  const rng = new Rng(seed);

  const results: Record<string, Record<string, Stats>> = {};
  for (const name of names) results[name] = {};
  const perClass: Record<string, Record<string, number[]>> = {};
  // Pure-noise cases only: share where the engine wrongly reported a signal.
  const noiseAlarms: Record<string, Record<string, number[]>> = {};
  const noiseTracks: Record<string, Record<string, number>> = {};

  for (const scenario of SCENARIOS) {
    const outcomes: Record<string, Outcome[]> = Object.fromEntries(names.map((n) => [n, []]));
    for (const cls of SIGNAL_CLASSES) {
      for (let i = 0; i < per; i++) {
        const c = scenario.build(cls, rng, per);
        for (const name of names) {
          const o = runEngine(CONFIGS[name], c);
          outcomes[name].push(o);
          const bucket = ((perClass[name] ??= {})[cls] ??= [0, 0]);
          bucket[1]++;
          if (o.correct) bucket[0]++;
          if (cls === "NOISE") {
            const nb = ((noiseAlarms[name] ??= {})[scenario.id] ??= [0, 0]);
            nb[1]++;
            if (!o.correct) nb[0]++;
            const tb = ((noiseTracks[name] ??= {})[scenario.id] ??= 0);
            noiseTracks[name][scenario.id] = tb + (o.falseTracks ?? 0);
          }
        }
      }
    }
    for (const name of names) results[name][scenario.id] = summarise(outcomes[name]);
    process.stderr.write(`  ${scenario.id} done\n`);
  }

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
  console.log(`\nHeld-out synthetic evaluation: ${per}/class × ${SIGNAL_CLASSES.length} classes, seed ${seed}\n`);
  console.log("accuracy (misses count as wrong) | detection rate | ECE (lower is better)\n");
  console.log(["scenario", ...names.flatMap((n) => [`${n}`])].join(" | "));
  console.log(["---", ...names.map(() => "---")].join(" | "));
  for (const s of SCENARIOS) {
    const cells = names.map((n) => {
      const r = results[n][s.id];
      return `${pct(r.correct)} (det ${pct(r.detected)}, ECE ${r.ece.toFixed(2)})`;
    });
    console.log([s.label, ...cells].join(" | "));
  }
  const mean = (n: string, key: keyof Stats) =>
    SCENARIOS.reduce((sum, s) => sum + (results[n][s.id][key] as number), 0) / SCENARIOS.length;
  console.log(
    ["**mean over scenarios**", ...names.map((n) => `${pct(mean(n, "correct"))} (det ${pct(mean(n, "detected"))}, ECE ${mean(n, "ece").toFixed(2)})`)].join(" | "),
  );
  console.log("\nper-class accuracy over all scenarios:");
  console.log(["class", ...names].join(" | "));
  for (const cls of SIGNAL_CLASSES) {
    console.log([cls, ...names.map((n) => pct(perClass[n][cls][0] / perClass[n][cls][1]))].join(" | "));
  }
  console.log("\nmean false tracks per pure-noise spectrum (lower is better):");
  console.log(["scenario", ...names].join(" | "));
  for (const s of SCENARIOS) {
    console.log(
      [s.label, ...names.map((n) => (noiseTracks[n][s.id] / noiseAlarms[n][s.id][1]).toFixed(2))].join(" | "),
    );
  }
  console.log("\nfalse-alarm rate on pure-noise cases (lower is better):");
  console.log(["scenario", ...names].join(" | "));
  for (const s of SCENARIOS) {
    console.log(
      [s.label, ...names.map((n) => pct(noiseAlarms[n][s.id][0] / noiseAlarms[n][s.id][1]))].join(" | "),
    );
  }
  if (jsonPath) writeFileSync(jsonPath, JSON.stringify({ per, seed, results }, null, 2));
}

main();
