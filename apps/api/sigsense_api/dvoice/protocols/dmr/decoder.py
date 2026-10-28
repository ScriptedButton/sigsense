"""DMR (Tier II/III, TDMA) protocol decoder (WP-D). Original code from ETSI TS 102 361-1..4.

Pipeline per channel:

    dibits -> frame sync (9 patterns, BS/MS/direct-mode, either polarity) -> burst clock
    -> per 30 ms slot: CACH (TACT Hamming(7,4), short LC) + 132-dibit burst
       voice burst : sync (A) or EMB QR(16,7,6) + embedded LC fragment (B-E), 3 x 72-bit vocoder frames
       data burst  : slot type Golay(20,8), then BPTC(196,96) / rate-3/4 trellis payload
    -> typed events (calls, system info, trunk grants), vocoder frames to per-call sessions

Scope policy (docs/digital-voice-architecture.md): clear traffic only. Privacy/encryption is
reported (service-option privacy bit, PI header, manufacturer feature sets) and such calls never
reach a vocoder. No keys are handled.

AMBE+2 voice audio needs the 72-bit -> 49-bit channel decode of the vocoder, which ETSI does not
define (it is vendor-specific). The decoder uses `voice_map.decode_air72` (see voice_map.py for its
sources) unless `config.extra["ambe_channel_decode"]` supplies another (callable raw72 -> (bits49,
errors) | None), or `config.extra["ambe_raw_frames"] = True` hands the raw frame to a backend that
does its own FEC (hardware). Whether a codec backend is installed is separate: without one, calls and
metadata are decoded and an `error` event (`vocoder_unavailable`) explains why there is no audio.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import (
    Call,
    CallKind,
    EncryptionInfo,
    EncryptionState,
    ErrorEvent,
    Modulation,
    Protocol,
    Severity,
    SyncEvent,
    SystemInfo,
    Talkgroup,
    TrunkEvent,
    VocoderId,
    VoiceFrame,
)
from sigsense_api.dvoice.fec import bptc, crc, trellis
from sigsense_api.dvoice.protocols.base import ProtocolDecoder
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr import csbk as csbk_mod
from sigsense_api.dvoice.protocols.dmr import data as data_mod
from sigsense_api.dvoice.protocols.dmr import layer2, lc as lc_mod, voice_map
from sigsense_api.dvoice.protocols.dmr.bits import (
    bits_to_bytes,
    bits_to_dibits,
    dibits_to_bits,
)
from sigsense_api.dvoice.vocoder.base import VocoderFrame
from sigsense_api.dvoice.vocoder.session import VocoderSession

SEARCH_MAX_ERRORS = 3  # sync symbol errors tolerated while hunting (24 symbols; random hit ~ 1e-9)
TRACK_MAX_ERRORS = 5  # while locked
ALIGN_RANGE = 3  # symbols of timing slip searched around the predicted burst start
MAX_MISSES = 8  # consecutive undecodable bursts before the lock is dropped
VOCODER = VocoderId.AMBE_PLUS2_3600X2450
EMB_LCSS_EXPECT = (1, 3, 3, 2)  # bursts B..E: first, continuation x2, last


@dataclass
class _Lock:
    next_abs: int
    mode: str  # "bs" (CACH precedes each burst) | "ms"
    misses: int = 0


@dataclass
class _DataState:
    header: data_mod.DataHeader
    blocks: list[bytes] = field(default_factory=list)
    bad_blocks: int = 0
    rate_dt: Optional[int] = None


@dataclass
class _Slot:
    index: int
    call: Optional[Call] = None
    voice_idx: Optional[int] = None  # next expected voice burst 0..5 (A..F)
    emb: list = field(default_factory=lambda: [None] * 4)
    pending_frames: list = field(default_factory=list)
    voice_syncs: int = 0
    last_activity: float = 0.0
    counter: int = 0
    seq: int = 0
    session: Optional[VocoderSession] = None
    alias: lc_mod.TalkerAliasAssembler = field(default_factory=lc_mod.TalkerAliasAssembler)
    data: Optional[_DataState] = None
    mbc: csbk_mod.MbcAssembler = field(default_factory=csbk_mod.MbcAssembler)
    unavailable_reported: bool = False
    last_lc_key: Optional[tuple] = None


class DmrDecoder(ProtocolDecoder):
    protocol = Protocol.DMR
    symbol_rate = 4800.0
    modulation = Modulation.FSK4
    sync_specs = ("dmr_bs_voice", "dmr_bs_data", "dmr_ms_voice", "dmr_ms_data")
    timeslots = 2

    def __init__(self, emit, vocoders, config=None) -> None:
        super().__init__(emit, vocoders, config)
        self._sync_dibits = [(sw, sw.dibits) for sw in C.SYNC_WORDS]
        self.reset()

    # ------------------------------------------------------------------ lifecycle

    def reset(self) -> None:
        for slot in getattr(self, "_slots", []):
            self._end_call(slot, "retune", self._now)
        self._buf = np.empty(0, dtype=np.uint8)
        self._base = 0
        self._ref = (0, 0.0, 4800.0)  # first_index, t0, symbol rate of the newest block
        self._now = 0.0
        self._invert = False
        self._inv_votes = 0
        self._lock: Optional[_Lock] = None
        self._cc: Optional[int] = None
        self._cc_votes: Counter = Counter()
        self._slots = [_Slot(0), _Slot(1)]
        self._last_slot = 1
        self._short_lc: list[np.ndarray] = []
        self._system_key: Optional[tuple] = None
        self._announced_cc = False
        self._seen_tg: set[int] = set()
        self._stats = Counter()

    def stats(self) -> dict[str, Any]:
        s = self._stats
        total = s["bursts_ok"] + s["bursts_bad"]
        return {
            "frames_ok": s["bursts_ok"],
            "frames_bad": s["bursts_bad"],
            "fec_corrected": s["fec_corrected"],
            "fec_failed": s["fec_failed"],
            "sync_ratio": (s["sync_hits"] / total) if total else 0.0,
            "locked": self._lock is not None,
            "inverted": self._invert,
            "color_code": self._cc,
            "voice_frames": s["voice_frames"],
            "voice_frames_unavailable": s["voice_frames_unavailable"],
            "calls": s["calls"],
            "csbk": s["csbk"],
            "foreign_cc_bursts": s["foreign_cc"],
        }

    # ------------------------------------------------------------------ input

    def feed(self, block: SymbolBlock) -> None:
        d = np.asarray(block.dibits, dtype=np.uint8) & 3
        if d.size == 0:
            return
        if self._buf.size and block.first_index != self._base + self._buf.size:
            self._on_gap(block.first_index)
        if self._buf.size == 0:
            self._base = int(block.first_index)
        if self._invert != bool(block.polarity_inverted):
            d = d ^ 2
        self._buf = np.concatenate([self._buf, d])
        self._ref = (int(block.first_index), float(block.t0), float(block.symbol_rate or 4800.0))
        self._pump()

    def _on_gap(self, new_first: int) -> None:
        """Discontinuity in the symbol stream: nothing carried over is trustworthy."""
        for slot in self._slots:
            self._end_call(slot, "sync_loss", self._now)
            slot.voice_idx = None
            slot.emb = [None] * 4
        self._lock = None
        self._short_lc = []
        self._buf = np.empty(0, dtype=np.uint8)
        self._base = int(new_first)

    def _time_of(self, abs_index: int) -> float:
        first, t0, rate = self._ref
        return t0 + (abs_index - first) / rate

    # ------------------------------------------------------------------ sync and timing

    def _pump(self) -> None:
        while True:
            if self._lock is None:
                if not self._search():
                    return
            elif not self._step():
                return

    def _slot_type_ok(self, burst: np.ndarray) -> bool:
        """Strict slot-type check (Golay(20,8), at most one corrected bit) used to settle polarity."""
        bits = dibits_to_bits(burst)
        st = layer2.decode_slot_type(np.concatenate([bits[C.SLOT_TYPE_A], bits[C.SLOT_TYPE_B]]))
        return st is not None and st.corrected <= 1

    def _search(self) -> bool:
        """Find a burst to lock onto.

        A data sync is the exact symbol complement of the voice sync of the same family, so a
        sync word alone cannot tell "voice, normal polarity" from "data, inverted polarity".
        The slot type (Golay-protected) settles it: a data burst must decode in the polarity
        it is read in."""
        buf = self._buf
        n_pos = buf.size - (C.SYNC_DIBITS - 1)
        if n_pos <= 0:
            return False
        cands = []
        for sw, pattern in self._sync_dibits:
            err = np.zeros(n_pos, dtype=np.int16)
            for j in range(C.SYNC_DIBITS):
                err += buf[j : j + n_pos] != pattern[j]
            for pos in np.flatnonzero(err <= SEARCH_MAX_ERRORS)[:16]:
                cands.append((int(pos), int(err[pos]), sw))
        cands.sort(key=lambda c: (c[0], c[1]))
        for pos, errors, sw in cands:
            if sw.traffic == "rc":
                continue
            start = pos - C.SYNC_DIBIT_OFFSET
            cach_len = C.CACH_DIBITS if sw.source == "bs" else 0
            if start - cach_len < 0:
                continue
            if start + C.BURST_DIBITS > buf.size:
                self._trim_search(cands_before=pos)
                return False  # the burst is not fully here yet
            burst = buf[start : start + C.BURST_DIBITS]
            invert, use = False, sw
            if sw.traffic == "data":
                if not self._slot_type_ok(burst):
                    self._inv_votes += 1
                    if self._inv_votes < 3:
                        continue
                    # Three data-looking syncs whose slot types never decode: an inverted voice stream.
                    invert, use = True, C.SYNC_BY_NAME[sw.name.replace("data", "voice")]
            else:  # voice-looking: it may be an inverted data burst
                if self._slot_type_ok(burst ^ 2):
                    invert, use = True, C.SYNC_BY_NAME[sw.name.replace("voice", "data")]
            self._inv_votes = 0
            if invert:
                self._buf = self._buf ^ 2
                self._invert = not self._invert
            self._lock = _Lock(self._base + start, "bs" if use.source == "bs" else "ms")
            self.emit("sync", SyncEvent(Protocol.DMR, use.name, errors, self._invert, self._base + pos))
            self._stats["locks"] += 1
            return True
        self._trim_search()
        return False

    def _trim_search(self, cands_before: Optional[int] = None) -> None:
        keep = C.SLOT_DIBITS + C.BURST_DIBITS
        limit = self._buf.size - keep
        if cands_before is not None:
            limit = min(limit, cands_before - C.SYNC_DIBIT_OFFSET - C.CACH_DIBITS - 1)
        if limit > 0:
            self._drop(limit)

    def _drop(self, count: int) -> None:
        if count > 0:
            self._buf = self._buf[count:]
            self._base += count

    def _match_sync(self, burst: np.ndarray) -> Optional[tuple[C.SyncWord, int]]:
        center = burst[C.SYNC_DIBIT_OFFSET : C.SYNC_DIBIT_OFFSET + C.SYNC_DIBITS]
        best = None
        for sw, pattern in self._sync_dibits:
            e = int(np.count_nonzero(center != pattern))
            if e <= TRACK_MAX_ERRORS and (best is None or e < best[1]):
                best = (sw, e)
        return best

    def _step(self) -> bool:
        lk = self._lock
        assert lk is not None
        cach_len = C.CACH_DIBITS if lk.mode == "bs" else 0
        rel = lk.next_abs - self._base
        if rel - cach_len < 0:
            lk.next_abs += C.SLOT_DIBITS  # its head was already discarded
            return True
        if rel + C.BURST_DIBITS + ALIGN_RANGE + 1 > self._buf.size:
            return False
        shift = 0
        if self._match_sync(self._buf[rel : rel + C.BURST_DIBITS]) is None:
            shift = self._align(rel)
        rel += shift
        if rel - cach_len < 0:
            lk.next_abs += C.SLOT_DIBITS
            return True
        burst = self._buf[rel : rel + C.BURST_DIBITS]
        cach = self._buf[rel - cach_len : rel] if cach_len else None
        t = self._time_of(self._base + rel)
        self._now = t
        ok = self._handle_burst(burst, cach, t)
        lk.misses = 0 if ok else lk.misses + 1
        self._stats["bursts_ok" if ok else "bursts_bad"] += 1
        lk.next_abs += shift + C.SLOT_DIBITS
        self._expire(t)
        if lk.misses >= MAX_MISSES:
            for slot in self._slots:
                self._end_call(slot, "sync_loss", t)
                slot.voice_idx = None
                slot.emb = [None] * 4
            self._lock = None
            self._short_lc = []
            self.emit("error", ErrorEvent("sync_lost", Severity.INFO, "DMR frame sync lost", {}))
        trim = (lk.next_abs - self._base) - C.CACH_DIBITS - 2 * ALIGN_RANGE - 2
        if trim > 0:
            self._drop(trim)
        return True

    def _align(self, rel: int) -> int:
        """Look for a sync word a few symbols either side of the predicted position."""
        best = None
        for off in sorted(range(-ALIGN_RANGE, ALIGN_RANGE + 1), key=abs):
            if off == 0 or rel + off < 0 or rel + off + C.BURST_DIBITS > self._buf.size:
                continue
            m = self._match_sync(self._buf[rel + off : rel + off + C.BURST_DIBITS])
            if m is not None and m[1] <= 2 and (best is None or m[1] < best[1]):
                best = (off, m[1])
        return best[0] if best else 0

    # ------------------------------------------------------------------ burst handling

    def _handle_burst(self, burst: np.ndarray, cach: Optional[np.ndarray], t: float) -> bool:
        bits = dibits_to_bits(burst)
        lk = self._lock
        assert lk is not None
        tact = None
        if cach is not None:
            tact, payload = layer2.decode_cach(dibits_to_bits(cach))
            if tact is not None:
                self._on_short_lc(tact, payload)
        sw_match = self._match_sync(burst)
        sw = sw_match[0] if sw_match else None
        if sw is not None:
            self._stats["sync_hits"] += 1

        slot_no = None
        if sw is not None and sw.slot is not None:
            slot_no = sw.slot
        elif tact is not None:
            slot_no = tact.tc
        else:
            slot_no = self._last_slot ^ 1 if lk.mode == "bs" else 0
        self._last_slot = slot_no
        slot = self._slots[slot_no]

        if sw is not None and sw.traffic == "rc":
            return True
        if sw is not None and sw.traffic == "voice":
            slot.voice_idx = 0
            return self._voice_burst(slot, bits, 0, t, from_sync=True)
        if sw is not None and sw.traffic == "data":
            return self._data_burst(slot, bits, t)
        if slot.voice_idx is not None:  # inside a voice superframe: bursts B-F carry EMB, not sync
            return self._voice_burst(slot, bits, slot.voice_idx, t, from_sync=False)
        self._stats["unclassified"] += 1
        return False

    # -- colour code ----------------------------------------------------------------

    def _cc_accept(self, cc: int, corrected: int) -> bool:
        forced = self.config.extra.get("color_code")
        if forced is not None:
            return cc == forced
        if self._cc is None:
            if corrected == 0:
                self._cc_votes[cc] += 1
                if self._cc_votes[cc] >= 2:
                    self._cc = cc
                    self._emit_system(cc=cc)
            return True
        if cc == self._cc:
            return True
        self._stats["foreign_cc"] += 1
        return False

    # -- voice ----------------------------------------------------------------------

    def _voice_burst(self, slot: _Slot, bits: np.ndarray, idx: int, t: float, from_sync: bool) -> bool:
        if not from_sync:
            emb = layer2.decode_emb(np.concatenate([bits[C.EMB_FIRST], bits[C.EMB_LAST]]))
            if emb is not None:
                self._stats["fec_corrected"] += emb.corrected
                if not self._cc_accept(emb.cc, emb.corrected):
                    return False
            else:
                self._stats["fec_failed"] += 1
            if 1 <= idx <= 4:
                good = emb is not None and emb.pi == 0 and emb.lcss == EMB_LCSS_EXPECT[idx - 1]
                slot.emb[idx - 1] = bits[C.EMBEDDED].copy() if good else None
        else:
            slot.voice_syncs += 1
            slot.emb = [None] * 4
        slot.voice_idx = (idx + 1) % C.SUPERFRAME_BURSTS
        slot.last_activity = t

        if idx == 4 and all(f is not None for f in slot.emb):
            res = bptc.embedded_lc_decode(np.concatenate(slot.emb))
            if res.ok:
                self._stats["fec_corrected"] += res.corrected
                self._on_lc(slot, lc_mod.parse_full_lc(bits_to_bytes(res.data)), t, "embedded")
            else:
                self._stats["fec_failed"] += 1
        if slot.call is None and slot.voice_syncs >= 2 and idx == 5:
            self._start_call(slot, None, t, late_entry_no_lc=True)

        vs = np.concatenate([bits[C.VOICE_A], bits[C.VOICE_B]])
        frames = [vs[0:72], vs[72:144], vs[144:216]]
        if slot.call is None:
            slot.pending_frames = (slot.pending_frames + frames)[-36:]
        else:
            self._emit_frames(slot, frames, t)
        return True

    def _emit_frames(self, slot: _Slot, frames: list[np.ndarray], t: float) -> None:
        call = slot.call
        assert call is not None
        for raw in frames:
            call.voice_frames += 1
            self._stats["voice_frames"] += 1
            if call.encryption.state == EncryptionState.ENCRYPTED:
                continue
            frame = self._vocoder_frame(slot, raw)
            if frame is None:
                self._stats["voice_frames_unavailable"] += 1
                continue
            if slot.session is None:
                try:
                    slot.session = VocoderSession(VOCODER, backend=self.vocoders(VOCODER))
                except Exception as exc:  # noqa: BLE001 - a backend fault must not stop decoding
                    self._report_unavailable(slot, f"vocoder could not start: {exc}")
                    continue
            out = slot.session.push(frame)
            if not out.available:
                self._report_unavailable(slot, "no AMBE+2 vocoder backend is available")
                continue
            call.encryption.audio_decoded = True
            slot.seq += 1
            self.emit(
                "voice",
                VoiceFrame(call.call_id, slot.seq, VOCODER, out.pcm8k, 8000, frame.fec_errors, time.time()),
            )

    def _vocoder_frame(self, slot: _Slot, raw72: np.ndarray) -> Optional[VocoderFrame]:
        hook = self.config.extra.get("ambe_channel_decode")
        if hook is None and not self.config.extra.get("ambe_raw_frames"):
            hook = voice_map.decode_air72
        if hook is not None:
            res = hook(raw72)
            if res is None:
                return VocoderFrame(VOCODER, np.zeros(49, np.uint8), Protocol.DMR, erased=True, raw_bits=raw72)
            bits49, errors = res
            return VocoderFrame(VOCODER, np.asarray(bits49, np.uint8), Protocol.DMR, fec_errors=int(errors), raw_bits=raw72)
        if self.config.extra.get("ambe_raw_frames"):
            return VocoderFrame(VOCODER, np.zeros(0, np.uint8), Protocol.DMR, raw_bits=raw72)
        self._report_unavailable(
            slot,
            "AMBE+2 voice needs a channel decode (72 -> 49 bits) and a vocoder backend; this configuration "
            "has neither. Calls and metadata are still decoded.",
        )
        return None

    def _report_unavailable(self, slot: _Slot, why: str) -> None:
        if slot.unavailable_reported or slot.call is None:
            return
        slot.unavailable_reported = True
        self.emit(
            "error",
            ErrorEvent("vocoder_unavailable", Severity.WARNING, why, {"call_id": slot.call.call_id, "slot": slot.index}),
        )

    # -- link control ---------------------------------------------------------------

    def _on_lc(self, slot: _Slot, lc: lc_mod.FullLc, t: float, source: str) -> None:
        gps = lc_mod.parse_gps(lc)
        if lc.fid == 0 and lc.flco in (lc_mod.FLCO_TA_HEADER, lc_mod.FLCO_TA_BLOCK1, lc_mod.FLCO_TA_BLOCK2, lc_mod.FLCO_TA_BLOCK3):
            text = slot.alias.add(lc)
            if text and slot.call is not None and slot.call.meta.get("talker_alias") != text:
                slot.call.meta["talker_alias"] = text
                slot.call.meta["talker_alias_format"] = slot.alias.format_name
                self._update_call(slot, t)
            return
        if gps is not None:
            if slot.call is not None:
                slot.call.meta["gps"] = gps
                self._update_call(slot, t)
            return
        if slot.call is None:
            self._start_call(slot, lc, t)
        else:
            self._apply_lc(slot.call, lc)
            self._update_call(slot, t)

    def _apply_lc(self, call: Call, lc: Optional[lc_mod.FullLc]) -> None:
        if lc is None:
            return
        vu = lc_mod.parse_voice_user(lc)
        if vu is None:
            call.meta["lc"] = lc.name
            if lc.fid != 0:
                call.meta["mfid"] = lc.fid
            return
        call.kind = vu.kind
        call.target_id = vu.target
        call.source_id = vu.source
        call.emergency = vu.service.emergency
        call.priority = vu.service.priority
        call.meta["service_options"] = {
            "emergency": vu.service.emergency, "privacy": vu.service.privacy,
            "broadcast": vu.service.broadcast, "ovcm": vu.service.ovcm, "priority": vu.service.priority,
        }
        if vu.service.privacy or call.meta.get("pi_header"):
            call.encryption = EncryptionInfo(EncryptionState.ENCRYPTED)
        elif call.encryption.state != EncryptionState.ENCRYPTED:
            call.encryption.state = EncryptionState.CLEAR

    def _start_call(self, slot: _Slot, lc: Optional[lc_mod.FullLc], t: float, late_entry_no_lc: bool = False) -> None:
        slot.counter += 1
        slot.seq = 0
        slot.unavailable_reported = False
        slot.alias.reset()
        now = time.time()
        call = Call(
            call_id=f"dmr-{self.config.channel}-{slot.index}-{slot.counter}",
            protocol=Protocol.DMR,
            slot=slot.index,
            frequency_hz=self.config.frequency_hz,
            channel=self.config.channel,
            started_at=now,
            updated_at=now,
        )
        if late_entry_no_lc:
            call.meta["late_entry_no_lc"] = True
        self._apply_lc(call, lc)
        slot.call = call
        slot.last_activity = t
        slot.last_lc_key = self._call_key(call)
        self._stats["calls"] += 1
        self._announce_talkgroup(call)
        self.emit("call_start", self._snapshot(call))
        pending, slot.pending_frames = slot.pending_frames, []
        if pending:
            self._emit_frames(slot, pending, t)

    def _announce_talkgroup(self, call: Call) -> None:
        if call.kind == CallKind.GROUP and call.target_id is not None and call.target_id not in self._seen_tg:
            self._seen_tg.add(call.target_id)
            self.emit(
                "talkgroup",
                Talkgroup(call.target_id, Protocol.DMR, self.config.talkgroup_labels.get(call.target_id), time.time(), 1,
                          int(call.encryption.state == EncryptionState.ENCRYPTED)),
            )

    @staticmethod
    def _snapshot(call: Call) -> Call:
        import copy

        return copy.deepcopy(call)

    @staticmethod
    def _call_key(call: Call) -> tuple:
        return (call.kind, call.target_id, call.source_id, call.emergency, call.encryption.state,
                call.meta.get("talker_alias"), call.meta.get("pi_header"), str(call.meta.get("gps")))

    def _update_call(self, slot: _Slot, t: float) -> None:
        call = slot.call
        if call is None:
            return
        key = self._call_key(call)
        if key == slot.last_lc_key:
            return
        slot.last_lc_key = key
        call.updated_at = time.time()
        self._announce_talkgroup(call)
        self.emit("call_update", self._snapshot(call))

    def _end_call(self, slot: _Slot, reason: str, t: float) -> None:
        call = slot.call
        if call is None:
            return
        call.ended_at = time.time()
        call.updated_at = call.ended_at
        call.end_reason = reason
        if slot.session is not None:
            slot.session.close()
            slot.session = None
        slot.call = None
        slot.last_lc_key = None
        slot.pending_frames = []
        slot.voice_syncs = 0
        slot.data = None
        self.emit("call_end", self._snapshot(call))

    def _expire(self, t: float) -> None:
        for slot in self._slots:
            if slot.call is not None and t - slot.last_activity > self.config.hang_time_s:
                self._end_call(slot, "timeout", t)
                slot.voice_idx = None
                slot.emb = [None] * 4

    # -- data / control bursts ------------------------------------------------------

    def _data_burst(self, slot: _Slot, bits: np.ndarray, t: float) -> bool:
        st = layer2.decode_slot_type(np.concatenate([bits[C.SLOT_TYPE_A], bits[C.SLOT_TYPE_B]]))
        if st is None:
            self._stats["fec_failed"] += 1
            return False
        self._stats["fec_corrected"] += st.corrected
        if not self._cc_accept(st.cc, st.corrected):
            return False
        try:
            dt = C.DataType(st.data_type)
        except ValueError:
            return True  # reserved data type
        info = np.concatenate([bits[C.DATA_INFO_A], bits[C.DATA_INFO_B]])
        if dt != C.DataType.IDLE:
            slot.last_activity = t if slot.call is not None else slot.last_activity

        if dt == C.DataType.VOICE_LC_HEADER:
            res = lc_mod.decode_lc_burst(info, dt)
            if res is None:
                self._stats["fec_failed"] += 1
                return False
            self._stats["fec_corrected"] += res[1]
            slot.voice_idx = 0
            slot.voice_syncs = 0
            if slot.call is not None and slot.call.meta.get("data"):
                self._end_call(slot, "terminator", t)
            if slot.call is None:
                self._start_call(slot, res[0], t)
            else:
                self._apply_lc(slot.call, res[0])
                self._update_call(slot, t)
            slot.last_activity = t
            return True
        if dt == C.DataType.TERMINATOR_WITH_LC:
            res = lc_mod.decode_lc_burst(info, dt)
            if res is not None:
                self._stats["fec_corrected"] += res[1]
                if slot.call is None:
                    self._start_call(slot, res[0], t)
                else:
                    self._apply_lc(slot.call, res[0])
            slot.voice_idx = None
            slot.emb = [None] * 4
            self._end_call(slot, "terminator", t)
            return True
        if dt == C.DataType.PI_HEADER:
            out = csbk_mod.decode_block(info, dt)
            if out is None:
                self._stats["fec_failed"] += 1
                return False
            if slot.call is not None:
                slot.call.meta["pi_header"] = True
                slot.call.encryption = EncryptionInfo(EncryptionState.ENCRYPTED)
                self._update_call(slot, t)
            return True
        if dt == C.DataType.CSBK:
            return self._on_csbk(info)
        if dt == C.DataType.MBC_HEADER:
            out = csbk_mod.decode_block(info, dt)
            if out is None:
                self._stats["fec_failed"] += 1
                return False
            slot.mbc.add_header(out[0])
            return True
        if dt == C.DataType.MBC_CONTINUATION:
            res = bptc.bptc_196_96_decode(info)
            if not res.ok:
                self._stats["fec_failed"] += 1
                return False
            done = slot.mbc.add_continuation(bits_to_bytes(res.data))
            if done is not None:
                header, body = done
                self._emit_csbk_info(csbk_mod.describe_csbk(header), multi_block=True)
            return True
        if dt == C.DataType.DATA_HEADER:
            return self._on_data_header(slot, info, t)
        if dt in (C.DataType.RATE_12_DATA, C.DataType.RATE_34_DATA):
            return self._on_data_block(slot, info, dt, t)
        if dt == C.DataType.RATE_1_DATA or dt == C.DataType.UNIFIED_SINGLE_BLOCK:
            self._stats["unsupported_" + dt.name.lower()] += 1
            return True
        return True  # Idle

    def _on_csbk(self, info: np.ndarray) -> bool:
        res = csbk_mod.decode_csbk_block(info)
        if res is None:
            self._stats["fec_failed"] += 1
            return False
        self._stats["fec_corrected"] += res[1]
        self._stats["csbk"] += 1
        self._emit_csbk_info(csbk_mod.describe_csbk(res[0]))
        return True

    def _emit_csbk_info(self, info: csbk_mod.CsbkInfo, multi_block: bool = False) -> None:
        if info.system is not None:
            s = info.system
            self._emit_system(
                network_id=s["net"], site_id=s["site"],
                extra={"model": ("tiny", "small", "large", "huge")[s["model"]], "par": s["par"], "syscode": s["syscode"]},
            )
            if info.name == "C_ALOHA" and not self._announced_cc:
                self._announced_cc = True
                self.emit("trunk", TrunkEvent("cc_acquired", self.config.frequency_hz, detail={"source": "C_ALOHA"}))
        if info.grant is not None:
            g = info.grant
            self.emit(
                "trunk",
                TrunkEvent(
                    "grant", None, g.get("target") if g.get("call") != "private" else None, g.get("source"),
                    g.get("tdma_slot"), False,
                    {k: v for k, v in g.items() if k not in ("target", "source")} | {"target": g.get("target"), "multi_block": multi_block},
                ),
            )
        elif info.system is None:
            self.emit(
                "trunk",
                TrunkEvent("system_update", self.config.frequency_hz, detail={"csbk": info.name, **info.fields}),
            )

    def _emit_system(self, cc: Optional[int] = None, network_id: Optional[int] = None,
                     site_id: Optional[int] = None, extra: Optional[dict] = None) -> None:
        key = (cc if cc is not None else self._cc, network_id, site_id, tuple(sorted((extra or {}).items())))
        if key == self._system_key:
            return
        self._system_key = key
        self.emit(
            "system_info",
            SystemInfo(Protocol.DMR, color_code=key[0], network_id=network_id, site_id=site_id,
                       control_channel_hz=self.config.frequency_hz if network_id is not None else None,
                       extra=extra or {}),
        )

    def _on_short_lc(self, tact: layer2.Tact, payload: np.ndarray) -> None:
        """Collect the four CACH fragments of a Short LC (LCSS 01, 11, 11, 10)."""
        if tact.lcss == C.Lcss.FIRST:
            self._short_lc = [payload.copy()]
        elif tact.lcss == C.Lcss.CONTINUATION and self._short_lc:
            self._short_lc.append(payload.copy())
        elif tact.lcss == C.Lcss.LAST and self._short_lc:
            self._short_lc.append(payload.copy())
            parts, self._short_lc = self._short_lc, []
            res = layer2.decode_short_lc(parts)
            if res is None:
                self._stats["fec_failed"] += 1
                return
            self._stats["fec_corrected"] += res.corrected
            if res.slco in (2, 3):  # C_SYS_Parms / P_SYS_Parms (TS 102 361-4 tables 7.33, 7.34)
                model = (res.data >> 22) & 3
                body = (res.data >> 10) & 0xFFF
                net_bits, site_bits = {0: (9, 3), 1: (7, 5), 2: (4, 8), 3: (2, 10)}[model]
                self._emit_system(
                    network_id=body >> site_bits, site_id=body & ((1 << site_bits) - 1),
                    extra={"model": ("tiny", "small", "large", "huge")[model],
                           "slco": "C_SYS_Parms" if res.slco == 2 else "P_SYS_Parms",
                           "reg": bool((res.data >> 9) & 1), "common_slot_counter": res.data & 0x1FF},
                )
        else:
            self._short_lc = []

    # -- packet data ----------------------------------------------------------------

    def _on_data_header(self, slot: _Slot, info: np.ndarray, t: float) -> bool:
        out = csbk_mod.decode_block(info, C.DataType.DATA_HEADER)
        if out is None:
            self._stats["fec_failed"] += 1
            return False
        self._stats["fec_corrected"] += out[1]
        header = data_mod.parse_data_header(out[0])
        if header is None:
            return True
        if slot.call is not None:
            self._end_call(slot, "terminator", t)
        slot.counter += 1
        now = time.time()
        call = Call(
            call_id=f"dmr-{self.config.channel}-{slot.index}-{slot.counter}", protocol=Protocol.DMR, slot=slot.index,
            kind=CallKind.DATA, target_id=header.dst, source_id=header.src, frequency_hz=self.config.frequency_hz,
            channel=self.config.channel, started_at=now, updated_at=now,
            encryption=EncryptionInfo(EncryptionState.UNKNOWN),
            meta={"data": True, "dpf": header.dpf_name, "sap": header.sap_name, "group": header.group,
                  "blocks_to_follow": header.blocks_to_follow, "confirmed": header.confirmed},
        )
        slot.call = call
        slot.voice_idx = None
        slot.data = _DataState(header)
        slot.last_activity = t
        self._stats["calls"] += 1
        self.emit("call_start", self._snapshot(call))
        if header.blocks_to_follow == 0:
            self._finish_data(slot, t)
        return True

    def _on_data_block(self, slot: _Slot, info: np.ndarray, dt: C.DataType, t: float) -> bool:
        state = slot.data
        if state is None or slot.call is None:
            return True  # continuation without a header we saw
        if dt == C.DataType.RATE_12_DATA:
            res = bptc.bptc_196_96_decode(info)
            ok, raw = res.ok, bits_to_bytes(res.data)
        else:
            res = trellis.dmr_trellis_three_quarter_decode(bits_to_dibits(info))
            ok, raw = res.ok, bits_to_bytes(res.data)
        if not ok:
            self._stats["fec_failed"] += 1
            state.bad_blocks += 1
        else:
            self._stats["fec_corrected"] += res.corrected
        state.rate_dt = int(dt)
        state.blocks.append(raw if ok else b"")
        slot.last_activity = t
        if len(state.blocks) >= state.header.blocks_to_follow:
            self._finish_data(slot, t)
        return ok

    def _finish_data(self, slot: _Slot, t: float) -> None:
        state, call = slot.data, slot.call
        if state is None or call is None:
            return
        header = state.header
        call.meta["blocks_received"] = len(state.blocks)
        call.meta["blocks_bad"] = state.bad_blocks
        if state.blocks and not state.bad_blocks:
            payload = b"".join(b[2:] if header.confirmed else b for b in state.blocks)
            body, tail = payload[:-4], payload[-4:]
            if len(body) % 2 == 0 and len(tail) == 4:
                call.meta["message_crc_ok"] = crc.crc32_dmr_message(body) == tail
                if call.meta["message_crc_ok"] and self.config.decode_data:
                    call.meta["payload_hex"] = body.hex()
        self._end_call(slot, "terminator", t)
