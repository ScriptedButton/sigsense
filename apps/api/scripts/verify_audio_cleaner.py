"""Controlled speech/noise replay through the actual streaming backend and local RNNoise."""
import asyncio
import json
import time
import wave
from pathlib import Path

import numpy as np
from scipy import signal

from sigsense_api.audio_cleaner import RATE, ROOT, LiveCleaner, NeuralWorker, pcm16, repair_impulses


def sisdr(reference, observed):
    reference = reference - np.mean(reference)
    observed = observed - np.mean(observed)
    target = reference * np.dot(reference, observed) / max(np.dot(reference, reference), 1e-15)
    return float(10 * np.log10(np.dot(target, target) / max(np.sum((observed - target) ** 2), 1e-15)))


async def main():
    path = ROOT / "data/processed/audio-enhancement/clean.f32"
    if not path.exists():
        raise SystemExit("First prepare the speech fixture described in docs/hf-audio-processing.md")
    speech = signal.resample_poly(np.fromfile(path, dtype="<f4")[:48000 * 8], 1, 3)
    speech *= .18 / max(np.max(abs(speech)), 1e-8)
    reference = np.r_[np.zeros(RATE), speech, np.zeros(RATE)].astype(np.float32)
    t = np.arange(len(reference)) / RATE
    reference *= .6 + .4 * np.cos(2 * np.pi * .15 * t) ** 2
    rng = np.random.default_rng(200)
    raw = reference + rng.normal(0, .012, len(reference)) + .015 * np.sin(2 * np.pi * 50 * t) + .008 * np.sin(2 * np.pi * 1520 * t)
    raw[np.arange(7000, len(raw), 7531)] += .6
    cleaner, worker = LiveCleaner(), NeuralWorker()
    reports, cleaned = [], []
    await worker.start()
    started = time.perf_counter()
    try:
        for at in range(0, len(raw), 1600):
            block = raw[at:at + 1600]
            working = repair_impulses(block)
            neural = await worker.apply(working)
            delayed = cleaner.align(working)
            output, _ = cleaner.process(delayed, neural)
            cleaned.append(output)
            if cleaner.due():
                reports.append(await asyncio.to_thread(cleaner.evaluate, True))
    finally:
        await worker.close()
    elapsed = time.perf_counter() - started
    output = np.concatenate(cleaned)
    correlation = signal.correlate(output, reference, method="fft")
    lags = signal.correlation_lags(len(output), len(reference))
    valid = (lags >= 0) & (lags < 1600)
    lag = int(lags[valid][np.argmax(correlation[valid])])
    # Omit startup and compare the same source samples, compensating measured DSP latency.
    begin, end = RATE, len(reference) - lag
    original = raw[begin:end]
    aligned = output[begin + lag:end + lag]
    wanted = reference[begin:end]
    report = {"fixture": "Known speech plus seeded hiss, 50 Hz hum, whistle, impulses and amplitude fading; not a live RF reception",
        "durationSeconds": len(raw) / RATE, "processingSeconds": elapsed, "realTimeFactor": elapsed / (len(raw) / RATE),
        "latencyMs": lag * 1000 / RATE, "inputSiSdrDb": sisdr(wanted, original), "outputSiSdrDb": sisdr(wanted, aligned),
        "speechCorrelation": float(np.corrcoef(wanted, aligned)[0, 1]), "iterations": reports}
    directory = ROOT / ".runtime/audio-cleaner-validation"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, data in (("original", raw), ("cleaned", output), ("reference", reference)):
        with wave.open(str(directory / f"{name}.wav"), "wb") as file:
            file.setnchannels(1); file.setsampwidth(2); file.setframerate(RATE); file.writeframes(pcm16(data))
    (directory / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "iterations"}, indent=2))
    print(f"Listen to the paired WAV files under {directory}")
    if report["outputSiSdrDb"] < report["inputSiSdrDb"] + 1:
        raise SystemExit("Cleanup did not meet the controlled-replay improvement threshold")


if __name__ == "__main__":
    asyncio.run(main())
