"""Receive-only, reference-guided LOCAL audio suppression, never remote substitution.

Independent Kiwi clocks/network delays rule out RF phase cancellation. We align
demodulated audio, require a unique broadband match, track clock drift, then use
cross-spectral coherence to attenuate local-only energy. Remote PCM is NEVER
mixed into the output. On any loss of evidence the output is delayed local PCM.
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections import deque

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from scipy import signal

router = APIRouter(prefix="/kiwi-diversity")
RATE = 16000
HOP = 512
HISTORY = RATE * 12
MAX_SESSIONS = 2
_sessions: set[object] = set()


class Resampler:
    """Continuous anti-aliasing and fractional clock, independent of neural/LAN runtimes."""
    def __init__(self, rate: int):
        if type(rate) is not int or not 8000 <= rate <= 48000:
            raise ValueError("Expected 8–48 kHz mono PCM16")
        self.rate = rate
        self.sos = signal.butter(10, min(6400, rate * .42), fs=rate, output="sos")
        self.zi = np.zeros((len(self.sos), 2))
        self.position = 0.
        self.seen = 0
        self.previous = 0.

    def process(self, samples):
        if not len(samples) or self.rate == RATE:
            return samples.copy()
        filtered, self.zi = signal.sosfilt(self.sos, samples, zi=self.zi)
        data = np.r_[self.previous, filtered]
        start, end = self.seen - 1, self.seen + len(samples) - 1
        step = self.rate / RATE
        count = max(0, int(math.floor((end - self.position) / step)) + 1)
        positions = self.position + np.arange(count) * step
        result = np.interp(positions - start, np.arange(len(data)), data)
        self.position += count * step
        self.seen += len(samples)
        self.previous = float(filtered[-1])
        return result.astype(np.float32)


class History:
    def __init__(self):
        self.data = np.empty(0, dtype=np.float32)
        self.end = 0

    @property
    def start(self):
        return self.end - len(self.data)

    def push(self, samples):
        self.end += len(samples)
        self.data = np.concatenate((self.data, samples))[-HISTORY:]

    def get(self, start, end):
        if start < self.start or end > self.end:
            return None
        return self.data[start - self.start:end - self.start]


def find_alignment(local: np.ndarray, reference: np.ndarray):
    """Return (reference start index, correlation), rejecting tones/ambiguous peaks."""
    if len(reference) < len(local) or len(local) < RATE:
        return None
    # Voice-band pre-emphasis reduces hum/carrier-driven false matches. A match
    # is allowed to be inverted (independent receiver audio polarity).
    sos = signal.butter(4, [200, 1800], btype="bandpass", fs=RATE, output="sos")
    x = signal.sosfilt(sos, local)[::4]
    r = signal.sosfilt(sos, reference)[::4]
    x = np.diff(x)
    r = np.diff(r)
    x -= x.mean()
    power = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    # A pure whistle, silence or carrier is not evidence of the same programme.
    if np.sum(power) < 1e-8 or np.max(power) / np.sum(power) > .18:
        return None
    x_energy = np.dot(x, x)
    corr = signal.correlate(r, x, mode="valid", method="fft")
    energies = signal.convolve(r * r, np.ones(len(x)), mode="valid", method="fft")
    corr = np.abs(corr) / np.sqrt(np.maximum(energies * x_energy, 1e-20))
    best = int(np.argmax(corr))
    confidence = float(min(1, corr[best]))
    alternatives = corr.copy()
    alternatives[max(0, best - 160):best + 161] = 0  # exclude +/-40 ms
    if confidence < .45 or confidence < float(np.max(alternatives)) * 1.15:
        return None
    return best * 4, confidence


class CoherenceFilter:
    """Streaming sqrt-Hann OLA; same delay and gain for local A/B outputs."""
    def __init__(self):
        self.window = np.sqrt(signal.windows.hann(HOP * 2, sym=False))
        self.previous = np.zeros(HOP)
        self.reference = np.zeros(HOP)
        self.overlap = np.zeros(HOP)
        self.raw_overlap = np.zeros(HOP)
        self.px = np.zeros(HOP + 1)
        self.pr = np.zeros(HOP + 1)
        self.cross = np.zeros(HOP + 1, dtype=complex)
        self.gain = np.ones(HOP + 1)
        self.frames = 0
        self.reduction_db = 0.

    def process(self, local, reference=None):
        frame = np.r_[self.previous, local] * self.window
        spectrum = np.fft.rfft(frame)
        raw = frame * self.window
        eligible = reference is not None
        if eligible:
            rf = np.fft.rfft(np.r_[self.reference, reference] * self.window)
            self.reference = reference.copy()
            # Never keep suppressing after the remote programme changes/stops.
            band = slice(12, 225)
            instantaneous = abs(np.vdot(spectrum[band], rf[band])) / np.sqrt(
                max(1e-20, np.sum(abs(spectrum[band]) ** 2) * np.sum(abs(rf[band]) ** 2)))
            eligible = instantaneous >= .15
        if eligible:
            alpha = .9
            self.px = alpha * self.px + (1 - alpha) * abs(spectrum) ** 2
            self.pr = alpha * self.pr + (1 - alpha) * abs(rf) ** 2
            self.cross = alpha * self.cross + (1 - alpha) * spectrum * rf.conj()
            self.frames += 1
            coherence = np.clip(abs(self.cross) ** 2 / np.maximum(self.px * self.pr, 1e-20), 0, 1)
            desired = np.maximum(.32, np.minimum(1., np.sqrt(coherence / .65)))
            # Preserve rumble edges/DC; only suppress evidence-backed audio band.
            desired[:5] = 1
            desired[260:] = 1
            if self.frames < 12:
                desired[:] = 1
            self.gain = .65 * self.gain + .35 * desired
        else:
            self.frames = 0
            self.px.fill(0); self.pr.fill(0); self.cross.fill(0)
            self.reference.fill(0)
            self.gain.fill(1)
        cleaned = np.fft.irfft(spectrum * self.gain) * self.window
        output = self.overlap + cleaned[:HOP]
        original = self.raw_overlap + raw[:HOP]
        self.overlap = cleaned[HOP:]
        self.raw_overlap = raw[HOP:]
        self.previous = local.copy()
        self.reduction_db = float(-20 * np.log10(max(.01, np.sqrt(np.mean(self.gain ** 2)))))
        return output, original


class DiversitySession:
    def __init__(self, local_rate: int, reference_rate: int):
        self.resamplers = [Resampler(local_rate), Resampler(reference_rate)]
        self.history = [History(), History()]
        self.filter = CoherenceFilter()
        self.cursor = 0
        self.next_alignment = RATE * 3
        self.matches = deque(maxlen=12)
        self.confidence = 0.
        self.locked = False
        self.offset = 0.
        self.anchor = 0.
        self.drift = 0.
        self.last_seen = [time.monotonic(), time.monotonic()]

    def status(self):
        return {"type": "status", "state": "matched" if self.locked else "local-only",
                "matchConfidence": round(self.confidence, 3),
                "alignmentMs": round(self.offset / RATE * 1000, 2) if self.locked else None,
                "clockDriftPpm": round(self.drift * 1e6, 1),
                "attenuationDb": round(self.filter.reduction_db, 2), "sampleRate": RATE}

    def align(self):
        local, reference = self.history
        end = local.end - RATE  # allow network latency on the reference path
        start = end - int(RATE * 1.5)
        x = local.get(start, end)
        match = find_alignment(x, reference.data) if x is not None else None
        self.confidence = match[1] if match else 0.
        if match is None:
            self.locked = False
            self.matches.clear()
            return
        offset = reference.start + match[0] - start
        if self.matches and abs(offset - self.matches[-1][1]) > RATE * .012:
            self.matches.clear()
        self.matches.append((end, offset))
        self.locked = len(self.matches) >= 3
        self.anchor = float(end)
        self.offset = float(offset)
        self.drift = 0.
        if len(self.matches) >= 5:
            coordinates = np.array(self.matches, dtype=float)
            self.drift, intercept = np.polyfit(coordinates[:, 0] - self.anchor, coordinates[:, 1], 1)
            # A wildly moving lag is not ordinary independent ADC clock drift.
            if abs(self.drift) > .001:
                self.locked = False
                self.matches.clear()
                self.drift = 0.
            else:
                self.offset = float(intercept)

    def feed(self, channel: int, samples: np.ndarray):
        self.last_seen[channel] = time.monotonic()
        self.history[channel].push(self.resamplers[channel].process(samples))
        local, reference = self.history
        if local.end >= self.next_alignment:
            self.align()
            self.next_alignment = local.end + RATE
        if time.monotonic() - min(self.last_seen) > 1.5:
            self.locked = False
            self.matches.clear()
        if self.cursor < local.start:
            self.cursor = local.start
            self.filter = CoherenceFilter()
            self.locked = False
            self.matches.clear()
        output = []
        # Fixed two-second jitter/alignment buffer, bounded independently of
        # websocket packet boundaries. 32ms filter latency is shared by A/B.
        while self.cursor + HOP <= local.end - RATE * 2:
            raw = local.get(self.cursor, self.cursor + HOP)
            remote = None
            if self.locked:
                indexes = np.arange(self.cursor, self.cursor + HOP, dtype=float)
                positions = indexes + self.offset + self.drift * (indexes - self.anchor)
                if positions[0] >= reference.start and positions[-1] < reference.end - 1:
                    left, right = int(np.floor(positions[0])), int(np.ceil(positions[-1])) + 1
                    segment = reference.get(left, right)
                    remote = np.interp(positions - left, np.arange(len(segment)), segment)
                else:
                    self.locked = False
                    self.matches.clear()
            cleaned, original = self.filter.process(raw, remote)
            output.append((cleaned, original))
            self.cursor += HOP
        return output


@router.websocket("")
async def diversity_socket(websocket: WebSocket):
    await websocket.accept()
    token = object()
    if len(_sessions) >= MAX_SESSIONS:
        await websocket.send_json({"type": "error", "message": "Both Kiwi diversity processing slots are busy."})
        await websocket.close(code=1013)
        return
    _sessions.add(token)
    try:
        message = await asyncio.wait_for(websocket.receive(), 10)
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None or len(text) > 4096:
            raise ValueError("Expected a small JSON configure message")
        config = json.loads(text)
        if not isinstance(config, dict) or config.get("type") != "configure":
            raise ValueError("Send a configure message before PCM")
        rates = [config.get("localRate"), config.get("referenceRate")]
        if any(type(rate) is not int or not 8000 <= rate <= 48000 for rate in rates):
            raise ValueError("Audio rates must be integers between 8000 and 48000 Hz")
        session = DiversitySession(*rates)
        await websocket.send_json(session.status())
        next_status = time.monotonic()
        samples_received = [0, 0]
        started = time.monotonic()
        while True:
            message = await asyncio.wait_for(websocket.receive(), 12)
            if message["type"] == "websocket.disconnect":
                return
            packet = message.get("bytes")
            if packet is None:
                raise ValueError("Send binary PCM after configuration; reconnect to change sample rates")
            if len(packet) < 3 or packet[0] not in (0, 1) or len(packet) % 2 != 1:
                raise ValueError("Expected source byte (0=local, 1=Kiwi) followed by little-endian PCM16")
            channel = packet[0]
            if len(packet) > 1 + rates[channel] * 2:
                raise ValueError("PCM packets may not exceed one second")
            samples = np.frombuffer(packet, dtype="<i2", offset=1).astype(np.float32) / 32768
            samples_received[channel] += len(samples)
            if samples_received[channel] > rates[channel] * (time.monotonic() - started + 8):
                raise ValueError("Audio must be sent at its real-time sample rate")
            output = await asyncio.to_thread(session.feed, channel, samples)
            if output:
                # Interleaved clean/original PCM keeps sample-accurate A/B.
                pair = np.column_stack((np.concatenate([x[0] for x in output]),
                                        np.concatenate([x[1] for x in output])))
                # Full-scale PCM round-trip: no extra gain/limiting in the
                # original or fallback path (saturation only on actual overflow).
                encoded = np.rint(np.clip(np.nan_to_num(pair), -1, 32767 / 32768) * 32768).astype("<i2")
                await websocket.send_bytes(encoded.tobytes())
            if time.monotonic() >= next_status:
                await websocket.send_json(session.status())
                next_status = time.monotonic() + .5
    except (ValueError, TypeError, json.JSONDecodeError, asyncio.TimeoutError) as error:
        try:
            await websocket.send_json({"type": "error", "message": str(error) or "Audio stream timed out"})
            await websocket.close(code=1008)
        except (RuntimeError, WebSocketDisconnect):
            pass
    except WebSocketDisconnect:
        pass
    finally:
        _sessions.discard(token)
