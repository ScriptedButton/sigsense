"""P25 Phase 2 (two-slot TDMA, 6000 sym/s) decoder. Original code; layout facts are documented in framing.py.

Pipeline per channel:

    dibits -> S-ISCH frame sync pair (slots C and D, either polarity) -> fragment clock (720 dibits)
    -> per fragment: I-ISCH (slots A, B: which fragment of the superframe, channel type)
    -> per slot: DUID (8,4,4), descramble (WACN/system/NAC LFSR), then
         4V / 2V voice : four / two 72-bit AMBE+2 frames + encryption-sync pieces (RS(44,16) over 4 bursts + 1)
         SACCH/FACCH/LCCH : RS(63,35) shortened + CRC -> MAC PDU (PTT, END-PTT, user/grant/status structures)
    -> typed events (calls, system info, trunk grants), vocoder frames to per-call sessions

Scope policy (docs/digital-voice-architecture.md): clear traffic only. The encryption state is reported
(PTT / ESS algorithm id, service-option protected bit) and such calls never reach a vocoder.

Scrambling needs (WACN, system id, NAC). They are taken from `config.extra` ("wacn", "system_id", "nac") or
learned from an unscrambled Network Status Broadcast on the channel; until then voice is counted but not
decoded, and an `error` event (`p25p2_scramble_unknown`) says why.

AMBE+2 voice: the 72-bit frames go through the same channel decode as DMR (`dmr.voice_map.decode_air72`),
as SDRTrunk feeds both to the one AMBE 3600x2450 decoder; override with `config.extra["ambe_channel_decode"]`
or pass the raw frame to a hardware vocoder with `config.extra["ambe_raw_frames"] = True`.
"""

from __future__ import annotations

import copy
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
from sigsense_api.dvoice.protocols.base import ProtocolDecoder
from sigsense_api.dvoice.protocols.dmr import voice_map
from sigsense_api.dvoice.protocols.dmr.bits import dibits_to_bits
from sigsense_api.dvoice.protocols.p25p1 import words
from sigsense_api.dvoice.protocols.p25p2 import framing as fr
from sigsense_api.dvoice.protocols.p25p2 import mac as mac_mod
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS
from sigsense_api.dvoice.vocoder.base import VocoderFrame
from sigsense_api.dvoice.vocoder.session import VocoderSession

VOCODER = VocoderId.AMBE_PLUS2_3600X2450
SEARCH_MAX_ERRORS = 3  # sync symbol errors tolerated while hunting (20 symbols)
SEARCH_PAIR_ERRORS = 5  # ... for the second sync of the pair
TRACK_MAX_ERRORS = 8  # summed over the two syncs of a tracked fragment
ALIGN_RANGE = 2  # symbols of timing slip searched around the predicted fragment
MAX_MISSES = 4  # consecutive fragments without sync before the lock is dropped
LATE_ENTRY_SLOTS = 2  # voice slots heard with no call before one is started from them
PENDING_MAX_SLOTS = 14  # voice slots held while the encryption state is unknown (> 2 superframes)
ALG_CLEAR = (0x80, 0x00)

_SYNC = np.array(SYNC_SPECS["p25p2"].values, dtype=np.uint8)
_SYNC_INV = _SYNC ^ 2


@dataclass
class _Lock:
    next_abs: int  # absolute dibit index of the next fragment start
    misses: int = 0


@dataclass
class _Chan:
    index: int
    call: Optional[Call] = None
    session: Optional[VocoderSession] = None
    counter: int = 0
    seq: int = 0
    voice_slots: int = 0
    pending: list = field(default_factory=list)  # (frames, t) held until the encryption state is known
    enc_known: bool = False
    ess_b: list = field(default_factory=lambda: [None] * 4)
    last_activity: float = 0.0
    unavailable_reported: bool = False
    last_key: Optional[tuple] = None


class P25P2Decoder(ProtocolDecoder):
    protocol = Protocol.P25P2
    symbol_rate = 6000.0
    modulation = Modulation.CQPSK
    sync_specs = ("p25p2",)
    timeslots = 2

    def __init__(self, emit, vocoders, config=None) -> None:
        super().__init__(emit, vocoders, config)
        self.reset()

    # ------------------------------------------------------------------ lifecycle

    def reset(self) -> None:
        for chan in getattr(self, "_chans", []):
            self._end_call(chan, "retune")
        self._buf = np.empty(0, dtype=np.uint8)
        self._base = 0
        self._ref = (0, 0.0, 6000.0)
        self._now = 0.0
        self._invert = False
        self._lock: Optional[_Lock] = None
        self._chans = [_Chan(0), _Chan(1)]
        self._next_seq: Optional[int] = None  # predicted ISCH sequence of the next fragment
        self._next_sf: Optional[int] = None
        self._predicted_run = 0
        self._params: Optional[tuple[int, int, int]] = self._configured_params()
        self._params_learned = False
        self._params_valid = False  # a CRC-checked scrambled MAC word or an RS-checked ESS has decoded with them
        self._scramble_warned = False
        self._system_key: Optional[tuple] = None
        self._seen_tg: set[int] = set()
        self._stats = Counter()

    def _configured_params(self) -> Optional[tuple[int, int, int]]:
        x = self.config.extra
        if all(x.get(k) is not None for k in ("wacn", "system_id", "nac")):
            return int(x["wacn"]), int(x["system_id"]), int(x["nac"])
        return None

    def stats(self) -> dict[str, Any]:
        s = self._stats
        total = s["fragments_ok"] + s["fragments_bad"]
        return {
            "frames_ok": s["slots_ok"],
            "frames_bad": s["slots_bad"],
            "fec_corrected": s["fec_corrected"],
            "fec_failed": s["fec_failed"],
            "sync_ratio": (s["fragments_ok"] / total) if total else 0.0,
            "locked": self._lock is not None,
            "inverted": self._invert,
            "scramble_known": self._params is not None,
            "voice_frames": s["voice_frames"],
            "voice_frames_unavailable": s["voice_frames_unavailable"],
            "voice_slots_unscrambled": s["voice_no_params"],
            "calls": s["calls"],
            "mac_ok": s["mac_ok"],
            "mac_bad": s["mac_bad"],
            "ess_ok": s["ess_ok"],
            "isch_predicted": s["isch_predicted"],
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
        self._ref = (int(block.first_index), float(block.t0), float(block.symbol_rate or 6000.0))
        self._pump()

    def _on_gap(self, new_first: int) -> None:
        for chan in self._chans:
            self._end_call(chan, "sync_loss")
            chan.ess_b = [None] * 4
        self._lock = None
        self._next_seq = None
        self._buf = np.empty(0, dtype=np.uint8)
        self._base = int(new_first)

    def _time_of(self, abs_index: int) -> float:
        first, t0, rate = self._ref
        return t0 + (abs_index - first) / rate

    def _drop(self, count: int) -> None:
        if count > 0:
            self._buf = self._buf[count:]
            self._base += count

    # ------------------------------------------------------------------ sync and timing

    def _pump(self) -> None:
        while True:
            if self._lock is None:
                if not self._search():
                    return
            elif not self._step():
                return

    @staticmethod
    def _sync_errors(buf: np.ndarray, pattern: np.ndarray) -> np.ndarray:
        n_pos = buf.size - fr.SYNC_DIBITS + 1
        err = np.zeros(max(n_pos, 0), dtype=np.int16)
        for j in range(fr.SYNC_DIBITS):
            err += buf[j : j + n_pos] != pattern[j]
        return err

    def _search(self) -> bool:
        """Find a pair of sync words 180 symbols apart (the S-ISCH of slots C and D)."""
        buf = self._buf
        need = fr.SLOT_DIBITS + fr.SYNC_DIBITS
        if buf.size < need:
            return False
        for inverted, pattern in ((False, _SYNC), (True, _SYNC_INV)):
            err = self._sync_errors(buf, pattern)
            first = err[: err.size - fr.SLOT_DIBITS]
            second = err[fr.SLOT_DIBITS :]
            hits = np.flatnonzero((first <= SEARCH_MAX_ERRORS) & (second <= SEARCH_PAIR_ERRORS))
            if hits.size == 0:
                continue
            pos = int(hits[0])
            if inverted:
                self._buf = self._buf ^ 2
                self._invert = not self._invert
            start = pos - fr.SYNC_OFFSETS[0]  # fragment start (relative to the buffer)
            self._lock = _Lock(self._base + start)
            self.emit("sync", SyncEvent(Protocol.P25P2, "p25p2", int(err[pos]), self._invert, self._base + pos))
            self._stats["locks"] += 1
            self._trim_to_lock()
            return True
        # nothing: keep only what could still be the head of a pair
        self._drop(buf.size - need)
        return False

    def _trim_to_lock(self) -> None:
        assert self._lock is not None
        trim = (self._lock.next_abs - self._base) - ALIGN_RANGE - 1
        if trim > 0:
            self._drop(trim)

    def _sync_sum(self, rel: int) -> int:
        a = self._buf[rel + fr.SYNC_OFFSETS[0] : rel + fr.SYNC_OFFSETS[0] + fr.SYNC_DIBITS]
        b = self._buf[rel + fr.SYNC_OFFSETS[1] : rel + fr.SYNC_OFFSETS[1] + fr.SYNC_DIBITS]
        return int(np.count_nonzero(a != _SYNC) + np.count_nonzero(b != _SYNC))

    def _step(self) -> bool:
        lk = self._lock
        assert lk is not None
        rel = lk.next_abs - self._base
        if rel < 0:
            lk.next_abs += fr.FRAGMENT_DIBITS  # its head was already discarded
            return True
        if rel + fr.FRAGMENT_DIBITS + ALIGN_RANGE >= self._buf.size:
            return False
        best_off, best = 0, self._sync_sum(rel)
        if best > TRACK_MAX_ERRORS:
            for off in sorted(range(-ALIGN_RANGE, ALIGN_RANGE + 1), key=abs):
                if off == 0 or rel + off < 0:
                    continue
                e = self._sync_sum(rel + off)
                if e < best:
                    best_off, best = off, e
        ok = best <= TRACK_MAX_ERRORS
        rel += best_off
        t = self._time_of(self._base + rel)
        self._now = t
        if ok:
            self._stats["fragments_ok"] += 1
            lk.misses = 0
            self._fragment(self._buf[rel : rel + fr.FRAGMENT_DIBITS], t)
        else:
            self._stats["fragments_bad"] += 1
            lk.misses += 1
            if self._next_seq is not None:  # the clock keeps running through short fades
                self._roll(self._next_seq, self._next_sf or 0)
        lk.next_abs += best_off + fr.FRAGMENT_DIBITS
        self._expire(t)
        if lk.misses >= MAX_MISSES:
            for chan in self._chans:
                self._end_call(chan, "sync_loss")
                chan.ess_b = [None] * 4
            self._lock = None
            self._next_seq = None
            self.emit("error", ErrorEvent("sync_lost", Severity.INFO, "P25 Phase 2 frame sync lost", {}))
        if self._lock is not None:
            self._trim_to_lock()
        return True

    def _roll(self, seq: int, superframe: int) -> None:
        """Predict the ISCH sequence / superframe number of the fragment after one carrying (seq, superframe)."""
        self._next_seq = (seq + 1) % fr.FRAGMENTS_PER_SUPERFRAME
        self._next_sf = (superframe + 1) % 4 if seq == fr.FRAGMENTS_PER_SUPERFRAME - 1 else superframe

    # ------------------------------------------------------------------ fragment

    def _resolve_isch(self, a: Optional[fr.Isch], b: Optional[fr.Isch]) -> Optional[tuple[int, int, int, bool]]:
        """-> (sequence, superframe, lch_type, predicted?) for this fragment, or None."""
        cands = [c for c in (a, b) if c is not None]
        pred = self._next_seq
        good = [c for c in cands if pred is None or c.seq == pred]
        if good:
            c = min(good, key=lambda x: x.errors)
            if pred is not None or c.errors <= 3 or len(cands) == 2 and cands[0].seq == cands[1].seq:
                return c.seq, c.superframe, c.lch_type, False
        elif cands:
            c = min(cands, key=lambda x: x.errors)
            if c.errors <= 3 or len(cands) == 2 and cands[0].seq == cands[1].seq:
                return c.seq, c.superframe, c.lch_type, False  # the clock slipped: trust the ISCH
        if pred is not None and self._predicted_run < 6:
            return pred, self._next_sf if self._next_sf is not None else 0, int(fr.LchType.VCH), True
        return None

    def _fragment(self, dibits: np.ndarray, t: float) -> None:
        bits = dibits_to_bits(dibits)
        a = fr.decode_isch(bits[0:fr.ISCH_BITS], 0)
        b = fr.decode_isch(bits[360 : 360 + fr.ISCH_BITS], 1)
        res = self._resolve_isch(a, b)
        if res is None:
            self._stats["isch_failed"] += 1
            self._stats["slots_bad"] += 4
            return
        seq, sf, _lch_type, predicted = res
        if predicted:
            self._predicted_run += 1
            self._stats["isch_predicted"] += 1
        else:
            self._predicted_run = 0
            self._stats["fec_corrected"] += min(x.errors for x in (a, b) if x is not None)
        self._roll(seq, sf)
        final = seq == fr.FRAGMENTS_PER_SUPERFRAME - 1
        for pos in range(fr.SLOTS_PER_FRAGMENT):
            start = pos * fr.SLOT_DIBITS * 2 + fr.ISCH_BITS
            body = bits[start : start + fr.BODY_BITS].copy()
            chan = fr.slot_channel(pos, final)
            self._slot(body, chan, fr.SLOTS_PER_FRAGMENT * seq + pos, fr.channel_ordinal(seq, pos), t + pos * 0.03)

    # ------------------------------------------------------------------ slots

    def _slot(self, body: np.ndarray, channel: int, slot_index: int, ordinal: int, t: float) -> None:
        duid, _ = fr.read_duid(body)
        if duid is None or duid not in tuple(int(d) for d in fr.Duid):
            self._stats["slots_bad"] += 1
            self._stats["duid_failed"] += 1
            return
        duid = fr.Duid(duid)
        if duid in fr.SCRAMBLED_DUIDS:
            if self._params is None:
                self._stats["voice_no_params"] += int(duid in (fr.Duid.VOICE_4, fr.Duid.VOICE_2))
                self._report_scramble_unknown()
                return
            body ^= fr.scramble_segment(self._params, slot_index)
        self._stats["slots_ok"] += 1
        chan = self._chans[channel]
        if ordinal == 0:
            chan.ess_b = [None] * 4  # a new superframe: the pieces held belong to the last one
        if duid in (fr.Duid.VOICE_4, fr.Duid.VOICE_2):
            self._voice_slot(chan, body, duid, ordinal, t)
        elif duid in (fr.Duid.SACCH_S, fr.Duid.SACCH_U):
            self._mac_slot(chan, body, fr.SACCH, "sacch", duid, t)
        elif duid in (fr.Duid.FACCH_S, fr.Duid.FACCH_U):
            self._mac_slot(chan, body, fr.FACCH, "facch", duid, t)
        elif duid == fr.Duid.LCCH_U:
            self._mac_slot(chan, body, fr.LCCH, "lcch", duid, t)
        else:
            self._stats["datch"] += 1  # data channel: not decoded

    def _report_scramble_unknown(self) -> None:
        if self._scramble_warned:
            return
        self._scramble_warned = True
        self.emit(
            "error",
            ErrorEvent(
                "p25p2_scramble_unknown", Severity.WARNING,
                "P25 Phase 2 traffic is scrambled with the system's WACN, system id and NAC. Set them "
                "(wacn / system_id / nac in the decoder options) or listen to the control channel first "
                "so a Network Status Broadcast supplies them.",
                {},
            ),
        )

    # -- voice -----------------------------------------------------------------------

    def _voice_slot(self, chan: _Chan, body: np.ndarray, duid: fr.Duid, ordinal: int, t: float) -> None:
        chan.last_activity = t
        chan.voice_slots += 1
        if duid == fr.Duid.VOICE_4:
            if ordinal < 4:
                chan.ess_b[ordinal] = fr.ess_b_bits(body).copy()
        else:
            self._on_ess(chan, fr.decode_ess(chan.ess_b, fr.ess_a_bits(body)))
            chan.ess_b = [None] * 4
        frames = fr.voice_frames(body, duid)
        if chan.call is None:
            # Voice has no checksum of its own: with wrong scrambler parameters it is just noise. A call is
            # only started from voice once something with an error check has decoded with these parameters.
            if chan.voice_slots >= LATE_ENTRY_SLOTS and self._params_valid:
                self._start_call(chan, {}, t, late_entry=True)
            else:
                chan.pending = (chan.pending + [(frames, t)])[-PENDING_MAX_SLOTS:]
                return
        if chan.call is not None and not chan.enc_known:
            chan.pending.append((frames, t))
            if len(chan.pending) > PENDING_MAX_SLOTS:
                chan.enc_known = True  # no ESS or PTT in two superframes: treat as unknown, decode
                self._flush(chan)
            return
        self._emit_frames(chan, frames, t)

    def _on_ess(self, chan: _Chan, ess: Optional[fr.Ess]) -> None:
        if ess is None:
            self._stats["ess_failed"] += 1
            return
        self._stats["ess_ok"] += 1
        self._stats["fec_corrected"] += ess.corrected
        self._params_valid = True
        if chan.call is None:
            return
        self._set_encryption(chan, ess.algorithm, ess.key_id, True)
        self._flush(chan)

    @staticmethod
    def _apply_encryption(call: Call, algorithm: int, key_id: int, mi_present: bool) -> bool:
        clear = algorithm in ALG_CLEAR
        enc = call.encryption
        new = (EncryptionState.CLEAR if clear else EncryptionState.ENCRYPTED, algorithm, None if clear else key_id)
        changed = (enc.state, enc.algorithm_id, enc.key_id) != new
        if changed:
            enc.state, enc.algorithm_id, enc.key_id = new
            enc.algorithm_name = words.algorithm_name(algorithm if algorithm else words.ALGID_CLEAR)
            enc.mi_present = mi_present and not clear
        return changed

    def _set_encryption(self, chan: _Chan, algorithm: int, key_id: int, mi_present: bool) -> None:
        call = chan.call
        assert call is not None
        if self._apply_encryption(call, algorithm, key_id, mi_present):
            self._update_call(chan)
        chan.enc_known = True
        if call.encryption.state == EncryptionState.ENCRYPTED:
            chan.pending = []

    def _flush(self, chan: _Chan) -> None:
        if not chan.enc_known or chan.call is None:
            return
        held, chan.pending = chan.pending, []
        for frames, t in held:
            self._emit_frames(chan, frames, t)

    def _emit_frames(self, chan: _Chan, frames: list[np.ndarray], t: float) -> None:
        call = chan.call
        assert call is not None
        for raw in frames:
            call.voice_frames += 1
            self._stats["voice_frames"] += 1
            if call.encryption.state == EncryptionState.ENCRYPTED:
                continue
            frame = self._vocoder_frame(chan, np.asarray(raw, dtype=np.uint8))
            if frame is None:
                self._stats["voice_frames_unavailable"] += 1
                continue
            if chan.session is None:
                try:
                    chan.session = VocoderSession(VOCODER, backend=self.vocoders(VOCODER))
                except Exception as exc:  # noqa: BLE001 - a backend fault must not stop decoding
                    self._report_unavailable(chan, f"vocoder could not start: {exc}")
                    continue
            out = chan.session.push(frame)
            if not out.available:
                self._report_unavailable(chan, "no AMBE+2 vocoder backend is available")
                continue
            call.encryption.audio_decoded = True
            chan.seq += 1
            self.emit("voice", VoiceFrame(call.call_id, chan.seq, VOCODER, out.pcm8k, 8000, frame.fec_errors, time.time()))

    def _vocoder_frame(self, chan: _Chan, raw72: np.ndarray) -> Optional[VocoderFrame]:
        hook = self.config.extra.get("ambe_channel_decode")
        if hook is None and not self.config.extra.get("ambe_raw_frames"):
            hook = voice_map.decode_air72
        if hook is not None:
            res = hook(raw72)
            if res is None:
                return VocoderFrame(VOCODER, np.zeros(49, np.uint8), Protocol.P25P2, erased=True, raw_bits=raw72)
            bits49, errors = res
            return VocoderFrame(VOCODER, np.asarray(bits49, np.uint8), Protocol.P25P2, fec_errors=int(errors), raw_bits=raw72)
        return VocoderFrame(VOCODER, np.zeros(0, np.uint8), Protocol.P25P2, raw_bits=raw72)

    def _report_unavailable(self, chan: _Chan, why: str) -> None:
        if chan.unavailable_reported or chan.call is None:
            return
        chan.unavailable_reported = True
        self.emit("error", ErrorEvent("vocoder_unavailable", Severity.WARNING, why,
                                      {"call_id": chan.call.call_id, "slot": chan.index}))

    # -- MAC -------------------------------------------------------------------------

    def _mac_slot(self, chan: _Chan, body: np.ndarray, channel: fr.MacChannel, name: str, duid: fr.Duid, t: float) -> None:
        word = fr.decode_mac(body, channel)
        if word is None or not word.crc_ok:
            self._stats["mac_bad"] += 1
            self._stats["fec_failed"] += 1
            return
        self._stats["mac_ok"] += 1
        self._stats["fec_corrected"] += word.corrected
        if duid in (fr.Duid.SACCH_S, fr.Duid.FACCH_S):
            self._params_valid = True
        pdu = mac_mod.parse_mac(word.bits, name)
        if name != "lcch":
            chan.last_activity = t
        for st in pdu.structures:
            self._structure(chan, pdu, st, name, t)
        if pdu.pdu_type == mac_mod.PDU_IDLE and chan.call is not None:
            self._end_call(chan, "terminator")

    def _structure(self, chan: _Chan, pdu: mac_mod.MacPdu, st: mac_mod.MacStructure, channel: str, t: float) -> None:
        f = st.fields
        op = st.opcode
        if st.name == "PUSH_TO_TALK":
            ids = {"group": f["group"], "source": f["source"], "algorithm": f["algorithm"], "key_id": f["key_id"]}
            if chan.call is not None and chan.call.target_id not in (None, f["group"]) and f["group"]:
                self._end_call(chan, "terminator")  # a new talker on the same slot
            if chan.call is None:
                self._start_call(chan, ids, t)
            else:
                self._apply_ids(chan.call, ids)
                self._set_encryption(chan, f["algorithm"], f["key_id"], True)
                self._flush(chan)
        elif st.name == "END_PUSH_TO_TALK":
            self._end_call(chan, "terminator")
        elif op in (mac_mod.OP_GVCU, mac_mod.OP_GVCU_EXT, mac_mod.OP_UUVCU):
            ids = {"group": f.get("group", f.get("target")), "source": f["source"], "service": f["service"],
                   "private": op == mac_mod.OP_UUVCU}
            if chan.call is None:
                self._start_call(chan, ids, t)
            else:
                self._apply_ids(chan.call, ids)
                self._update_call(chan)
        elif op == mac_mod.OP_MAC_RELEASE:
            self._end_call(chan, "terminator")
        elif op in (mac_mod.OP_NSB_IMPLICIT, mac_mod.OP_NSB_EXPLICIT):
            self._network_status(f)
        elif op in (mac_mod.OP_GRANT_IMPLICIT, mac_mod.OP_GRANT_EXPLICIT):
            svc = words.ServiceOptions.parse(f["service"])
            self.emit("trunk", TrunkEvent("grant", None, f["group"], f["source"], None, svc.protected,
                                          {"opcode": st.name, "channel": f["channel"], "iden": f["channel"] >> 12,
                                           "number": f["channel"] & 0xFFF, "emergency": svc.emergency,
                                           "priority": svc.priority, "service_options": svc.raw}))
        elif op == mac_mod.OP_GRANT_UPDATE_IMPLICIT:
            for ch, grp in ((f["channel"], f["group"]), (f.get("channel2"), f.get("group2"))):
                if ch is not None and grp:
                    self.emit("trunk", TrunkEvent("grant", None, grp, None, None, False,
                                                  {"opcode": st.name, "channel": ch, "iden": ch >> 12,
                                                   "number": ch & 0xFFF, "update": True}))

    def _network_status(self, f: dict[str, Any]) -> None:
        params = (f["wacn"], f["system_id"], f["nac"])
        if self._params != params and not self._configured_params():
            self._params = params
            self._params_learned = True
            self._params_valid = False
            self._scramble_warned = False
            self.emit("trunk", TrunkEvent("system_update", detail={"opcode": "NET_STS_BCST", "scramble": "learned"}))
        key = params + (f["channel"],)
        if key != self._system_key:
            self._system_key = key
            self.emit("system_info", SystemInfo(Protocol.P25P2, wacn=f["wacn"], system_id=f["system_id"], nac=f["nac"],
                                                extra={"lra": f["lra"], "service_class": f["service_class"],
                                                       "channel": f["channel"], "scramble_learned": self._params_learned}))

    # -- calls -----------------------------------------------------------------------

    def _apply_ids(self, call: Call, ids: dict[str, Any]) -> None:
        group = ids.get("group")
        if group:
            call.target_id = group
        if ids.get("source"):
            call.source_id = ids["source"]
        call.kind = CallKind.PRIVATE if ids.get("private") else (CallKind.GROUP if call.target_id else call.kind)
        if "service" in ids:
            svc = words.ServiceOptions.parse(ids["service"])
            call.emergency = svc.emergency
            call.priority = svc.priority
            call.meta["service_options"] = {"emergency": svc.emergency, "protected": svc.protected,
                                            "packet": svc.packet_mode, "priority": svc.priority}
            if svc.protected and call.encryption.state != EncryptionState.ENCRYPTED:
                call.encryption.state = EncryptionState.ENCRYPTED
                call.encryption.algorithm_name = call.encryption.algorithm_name or "protected (see ESS)"

    def _start_call(self, chan: _Chan, ids: dict[str, Any], t: float, late_entry: bool = False) -> None:
        chan.counter += 1
        chan.seq = 0
        chan.unavailable_reported = False
        chan.enc_known = False
        now = time.time()
        call = Call(
            call_id=f"p25p2-{self.config.channel}-{chan.index}-{chan.counter}",
            protocol=Protocol.P25P2,
            slot=chan.index,
            frequency_hz=self.config.frequency_hz,
            channel=self.config.channel,
            started_at=now,
            updated_at=now,
        )
        if late_entry:
            call.meta["late_entry_no_ptt"] = True
        self._apply_ids(call, ids)
        if "algorithm" in ids:
            self._apply_encryption(call, ids["algorithm"], ids["key_id"], True)
        chan.enc_known = "algorithm" in ids or call.encryption.state == EncryptionState.ENCRYPTED
        chan.call = call
        chan.last_activity = t
        chan.last_key = self._call_key(call)
        self._stats["calls"] += 1
        self._announce_talkgroup(call)
        self.emit("call_start", copy.deepcopy(call))
        if call.encryption.state == EncryptionState.ENCRYPTED:
            chan.pending = []
        self._flush(chan)

    def _announce_talkgroup(self, call: Call) -> None:
        if call.kind == CallKind.GROUP and call.target_id is not None and call.target_id not in self._seen_tg:
            self._seen_tg.add(call.target_id)
            self.emit("talkgroup", Talkgroup(call.target_id, Protocol.P25P2, self.config.talkgroup_labels.get(call.target_id),
                                             time.time(), 1, int(call.encryption.state == EncryptionState.ENCRYPTED)))

    @staticmethod
    def _call_key(call: Call) -> tuple:
        return (call.kind, call.target_id, call.source_id, call.emergency, call.encryption.state,
                call.encryption.algorithm_id, call.encryption.key_id)

    def _update_call(self, chan: _Chan) -> None:
        call = chan.call
        if call is None:
            return
        key = self._call_key(call)
        if key == chan.last_key:
            return
        chan.last_key = key
        call.updated_at = time.time()
        self._announce_talkgroup(call)
        self.emit("call_update", copy.deepcopy(call))

    def _end_call(self, chan: _Chan, reason: str) -> None:
        call = chan.call
        if call is None:
            chan.pending = []
            chan.voice_slots = 0
            return
        call.ended_at = time.time()
        call.updated_at = call.ended_at
        call.end_reason = reason
        if chan.session is not None:
            chan.session.close()
            chan.session = None
        chan.call = None
        chan.last_key = None
        chan.pending = []
        chan.voice_slots = 0
        chan.enc_known = False
        chan.ess_b = [None] * 4
        self.emit("call_end", copy.deepcopy(call))

    def _expire(self, t: float) -> None:
        for chan in self._chans:
            if chan.call is not None and t - chan.last_activity > self.config.hang_time_s:
                self._end_call(chan, "timeout")
