"""Shared fixtures for the digital-voice engine/API/e2e tests (WP-G). Contains no tests.

* `Collector`: thread-safe sink for engine events and audio frames, with wait helpers.
* `wideband()`: a 48 kHz baseband transmission -> a wideband "HackRF" capture at an offset,
  so the Channelizer path is exercised the way real captures are.
* `FakeDecoder` + `script()`: a scriptable decoder to drive engine policy paths directly
  (encryption leaks, exceptions, stale calls...) without needing real air-interface frames.
"""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any, Callable, Optional

import numpy as np
from scipy import signal as sp_signal

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import (
    Call,
    CallKind,
    EncryptionInfo,
    EncryptionState,
    Modulation,
    Protocol,
    VocoderId,
    VoiceFrame,
    unpack_audio_frame,
)
from sigsense_api.dvoice.protocols.base import ProtocolConfig, ProtocolDecoder
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import modulate

CAPTURE_FS = 2_400_000.0
CAPTURE_CENTER = 462_600_000.0


class Collector:
    """Receives engine events/audio from worker threads."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.audio: list[bytes] = []
        self.sub_audio: list[tuple[str, np.ndarray, int]] = []
        self._lock = threading.Lock()

    def on_event(self, env: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(env)

    def on_audio(self, frame: bytes) -> None:
        with self._lock:
            self.audio.append(frame)

    def on_sub(self, call_id: str, pcm: np.ndarray, rate: int) -> None:
        with self._lock:
            self.sub_audio.append((call_id, pcm, rate))

    def of(self, type_: str) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.events if e["type"] == type_]

    def data(self, type_: str) -> list[dict[str, Any]]:
        return [e["data"] for e in self.of(type_)]

    def errors(self, code: str) -> list[dict[str, Any]]:
        return [d for d in self.data("error") if d["code"] == code]

    def frames(self) -> list[tuple[dict[str, Any], np.ndarray]]:
        with self._lock:
            return [unpack_audio_frame(f) for f in self.audio]

    def pcm_frames(self) -> list[tuple[dict[str, Any], np.ndarray]]:
        return [(h, p) for h, p in self.frames() if p.size]

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 10.0, step: float = 0.02) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(step)
        return predicate()


def drain(engine: Any, timeout: float = 30.0, settle: float = 0.15) -> bool:
    """Wait until every channel queue is empty and stays empty briefly (all fed input processed)."""
    end = time.monotonic() + timeout
    quiet_since: Optional[float] = None
    while time.monotonic() < end:
        status = engine.status()
        busy = any(c["queue_seconds"] > 0.001 for c in status.channels.values())
        if busy:
            quiet_since = None
        else:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= settle:
                return True
        time.sleep(0.01)
    return False


def feed_capture(
    engine: Any,
    wb: np.ndarray,
    fs: float = CAPTURE_FS,
    center: float = CAPTURE_CENTER,
    chunk_s: float = 0.05,
    chunks: Optional[list[int]] = None,
    max_backlog_s: float = 0.25,
) -> None:
    """Feed a wideband capture with backpressure (so a slow test machine doesn't trigger input_overrun)."""
    n = int(fs * chunk_s)
    pos = 0
    sizes = iter(chunks) if chunks else None
    while pos < wb.size:
        step = next(sizes, n) if sizes else n
        while True:
            backlog = max((c["queue_seconds"] for c in engine.status().channels.values()), default=0.0)
            if backlog < max_backlog_s:
                break
            time.sleep(0.005)
        engine.feed_iq(wb[pos : pos + step], fs, center, "*")
        pos += step


def wideband(
    bb48: np.ndarray,
    offset_hz: float,
    fs: float = CAPTURE_FS,
    lead_s: float = 0.0,
    tail_s: float = 0.3,
) -> np.ndarray:
    """48 kHz complex baseband -> capture at `fs`, shifted to `offset_hz` (silence added before/after)."""
    ratio = int(round(fs / 48_000.0))
    assert abs(ratio * 48_000.0 - fs) < 1e-6, "capture rate must be a multiple of 48 kHz"
    padded = np.concatenate(
        [np.zeros(int(lead_s * 48_000), np.complex64), bb48.astype(np.complex64), np.zeros(int(tail_s * 48_000), np.complex64)]
    )
    up = sp_signal.resample_poly(padded, ratio, 1).astype(np.complex64)
    t = np.arange(up.size) / fs
    return (up * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)


def transmit(
    dibits: np.ndarray,
    symbol_rate: float,
    modulation: Modulation,
    snr_db: float = 25.0,
    offset_hz: float = 0.0,
    seed: int = 1,
    lead_s: float = 0.2,
    tail_s: float = 0.4,
    channel_bw_hz: float = 12_500.0,
    fs: float = CAPTURE_FS,
    freq_error_hz: float = 0.0,
    invert: bool = False,
    deviation_hz: Optional[float] = None,
) -> np.ndarray:
    """dibits -> modulated, impaired, wideband capture with the signal at `offset_hz`.

    The lead-in and tail are padded BEFORE the channel impairment, so the silence carries noise
    like any real capture does: a receiver never sees exact digital zeros, and the slicer's level
    tracker (rightly) has nothing to lock to in a block of them.
    """
    bb = modulate(dibits, symbol_rate, modulation, 48_000.0, deviation_hz)
    padded = np.concatenate(
        [np.zeros(int(lead_s * 48_000), np.complex64), bb, np.zeros(int(tail_s * 48_000), np.complex64)]
    )
    # the noise level is set by the signal power, so measure it over the signal alone
    sig_power = float(np.mean(np.abs(bb) ** 2))
    noisy = impair(
        padded,
        np.inf,
        freq_offset_hz=freq_error_hz,
        invert=invert,
        seed=seed,
    )
    rng = np.random.default_rng(seed + 1000)
    noise_power = sig_power / (10.0 ** (snr_db / 10.0)) * (48_000.0 / channel_bw_hz)
    sigma = np.sqrt(noise_power / 2.0)
    noisy = noisy + (sigma * (rng.standard_normal(noisy.size) + 1j * rng.standard_normal(noisy.size))).astype(np.complex64)
    return wideband(noisy, offset_hz, fs=fs, lead_s=0.0, tail_s=0.0)


# ------------------------------------------------------------------------- scriptable decoder


class FakeDecoder(ProtocolDecoder):
    """Runs `script(decoder, n)` on every fed block (n = block count), emitting whatever the test wants."""

    protocol = Protocol.DMR  # a label only; tests register it via engine.DECODER_OVERRIDES["dmr"]
    symbol_rate = 4800.0
    modulation = Modulation.FSK4
    sync_specs = ("dmr_bs_voice",)
    timeslots = 1
    script: Callable[["FakeDecoder", int], None] = staticmethod(lambda d, n: None)  # type: ignore[assignment]

    def __init__(self, emit, vocoders, config: Optional[ProtocolConfig] = None) -> None:
        super().__init__(emit, vocoders, config)
        self.blocks = 0
        self.resets = 0
        self.symbols = 0

    def feed(self, block: SymbolBlock) -> None:
        self.blocks += 1
        self.symbols += int(block.dibits.size)
        type(self).script(self, self.blocks)

    def reset(self) -> None:
        self.resets += 1

    def stats(self) -> dict[str, Any]:
        return {"frames_ok": self.blocks}


def make_call(
    call_id: str = "fake-ch0-x-1",
    *,
    target: int = 100,
    source: int = 4321,
    encrypted: bool = False,
    emergency: bool = False,
    slot: Optional[int] = None,
    ended: bool = False,
) -> Call:
    enc = EncryptionInfo(
        state=EncryptionState.ENCRYPTED if encrypted else EncryptionState.CLEAR,
        algorithm_id=0x84 if encrypted else 0x80,
        key_id=7 if encrypted else None,
        mi_present=encrypted,
    )
    return Call(
        call_id=call_id,
        protocol=Protocol.DMR,
        slot=slot,
        kind=CallKind.GROUP,
        target_id=target,
        source_id=source,
        started_at=time.time(),
        updated_at=time.time(),
        ended_at=time.time() if ended else None,
        encryption=enc,
        emergency=emergency,
        end_reason="terminator" if ended else None,
    )


def tone_frame(call_id: str, seq: int, freq: float = 440.0) -> VoiceFrame:
    t = (np.arange(160) + seq * 160) / 8000.0
    pcm = (8000 * np.sin(2 * np.pi * freq * t)).astype(np.int16)
    return VoiceFrame(call_id, seq, VocoderId.AMBE_PLUS2_3600X2450, pcm, 8000, 0, time.time())


def noise_iq16(seconds: float, seed: int = 0, level: float = 0.2) -> bytes:
    """48 kHz IQ16 noise payload (drives a forced-protocol channel so a fake decoder gets blocks)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * 48_000)
    iq = (rng.standard_normal((n, 2)) * level * 12000).clip(-32768, 32767).astype("<i2")
    return iq.tobytes()


def feed_noise(engine: Any, seconds: float, channel: str = "ch0", chunk_s: float = 0.04, seed: int = 0, pace: bool = True) -> None:
    """Feed 48 kHz IQ16 noise in small chunks: the engine hands the decoder one block per chunk."""
    from sigsense_api.dvoice.engine import INPUT_IQ16_48K

    n = max(1, int(seconds / chunk_s))
    for i in range(n):
        if pace:
            while max((c["queue_seconds"] for c in engine.status().channels.values()), default=0.0) > 0.2:
                time.sleep(0.003)
        engine.feed_upload(INPUT_IQ16_48K, noise_iq16(chunk_s, seed=seed + i), channel)


class EngineTestCase(unittest.TestCase):
    """Starts/stops an engine around each test with a Collector wired in."""

    def make_engine(self, channels=None, **overrides):
        from sigsense_api.dvoice.engine import ChannelConfig, DigitalVoiceEngine, EngineConfig

        cfg = EngineConfig(channels=channels or [ChannelConfig(name="ch0", protocol="auto")], **overrides)
        self.collector = Collector()
        self.engine = DigitalVoiceEngine(cfg, self.collector.on_event, self.collector.on_audio)
        self.unsub = self.engine.subscribe_audio(self.collector.on_sub)
        self.engine.start()
        self.addCleanup(self.engine.close)
        return self.engine
