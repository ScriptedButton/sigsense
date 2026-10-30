"""Receive-only adaptive HF speech cleanup, live A/B audio, and RF feedback.

Selection compares the SAME observed audio, with a raw-Silero speech mask and
explicit spectral retention constraints. Scores are engineering proxies, not
an intelligibility score or an estimate of ground-truth SNR.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from scipy import signal
from scipy.ndimage import median_filter, uniform_filter1d

from sigsense_api.autofinder import _lock, neural_detector
from sigsense_api.processing_client import LanNeuralWorker, worker_health

router = APIRouter(prefix="/autofinder/cleaner")
RATE = 16000
ROOT = Path(__file__).resolve().parents[3]
MAX_SOURCES = 2
CHANNELS: dict[str, "Channel"] = {}
ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_profile_lock = threading.Lock()


@dataclass(frozen=True)
class Profile:
    name: str
    highpass: int
    lowpass: int
    suppression: float
    neural_mix: float


PROFILES = (
    Profile("Natural", 100, 3600, 0, 0),
    Profile("Balanced", 130, 3300, .65, .35),
    Profile("Crowded band", 180, 2700, .9, .5),
    Profile("Speech isolation", 120, 3400, .4, .85),
    Profile("Light cleanup", 100, 3600, .4, 0),
)


def pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(np.nan_to_num(audio), -.98, .98) * 32767).astype("<i2").tobytes()


class Resampler:
    """Continuous anti-alias filter and fractional clock; arbitrary packet sizes."""
    def __init__(self, rate: int):
        if rate < 8000 or rate > 48000:
            raise ValueError("Expected 8–48 kHz mono PCM16")
        self.rate = rate
        self.sos = signal.butter(10, min(6400, rate * .42), fs=rate, output="sos")
        self.zi = np.zeros((len(self.sos), 2))
        self.position = 0.
        self.seen = 0
        self.previous = 0.

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.rate == RATE:
            return x.copy()
        x, self.zi = signal.sosfilt(self.sos, x, zi=self.zi)
        data = np.r_[self.previous, x]
        start, end = self.seen - 1, self.seen + len(x) - 1
        count = max(0, int(math.floor((end - self.position) / (self.rate / RATE))) + 1)
        positions = self.position + np.arange(count) * self.rate / RATE
        result = np.interp(positions - start, np.arange(len(data)), data)
        self.position += count * self.rate / RATE
        self.seen += len(x)
        self.previous = float(x[-1]) if len(x) else self.previous
        return result.astype(np.float32)


class SpectralReducer:
    """Causal sqrt-Hann overlap/add Wiener reduction with bounded attenuation."""
    N = 512
    HOP = 256

    def __init__(self, strength: float):
        self.strength = strength
        self.window = np.sqrt(signal.windows.hann(self.N, sym=False))
        self.pending = np.empty(0)
        self.queue = np.zeros(self.N)
        self.overlap = np.zeros(self.HOP)
        self.powers: deque[np.ndarray] = deque(maxlen=96)
        self.noise = None
        self.gain = np.ones(self.N // 2 + 1)
        self.frames = 0

    def process(self, x: np.ndarray) -> np.ndarray:
        self.pending = np.r_[self.pending, x]
        output = []
        while len(self.pending) >= self.N:
            spectrum = np.fft.rfft(self.pending[:self.N] * self.window)
            power = np.abs(spectrum) ** 2 + 1e-15
            self.powers.append(power)
            if self.noise is None:
                self.noise = power * .03
            if self.frames % 12 == 0 and len(self.powers) >= 24:
                # A low percentile across time limits contamination by brief speech peaks.
                estimate = np.quantile(np.asarray(self.powers), .15, axis=0)
                self.noise = .85 * self.noise + .15 * estimate
            target = np.sqrt(np.maximum(.12, 1 - self.strength * self.noise / power))
            target = uniform_filter1d(target, size=3, mode="nearest")
            self.gain = .7 * self.gain + .3 * target if self.strength else np.ones_like(target)
            frame = np.fft.irfft(spectrum * self.gain) * self.window
            output.append(frame[:self.HOP] + self.overlap)
            self.overlap = frame[self.HOP:]
            self.pending = self.pending[self.HOP:]
            self.frames += 1
        if output:
            self.queue = np.r_[self.queue, *output]
        result = self.queue[:len(x)].copy()
        self.queue = self.queue[len(x):]
        if len(result) != len(x):
            raise RuntimeError("Spectral reconstruction lost its sample clock")
        return result


class Processor:
    TAPS = 1025
    LATENCY = SpectralReducer.N + (TAPS - 1) // 2

    def __init__(self, profile: Profile, notches: tuple[float, ...] = ()):
        self.profile = profile
        self.reducer = SpectralReducer(profile.suppression)
        # Identical linear-phase latency for every candidate keeps A/B and neural
        # blending aligned and avoids frequency-dependent phase distortion.
        frequencies = np.linspace(0, RATE / 2, 4097)
        response = np.clip((frequencies - profile.highpass * .55) / (profile.highpass * .45), 0, 1)
        response *= np.clip((profile.lowpass + 200 - frequencies) / 200, 0, 1)
        for hz in notches:
            response *= 1 - .995 * np.exp(-.5 * ((frequencies - hz) / max(15, hz / 70)) ** 2)
        self.kernel = signal.firwin2(self.TAPS, frequencies, response, fs=RATE)
        self.zi = np.zeros(self.TAPS - 1)

    def process(self, raw: np.ndarray, neural: np.ndarray) -> np.ndarray:
        mixed = raw * (1 - self.profile.neural_mix) + neural * self.profile.neural_mix
        filtered, self.zi = signal.lfilter(self.kernel, [1.], mixed, zi=self.zi)
        return self.reducer.process(filtered)


def speech_mask(audio: np.ndarray) -> np.ndarray:
    detect, options = neural_detector()
    with _lock:
        segments = detect(audio.astype(np.float32), vad_options=options(threshold=.55, speech_pad_ms=80))
    mask = np.zeros(len(audio), dtype=bool)
    for segment in segments:
        mask[max(0, int(segment["start"])):min(len(mask), int(segment["end"]))] = True
    return mask


def whistle_frequencies(audio: np.ndarray, mask: np.ndarray) -> tuple[float, ...]:
    """Require a narrow, persistent peak during VAD silence before notching speech-band tones."""
    quiet = audio[~mask]
    if len(quiet) < RATE // 3:
        return ()
    f, p = signal.welch(quiet, RATE, nperseg=2048)
    local = median_filter(p, size=21) + 1e-15
    peaks, _ = signal.find_peaks(10 * np.log10(p / local + 1e-15), height=17, distance=8)
    candidates = [k for k in peaks if 45 <= f[k] <= 3600]
    _, _, frames = signal.stft(quiet, RATE, nperseg=1024, noverlap=512, boundary=None)
    frame_power = abs(frames) ** 2
    frame_floor = median_filter(frame_power, size=(15, 1)) + 1e-15
    candidates = [k for k in candidates if np.mean(frame_power[round(f[k] * 1024 / RATE)] / frame_floor[round(f[k] * 1024 / RATE)] > 12) >= .7]
    candidates.sort(key=lambda k: p[k] / local[k], reverse=True)
    return tuple(float(f[k]) for k in candidates[:3])


def quality(reference: np.ndarray, candidate: np.ndarray, mask: np.ndarray) -> dict:
    """Level-invariant speech spectral retention plus attenuation in observed VAD silence."""
    n = min(len(reference), len(candidate), len(mask))
    reference, candidate, mask = reference[:n], candidate[:n], mask[:n]
    f, _, r = signal.stft(reference, RATE, nperseg=512, noverlap=256, boundary=None)
    _, _, c = signal.stft(candidate, RATE, nperseg=512, noverlap=256, boundary=None)
    starts = np.arange(r.shape[1]) * 256
    voiced = np.array([np.mean(mask[s:s + 512]) > .6 for s in starts])
    voice = (f >= 200) & (f <= 3200)
    if voiced.sum() < 6:
        return {"score": -100., "speechRetention": 0., "noiseReductionDb": 0., "eligible": False}
    a, b = np.abs(r[voice][:, voiced]), np.abs(c[voice][:, voiced])
    gain = math.sqrt(float(np.sum(b * b) / (np.sum(a * a) + 1e-15)))
    fidelity = float(np.sum(a * b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-15))
    high = (f >= 1600) & (f <= 3200)
    high_gain = math.sqrt(float(np.sum(abs(c[high][:, voiced]) ** 2) / (np.sum(abs(r[high][:, voiced]) ** 2) + 1e-15)))
    quiet = ~mask
    enough_quiet = np.sum(quiet) >= RATE // 4
    raw_noise = float(np.mean(reference[quiet] ** 2)) if enough_quiet else 0.
    clean_noise = float(np.mean(candidate[quiet] ** 2)) / max(gain * gain, .01) if enough_quiet else 0.
    reduction = float(np.clip(10 * np.log10((raw_noise + 1e-15) / (clean_noise + 1e-15)), -20, 30)) if enough_quiet else 0.
    eligible = fidelity >= .87 and gain >= .40 and high_gain >= .22 and np.max(abs(candidate), initial=0) <= 1
    # Reductions have a cap: deleting more audio cannot produce an unbounded score.
    score = min(14., reduction) - 30 * (1 - fidelity) - max(0, -.8 - 20 * math.log10(max(gain, 1e-6))) * .2
    return {"score": round(score, 3), "speechRetention": round(fidelity, 4), "noiseReductionDb": round(reduction, 2),
            "eligible": bool(eligible), "quietMeasured": bool(enough_quiet)}


def optimize(raw: np.ndarray, neural: np.ndarray, current: str, neural_available: bool, mask=None, previous_notches=()) -> dict:
    mask = speech_mask(raw) if mask is None else mask
    enough_quiet = np.sum(~mask) >= RATE // 3
    notches = whistle_frequencies(raw, mask) if enough_quiet else previous_notches
    trials = []
    # Every candidate receives the identical source window and neural history.
    delay = Processor.LATENCY
    for profile in PROFILES:
        if profile.neural_mix and not neural_available:
            continue
        output = Processor(profile, notches).process(raw, neural)
        reference = raw[:-delay]
        result = quality(reference[RATE // 2:], output[delay + RATE // 2:], mask[:len(reference)][RATE // 2:])
        trials.append({"profile": profile.name, **result})
    valid = [trial for trial in trials if trial["eligible"]]
    incumbent = next((trial for trial in valid if trial["profile"] == current), None)
    best = max(valid, key=lambda trial: trial["score"]) if valid else None
    # Hysteresis and rollback: continuous rechecks can return to Natural as conditions change.
    chosen = best if best and (incumbent is None or best["score"] > incumbent["score"] + .7) else incumbent
    if incumbent and not incumbent.get("quietMeasured"):
        # Continuous speech is not evidence that an established noise model got
        # worse. Keep it while speech retention remains valid; recheck on silence.
        chosen = incumbent
    return {"profile": chosen["profile"] if chosen else "Natural", "notchesHz": list(notches),
            "metrics": chosen, "trials": trials, "speechRatio": round(float(np.mean(mask)), 4)}


def rf_advice(meta: dict) -> dict:
    """Conservative, receive-only tuning improvements using measured panoramic FFT bins."""
    advice = {}
    clip, gain = meta.get("rfClipFraction", 0), meta.get("gainDb", 0)
    if isinstance(clip, (float, int)) and isinstance(gain, (float, int)) and clip > .005 and gain > 0:
        advice["gainDb"] = max(0, gain - 3)
    spectrum = meta.get("spectrum") or {}
    values = np.asarray(spectrum.get("values", []), dtype=float)
    step, start, frequency = spectrum.get("binHz", 0), spectrum.get("startHz", 0), meta.get("tunedHz", meta.get("frequencyHz", 0))
    if not 8 <= len(values) <= 256 or not np.isfinite(values).all() or not 1 <= step <= 1000:
        return advice
    frequencies = start + np.arange(len(values)) * step
    near = np.flatnonzero(abs(frequencies - frequency) <= 600)
    if not len(near):
        return advice
    k = near[np.argmax(values[near])]
    prominence = values[k] - np.median(values)
    if prominence < 10 or k < 1 or k >= len(values) - 1:
        return advice
    denominator = values[k - 1] - 2 * values[k] + values[k + 1]
    fraction = .5 * (values[k - 1] - values[k + 1]) / denominator if abs(denominator) > 1e-9 else 0
    error = float(frequencies[k] + np.clip(fraction, -.5, .5) * step - frequency)
    if 20 <= abs(error) <= 250:
        advice["offsetCorrectionHz"] = round(error)
    neighbors = (abs(frequencies - frequencies[k]) >= 3800) & (abs(frequencies - frequencies[k]) <= 7500)
    if np.any(neighbors) and np.max(values[neighbors]) >= values[k] - 10 and meta.get("bandwidthHz", 6000) > 4000:
        advice["bandwidthHz"] = 4000
    return advice


class LiveCleaner:
    def __init__(self, profile="Natural", neural_delay=480):
        self.previous_profile = profile
        # Reception may have changed since a saved win. Revalidate stronger
        # processing on this signal before applying it to a new stream.
        self.profile = PROFILES[0]
        self.processor = Processor(self.profile)
        self.notches: tuple[float, ...] = ()
        self.previous_processor = None
        self.fade_left = 0
        self.raw = np.empty(0, dtype=np.float32)
        self.neural = np.empty(0, dtype=np.float32)
        self.neural_delay = np.zeros(neural_delay, dtype=np.float32)
        self.original_delay = np.zeros(Processor.LATENCY, dtype=np.float32)
        self.evaluated_at = 0
        self.total = 0
        self.iterations = 0
        self.level = 1.
        self.report: dict = {"profile": self.profile.name, "iterations": 0, "notchesHz": [], "metrics": None, "trials": []}

    def align(self, raw: np.ndarray) -> np.ndarray:
        delayed = np.r_[self.neural_delay, raw]
        self.neural_delay = delayed[len(raw):]
        return delayed[:len(raw)].astype(np.float32)

    def process(self, raw: np.ndarray, neural: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        self.total += len(raw)
        self.raw = np.r_[self.raw, raw][-RATE * 6:]
        self.neural = np.r_[self.neural, neural][-RATE * 6:]
        output = self.processor.process(raw, neural)
        if self.previous_processor:
            previous = self.previous_processor.process(raw, neural)
            weight = np.clip((np.arange(len(raw)) + RATE // 10 - self.fade_left) / (RATE // 10), 0, 1)
            output = previous * (1 - weight) + output * weight
            self.fade_left -= len(raw)
            if self.fade_left <= 0:
                self.previous_processor = None
        reference = np.r_[self.original_delay, raw]
        self.original_delay = reference[len(raw):]
        reference = reference[:len(raw)]
        # Match output loudness to the received voice. Bounded gain cannot amplify a deep fade without limit.
        target = min(2., max(.5, math.sqrt(float(np.mean(reference ** 2)) / (float(np.mean(output ** 2)) + 1e-12))))
        levels = np.linspace(self.level, .97 * self.level + .03 * target, len(output))
        self.level = float(levels[-1]) if len(levels) else self.level
        output = output * levels
        peak = np.max(abs(output), initial=0)
        if peak > .95:
            output *= .95 / peak
        return output.astype(np.float32), reference.astype(np.float32)

    def due(self):
        return len(self.raw) >= RATE * 4 and self.total - self.evaluated_at >= RATE * 4

    def invalidate_neural(self):
        """Do not carry a winning profile across different models or a failed worker."""
        self.profile = PROFILES[0]
        self.processor = Processor(self.profile, self.notches)
        if len(self.raw):
            self.processor.process(self.raw, self.raw)
        self.previous_processor = None
        self.raw = np.empty(0, dtype=np.float32)
        self.neural = np.empty(0, dtype=np.float32)
        self.report = {"profile": "Natural", "iterations": self.iterations, "notchesHz": list(self.notches), "metrics": None, "trials": []}

    def evaluate(self, neural_available: bool, mask=None):
        result = optimize(self.raw.copy(), self.neural.copy(), self.profile.name, neural_available, mask, self.notches)
        self.evaluated_at = self.total
        self.iterations += 1
        notches = tuple(result["notchesHz"])
        if result["profile"] != self.profile.name or notches != self.notches:
            updated = next(p for p in PROFILES if p.name == result["profile"])
            processor = Processor(updated, notches)
            # Warm filters and noise estimator from recent history before crossfading.
            processor.process(self.raw, self.neural)
            self.previous_processor, self.processor = self.processor, processor
            self.profile, self.notches = updated, notches
            self.fade_left = RATE // 10
        self.report = {**result, "iterations": self.iterations}
        return self.report


class NeuralWorker:
    def __init__(self):
        self.process = None

    async def start(self):
        local_node = Path.home() / ".local/bin/node"
        node = os.environ.get("SIGSENSE_CLEANER_NODE") or shutil.which("node") or (str(local_node) if local_node.exists() else None)
        if not node:
            raise RuntimeError("Node.js is unavailable for local RNNoise")
        self.process = await asyncio.create_subprocess_exec(node, "--import", "tsx", "server/audio-cleaner/rnnoise-worker.ts", cwd=ROOT / "apps/web",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        ready = await asyncio.wait_for(self.process.stdout.readline(), 15)
        if ready != b"READY\n":
            raise RuntimeError("Local RNNoise worker could not load")

    async def apply(self, audio):
        self.process.stdin.write(np.asarray(audio, dtype="<f4").tobytes())
        await asyncio.wait_for(self.process.stdin.drain(), 2)
        data = await asyncio.wait_for(self.process.stdout.readexactly(len(audio) * 4), 3)
        return np.frombuffer(data, dtype="<f4").copy()

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


def saved_profiles() -> tuple[Path, dict]:
    path = Path(os.environ.get("SIGSENSE_CLEANER_DIR", ROOT / ".runtime/audio-cleaner")) / "profiles.json"
    try:
        data = json.loads(path.read_text())
        return path, data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return path, {}


def save_profile(frequency: int, report: dict):
    with _profile_lock:
        _save_profile(frequency, report)


def _save_profile(frequency: int, report: dict):
    path, data = saved_profiles()
    data[str(round(frequency / 1000) * 1000)] = {"profile": report["profile"], "at": time.time(), "metrics": report.get("metrics")}
    data = dict(sorted(data.items(), key=lambda item: item[1].get("at", 0), reverse=True)[:200])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(data, file)
    temporary.replace(path)


class Channel:
    def __init__(self, stream_id: str, meta: dict):
        self.id, self.meta = stream_id, meta
        self.listeners: set[asyncio.Queue] = set()
        self.status = {"status": "starting", "streamId": stream_id, "frequencyHz": meta["frequencyHz"], "profile": "Natural", "iterations": 0}
        self.last_frame = time.monotonic()

    def broadcast(self, message):
        self.last_frame = time.monotonic()
        for queue in self.listeners:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(message)


def validate_meta(meta: dict):
    if not isinstance(meta, dict) or not isinstance(meta.get("frequencyHz"), (int, float)) or not math.isfinite(meta["frequencyHz"]) or not 2_300_000 <= meta["frequencyHz"] <= 30_000_000:
        raise ValueError("Expected a shortwave frequency between 2.3 and 30 MHz")
    if meta.get("source") not in ("rtltcp", "device", "demo"):
        raise ValueError("Expected a supported receiver source")
    rate = meta.get("sampleRate")
    if not isinstance(rate, int) or not 8000 <= rate <= 48000:
        raise ValueError("Expected an 8–48 kHz audio sample rate")


def repair_impulses(block: np.ndarray) -> np.ndarray:
    median = median_filter(block, size=5, mode="nearest")
    scale = np.maximum(median_filter(abs(block), size=31), .005)
    return np.where(abs(block - median) > 8 * scale, median, block)


@router.get("/worker")
async def processing_worker_status():
    return await worker_health()


@router.get("/status/{stream_id}")
def cleaner_status(stream_id: str):
    channel = CHANNELS.get(stream_id)
    if not channel:
        raise HTTPException(404, "Cleaner is not receiving this signal")
    return {**channel.status, "stale": time.monotonic() - channel.last_frame > 5}


@router.websocket("/source/{stream_id}")
async def source(ws: WebSocket, stream_id: str):
    await ws.accept()
    worker = LanNeuralWorker(NeuralWorker())
    channel = None
    try:
        if not ID.fullmatch(stream_id) or stream_id in CHANNELS or len(CHANNELS) >= MAX_SOURCES:
            await ws.close(1008, "Cleaner source limit or invalid stream ID")
            return
        text = await asyncio.wait_for(ws.receive_text(), 10)
        if len(text) > 12000:
            raise ValueError("Cleaner metadata is too large")
        meta = json.loads(text)
        validate_meta(meta)
        if stream_id in CHANNELS or len(CHANNELS) >= MAX_SOURCES:
            await ws.close(1008, "Cleaner source limit")
            return
        channel = Channel(stream_id, meta)
        CHANNELS[stream_id] = channel
        await worker.start()
        neural, warning = worker.available, worker.warning
        _, profiles = saved_profiles()
        saved = profiles.get(str(round(meta["frequencyHz"] / 1000) * 1000), {}) if meta["source"] != "demo" else {}
        cleaner = LiveCleaner(saved.get("profile", "Natural") if neural else "Natural", neural_delay=worker.delay_samples)
        resampler = Resampler(meta["sampleRate"])
        pending = np.empty(0, dtype=np.float32)
        original_delay = np.zeros(worker.delay_samples + Processor.LATENCY, dtype=np.float32)
        worker_generation = worker.generation
        advice_history: deque[dict] = deque(maxlen=3)
        channel.status = {**channel.status, "status": "optimizing", "neuralAvailable": neural, "warning": warning, "processing": worker.status, "sampleRate": RATE, "previousProfile": cleaner.previous_profile}
        await ws.send_json(channel.status)
        last_meta = time.monotonic()
        while True:
            message = await asyncio.wait_for(ws.receive(), 15)
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text") is not None:
                if len(message["text"]) > 12000:
                    raise ValueError("Cleaner metadata is too large")
                update = json.loads(message["text"])
                validate_meta(update)
                if update["frequencyHz"] != meta["frequencyHz"] or update["sampleRate"] != meta["sampleRate"] or update["source"] != meta["source"]:
                    raise ValueError("Retuning requires a fresh cleaner stream")
                channel.meta = update
                advice_history.append(rf_advice(update))
                advice = {}
                if len(advice_history) == 3:
                    for key in ("gainDb", "bandwidthHz", "offsetCorrectionHz"):
                        values = [a.get(key) for a in advice_history]
                        if all(v is not None for v in values) and max(values) - min(values) <= (40 if key == "offsetCorrectionHz" else 0):
                            advice[key] = round(float(np.median(values)))
                if advice:
                    await ws.send_json({"type": "rf", "frequencyHz": meta["frequencyHz"], **advice})
                    advice_history.clear()
                continue
            data = message.get("bytes", b"")
            if not data or len(data) % 2 or len(data) > 48000:
                raise ValueError("Expected a PCM16 packet no longer than 500 ms")
            raw = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
            pending = np.r_[pending, resampler.process(raw)]
            while len(pending) >= 1600:
                block, pending = pending[:1600], pending[1600:]
                # Repair only isolated impulsive outliers, retaining normal modulation transients.
                working = repair_impulses(block)
                isolated = await worker.apply(working)
                neural = worker.available
                if worker_generation != worker.generation:
                    worker_generation = worker.generation
                    cleaner.invalidate_neural()
                    channel.status = {**channel.status, **cleaner.report, "neuralAvailable": neural,
                                      "warning": worker.warning, "processing": worker.status, "updatedAt": time.time()}
                    await ws.send_json(channel.status)
                    channel.broadcast(channel.status)
                delayed = cleaner.align(working)
                output, _ = cleaner.process(delayed, isolated)
                original = np.r_[original_delay, block]
                original_delay = original[len(block):]
                original = original[:len(block)]
                channel.broadcast({"type": "audio", "sampleRate": RATE, "raw": pcm16(original), "clean": pcm16(output)})
                await asyncio.wait_for(ws.send_bytes(pcm16(output)), 2)
                if cleaner.due():
                    report = await asyncio.to_thread(cleaner.evaluate, neural)
                    channel.status = {**channel.status, **report, "status": "optimizing", "updatedAt": time.time(), "frequencyHz": meta["frequencyHz"]}
                    await ws.send_json(channel.status)
                    channel.broadcast(channel.status)
                    if meta["source"] != "demo" and (report.get("metrics") or {}).get("eligible"):
                        await asyncio.to_thread(save_profile, int(meta["frequencyHz"]), report)
                if time.monotonic() - last_meta >= 2:
                    last_meta = time.monotonic()
                    channel.status = {**channel.status, "updatedAt": time.time(), "neuralAvailable": neural, "warning": worker.warning, "processing": worker.status}
                    await ws.send_json(channel.status)
                    channel.broadcast(channel.status)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as exc:
        try:
            await ws.send_json({"status": "error", "message": str(exc)})
            await ws.close(1011)
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        if channel:
            channel.broadcast({"status": "stopped", "streamId": stream_id})
            CHANNELS.pop(stream_id, None)
        await worker.close()


@router.websocket("/listen/{stream_id}")
async def listen(ws: WebSocket, stream_id: str):
    await ws.accept()
    channel = CHANNELS.get(stream_id)
    if not channel or len(channel.listeners) >= 8:
        await ws.close(1008, "Cleaner stream unavailable")
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=3)
    channel.listeners.add(queue)
    variant = ws.query_params.get("stage", "clean")
    receive = asyncio.create_task(ws.receive())
    pending = None
    try:
        await ws.send_json(channel.status)
        while True:
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait([receive, pending], timeout=15, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            if receive in done:
                if receive.result()["type"] == "websocket.disconnect":
                    break
                receive = asyncio.create_task(ws.receive())
                if pending not in done:
                    pending.cancel()
                    continue
            message = pending.result()
            if message.get("type") == "audio":
                await asyncio.wait_for(ws.send_bytes(message["raw" if variant == "raw" else "clean"]), 2)
            else:
                await ws.send_json(message)
                if message.get("status") == "stopped":
                    break
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
        pass
    finally:
        channel.listeners.discard(queue)
        receive.cancel()
        if pending:
            pending.cancel()
        await asyncio.gather(receive, *([pending] if pending else []), return_exceptions=True)
