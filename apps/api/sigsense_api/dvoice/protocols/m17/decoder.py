"""M17 decoder (WP-E). Original code from the public M17 specification (revision 2.0.0).

Symbol-driven state machine:

  scanning   look for any of the four frame syncs (LSF 0x55F7, stream 0xFF5D, BERT 0xDF55,
             packet 0x75FF; one wrong symbol tolerated, both polarities); a candidate is only
             accepted when the frame behind it decodes (LSF: CRC-16; stream: Golay LICH + Viterbi
             path metric; packet: Viterbi metric)
  locked     frames repeat every 192 symbols; the next sync is checked at the expected place
             (three wrong symbols tolerated, a missing sync still decodes as the same frame type)
             until the End-of-Transmission marker (0x555D), a new LSF, or three bad frames in a row.

Trust rules (nothing is emitted from data that failed its check):
  * A call starts only from a CRC-verified LSF, either received directly or re-assembled from
    six LICH chunks of a stream that was joined late.
  * Stream frames have no CRC. Their Viterbi path metric decides: <= 24 keeps the frame in the
    stream (random input never scores below 29), but only <= 16 lets its payload reach the
    vocoder (measured on a 4-16 % symbol-error mix: payload exactly right 87 % at <= 16, 33 % at 17-24, 5 % above);
    frames in between are counted as bad and the vocoder is told they were erased, so
    concealment (repeat-and-fade) applies instead of garbage audio.
  * Encrypted streams (scrambler / AES, from TYPE) are reported and never decoded to audio.

Voice: 3200 bps streams carry two 64-bit Codec2 frames per stream frame; "voice + data" (1600)
carries one 64-bit Codec2-1600 frame plus 64 data bits. Packet payload contents are only
reported when `config.decode_data` is set; otherwise just the protocol id, size and CRC result.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import (
    Call,
    CallKind,
    EncryptionState,
    ErrorEvent,
    Modulation,
    Protocol,
    Severity,
    SyncEvent,
    SystemInfo,
    VocoderId,
    VoiceFrame,
)
from sigsense_api.dvoice.fec import crc as fec_crc
from sigsense_api.dvoice.protocols.base import ProtocolConfig, ProtocolDecoder
from sigsense_api.dvoice.protocols.m17 import frames as fr
from sigsense_api.dvoice.protocols.m17 import lsf as lsfmod
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS
from sigsense_api.dvoice.vocoder.base import VocoderFrame
from sigsense_api.dvoice.vocoder.session import ConcealmentPolicy, VocoderSession

FRAME = fr.FRAME_SYMBOLS
SYNC = fr.SYNC_SYMBOLS

# Viterbi path-metric trust limits (see module doc; calibrated on random input vs noisy valid frames).
STREAM_METRIC_MAX = 24  # the frame belongs to this stream (keeps the lock, carries LICH/FN)
VOICE_METRIC_MAX = 16  # ... and its payload is trusted as speech: above this a damaged frame decodes
#                        to wrong bits about two times in three (no CRC on stream frames), so it is erased
PACKET_METRIC_MAX = 30
BERT_METRIC_MAX = 30
# With soft symbols the Viterbi metric is a soft cost (sum of (1 - expected x confidence)/2), which is
# larger than a hard error count even for a correct decode and grows with channel noise. Random input
# scored >= 46 (stream), >= 61 (packet), >= 64 (BERT) at every noise level measured, so the soft
# gates sit just below those; valid frames score 25-47 (stream), 34-64 (packet) over sigma 0.4-0.9.
STREAM_SOFT_METRIC_MAX = 44
VOICE_SOFT_METRIC_MAX = 42
PACKET_SOFT_METRIC_MAX = 58
BERT_SOFT_METRIC_MAX = 62

SCAN_MAX_ERRORS = 1  # sync symbol errors tolerated while searching (hard decisions)
# With soft symbols a sync is also accepted on correlation: every sync word is made of outer
# symbols only, so the normalised sum of (+-1 expected sign x clipped level / 3) is 1.0 for a perfect
# word and drops by 2/8 for each completely wrong symbol. A candidate is only a *candidate*: its
# frame still has to pass the CRC / path-metric checks, so a looser sync costs time, not false calls.
SOFT_SYNC_MIN = 0.45
LOCK_MAX_ERRORS = 3  # ... while locked (the position is already known)
MAX_MISSES = 3  # consecutive bad/unsynchronised frames before the lock is dropped
RETIME_SYMBOLS = 3  # a locked decoder looks this far either side of the expected sync (a front-end clock slip)
EOT_PATTERN = np.array([1, 1, 1, 1, 1, 1, 3, 1], dtype=np.uint8)  # 0x555D
EOT_MAX_ERRORS = 1
UPDATE_INTERVAL_SYMBOLS = 1200  # call_update at most ~4 Hz (4800 symbols/s)
LATE_JOIN_BUFFER = 8  # stream frames kept while the LSF is still being assembled from the LICH

_NAMES = ("m17_lsf", "m17_stream", "m17_bert", "m17_packet")
_KIND = {"m17_lsf": "lsf", "m17_stream": "stream", "m17_bert": "bert", "m17_packet": "packet"}
_PATTERNS = {name: np.array(SYNC_SPECS[name].values, dtype=np.uint8) for name in _NAMES}

_SIGNATURE_FNS = (0x7FFC, 0x7FFD, 0x7FFE, 0x7FFF)


def _hex(data: bytes, limit: int = 64) -> str:
    return data[:limit].hex()


class _CallState:
    """Everything known about the transmission currently being received."""

    def __init__(self, call: Call, lsf: lsfmod.Lsf, session: Optional[VocoderSession]) -> None:
        self.call = call
        self.lsf = lsf
        self.session = session
        self.decode_audio = False
        self.vocoder_error_sent = False
        self.seq = 0
        self.text = lsfmod.TextAssembler()
        self.last_fn: Optional[int] = None
        self.corrected_bits = 0
        self.coded_bits = 0
        self.last_update_symbol = -10**9
        self.dirty = False
        # packet mode
        self.packet = bytearray()
        self.packet_frames = 0


class M17Decoder(ProtocolDecoder):
    protocol = Protocol.M17
    symbol_rate = 4800.0
    modulation = Modulation.FSK4
    sync_specs = ("m17_lsf", "m17_stream", "m17_bert", "m17_packet")
    timeslots = 1

    def __init__(self, emit, vocoders, config: Optional[ProtocolConfig] = None) -> None:
        super().__init__(emit, vocoders, config)
        self._counter = 0
        self._last_can: Optional[int] = None
        self._reset_state()

    # ------------------------------------------------------------------ state
    def _reset_state(self) -> None:
        self._buf = np.zeros(0, dtype=np.uint8)
        self._soft = np.zeros(0, dtype=np.float32)
        self._base = 0  # absolute symbol index of _buf[0]
        self._pos = 0  # scan position inside _buf
        self._next_index: Optional[int] = None
        self._locked = False
        self._inverted = False
        self._misses = 0
        self._last_type: Optional[str] = None
        self._call: Optional[_CallState] = None
        self._last_frame_end = 0  # absolute index just past the last accepted frame
        self._lich: list[Optional[bytes]] = [None] * 6
        self._lich_disagree = 0
        self._pending: list[tuple[int, bytes, int, bool]] = []  # (fn, payload, metric, trusted)
        self._bert = fr.Prbs9Checker()
        self.counters = {
            "frames_ok": 0,
            "frames_bad": 0,
            "fec_corrected": 0,
            "fec_failed": 0,
            "syncs": 0,
            "lsf_ok": 0,
            "lsf_bad": 0,
            "calls": 0,
            "packets_ok": 0,
            "packets_bad": 0,
            "bert_frames": 0,
        }

    def reset(self) -> None:
        if self._call is not None:
            self._end_call("retune")
        self._reset_state()

    def stats(self) -> dict[str, Any]:
        total = self.counters["frames_ok"] + self.counters["frames_bad"]
        out: dict[str, Any] = dict(self.counters)
        out["sync_ratio"] = (self.counters["frames_ok"] / total) if total else 0.0
        out["bert_ber"] = self._bert.ber
        out["bert_bits"] = self._bert.bits
        return out

    # ------------------------------------------------------------------ input
    def feed(self, block: SymbolBlock) -> None:
        try:
            self._feed(block)
        except Exception as exc:  # a decoder fault must never take the engine down
            self.emit(
                "error",
                ErrorEvent(
                    code="decoder_fault",
                    severity=Severity.WARNING,
                    message=f"M17 decoder reset after an internal error: {type(exc).__name__}",
                    detail={"protocol": "m17"},
                ),
            )
            self._reset_state()

    def _feed(self, block: SymbolBlock) -> None:
        dibits = np.asarray(block.dibits, dtype=np.uint8).ravel() & 3
        if dibits.size == 0:
            return
        if self._next_index is not None and block.first_index != self._next_index:
            # A gap or a rewind in the symbol stream: nothing buffered can be joined to it.
            self._buf = np.zeros(0, dtype=np.uint8)
            self._soft = np.zeros(0, dtype=np.float32)
            self._pos = 0
            self._base = int(block.first_index)
            self._locked = False
            self._misses = 0
        elif self._next_index is None:
            self._base = int(block.first_index)
        self._next_index = int(block.first_index) + dibits.size
        if block.soft is not None and len(block.soft) == dibits.size:
            soft = np.asarray(block.soft, dtype=np.float32).ravel()
        else:
            soft = np.full(dibits.size, np.nan, dtype=np.float32)
        self._buf = np.concatenate([self._buf, dibits])
        self._soft = np.concatenate([self._soft, soft])
        self._process()
        self._timeout_check()
        self._trim()

    def _trim(self) -> None:
        # Keep a few symbols behind the scan position so a locked decoder can re-time by a symbol or two.
        drop = max(0, self._pos - RETIME_SYMBOLS)
        if drop > 0:
            self._buf = self._buf[drop:]
            self._soft = self._soft[drop:]
            self._base += drop
            self._pos -= drop

    # ------------------------------------------------------------------ scanning / locking
    def _sync_errors(self, pattern: np.ndarray, start: int, count: int) -> np.ndarray:
        err = np.zeros(count, dtype=np.int16)
        for k in range(SYNC):
            err += self._buf[start + k : start + k + count] != pattern[k]
        return err

    def _process(self) -> None:
        guard = 0
        while guard < 100000:
            guard += 1
            avail = self._buf.size - self._pos
            if avail < FRAME + (RETIME_SYMBOLS if self._locked else 0):
                if not self._locked:
                    # nothing more can be decided yet; keep 191 symbols so a sync at the very end is not lost
                    self._pos = max(self._pos, self._buf.size - (FRAME - 1))
                return
            if self._locked:
                self._locked_step()
            else:
                if not self._scan_step():
                    return

    def _candidates(self) -> list[tuple[int, str, bool, int]]:
        """Sync candidates (position, type, inverted, errors) with a whole frame available."""
        count = self._buf.size - FRAME - self._pos + 1
        if count <= 0:
            return []
        found: list[tuple[int, str, bool, int]] = []
        window = self._soft[self._pos : self._pos + count + SYNC - 1]
        use_soft = window.size == count + SYNC - 1 and not np.isnan(window).any()
        clipped = np.clip(window, -3.0, 3.0) / 3.0 if use_soft else None
        for name in _NAMES:
            for inverted in (False, True):
                pattern = _PATTERNS[name] ^ 2 if inverted else _PATTERNS[name]
                err = self._sync_errors(pattern, self._pos, count)
                hit = err <= SCAN_MAX_ERRORS
                if use_soft:
                    # expected sign: dibit 1 is the +3 symbol, dibit 3 the -3 symbol
                    sign = np.where(pattern == 1, 1.0, -1.0)
                    score = np.zeros(count)
                    for k in range(SYNC):
                        score += sign[k] * clipped[k : k + count]
                    hit = hit | (score / SYNC >= SOFT_SYNC_MIN)
                for idx in np.flatnonzero(hit):
                    found.append((self._pos + int(idx), _KIND[name], inverted, int(err[idx])))
        found.sort(key=lambda c: (c[0], c[3]))
        return found

    def _scan_step(self) -> bool:
        """Try the earliest sync candidate. Returns False when no further progress is possible."""
        cands = self._candidates()
        if not cands:
            self._pos = max(self._pos, self._buf.size - (FRAME - 1))
            return False
        for pos, kind, inverted, errors in cands:
            if self._try_frame(pos, kind, inverted, errors, locked=False):
                self._pos = pos + FRAME
                self._locked = True
                self._inverted = inverted
                self._misses = 0
                return True
        # Every candidate in the buffer was tried and none decoded: move past the last one.
        self._pos = cands[-1][0] + 1
        return True

    def _sync_distance(self, pos: int) -> int:
        """Fewest symbol errors of any frame sync (or the EOT marker) at `pos`, in the locked polarity."""
        seg = self._buf[pos : pos + SYNC]
        if self._inverted:
            seg = seg ^ 2
        best = int(np.count_nonzero(seg != EOT_PATTERN))
        for name in _NAMES:
            best = min(best, int(np.count_nonzero(seg != _PATTERNS[name])))
        return best

    def _retime(self, pos: int) -> int:
        """If the sync is not where the lock expects it, look a few symbols either side: a front-end clock
        slip moves every following frame by a symbol, which must not look like lost sync."""
        here = self._sync_distance(pos)
        if here <= 1:
            return pos
        # Not (nearly) exact here. A sync word shifted by one symbol can still sit within the lock tolerance of
        # the expected place by chance, so look either side for a clearly better match before accepting it.
        best_off, best = 0, here
        for off in (-1, 1, -2, 2, -3, 3):
            p = pos + off
            if p < 0 or p + FRAME > self._buf.size:
                continue
            d = self._sync_distance(p)
            if d <= 1 and d < best:
                best_off, best = off, d
        return pos + best_off

    def _locked_step(self) -> None:
        pos = self._retime(self._pos)
        seg = self._buf[pos : pos + FRAME]
        if self._inverted:
            seg = seg ^ 2
        # End-of-transmission marker: 192 symbols of 0x555D
        eot_err = int(np.count_nonzero(seg[:SYNC] != EOT_PATTERN))
        if eot_err <= EOT_MAX_ERRORS and int(np.count_nonzero(seg[SYNC : 2 * SYNC] != EOT_PATTERN)) <= EOT_MAX_ERRORS:
            self._pos = pos + FRAME
            self._last_frame_end = self._base + self._pos
            self._end_call("terminator")
            self._locked = False
            return
        # which frame type does the sync say?
        best_kind, best_err = None, 99
        for name in _NAMES:
            e = int(np.count_nonzero(seg[:SYNC] != _PATTERNS[name]))
            if e < best_err:
                best_kind, best_err = _KIND[name], e
        if best_kind is not None and best_err <= LOCK_MAX_ERRORS:
            if best_kind == "lsf" and self._call is not None:
                self._end_call("terminator")  # a fresh LSF while a call is open: the old one is over
            ok = self._try_frame(pos, best_kind, self._inverted, best_err, locked=True)
        else:
            # sync not found where expected: decode as the previous type and count it against the lock
            best_kind = self._last_type if self._last_type in ("stream", "packet") else None
            ok = False
            if best_kind is not None:
                # A frame whose sync was damaged is still good if its own FEC check passes.
                ok = self._try_frame(pos, best_kind, self._inverted, best_err, locked=True, forced=True)
        self._pos = pos + FRAME
        if ok:
            self._misses = 0
        else:
            self._misses += 1
            if self._misses >= MAX_MISSES:
                self._locked = False
                self._end_call("sync_loss")

    # ------------------------------------------------------------------ frame handling
    def _frame_bits(self, pos: int, inverted: bool) -> tuple[np.ndarray, Optional[np.ndarray]]:
        d = self._buf[pos + SYNC : pos + FRAME]
        s = self._soft[pos + SYNC : pos + FRAME]
        if inverted:
            d = d ^ 2
            s = -s
        bits = fr.dibits_to_bits(d)
        soft = None if np.isnan(s).any() else fr.symbols_to_soft_bits(s)
        return bits, soft

    def _try_frame(self, pos: int, kind: str, inverted: bool, sync_errors: int, *, locked: bool, forced: bool = False) -> bool:
        """Decode the frame at `pos`; emit events for what is valid. Returns True when it was trusted."""
        bits, soft = self._frame_bits(pos, inverted)
        index = self._base + pos
        if kind == "lsf":
            ok = self._on_lsf(bits, soft, index)
        elif kind == "stream":
            ok = self._on_stream(bits, soft, index)
        elif kind == "packet":
            ok = self._on_packet(bits, soft, index)
        else:
            ok = self._on_bert(bits, soft, index)
        if ok:
            self._last_type = kind
            self._last_frame_end = index + FRAME
            self.counters["frames_ok"] += 1
            if not forced:
                self.counters["syncs"] += 1
                self.emit(
                    "sync",
                    SyncEvent(Protocol.M17, f"m17_{kind}", sync_errors, inverted, index),
                )
        else:
            self.counters["frames_bad"] += 1
        return ok

    # --- LSF
    def _on_lsf(self, bits: np.ndarray, soft: Optional[np.ndarray], index: int) -> bool:
        frame = fr.decode_lsf_payload(bits, soft)
        if frame.lsf is None:
            self.counters["lsf_bad"] += 1
            self.counters["fec_failed"] += 1
            return False
        self.counters["lsf_ok"] += 1
        self.counters["fec_corrected"] += frame.corrected
        if self._call is not None:
            self._end_call("terminator")
        self._store_lsf_chunks(frame.lsf)
        self._begin_call(frame.lsf, index)
        return True

    def _store_lsf_chunks(self, lsf: lsfmod.Lsf) -> None:
        for i in range(6):
            self._lich[i] = lsf.raw[5 * i : 5 * i + 5]
        self._lich_disagree = 0

    # --- call lifecycle
    def _begin_call(self, lsf: lsfmod.Lsf, index: int) -> None:
        self._counter += 1
        self.counters["calls"] += 1
        t = lsf.type
        enc = lsfmod.encryption_info(t)
        dst_text, dst_cat = lsfmod.decode_address(lsf.dst)
        if not t.stream:
            kind = CallKind.DATA
        elif dst_cat == "broadcast":
            kind = CallKind.ALL
        elif dst_cat == "standard" and dst_text.startswith("M17-"):
            kind = CallKind.GROUP  # reflector module
        elif dst_cat == "standard":
            kind = CallKind.PRIVATE
        else:
            kind = CallKind.UNKNOWN
        now = time.time()
        call = Call(
            call_id=f"m17-{self.config.channel}-x-{self._counter}",
            protocol=Protocol.M17,
            slot=None,
            kind=kind,
            target_id=lsf.dst,
            source_id=lsf.src,
            frequency_hz=self.config.frequency_hz,
            channel=self.config.channel,
            started_at=now,
            updated_at=now,
            encryption=enc,
            meta={
                "src": lsf.src_label,
                "dst": lsf.dst_label,
                "can": t.can,
                "mode": "stream" if t.stream else "packet",
                "type_raw": f"0x{t.raw:04X}",
                "signed": t.signed,
            },
        )
        session: Optional[VocoderSession] = None
        vocoder_id = self._vocoder_for(t)
        state = _CallState(call, lsf, None)
        # Audio only for clear voice streams; an encrypted stream is never given to a vocoder.
        state.decode_audio = (
            t.stream and enc.state == EncryptionState.CLEAR and vocoder_id is not None
        )
        if state.decode_audio and vocoder_id is not None:
            session = VocoderSession(
                vocoder_id,
                backend=self.vocoders(vocoder_id),
                policy=ConcealmentPolicy(repeat_limit=3),
            )
            state.session = session
        if t.stream and t.data_type in (lsfmod.DATA_VOICE, lsfmod.DATA_VOICE_DATA) and enc.state == EncryptionState.ENCRYPTED:
            call.meta["audio"] = "encrypted - not decoded"
        elif t.stream and t.data_type == lsfmod.DATA_DATA:
            call.meta["audio"] = "data stream"
        self._call = state
        self._apply_meta(state, lsf.meta, announce=False)
        if self._last_can != t.can:
            self._last_can = t.can
            self.emit("system_info", SystemInfo(protocol=Protocol.M17, extra={"can": t.can}))
        self.emit("call_start", self._snapshot(state))
        state.last_update_symbol = index
        self._flush_pending()

    @staticmethod
    def _vocoder_for(t: lsfmod.M17Type) -> Optional[VocoderId]:
        if not t.stream:
            return None
        if t.data_type == lsfmod.DATA_VOICE:
            return VocoderId.CODEC2_3200
        if t.data_type == lsfmod.DATA_VOICE_DATA:
            return VocoderId.CODEC2_1600
        return None

    def _snapshot(self, state: _CallState) -> Call:
        """A copy for the event: later changes to the live call must not alter what was emitted."""
        call = state.call
        call.updated_at = time.time()
        return replace(call, encryption=replace(call.encryption), meta=dict(call.meta))

    def _maybe_update(self, state: _CallState, index: int, *, force: bool = False) -> None:
        if not state.dirty:
            return
        if force or index - state.last_update_symbol >= UPDATE_INTERVAL_SYMBOLS:
            state.dirty = False
            state.last_update_symbol = index
            self.emit("call_update", self._snapshot(state))

    def _end_call(self, reason: str) -> None:
        state = self._call
        self._call = None
        self._pending.clear()
        if state is None:
            return
        call = state.call
        call.ended_at = time.time()
        call.end_reason = reason
        if state.coded_bits:
            call.ber = round(state.corrected_bits / state.coded_bits, 6)
        if state.session is not None:
            state.session.close()
        if state.packet and call.meta.get("mode") == "packet" and "packet_ok" not in call.meta:
            call.meta["packet_ok"] = False
            call.meta["packet_incomplete"] = True
        self.emit("call_end", self._snapshot(state))

    def _timeout_check(self) -> None:
        if self._call is None:
            return
        hang = max(0.5, float(self.config.hang_time_s)) * self.symbol_rate
        if (self._base + self._buf.size) - self._last_frame_end > hang + 2 * FRAME and not self._locked:
            self._end_call("timeout")

    # --- META
    def _apply_meta(self, state: _CallState, meta: bytes, *, announce: bool = True) -> None:
        t = state.lsf.type
        kind = lsfmod.meta_kind(t)
        call = state.call
        changed = False
        if kind == lsfmod.META_TEXT:
            message = state.text.push(meta)
            if message is not None and call.meta.get("text") != message:
                call.meta["text"] = message
                changed = True
        elif kind == lsfmod.META_GNSS:
            gnss = lsfmod.parse_gnss(meta)
            if gnss is not None and call.meta.get("gnss") != gnss:
                call.meta["gnss"] = gnss
                changed = True
        elif kind == lsfmod.META_EXT_CALLSIGN:
            names = lsfmod.parse_extended_callsigns(meta)
            if names and call.meta.get("extended_callsigns") != names:
                call.meta["extended_callsigns"] = names
                changed = True
        elif t.stream and t.enc_type == lsfmod.ENC_AES and any(meta):
            call.meta["nonce_present"] = True
        if changed:
            state.dirty = True

    # --- stream
    def _on_stream(self, bits: np.ndarray, soft: Optional[np.ndarray], index: int) -> bool:
        frame = fr.decode_stream_payload(bits, soft)
        trusted = frame.corrected <= STREAM_METRIC_MAX
        good = frame.corrected <= VOICE_METRIC_MAX
        if soft is not None:
            # The absolute soft cost depends on how clean the front end's soft values are (a clean frame through
            # the real front end costs ~47, above the limits calibrated on ideal Gaussian noise), so a frame is
            # trusted if EITHER its hard-decision metric (same bits, sliced) OR its soft cost passes. The payload
            # still comes from the soft decode, which is at least as good.
            hard = fr.decode_stream_payload(bits)
            trusted = frame.corrected <= STREAM_SOFT_METRIC_MAX or hard.corrected <= STREAM_METRIC_MAX
            good = frame.corrected <= VOICE_SOFT_METRIC_MAX or hard.corrected <= VOICE_METRIC_MAX
            if hard.corrected <= STREAM_METRIC_MAX:
                frame.corrected = hard.corrected  # report the error estimate in channel-error units when it is valid
        self.counters["fec_corrected"] += frame.corrected + frame.lich_errors
        if not good:
            self.counters["fec_failed"] += 1
        state = self._call
        if state is not None:
            state.coded_bits += 272
            state.corrected_bits += frame.corrected
        if trusted and frame.lich_ok and frame.lich_count is not None and frame.lich_chunk is not None:
            self._on_lich(frame.lich_count, frame.lich_chunk, index)
        state = self._call
        if state is None:
            # Late join: keep the frame until the LSF is known (it decides whether audio may be decoded).
            if trusted:
                self._pending.append((frame.frame_number, frame.payload, frame.corrected, good))
                del self._pending[:-LATE_JOIN_BUFFER]
            return trusted
        self._voice(state, frame, good, index)
        if good and frame.eot:
            self._maybe_update(state, index, force=True)
            self._end_call("terminator")
            self._locked = False
        else:
            self._maybe_update(state, index)
        return trusted

    def _on_lich(self, count: int, chunk: bytes, index: int) -> None:
        state = self._call
        if state is not None:
            known = state.lsf.raw[5 * count : 5 * count + 5]
            if count <= 1:  # DST and the start of SRC never change within one transmission
                if chunk == known:
                    self._lich_disagree = 0
                else:
                    self._lich_disagree += 1
                    if self._lich_disagree >= 3:  # three bad matches: a different transmission took over
                        self._end_call("terminator")
                        self._lich = [None] * 6
                        self._lich_disagree = 0
                        state = None
        self._lich[count] = chunk
        if all(c is not None for c in self._lich):
            raw = b"".join(self._lich)  # type: ignore[arg-type]
            lsf = lsfmod.parse_lsf(raw)
            if lsf is None:
                return
            if self._call is None:
                self._begin_call(lsf, index)
            elif lsf.meta != self._call.lsf.meta and lsf.raw[:14] == self._call.lsf.raw[:14]:
                self._call.lsf = lsf  # META changed at a superframe boundary
                self._apply_meta(self._call, lsf.meta)

    def _flush_pending(self) -> None:
        state = self._call
        pending, self._pending = self._pending, []
        if state is None:
            return
        for fn, payload, metric, trusted in pending:
            frame = fr.StreamFrame(True, None, None, 0, fn, False, payload, metric, True)
            self._voice(state, frame, trusted, self._base + self._pos)

    def _voice(self, state: _CallState, frame: fr.StreamFrame, trusted: bool, index: int) -> None:
        t = state.lsf.type
        call = state.call
        if t.signed and frame.frame_number in _SIGNATURE_FNS:
            call.meta["signature_frames"] = call.meta.get("signature_frames", 0) + 1
            state.dirty = True
            return  # signature chunks are not speech
        state.last_fn = frame.frame_number
        if not state.decode_audio or state.session is None:
            return
        errors = int(frame.corrected)
        if t.data_type == lsfmod.DATA_VOICE:
            halves = (frame.payload[0:8], frame.payload[8:16])
            vid = VocoderId.CODEC2_3200
        else:  # voice + data: speech first, then 64 data bits
            halves = (frame.payload[0:8],)
            vid = VocoderId.CODEC2_1600
            call.meta["data_bytes"] = call.meta.get("data_bytes", 0) + 8
        for half in halves:
            bits = np.unpackbits(np.frombuffer(half, dtype=np.uint8))
            vf = VocoderFrame(vocoder=vid, bits=bits, protocol=Protocol.M17, fec_errors=errors, erased=not trusted)
            out = state.session.push(vf)
            call.voice_frames += 1
            if not trusted:
                call.voice_frames_bad += 1
            if not out.available:
                if not state.vocoder_error_sent:
                    state.vocoder_error_sent = True
                    self.emit(
                        "error",
                        ErrorEvent(
                            code="vocoder_unavailable",
                            severity=Severity.WARNING,
                            message="No Codec2 library is installed, so M17 voice is shown without audio.",
                            detail={"vocoder": vid.value, "call_id": call.call_id},
                        ),
                    )
                self.emit("voice", VoiceFrame(call.call_id, state.seq, vid, None, 8000, errors, time.time()))
                state.seq += 1
                continue
            if not call.encryption.audio_decoded:
                call.encryption.audio_decoded = True
                state.dirty = True
            pcm = out.pcm8k
            for start in range(0, pcm.size, 160):
                self.emit(
                    "voice",
                    VoiceFrame(call.call_id, state.seq, vid, pcm[start : start + 160].copy(), 8000, errors, time.time()),
                )
                state.seq += 1

    # --- packet
    def _on_packet(self, bits: np.ndarray, soft: Optional[np.ndarray], index: int) -> bool:
        frame = fr.decode_packet_payload(bits, soft)
        trusted = frame.corrected <= PACKET_METRIC_MAX
        if soft is not None:  # hard OR soft, as for stream frames
            trusted = frame.corrected <= PACKET_SOFT_METRIC_MAX or fr.decode_packet_payload(bits).corrected <= PACKET_METRIC_MAX
        self.counters["fec_corrected"] += frame.corrected
        state = self._call
        if not trusted:
            self.counters["fec_failed"] += 1
            return False
        if state is None or state.lsf.type.stream:
            return trusted  # packet frames need their LSF first (packet mode always starts with one)
        state.packet_frames += 1
        state.coded_bits += 368
        state.corrected_bits += frame.corrected
        if len(state.packet) > 825:
            return trusted  # more than 33 frames: ignore the excess
        if not frame.eof:
            state.packet.extend(frame.data)
            return trusted
        state.packet.extend(frame.data[: frame.valid_bytes])
        self._finish_packet(state)
        return trusted

    def _finish_packet(self, state: _CallState) -> None:
        call = state.call
        data = bytes(state.packet)
        ok = len(data) >= 3 and fec_crc.crc_compute_bytes("crc16_m17", data[:-2]) == int.from_bytes(data[-2:], "big")
        call.meta["packet_ok"] = ok
        call.meta["packet_bytes"] = max(0, len(data) - 2)
        if ok:
            self.counters["packets_ok"] += 1
            body = data[:-2]
            spec, used = _utf8_varint(body)
            call.meta["packet_protocol_id"] = spec
            call.meta["packet_protocol"] = _PACKET_PROTOCOLS.get(spec, "unknown")
            if self.config.decode_data:
                payload = body[used:]
                call.meta["packet_payload_hex"] = _hex(payload)
                if spec == 0x05:  # SMS: null-terminated UTF-8 text
                    call.meta["text"] = payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        else:
            self.counters["packets_bad"] += 1
        state.dirty = True
        self._maybe_update(state, self._base + self._pos, force=True)
        self._end_call("terminator")
        self._locked = False

    # --- BERT
    def _on_bert(self, bits: np.ndarray, soft: Optional[np.ndarray], index: int) -> bool:
        frame = fr.decode_bert_payload(bits, soft)
        passed = frame.corrected <= BERT_METRIC_MAX
        if soft is not None:
            passed = frame.corrected <= BERT_SOFT_METRIC_MAX or fr.decode_bert_payload(bits).corrected <= BERT_METRIC_MAX
        if not passed:
            self.counters["fec_failed"] += 1
            return False
        self.counters["fec_corrected"] += frame.corrected
        self.counters["bert_frames"] += 1
        self._bert.push(frame.bits)
        return True


# --- packet protocol identifiers (spec "Packet Data") ------------------------------------------

_PACKET_PROTOCOLS = {0x00: "RAW", 0x01: "AX.25", 0x02: "APRS", 0x03: "6LoWPAN", 0x04: "IPv4", 0x05: "SMS", 0x06: "Winlink"}


def _utf8_varint(data: bytes) -> tuple[int, int]:
    """The packet data-type specifier: a UTF-8 style 1-4 byte code point (0..2^21-1)."""
    if not data:
        return 0, 0
    first = data[0]
    if first < 0x80:
        return first, 1
    if first >> 5 == 0b110:
        n, value = 2, first & 0x1F
    elif first >> 4 == 0b1110:
        n, value = 3, first & 0x0F
    elif first >> 3 == 0b11110:
        n, value = 4, first & 0x07
    else:
        return 0, 1
    if len(data) < n:
        return 0, 1
    for b in data[1:n]:
        value = (value << 6) | (b & 0x3F)
    return value, n
