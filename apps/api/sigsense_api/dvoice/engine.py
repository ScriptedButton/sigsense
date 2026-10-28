"""Digital voice engine: orchestration (CONTRACT v1). Owner: WP-G.

Data flow (rates in brackets):

  source IQ [HackRF fs, server]            browser upload [48 kHz cf/disc int16]
        |                                          |
   Channelizer(offset) [-> 48 kHz complex] <-------+   (upload kinds: see INPUT_* below)
        |
   +----+-----------------------------+
   | FmDiscriminator -> FskSymbolRecovery   -> SymbolBlock (4FSK/C4FM/GMSK) |
   | CqpskSymbolRecovery                    -> SymbolBlock (CQPSK)          |
   +----+-----------------------------+
        |
   SyncCorrelator (all enabled protocols, both polarities)  -> SyncHit
        |   auto-detect picks/locks the ProtocolDecoder; manual mode forces one
   ProtocolDecoder.feed(SymbolBlock) -> events (call_*, system_info, voice, trunk, error)
        |                                   \\
   Vocoder (per call/slot) -> VoiceFrame     TrunkController -> TuneRequest -> engine/SDR
        |
   sinks: JSON events (+ binary audio) to clients; CallRecorder; speech-to-text hook

Threading: one `ChannelWorker` thread per channel owns its DSP + decoder state
(no shared mutable state between channels); `feed_*` only enqueues (bounded,
drop-oldest with an `input_overrun` error event). Hot loops use numba when
importable (optional), numpy otherwise; block sizes are >= 20 ms so either works.
Latency budget (HackRF IQ in -> audio frame out): front end <= 40 ms, frame
assembly = protocol frame time (P25 LDU 180 ms, DMR superframe 360 ms/burst 60 ms),
vocoder <= 5 ms, queueing <= 40 ms -> target < 350 ms after the voice frame ends.

SAFETY POLICY (non-negotiable; tests enforce it):
  * clear (unencrypted) voice and signalling only;
  * encryption is DETECTED and REPORTED (algorithm id, key id, MI presence) but never
    defeated: an encrypted call is logged as metadata and produces NO audio, whatever
    a protocol decoder emits (this engine drops the PCM itself as a second line of
    defence), and no WAV is ever recorded for it;
  * there is no key handling, key recovery or brute force anywhere in this package.

Channel selection: ``ChannelConfig.protocol`` is ``"auto"`` or a Protocol value
(``"p25p1"``, ``"dmr"``...). ``"p25p1_cqpsk"`` forces the P25 Phase 1 CQPSK/LSM front end
(plain ``"p25p1"`` is C4FM). When ``frequency_hz`` is set and the capture centre is known,
the channel offset is derived from it (so channels follow a capture retune); otherwise
``offset_hz`` is used.

Upload formats (``feed_upload``): INPUT_IQ16_48K is interleaved int16 I/Q at 48 kHz (full
scale 32768 = 1.0); INPUT_DISC16_48K is int16 FM-discriminator samples at 48 kHz where
32768 = +DISC16_FULL_SCALE_HZ of deviation (so one LSB = 0.49 Hz). Auto-detect needs IQ.
"""

from __future__ import annotations

import collections
import dataclasses
import itertools
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from sigsense_api.dvoice.events import (
    Call,
    CallKind,
    EncryptionState,
    ErrorEvent,
    LinkStats,
    Modulation,
    Protocol,
    Severity,
    SyncEvent,
    Talkgroup,
    VoiceFrame,
    make_event,
    pack_audio_frame,
)
from sigsense_api.dvoice.trunking.base import FollowPolicy, TuneRequest

log = logging.getLogger("sigsense.dvoice")

# Upload formats accepted from browser sources on WS /dvoice/stream?source=upload
INPUT_IQ16_48K = 0x01  # int16 LE interleaved I/Q, 48 kHz complex baseband around the signal
INPUT_DISC16_48K = 0x02  # int16 LE FM-discriminator samples, 48 kHz (FSK-family protocols only; no CQPSK)
INPUT_IQ8_SERVER = 0x03  # reserved: server-side wideband capture (HackRF) is read in-process, not uploaded

DISC16_FULL_SCALE_HZ = 16_000.0  # int16 full scale of INPUT_DISC16_48K, in Hz of deviation

EventSink = Callable[[dict[str, Any]], None]  # receives make_event() envelopes
AudioSink = Callable[[bytes], None]  # receives pack_audio_frame() bytes

LEGAL_DISCLAIMER = (
    "Monitoring unencrypted radio traffic is lawful in many places but not all: check the laws "
    "that apply to you before use. SigSense decodes clear voice and signalling only."
)
ENCRYPTION_POLICY = (
    "Encrypted calls are detected and reported (algorithm and key ids) but never decoded: "
    "no audio is produced or recorded for them, and SigSense has no key handling, key "
    "recovery or brute-force capability."
)

CHANNEL_RATE = 48_000.0
# The 48 kHz stream is re-blocked to this many samples before symbol recovery, so sync-driven
# recalibration (and everything downstream) happens at the same stream positions however the
# caller chunked its input: results are chunk-size independent. 40 ms adds at most 40 ms latency.
BLOCK_SAMPLES = 1920
MAX_CHANNELS = 8  # configured channels
MAX_FOLLOWERS = 4  # hard cap on dynamic voice-follower channels
QUEUE_SECONDS = 0.5  # per-channel input backlog before the oldest input is dropped
LINK_STATS_PERIOD_S = 0.5
CALL_UPDATE_MIN_S = 0.25  # call_update throttle (changes bypass it)
CALL_STALE_S = 10.0  # a call with no decoder activity this long is ended by the engine
LATE_FRAME_S = 3.0  # voice frames for a call that just ended are dropped for this long
AUTO_LOCK_SCORE = 0.55
AUTO_RELOCK_AFTER_S = 6.0  # locked protocol heard no sync for this long -> go back to searching
SYNC_EVENT_MIN_S = 0.2
DECODER_FAILURE_LIMIT = 20  # decoder exceptions inside DECODER_FAILURE_WINDOW_S disable the channel
DECODER_FAILURE_WINDOW_S = 60.0
FOLLOWER_GRACE_S = 3.0  # a new voice follower may take this long to produce its first call

# Soft symbols (SymbolBlock.soft) are handed only to decoders that declare `accepts_soft = True`.
# The front end's soft values are verified consistent with its dibits (sign 100%, outer ~3, inner ~1),
# but a decoder's soft path has to be validated end to end before it is trusted with them: the M17
# decoder's soft sync correlation produced thousands of bogus frame attempts on real front-end output
# while its hard-decision path decoded the same signal cleanly. Everything else gets hard dibits.
# SIGSENSE_DVOICE_SOFT=1 forces soft symbols to every decoder (for experiments); =0 strips them everywhere.
SOFT_OVERRIDE = os.environ.get("SIGSENSE_DVOICE_SOFT", "auto").strip().lower()

# Test hook: protocol value -> decoder class, consulted before the real registry.
DECODER_OVERRIDES: dict[str, type] = {}
# Test hook: Protocol -> trunk controller class.
TRUNK_OVERRIDES: dict[Protocol, type] = {}


@dataclass
class ChannelConfig:
    name: str = "ch0"
    protocol: str = "auto"  # "auto" or a Protocol value
    offset_hz: float = 0.0  # signal offset from the capture centre (server IQ source)
    frequency_hz: Optional[float] = None  # absolute, for labelling and trunk maths
    bandwidth_hz: float = 12_500.0
    invert: Optional[bool] = None  # None = auto polarity


@dataclass
class EngineConfig:
    channels: list[ChannelConfig] = field(default_factory=lambda: [ChannelConfig()])
    protocols: list[Protocol] = field(
        default_factory=lambda: [
            Protocol.P25P1,
            Protocol.P25P2,
            Protocol.DMR,
            Protocol.NXDN48,
            Protocol.NXDN96,
            Protocol.DSTAR,
            Protocol.YSF,
            Protocol.M17,
        ]
    )
    follow: FollowPolicy = field(default_factory=FollowPolicy)
    play_unencrypted_only: bool = True  # always true in v1; encrypted calls produce no audio
    record_dir: Optional[Path] = None  # None = recording off
    talkgroup_labels: dict[int, str] = field(default_factory=dict)
    vocoder_preference: dict[str, str] = field(default_factory=dict)  # VocoderId value -> backend substring
    decode_data: bool = False


@dataclass
class EngineStatus:
    running: bool = False
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {state, protocol, ...}
    vocoders: dict[str, dict[str, Any]] = field(default_factory=dict)  # registry.capabilities()
    contract_version: int = 1


# --------------------------------------------------------------------------- decoder discovery


def _decoder_classes() -> dict[str, type]:
    from sigsense_api.dvoice.protocols import all_decoders

    found = dict(all_decoders())
    found.update(DECODER_OVERRIDES)
    return found


_PROBE_CACHE: dict[str, tuple[float, bool, str]] = {}
_PROBE_TTL_S = 5.0


def probe_decoder(cls: type) -> tuple[bool, str]:
    """Is this decoder class a real implementation (not a contract stub)?"""
    from sigsense_api.dvoice.protocols.base import ProtocolConfig

    try:
        decoder = cls(lambda *_: None, lambda _vid: None, ProtocolConfig())
        decoder.reset()
    except NotImplementedError:
        return False, "decoder not implemented yet"
    except Exception as exc:  # a broken decoder must not break capabilities
        return False, f"decoder failed to start: {type(exc).__name__}: {exc}"
    return True, ""


def protocol_support() -> dict[str, dict[str, Any]]:
    """protocol value -> {implemented, reason, symbol_rate, modulation, timeslots} (cached a few seconds)."""
    now = time.monotonic()
    out: dict[str, dict[str, Any]] = {}
    for value, cls in _decoder_classes().items():
        hit = _PROBE_CACHE.get(value)
        if hit is None or now - hit[0] > _PROBE_TTL_S or value in DECODER_OVERRIDES:
            ok, reason = probe_decoder(cls)
            hit = (now, ok, reason)
            _PROBE_CACHE[value] = hit
        out[value] = {
            "implemented": hit[1],
            "reason": hit[2],
            "symbol_rate": float(getattr(cls, "symbol_rate", 0.0)),
            "modulation": getattr(getattr(cls, "modulation", None), "value", None),
            "timeslots": int(getattr(cls, "timeslots", 1)),
        }
    return out


def _parse_protocol(text: str) -> tuple[Optional[Protocol], Optional[Modulation]]:
    """'auto' -> (None, None); 'p25p1_cqpsk' -> (P25P1, CQPSK); 'dmr' -> (DMR, None)."""
    value = (text or "auto").strip().lower()
    if value == "auto":
        return None, None
    forced_mod = None
    if value.endswith("_cqpsk"):
        value, forced_mod = value[: -len("_cqpsk")], Modulation.CQPSK
    try:
        return Protocol(value), forced_mod
    except ValueError as exc:
        raise ValueError(f"unknown protocol {text!r}") from exc


def validate_config(config: EngineConfig) -> None:
    if not config.channels:
        raise ValueError("at least one channel is required")
    if len(config.channels) > MAX_CHANNELS:
        raise ValueError(f"at most {MAX_CHANNELS} channels")
    names = [c.name for c in config.channels]
    if len(set(names)) != len(names):
        raise ValueError("channel names must be unique")
    for ch in config.channels:
        if not ch.name or len(ch.name) > 40:
            raise ValueError("channel name must be 1-40 characters")
        _parse_protocol(ch.protocol)
        if not (3_000.0 <= ch.bandwidth_hz <= 24_000.0):
            raise ValueError("bandwidth_hz must be between 3 kHz and 24 kHz")
        if not np.isfinite(ch.offset_hz):
            raise ValueError("offset_hz must be finite")


# --------------------------------------------------------------------------- call bookkeeping


@dataclass
class _CallState:
    call: Call
    call_num: int
    channel: str
    last_sent: float = 0.0
    last_snapshot: tuple = ()
    last_activity: float = 0.0
    muted: bool = False  # talkgroup skip/hold: logged, no audio
    audio_frames: int = 0
    vocoder_warned: bool = False
    encrypted_dropped: int = 0


def _snapshot(call: Call) -> tuple:
    return (
        call.source_id,
        call.target_id,
        call.encryption.state,
        call.encryption.algorithm_id,
        call.encryption.key_id,
        call.emergency,
        call.kind,
    )


@dataclass
class _Item:
    kind: int  # INPUT_*
    data: np.ndarray
    sample_rate: float
    center_hz: float
    seconds: float


class _Pipeline:
    """One locked protocol on a channel: recovery -> correlator -> decoder (worker-thread only)."""

    def __init__(self, protocol: Protocol, modulation: Modulation, symbol_rate: float) -> None:
        from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
        from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
        from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
        from sigsense_api.dvoice.sync.correlator import SyncCorrelator
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

        self.protocol = protocol
        self.modulation = modulation
        self.symbol_rate = symbol_rate
        self.decoder: Any = None
        self.decoder_error = ""
        self.inverted = False
        self.is_cqpsk = modulation == Modulation.CQPSK
        if self.is_cqpsk:
            self.disc = None
            self.recovery: Any = CqpskSymbolRecovery(symbol_rate, CHANNEL_RATE)
        else:
            self.disc = FmDiscriminator(CHANNEL_RATE)
            levels = 2 if modulation == Modulation.GMSK else 4
            self.recovery = FskSymbolRecovery(symbol_rate, CHANNEL_RATE, levels, modulation)
        names = self._spec_names(protocol)
        specs = [SYNC_SPECS[n] for n in names if n in SYNC_SPECS]
        self.specs = {s.name: s for s in specs}
        self.correlator = SyncCorrelator(specs) if specs else None
        self.last_sync = time.monotonic()
        self.sync_hits = 0

    @staticmethod
    def _spec_names(protocol: Protocol) -> tuple[str, ...]:
        classes = _decoder_classes()
        cls = classes.get(protocol.value)
        names = tuple(getattr(cls, "sync_specs", ())) if cls is not None else ()
        if not names:
            from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

            names = tuple(n for n, s in SYNC_SPECS.items() if s.protocol == protocol)
        return names

    def reset(self) -> None:
        self.recovery.reset()
        if self.disc is not None:
            self.disc.reset()
        if self.correlator is not None:
            self.correlator.reset()
        self.recovery.set_polarity_inverted(self.inverted)

    def set_inverted(self, inverted: bool) -> None:
        self.inverted = bool(inverted)
        self.recovery.set_polarity_inverted(self.inverted)


class ChannelWorker:
    """Owns the whole receive chain of one channel on its own thread."""

    def __init__(self, engine: "DigitalVoiceEngine", cfg: ChannelConfig, follower_of: Optional[str] = None) -> None:
        self.engine = engine
        self.cfg = cfg
        self.follower_of = follower_of
        self.created = time.monotonic()
        self.last_call_activity = self.created
        self._cond = threading.Condition()
        self._queue: collections.deque[_Item] = collections.deque()
        self._queued_seconds = 0.0
        self._stop = False
        self._gap = False  # input was dropped or discontinuous: resync the DSP before the next block
        self._reconfigure = False
        self.dropped_items = 0
        self.thread = threading.Thread(target=self._run, name=f"dvoice-{cfg.name}", daemon=True)

        # --- state owned by the worker thread ---
        self._channelizer = None
        self._cap_key: Optional[tuple[float, float]] = None
        self._auto = None
        self._pipe: Optional[_Pipeline] = None
        self._forced, self._forced_mod = _parse_protocol(cfg.protocol)
        self._unsupported_warned: set[str] = set()
        self._failures: collections.deque[float] = collections.deque()
        self.failed = False
        self._last_error_at: dict[str, float] = {}
        self._sync_window: collections.deque[tuple[float, int, int]] = collections.deque()  # (t, errors, word symbols)
        self._sync_seconds: collections.deque[float] = collections.deque(maxlen=16)
        self._last_sync_event: dict[str, float] = {}
        self._stream_t = 0.0
        self._pend_iq = np.zeros(0, dtype=np.complex64)
        self._pend_disc = np.zeros(0, dtype=np.float32)
        self._last_stats_at = 0.0
        self._cpu_mark = (time.monotonic(), time.thread_time())
        self.cpu_fraction = 0.0
        self.link = LinkStats(channel=cfg.name)
        self._last_meta: dict[str, Any] = {}
        self._rate_error = ""

    # ------------------------------------------------------------------ queueing (any thread)
    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2.0)

    def set_config(self, cfg: ChannelConfig) -> None:
        with self._cond:
            self.cfg = cfg
            self._reconfigure = True
            self._cond.notify_all()

    def request_gap(self) -> None:
        with self._cond:
            self._gap = True
            self._cond.notify_all()

    def enqueue(self, item: _Item) -> None:
        overrun = False
        with self._cond:
            if self._stop or self.failed:
                return
            self._queue.append(item)
            self._queued_seconds += item.seconds
            while self._queued_seconds > QUEUE_SECONDS and len(self._queue) > 1:
                old = self._queue.popleft()
                self._queued_seconds -= old.seconds
                self.dropped_items += 1
                overrun = True
            if overrun:
                self._gap = True
            self._cond.notify()
        if overrun:
            self._report("input_overrun", Severity.WARNING, "the decoder fell behind; input was dropped", min_interval=2.0)

    @property
    def queue_seconds(self) -> float:
        return max(0.0, self._queued_seconds)

    # ------------------------------------------------------------------ reporting helpers
    def _report(self, code: str, severity: Severity, message: str, detail: Optional[dict] = None, min_interval: float = 5.0) -> None:
        now = time.monotonic()
        if now - self._last_error_at.get(code, -1e9) < min_interval:
            return
        self._last_error_at[code] = now
        self.engine._publish("error", ErrorEvent(code, severity, message, detail or {}), self.cfg.name)

    # ------------------------------------------------------------------ thread main
    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stop:
                    self._cond.wait(0.1)
                    break
                if self._stop:
                    return
                item = self._queue.popleft() if self._queue else None
                if item is not None:
                    self._queued_seconds -= item.seconds
                gap, self._gap = self._gap, False
                reconf, self._reconfigure = self._reconfigure, False
            try:
                if reconf:
                    self._apply_config()
                if gap:
                    self._resync("input_gap")
                if item is not None and not self.failed:
                    self._handle_item(item)
                self._maybe_emit_stats()
            except Exception as exc:  # the channel survives anything a block can do
                log.exception("channel %s: unexpected error", self.cfg.name)
                self._report("channel_error", Severity.ERROR, f"{type(exc).__name__}: {exc}", min_interval=2.0)
                try:
                    self._resync("channel_error")
                except Exception:
                    pass

    # ------------------------------------------------------------------ state changes
    def _apply_config(self) -> None:
        forced, forced_mod = _parse_protocol(self.cfg.protocol)
        changed = (forced, forced_mod) != (self._forced, self._forced_mod)
        self._forced, self._forced_mod = forced, forced_mod
        self._channelizer = None  # bandwidth/offset may have changed
        if changed:
            self._drop_pipeline("protocol_change")
        elif self._pipe is not None and self.cfg.invert is not None:
            self._pipe.set_inverted(bool(self.cfg.invert))

    def _drop_pipeline(self, reason: str) -> None:
        self.engine._end_channel_calls(self.cfg.name, "retune" if reason in ("retune", "protocol_change") else "sync_loss")
        self._pipe = None
        self._auto = None
        self.link = LinkStats(channel=self.cfg.name)

    def _resync(self, reason: str) -> None:
        """A discontinuity: forget partial frames and sync state, keep the protocol lock."""
        self.engine._end_channel_calls(self.cfg.name, "retune" if reason == "retune" else "sync_loss")
        self._pend_iq = np.zeros(0, dtype=np.complex64)
        self._pend_disc = np.zeros(0, dtype=np.float32)
        if self._channelizer is not None:
            self._channelizer.reset()
        if self._auto is not None:
            self._auto.reset()
        if self._pipe is not None:
            self._pipe.reset()
            if self._pipe.decoder is not None:
                try:
                    self._pipe.decoder.reset()
                except NotImplementedError:
                    pass

    # ------------------------------------------------------------------ input handling
    def _effective_offset(self, center_hz: float, fs: float) -> float:
        if self.cfg.frequency_hz is not None and center_hz:
            return float(self.cfg.frequency_hz) - float(center_hz)
        return float(self.cfg.offset_hz)

    def _handle_item(self, item: _Item) -> None:
        t_start = time.thread_time()
        iq48: Optional[np.ndarray] = None
        disc48: Optional[np.ndarray] = None
        if item.kind == INPUT_IQ8_SERVER:
            fs, center = float(item.sample_rate), float(item.center_hz)
            key = (fs, center)
            if key != self._cap_key:
                if self._cap_key is not None:  # retune or new capture: a discontinuity
                    self._resync("retune")
                self._cap_key = key
                self._channelizer = None
            offset = self._effective_offset(center, fs)
            if abs(offset) > 0.45 * fs:
                self._report(
                    "channel_out_of_band",
                    Severity.WARNING,
                    f"{self.cfg.name} sits {offset / 1e3:.1f} kHz from the capture centre, outside the captured span",
                    {"offset_hz": offset, "sample_rate_hz": fs},
                )
                return
            if fs < CHANNEL_RATE:
                self._report("capture_unavailable", Severity.ERROR, f"capture rate {fs:.0f} Hz is below 48 kHz")
                return
            if self._channelizer is None:
                from sigsense_api.dvoice.dsp.channel import Channelizer

                self._channelizer = Channelizer(fs, offset, self.cfg.bandwidth_hz, CHANNEL_RATE)
            elif abs(offset - self._channelizer.offset_hz) > 0.5:
                self._channelizer.set_offset(offset)
            iq48 = self._channelizer.process(item.data)
        elif item.kind == INPUT_IQ16_48K:
            iq48 = item.data
        elif item.kind == INPUT_DISC16_48K:
            disc48 = item.data
        else:
            return
        self._process48(iq48, disc48)
        self._account_cpu(time.thread_time() - t_start)

    def _account_cpu(self, spent: float) -> None:
        now = time.monotonic()
        mark_t, mark_cpu = self._cpu_mark
        if now - mark_t >= 2.0:
            cpu = time.thread_time()
            self.cpu_fraction = (cpu - mark_cpu) / (now - mark_t)
            self._cpu_mark = (now, cpu)

    # ------------------------------------------------------------------ protocol selection
    def _lock(self, protocol: Protocol, modulation: Modulation, symbol_rate: float) -> None:
        pipe = _Pipeline(protocol, modulation, symbol_rate)
        if self.cfg.invert is not None:
            pipe.set_inverted(bool(self.cfg.invert))
        classes = _decoder_classes()
        cls = classes.get(protocol.value)
        enabled = protocol in self.engine.config.protocols
        if cls is None or not enabled:
            reason = "protocol is disabled in the configuration" if cls is not None else "no decoder for this protocol"
            self._unsupported(protocol, reason)
        else:
            ok, reason = probe_decoder(cls) if protocol.value not in DECODER_OVERRIDES else (True, "")
            if not ok:
                self._unsupported(protocol, reason)
            else:
                from sigsense_api.dvoice.protocols.base import ProtocolConfig

                pcfg = ProtocolConfig(
                    channel=self.cfg.name,
                    frequency_hz=self.cfg.frequency_hz,
                    talkgroup_labels=dict(self.engine.config.talkgroup_labels),
                    decode_data=self.engine.config.decode_data,
                    hang_time_s=self.engine.config.follow.hang_time_s,
                )
                emit = self.engine._make_emit(self)
                pipe.decoder = cls(emit, self.engine._vocoder_provider, pcfg)
        self._pipe = pipe
        self._auto = None
        self.link = LinkStats(channel=self.cfg.name, protocol=protocol, state="searching", symbol_rate=symbol_rate)

    def _unsupported(self, protocol: Protocol, reason: str) -> None:
        pipe_reason = f"{protocol.value}: {reason}"
        if protocol.value in self._unsupported_warned:
            return
        self._unsupported_warned.add(protocol.value)
        self._report(
            "unsupported_protocol",
            Severity.WARNING,
            f"{protocol.value} was detected but cannot be decoded here ({reason})",
            {"protocol": protocol.value, "reason": reason},
            min_interval=0.0,
        )
        self._rate_error = pipe_reason

    def _default_modulation(self, protocol: Protocol) -> tuple[Modulation, float]:
        from sigsense_api.dvoice.dsp.autodetect import MODULATION_OF

        cls = _decoder_classes().get(protocol.value)
        if cls is not None:
            return getattr(cls, "modulation", MODULATION_OF.get(protocol, Modulation.C4FM)), float(cls.symbol_rate)
        rates = {Protocol.P25P1: 4800.0, Protocol.P25P2: 6000.0, Protocol.NXDN48: 2400.0}
        return MODULATION_OF.get(protocol, Modulation.C4FM), rates.get(protocol, 4800.0)

    # ------------------------------------------------------------------ the pipeline
    def _process48(self, iq48: Optional[np.ndarray], disc48: Optional[np.ndarray]) -> None:
        """Re-block to BLOCK_SAMPLES (deterministic block boundaries), then run each block."""
        if iq48 is not None:
            self._pend_iq = np.concatenate((self._pend_iq, iq48)) if self._pend_iq.size else iq48
            while self._pend_iq.size >= BLOCK_SAMPLES:
                block, self._pend_iq = self._pend_iq[:BLOCK_SAMPLES], self._pend_iq[BLOCK_SAMPLES:]
                self._process_block48(block, None)
        elif disc48 is not None:
            self._pend_disc = np.concatenate((self._pend_disc, disc48)) if self._pend_disc.size else disc48
            while self._pend_disc.size >= BLOCK_SAMPLES:
                block, self._pend_disc = self._pend_disc[:BLOCK_SAMPLES], self._pend_disc[BLOCK_SAMPLES:]
                self._process_block48(None, block)

    def _process_block48(self, iq48: Optional[np.ndarray], disc48: Optional[np.ndarray]) -> None:
        if self._pipe is None:
            if self._forced is not None:
                modulation, rate = self._default_modulation(self._forced)
                if self._forced_mod is not None:
                    modulation = self._forced_mod
                    if modulation == Modulation.CQPSK and self._forced == Protocol.P25P1:
                        rate = 4800.0
                self._lock(self._forced, modulation, rate)
            else:
                if iq48 is None:
                    self._report("auto_requires_iq", Severity.WARNING, "auto-detect needs IQ input; choose a protocol for discriminator uploads")
                    return
                if self._auto is None:
                    from sigsense_api.dvoice.dsp.autodetect import AutoDetector

                    self._auto = AutoDetector(candidates=list(self.engine.config.protocols))
                hyps = self._auto.feed(iq48)
                self._stream_t += iq48.size / CHANNEL_RATE
                self.link = LinkStats(
                    channel=self.cfg.name,
                    protocol=hyps[0].protocol if hyps else None,
                    state="searching",
                    symbol_rate=hyps[0].symbol_rate if hyps else 0.0,
                )
                if hyps and hyps[0].score >= AUTO_LOCK_SCORE:
                    self._lock(hyps[0].protocol, hyps[0].modulation, hyps[0].symbol_rate)
                return
        pipe = self._pipe
        assert pipe is not None
        if pipe.is_cqpsk:
            if iq48 is None:
                self._report("unsupported_input", Severity.WARNING, "CQPSK protocols need IQ input, not discriminator samples")
                return
            block = pipe.recovery.process(iq48)
        else:
            if disc48 is None:
                disc48 = pipe.disc.process(iq48)
            block = pipe.recovery.process(disc48)
        self._stream_t += (iq48.size if iq48 is not None else disc48.size) / CHANNEL_RATE
        if block.dibits.size:
            self._on_block(pipe, block)
        self._housekeep_lock(pipe)

    def _on_block(self, pipe: _Pipeline, block: Any) -> None:
        now = time.monotonic()
        self._last_meta = dict(block.meta or {})
        if pipe.correlator is not None:
            hits = pipe.correlator.scan(block.dibits, block.first_index)
            flipped = False
            for hit in hits:
                spec = pipe.specs[hit.spec]
                pipe.last_sync = now
                pipe.sync_hits += 1
                self._sync_window.append((now, hit.errors, len(spec.dibits)))
                self._sync_seconds.append(now)
                if hit.inverted and self.cfg.invert is None and not flipped and hit.errors <= max(1, spec.max_errors // 2):
                    # the signal is polarity-inverted: fix the front end and this block
                    pipe.set_inverted(not pipe.inverted)
                    block.dibits = (block.dibits ^ 2).astype(np.uint8)
                    if block.soft is not None:
                        block.soft = -block.soft
                    flipped = True
                elif not hit.inverted and not pipe.is_cqpsk and not flipped:
                    try:
                        pipe.recovery.recalibrate_at(hit.index, np.array(spec.values, dtype=np.uint8))
                    except Exception:
                        pass
                if now - self._last_sync_event.get(hit.spec, -1e9) >= SYNC_EVENT_MIN_S:
                    self._last_sync_event[hit.spec] = now
                    self.engine._publish(
                        "sync",
                        SyncEvent(pipe.protocol, hit.spec, hit.errors, bool(hit.inverted), hit.index),
                        self.cfg.name,
                    )
        if pipe.decoder is not None:
            if block.soft is not None and not _wants_soft(pipe.decoder):
                block.soft = None
            try:
                pipe.decoder.feed(block)
            except NotImplementedError:
                self._unsupported(pipe.protocol, "decoder not implemented yet")
                pipe.decoder = None
            except Exception as exc:
                self._decoder_failed(pipe, exc)

    def _decoder_failed(self, pipe: _Pipeline, exc: Exception) -> None:
        now = time.monotonic()
        self._failures.append(now)
        while self._failures and now - self._failures[0] > DECODER_FAILURE_WINDOW_S:
            self._failures.popleft()
        log.exception("channel %s: %s decoder failed", self.cfg.name, pipe.protocol.value)
        self._report(
            "decoder_error",
            Severity.ERROR,
            f"{pipe.protocol.value} decoder error: {type(exc).__name__}: {exc}",
            {"protocol": pipe.protocol.value},
            min_interval=2.0,
        )
        self.engine._end_channel_calls(self.cfg.name, "sync_loss")
        try:
            pipe.decoder.reset()
        except Exception:
            pass
        if len(self._failures) >= DECODER_FAILURE_LIMIT:
            self.failed = True
            self._report("channel_failed", Severity.ERROR, "too many decoder errors; this channel was stopped", min_interval=0.0)

    def _housekeep_lock(self, pipe: _Pipeline) -> None:
        # Auto mode only: a lock that stops hearing its protocol goes back to searching.
        if self._forced is None and time.monotonic() - pipe.last_sync > AUTO_RELOCK_AFTER_S:
            self._drop_pipeline("sync_loss")

    # ------------------------------------------------------------------ link stats
    def _maybe_emit_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_stats_at < LINK_STATS_PERIOD_S:
            return
        self._last_stats_at = now
        while self._sync_window and now - self._sync_window[0][0] > 10.0:
            self._sync_window.popleft()
        pipe = self._pipe
        link = LinkStats(channel=self.cfg.name)
        if pipe is not None:
            link.protocol = pipe.protocol
            link.symbol_rate = pipe.symbol_rate
            link.polarity_inverted = pipe.inverted
            # Share of the last 10 one-second windows that heard sync.
            bins = {int(t) for t in self._sync_seconds if now - t <= 10.0}
            link.sync_ratio = round(min(1.0, len(bins) / 10.0), 3)
            since_sync = now - pipe.last_sync
            active = self.engine._active_calls(self.cfg.name) > 0
            if active:
                link.state = "voice"
            elif pipe.sync_hits and since_sync < 1.5:
                link.state = "synced"
            elif pipe.sync_hits and since_sync >= 1.5:
                link.state = "lost"
            else:
                link.state = "searching"
            sync_bits = sum(w[2] * 2 for w in self._sync_window)
            sync_errs = sum(w[1] for w in self._sync_window)
            link.ber = round(sync_errs / sync_bits, 5) if sync_bits else None
            if pipe.decoder is not None:
                try:
                    stats = pipe.decoder.stats() or {}
                except Exception:
                    stats = {}
                link.fec_corrected = int(stats.get("fec_corrected", 0))
                link.fec_failed = int(stats.get("fec_failed", 0))
                link.frames_ok = int(stats.get("frames_ok", 0))
                link.frames_bad = int(stats.get("frames_bad", 0))
                if stats.get("ber") is not None:
                    link.ber = float(stats["ber"])
        else:
            link = dataclasses.replace(self.link, channel=self.cfg.name)
        meta = self._last_meta
        link.snr_db = _opt_float(meta.get("snr_db"))
        link.freq_error_hz = _opt_float(meta.get("freq_error_hz"))
        link.level = _opt_float(meta.get("level"))
        self.link = link
        self.engine._publish("link_stats", link, self.cfg.name)

    # ------------------------------------------------------------------ status
    def snapshot(self) -> dict[str, Any]:
        pipe = self._pipe
        decoder_state = "searching"
        if pipe is not None:
            if pipe.decoder is not None:
                decoder_state = "ok"
            else:
                decoder_state = "unavailable"
        elif self._forced is not None:
            decoder_state = "idle"
        from sigsense_api.dvoice.events import to_jsonable

        return {
            "state": self.link.state,
            "protocol": pipe.protocol.value if pipe is not None else (self._forced.value if self._forced else None),
            "mode": "forced" if self._forced is not None else "auto",
            "modulation": pipe.modulation.value if pipe is not None else None,
            "decoder": decoder_state,
            "decoder_note": self._rate_error if decoder_state == "unavailable" else "",
            "offset_hz": self.cfg.offset_hz,
            "frequency_hz": self.cfg.frequency_hz,
            "bandwidth_hz": self.cfg.bandwidth_hz,
            "follower_of": self.follower_of,
            "queue_seconds": round(self.queue_seconds, 3),
            "dropped_items": self.dropped_items,
            "cpu_fraction": round(self.cpu_fraction, 4),
            "failed": self.failed,
            "link": to_jsonable(self.link),
        }


def _wants_soft(decoder: Any) -> bool:
    if SOFT_OVERRIDE in ("1", "on", "true", "yes"):
        return True
    if SOFT_OVERRIDE in ("0", "off", "false", "no"):
        return False
    return bool(getattr(decoder, "accepts_soft", False))


def _opt_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


class DigitalVoiceEngine:
    def __init__(self, config: EngineConfig, on_event: EventSink, on_audio: Optional[AudioSink] = None) -> None:
        validate_config(config)
        self.config = config
        self._on_event = on_event
        self._on_audio = on_audio
        self._lock = threading.RLock()
        self._workers: dict[str, ChannelWorker] = {}
        self._followers: dict[str, dict[str, Any]] = {}
        self._calls: dict[str, _CallState] = {}
        self._recent_ended: collections.OrderedDict[str, float] = collections.OrderedDict()
        self._call_counter = itertools.count(1)
        self._follower_counter = itertools.count(1)
        self._talkgroups: dict[tuple[str, int], Talkgroup] = {}
        self._audio_subs: dict[int, Callable[[str, np.ndarray, int], None]] = {}
        self._audio_sub_ids = itertools.count(1)
        self._trunk: dict[Protocol, Any] = {}
        self._trunk_disabled: dict[Protocol, str] = {}
        self._running = False
        self._housekeeper: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._capture: dict[str, Any] = {"sample_rate": 0.0, "center_hz": 0.0, "last_feed": 0.0, "feeds": 0}
        self.recorder: Any = None
        self._recorder_external = False  # set_recorder(): the caller owns it and closes it
        self.metrics: dict[str, int] = collections.defaultdict(int)
        self._make_default_recorder()

    # ------------------------------------------------------------------ recorder
    def _make_default_recorder(self) -> None:
        from sigsense_api.dvoice.recorder import CallRecorder

        if self.recorder is not None and not self._recorder_external:
            self.recorder.close()
        root = self.config.record_dir
        self.recorder = CallRecorder(Path(root) if root else None, record_audio=root is not None)
        self._recorder_external = False

    def set_recorder(self, recorder: Any) -> None:
        """Swap the call recorder (the runtime uses this for log-only / audio / off modes).
        The caller keeps ownership of the recorder it passes in."""
        old, old_external = self.recorder, self._recorder_external
        self.recorder, self._recorder_external = recorder, True
        if old is not None and old is not recorder and not old_external:
            try:
                old.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_evt.clear()
            for cfg in self.config.channels:
                self._spawn(cfg)
            self._housekeeper = threading.Thread(target=self._housekeeping, name="dvoice-housekeeping", daemon=True)
            self._housekeeper.start()
        threading.Thread(target=_warm_up, name="dvoice-warmup", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_evt.set()
            workers = list(self._workers.values())
            self._workers.clear()
            self._followers.clear()
        for w in workers:
            w.stop()
        self._end_all_calls("sync_loss")
        if self._housekeeper is not None and self._housekeeper is not threading.current_thread():
            self._housekeeper.join(timeout=2.0)

    def close(self) -> None:
        """Stop and release the recorder (open WAVs are finalised). Call once, at shutdown."""
        self.stop()
        if self.recorder is not None and not self._recorder_external:
            self.recorder.close()

    def configure(self, config: EngineConfig) -> None:
        """Apply a new config live (channels added/removed, protocol forced, policy changed)."""
        validate_config(config)
        with self._lock:
            old = self.config
            self.config = config
            if config.record_dir != old.record_dir and not self._recorder_external:
                self._make_default_recorder()
            if not self._running:
                return
            wanted = {c.name: c for c in config.channels}
            for name in [n for n, w in self._workers.items() if w.follower_of is None and n not in wanted]:
                self._despawn(name)
            for name, cfg in wanted.items():
                existing = self._workers.get(name)
                if existing is None:
                    self._spawn(cfg)
                elif existing.cfg != cfg:
                    existing.set_config(cfg)
            if config.follow != old.follow:
                for ctl in self._trunk.values():
                    ctl.policy = config.follow

    def _spawn(self, cfg: ChannelConfig, follower_of: Optional[str] = None) -> ChannelWorker:
        worker = ChannelWorker(self, cfg, follower_of)
        self._workers[cfg.name] = worker
        worker.start()
        return worker

    def _despawn(self, name: str) -> None:
        worker = self._workers.pop(name, None)
        self._followers.pop(name, None)
        if worker is not None:
            worker.stop()
            self._end_channel_calls(name, "retune")

    # ------------------------------------------------------------------ input
    def feed_iq(self, iq: np.ndarray, sample_rate: float, center_hz: float, channel: str = "ch0") -> None:
        """Server-side wideband IQ (HackRF). Called from the capture tap; non-blocking.

        `channel="*"` feeds every channel (what the HackRF feeder uses, so voice followers
        share the one capture); a name feeds only that channel.
        """
        data = np.ascontiguousarray(iq, dtype=np.complex64)
        if data.size == 0:
            return
        item = _Item(INPUT_IQ8_SERVER, data, float(sample_rate), float(center_hz), data.size / float(sample_rate))
        self._capture.update(sample_rate=float(sample_rate), center_hz=float(center_hz), last_feed=time.monotonic())
        self._capture["feeds"] += 1
        with self._lock:
            targets = list(self._workers.values()) if channel == "*" else [w for n, w in self._workers.items() if n == channel]
        for w in targets:
            w.enqueue(item)

    def feed_upload(self, kind: int, payload: bytes, channel: str = "ch0") -> None:
        """Browser upload: INPUT_* kind + raw little-endian int16 samples. Non-blocking."""
        if kind not in (INPUT_IQ16_48K, INPUT_DISC16_48K):
            raise ValueError(f"unsupported upload kind {kind}")
        usable = len(payload) // 2 * 2
        if kind == INPUT_IQ16_48K:
            usable = usable // 4 * 4  # whole I/Q pairs
        samples = np.frombuffer(payload[:usable], dtype="<i2")
        if samples.size == 0:
            return
        if kind == INPUT_IQ16_48K:
            data = (samples[0::2].astype(np.float32) + 1j * samples[1::2].astype(np.float32)).astype(np.complex64) / 32768.0
        else:
            data = samples.astype(np.float32) * (DISC16_FULL_SCALE_HZ / 32768.0)
        item = _Item(kind, data, CHANNEL_RATE, 0.0, data.size / CHANNEL_RATE)
        with self._lock:
            worker = self._workers.get(channel)
        if worker is not None:
            worker.enqueue(item)

    def discontinuity(self, channel: str = "*") -> None:
        """The capture restarted (retune, device restart): partial frames and sync are stale."""
        with self._lock:
            targets = list(self._workers.values()) if channel == "*" else [w for n, w in self._workers.items() if n == channel]
        for w in targets:
            w.request_gap()

    # ------------------------------------------------------------------ status
    def status(self) -> EngineStatus:
        from sigsense_api.dvoice.vocoder import registry

        with self._lock:
            channels = {name: w.snapshot() for name, w in self._workers.items()}
        try:
            vocoders = registry.capabilities()
        except Exception:
            vocoders = {}
        return EngineStatus(running=self._running, channels=channels, vocoders=vocoders)

    def status_dict(self) -> dict[str, Any]:
        s = self.status()
        cap = dict(self._capture)
        cap["age_s"] = round(time.monotonic() - cap["last_feed"], 2) if cap["last_feed"] else None
        return {
            "running": s.running,
            "channels": s.channels,
            "vocoders": s.vocoders,
            "contract_version": s.contract_version,
            "capture": {k: v for k, v in cap.items() if k != "last_feed"},
            "active_calls": self._active_calls(None),
            "followers": {n: dict(v) for n, v in self._followers.items()},
            "metrics": dict(self.metrics),
            "trunk": {p.value: ctl.status() for p, ctl in self._trunk.items()} | {p.value: {"disabled": why} for p, why in self._trunk_disabled.items()},
        }

    def subscribe_audio(self, callback: Callable[[str, np.ndarray, int], None]) -> Callable[[], None]:
        """Per-call PCM tap for other consumers (speech-to-text): callback(call_id, pcm_int16, sample_rate).
        Returns an unsubscribe function. Called from the channel worker; must not block.
        An empty `pcm` array marks the end of that call."""
        with self._lock:
            sid = next(self._audio_sub_ids)
            self._audio_subs[sid] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._audio_subs.pop(sid, None)

        return unsubscribe

    # ------------------------------------------------------------------ talkgroups
    def talkgroups(self) -> list[dict[str, Any]]:
        from sigsense_api.dvoice.events import to_jsonable

        with self._lock:
            return [to_jsonable(tg) for tg in self._talkgroups.values()]

    def set_talkgroup_labels(self, labels: dict[int, str]) -> None:
        with self._lock:
            self.config.talkgroup_labels = {int(k): str(v)[:80] for k, v in labels.items()}
            for (_, tgid), tg in self._talkgroups.items():
                tg.label = self.config.talkgroup_labels.get(tgid, tg.label)

    # ------------------------------------------------------------------ plumbing used by workers
    def _publish(self, type_: str, payload: Any, channel: str) -> None:
        try:
            envelope = make_event(type_, payload, channel=channel)
            self._on_event(envelope)
        except Exception:
            log.exception("event sink failed for %s", type_)

    def _vocoder_provider(self, vocoder_id: Any) -> Any:
        from sigsense_api.dvoice.vocoder import registry

        return registry.get_vocoder(vocoder_id, self.config.vocoder_preference.get(vocoder_id.value))

    def _active_calls(self, channel: Optional[str]) -> int:
        with self._lock:
            return sum(1 for s in self._calls.values() if channel is None or s.channel == channel)

    def _make_emit(self, worker: ChannelWorker) -> Callable[[str, Any], None]:
        def emit(type_: str, payload: Any) -> None:
            self._on_decoder_event(worker, type_, payload)

        return emit

    # ---- event routing
    def _on_decoder_event(self, worker: ChannelWorker, type_: str, payload: Any) -> None:
        try:
            if type_ in ("call_start", "call_update", "call_end"):
                self._on_call(worker, type_, payload)
            elif type_ == "voice":
                self._on_voice(worker, payload)
            elif type_ == "talkgroup":
                self._on_talkgroup_event(worker, payload)
            elif type_ in ("system_info", "trunk", "error", "sync", "status", "link_stats"):
                self._publish(type_, payload, worker.cfg.name)
                if type_ in ("system_info", "trunk"):
                    self._feed_trunk(worker, type_, payload)
            else:
                self.metrics["unknown_event"] += 1
        except Exception:
            log.exception("handling %s from %s failed", type_, worker.cfg.name)
            self.metrics["event_errors"] += 1

    # ---- calls
    def _on_call(self, worker: ChannelWorker, type_: str, incoming: Call) -> None:
        now_wall, now = time.time(), time.monotonic()
        call = dataclasses.replace(
            incoming, encryption=dataclasses.replace(incoming.encryption), meta=dict(incoming.meta)
        )
        cid = call.call_id
        with self._lock:
            state = self._calls.get(cid)
            if state is None:
                if type_ == "call_end" and cid in self._recent_ended:
                    return  # duplicate end
                state = _CallState(call=call, call_num=next(self._call_counter), channel=worker.cfg.name)
                self._calls[cid] = state
                type_ = "call_start" if type_ != "call_end" else type_
            previous = state.call
            state.call = call
            state.last_activity = now
        worker.last_call_activity = now
        call.meta["call_num"] = state.call_num
        if not call.started_at:
            call.started_at = previous.started_at or now_wall
        call.updated_at = call.updated_at or now_wall
        if call.channel is None:
            call.channel = worker.cfg.name
        if call.frequency_hz is None:
            call.frequency_hz = worker.cfg.frequency_hz
        label = self.config.talkgroup_labels.get(call.target_id) if call.target_id is not None else None
        if label:
            call.meta["talkgroup_label"] = label
        self._apply_audio_policy(state, call)
        call.encryption.audio_decoded = bool(state.audio_frames) and call.encryption.state != EncryptionState.ENCRYPTED
        if call.encryption.state == EncryptionState.ENCRYPTED:
            call.encryption.audio_decoded = False
            call.meta["audio"] = "none (encrypted)"
        snapshot = _snapshot(call)
        throttled = (
            type_ == "call_update"
            and snapshot == state.last_snapshot
            and now - state.last_sent < CALL_UPDATE_MIN_S
        )
        ended = type_ == "call_end" or call.ended_at is not None
        if ended:
            type_ = "call_end"
            call.ended_at = call.ended_at or now_wall
            call.end_reason = call.end_reason or "terminator"
        if not throttled:
            state.last_sent = now
            state.last_snapshot = snapshot
            self._publish(type_, call, worker.cfg.name)
        self._track_talkgroup(worker, call, first=(type_ == "call_start"))
        self._record_call(call)
        if ended:
            self._finish_call(state, call)

    def _apply_audio_policy(self, state: _CallState, call: Call) -> None:
        follow = self.config.follow
        tg = call.target_id
        muted = False
        if tg is not None:
            if tg in follow.skip_talkgroups:
                muted = True
            elif follow.hold_talkgroups and tg not in follow.hold_talkgroups:
                muted = True
        state.muted = muted
        if muted:
            call.meta["muted"] = True

    def _finish_call(self, state: _CallState, call: Call) -> None:
        with self._lock:
            self._calls.pop(call.call_id, None)
            self._recent_ended[call.call_id] = time.monotonic()
            while len(self._recent_ended) > 256:
                self._recent_ended.popitem(last=False)
        # end-of-call marker for audio clients and subscribers (also a clean fade point)
        if state.audio_frames and self._on_audio is not None:
            try:
                self._on_audio(pack_audio_frame(state.call_num, state.audio_frames, np.zeros(0, dtype=np.int16), slot=call.slot, end_of_call=True))
            except Exception:
                log.exception("audio sink failed")
        self._notify_audio_subs(call.call_id, np.zeros(0, dtype=np.int16), 8000)

    def _end_channel_calls(self, channel: str, reason: str) -> None:
        with self._lock:
            ids = [cid for cid, s in self._calls.items() if s.channel == channel]
        for cid in ids:
            self._synth_end(cid, reason)

    def _end_all_calls(self, reason: str) -> None:
        with self._lock:
            ids = list(self._calls)
        for cid in ids:
            self._synth_end(cid, reason)

    def _synth_end(self, call_id: str, reason: str) -> None:
        with self._lock:
            state = self._calls.get(call_id)
        if state is None:
            return
        call = dataclasses.replace(state.call, encryption=dataclasses.replace(state.call.encryption), meta=dict(state.call.meta))
        call.ended_at = time.time()
        call.end_reason = reason
        call.updated_at = call.ended_at
        self._publish("call_end", call, state.channel)
        self._record_call(call)
        self._finish_call(state, call)

    def _record_call(self, call: Call) -> None:
        rec = self.recorder
        if rec is None:
            return
        try:
            rec.on_call(call)
        except Exception:
            log.exception("recorder failed")
            self.metrics["recorder_errors"] += 1

    # ---- voice
    def _on_voice(self, worker: ChannelWorker, vf: VoiceFrame) -> None:
        with self._lock:
            state = self._calls.get(vf.call_id)
        if state is None:
            self.metrics["voice_without_call"] += 1
            return
        now = time.monotonic()
        state.last_activity = now
        worker.last_call_activity = now
        call = state.call
        # --- safety policy, enforced here whatever the decoder emitted ---
        if call.encryption.state == EncryptionState.ENCRYPTED:
            state.encrypted_dropped += 1
            self.metrics["encrypted_frames_dropped"] += 1
            if state.audio_frames:
                state.audio_frames = 0  # audio had started before encryption was known: stop and un-record
            return
        if state.muted:
            return
        if vf.pcm is None or len(vf.pcm) == 0:
            if not state.vocoder_warned:
                state.vocoder_warned = True
                self._publish(
                    "error",
                    ErrorEvent(
                        "vocoder_unavailable",
                        Severity.WARNING,
                        f"no {vf.vocoder.value} vocoder is available: call metadata only. See /dvoice/codec.",
                        {"vocoder": vf.vocoder.value, "call_id": vf.call_id},
                    ),
                    worker.cfg.name,
                )
            return
        pcm = np.ascontiguousarray(vf.pcm, dtype=np.int16)
        seq = state.audio_frames
        state.audio_frames += 1
        if self._on_audio is not None:
            try:
                self._on_audio(
                    pack_audio_frame(
                        state.call_num,
                        seq,
                        pcm,
                        slot=call.slot,
                        sample_rate=int(vf.sample_rate),
                        had_errors=vf.errors > 6,
                    )
                )
            except Exception:
                log.exception("audio sink failed")
        self._notify_audio_subs(vf.call_id, pcm, int(vf.sample_rate))
        rec = self.recorder
        if rec is not None:
            try:
                rec.on_voice(vf.call_id, pcm, int(vf.sample_rate))
            except Exception:
                log.exception("recorder voice failed")

    def _notify_audio_subs(self, call_id: str, pcm: np.ndarray, rate: int) -> None:
        with self._lock:
            subs = list(self._audio_subs.items())
        for sid, cb in subs:
            try:
                cb(call_id, pcm, rate)
            except Exception:
                log.exception("audio subscriber failed")
                self.metrics["audio_sub_errors"] += 1
                if self.metrics["audio_sub_errors"] > 50:
                    with self._lock:
                        self._audio_subs.pop(sid, None)

    # ---- talkgroups
    def _track_talkgroup(self, worker: ChannelWorker, call: Call, first: bool) -> None:
        if call.target_id is None:
            return
        key = (call.protocol.value, int(call.target_id))
        publish = None
        with self._lock:
            tg = self._talkgroups.get(key)
            if tg is None:
                tg = Talkgroup(id=int(call.target_id), protocol=call.protocol, label=self.config.talkgroup_labels.get(int(call.target_id)))
                self._talkgroups[key] = tg
                publish = tg
            if first:
                tg.call_count += 1
                if call.encryption.state == EncryptionState.ENCRYPTED:
                    tg.encrypted_calls += 1
            tg.last_heard = time.time()
        if publish is not None:
            self._publish("talkgroup", publish, worker.cfg.name)

    def _on_talkgroup_event(self, worker: ChannelWorker, payload: Talkgroup) -> None:
        key = (payload.protocol.value, int(payload.id))
        with self._lock:
            known = self._talkgroups.get(key)
            if known is None:
                self._talkgroups[key] = payload
            else:
                if payload.label:
                    known.label = payload.label
                payload = known
            if payload.label is None:
                payload.label = self.config.talkgroup_labels.get(int(payload.id))
        self._publish("talkgroup", payload, worker.cfg.name)

    # ---- trunking
    def _trunk_for(self, protocol: Protocol) -> Optional[Any]:
        if protocol in self._trunk:
            return self._trunk[protocol]
        if protocol in self._trunk_disabled:
            return None
        cls = TRUNK_OVERRIDES.get(protocol)
        if cls is None:
            try:
                if protocol in (Protocol.P25P1, Protocol.P25P2):
                    from sigsense_api.dvoice.trunking.p25 import P25TrunkController as cls
                elif protocol == Protocol.DMR:
                    from sigsense_api.dvoice.trunking.dmr import DmrTrunkController as cls
                else:
                    return None
            except Exception as exc:
                self._trunk_disabled[protocol] = f"no trunk controller: {exc}"
                return None
        try:
            ctl = cls(self.config.follow)
        except Exception as exc:
            self._trunk_disabled[protocol] = f"trunk controller failed to start: {exc}"
            return None
        self._trunk[protocol] = ctl
        return ctl

    def _feed_trunk(self, worker: ChannelWorker, type_: str, payload: Any) -> None:
        if worker.follower_of is not None or self.config.follow.max_voice_followers <= 0:
            return
        pipe = worker._pipe
        if pipe is None:
            return
        ctl = self._trunk_for(pipe.protocol)
        if ctl is None:
            return
        try:
            requests = ctl.on_event(type_, payload) or []
        except NotImplementedError:
            self._trunk_disabled[pipe.protocol] = "trunk controller not implemented yet"
            self._trunk.pop(pipe.protocol, None)
            return
        except Exception as exc:
            log.exception("trunk controller failed")
            self._publish("error", ErrorEvent("trunk_error", Severity.WARNING, f"{type(exc).__name__}: {exc}"), worker.cfg.name)
            return
        for req in requests:
            self._handle_tune_request(worker, pipe.protocol, req)

    def _handle_tune_request(self, parent: ChannelWorker, protocol: Protocol, req: TuneRequest) -> None:
        if req.role != "voice":
            # "return to control" needs no action on a shared wideband capture: the control
            # channel never stopped being decoded.
            return
        policy = self.config.follow
        cap_fs, cap_center = self._capture["sample_rate"], self._capture["center_hz"]
        with self._lock:
            for name, info in self._followers.items():
                if abs(info["frequency_hz"] - req.frequency_hz) < 1_000.0:
                    info["deadline"] = time.monotonic() + max(policy.hang_time_s, 1.0)
                    return  # already following this frequency
            live = [n for n in self._followers if n in self._workers]
            if len(live) >= min(policy.max_voice_followers, MAX_FOLLOWERS):
                self.metrics["follow_skipped_busy"] += 1
                return
        offset = req.frequency_hz - cap_center
        if not cap_fs or abs(offset) > 0.45 * cap_fs:
            self._publish(
                "trunk",
                _trunk_event("hunting", req, {"retune_required": True, "reason": "voice frequency is outside the captured span"}),
                parent.cfg.name,
            )
            return
        name = f"{parent.cfg.name}-v{next(self._follower_counter)}"
        cfg = ChannelConfig(
            name=name,
            protocol=protocol.value,
            offset_hz=offset,
            frequency_hz=req.frequency_hz,
            bandwidth_hz=parent.cfg.bandwidth_hz,
            invert=parent._pipe.inverted if parent._pipe is not None else parent.cfg.invert,
        )
        with self._lock:
            if not self._running:
                return
            self._spawn(cfg, follower_of=parent.cfg.name)
            self._followers[name] = {
                "parent": parent.cfg.name,
                "frequency_hz": req.frequency_hz,
                "talkgroup": req.talkgroup,
                "slot": req.slot,
                "created": time.monotonic(),
                "deadline": time.monotonic() + FOLLOWER_GRACE_S,
                "protocol": protocol.value,
            }
        self._publish("trunk", _trunk_event("grant", req, {"follower": name}), parent.cfg.name)

    # ------------------------------------------------------------------ housekeeping
    def _housekeeping(self) -> None:
        last_status = 0.0
        while not self._stop_evt.wait(0.25):
            now = time.monotonic()
            try:
                self._expire_stale_calls(now)
                self._expire_followers(now)
                if now - last_status >= 5.0:
                    last_status = now
                    self._publish("status", self.status_dict(), "*")
            except Exception:
                log.exception("housekeeping failed")

    def _expire_stale_calls(self, now: float) -> None:
        with self._lock:
            stale = [cid for cid, s in self._calls.items() if now - s.last_activity > CALL_STALE_S]
        for cid in stale:
            self._synth_end(cid, "timeout")

    def _expire_followers(self, now: float) -> None:
        with self._lock:
            items = list(self._followers.items())
        for name, info in items:
            worker = self._workers.get(name)
            if worker is None:
                with self._lock:
                    self._followers.pop(name, None)
                continue
            busy = self._active_calls(name) > 0
            if busy:
                info["deadline"] = now + max(self.config.follow.hang_time_s, 1.0)
                continue
            if now > info["deadline"] and now - worker.last_call_activity > self.config.follow.hang_time_s:
                protocol = Protocol(info["protocol"])
                with self._lock:
                    self._despawn(name)
                ctl = self._trunk.get(protocol)
                if ctl is not None:
                    try:
                        ctl.on_voice_ended(info["frequency_hz"])
                    except Exception:
                        log.exception("trunk on_voice_ended failed")


def _trunk_event(kind: str, req: TuneRequest, detail: dict[str, Any]) -> Any:
    from sigsense_api.dvoice.events import TrunkEvent

    return TrunkEvent(
        kind=kind,
        frequency_hz=req.frequency_hz,
        talkgroup=req.talkgroup,
        slot=req.slot,
        detail={"reason": req.reason, **req.detail, **detail},
    )


def _warm_up() -> None:
    """Compile the numba kernels before the first real block (the JIT would otherwise land on a call)."""
    try:
        from sigsense_api.dvoice.dsp.channel import Channelizer
        from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
        from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
        from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
        from sigsense_api.dvoice.sync.correlator import SyncCorrelator
        from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

        rng = np.random.default_rng(0)
        iq = (rng.standard_normal(96_000) + 1j * rng.standard_normal(96_000)).astype(np.complex64) * 0.1
        Channelizer(2_400_000.0, 10_000.0).process(np.tile(iq, 25)[:200_000])
        disc = FmDiscriminator(CHANNEL_RATE).process(iq)
        block = FskSymbolRecovery(4800.0, CHANNEL_RATE, 4, Modulation.C4FM).process(disc)
        FskSymbolRecovery(4800.0, CHANNEL_RATE, 2, Modulation.GMSK).process(disc)
        CqpskSymbolRecovery(4800.0, CHANNEL_RATE).process(iq)
        SyncCorrelator(list(SYNC_SPECS.values())).scan(block.dibits, block.first_index)
    except Exception:
        log.exception("digital voice warm-up failed (not fatal)")
