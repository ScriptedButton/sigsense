"""P25 Phase 1 (FDMA, C4FM/CQPSK) protocol decoder (WP-C). Original code from TIA-102.BAAA-A.

Fed dibits from the DSP front end, it finds frame syncs, decodes the NID, then the
data unit (HDU, LDU1/LDU2, TDU, TDULC, TSDU, PDU) and emits the typed events of
events.py. Rules it keeps:

  * a Call is only started or updated from FEC-valid, CRC-valid data;
  * encrypted calls are reported (ALGID/KID/MI) and NEVER passed to a vocoder; when
    the encryption state is not yet known (a call joined mid-transmission) the first
    LDU's voice is held until the LDU2 encryption sync word resolves it;
  * keys are never handled anywhere in this package.
"""

from __future__ import annotations

import time
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
from sigsense_api.dvoice.fec import bch, crc, trellis
from sigsense_api.dvoice.protocols.base import EmitFn, ProtocolConfig, ProtocolDecoder, VocoderProvider
from sigsense_api.dvoice.protocols.p25p1 import framing as fr
from sigsense_api.dvoice.protocols.p25p1 import tsbk as tsbk_mod
from sigsense_api.dvoice.protocols.p25p1 import voice, words
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS
from sigsense_api.dvoice.trunking.p25 import P25BandPlan
from sigsense_api.dvoice.vocoder.base import UnavailableVocoder, Vocoder, VocoderFrame

try:
    _SYNC_MAX_ERRORS = int(SYNC_SPECS["p25p1"].max_errors)
except Exception:  # pragma: no cover - patterns module contract
    _SYNC_MAX_ERRORS = 4

_UPDATE_INTERVAL_S = 0.25  # call_update at most 4 Hz
_CC_TIMEOUT_S = 3.0  # control channel declared lost after this much stream time without a valid TSBK
_MAX_BUFFER = 20000  # dibits kept while waiting for a frame
_VOCODER = VocoderId.IMBE_7200X4400


@dataclass
class _CallState:
    call: Call
    seq: int = 0
    vocoder: Optional[Vocoder] = None
    vocoder_checked: bool = False
    pending: list[tuple[np.ndarray, int, bool]] = field(default_factory=list)  # (88 bits, errors, erased)
    last_seen: float = 0.0  # stream time of the last LDU/HDU for this call
    last_update: float = -1e9
    dirty: bool = False
    corrected_bits: int = 0
    voice_bits: int = 0
    algid: Optional[int] = None
    protected_lc: bool = False


class P25P1Decoder(ProtocolDecoder):
    protocol = Protocol.P25P1
    symbol_rate = 4800.0
    modulation = Modulation.C4FM
    sync_specs = ("p25p1",)
    timeslots = 1

    def __init__(self, emit: EmitFn, vocoders: VocoderProvider, config: Optional[ProtocolConfig] = None) -> None:
        super().__init__(emit, vocoders, config)
        self._time_base: Optional[float] = None
        self.bandplan = P25BandPlan()
        self._counter = 0
        self.reset()

    # -- lifecycle ---------------------------------------------------------------

    def reset(self) -> None:
        if getattr(self, "_call", None) is not None:
            self._end_call("retune")
        self._buf = np.empty(0, dtype=np.uint8)
        self._unit_name: Optional[str] = None
        self._locked: Optional[tuple[bool, int]] = None  # (inverted, sync errors) for buf[0:24]
        self._next_index: Optional[int] = None
        self._stream_t = 0.0
        self._call: Optional[_CallState] = None
        self._nac: Optional[int] = None
        self._sysinfo: dict[str, Any] = {}
        self._talkgroups: dict[int, Talkgroup] = {}
        self._cc_acquired = False
        self._last_tsbk_t: Optional[float] = None
        self._recent: list[bool] = []
        self._fail_streak = 0
        self._burst_reported = False
        self._stats = {
            "frames_ok": 0, "frames_bad": 0, "fec_corrected": 0, "fec_failed": 0,
            "syncs": 0, "nid_failed": 0, "crc_failed": 0, "voice_frames": 0, "voice_frames_bad": 0,
            "duid": {}, "inverted_syncs": 0, "dropped_pending_voice": 0,
        }

    def stats(self) -> dict[str, Any]:
        s = dict(self._stats)
        s["sync_ratio"] = (sum(self._recent) / len(self._recent)) if self._recent else 0.0
        s["nac"] = self._nac
        return s

    # -- input ------------------------------------------------------------------

    def feed(self, block: SymbolBlock) -> None:
        d = np.asarray(block.dibits, dtype=np.uint8)
        if d.size == 0:
            return
        if self._time_base is None:
            self._time_base = time.time() - block.t0
        if self._next_index is not None and block.first_index != self._next_index:
            # Gap or restart in the symbol stream: whatever was half received is unusable.
            self._buf = np.empty(0, dtype=np.uint8)
            self._locked = None
        self._next_index = block.first_index + d.size
        self._stream_t = block.t0 + d.size / (block.symbol_rate or self.symbol_rate)
        self._abs0 = block.first_index  # index of d[0]; the buffer start is tracked relative below
        if self._buf.size == 0:
            self._buf_index = block.first_index
        self._buf = np.concatenate((self._buf, d)) if self._buf.size else d.copy()
        self._process()
        if self._buf.size > _MAX_BUFFER:
            self._drop(self._buf.size - fr.SYNC_LEN)
        self._housekeeping()

    # -- frame search -------------------------------------------------------------

    def _drop(self, n: int) -> None:
        self._buf = self._buf[n:]
        self._buf_index += n
        self._locked = None

    def _find_sync(self) -> Optional[tuple[int, bool, int]]:
        buf = self._buf
        if buf.size < fr.SYNC_LEN:
            return None
        win = np.lib.stride_tricks.sliding_window_view(buf, fr.SYNC_LEN)
        e_norm = np.count_nonzero(win != fr.SYNC_DIBITS, axis=1)
        e_inv = np.count_nonzero(win != (fr.SYNC_DIBITS ^ 2), axis=1)
        best = np.minimum(e_norm, e_inv)
        hits = np.flatnonzero(best <= _SYNC_MAX_ERRORS)
        if hits.size == 0:
            return None
        idx = int(hits[0])
        inverted = bool(e_inv[idx] < e_norm[idx])
        return idx, inverted, int(best[idx])

    def _process(self) -> None:
        while True:
            if self._locked is None:
                found = self._find_sync()
                if found is None:
                    keep = fr.SYNC_LEN - 1
                    if self._buf.size > keep:
                        self._drop(self._buf.size - keep)
                    return
                idx, inverted, errors = found
                if idx:
                    self._drop(idx)
                self._locked = (inverted, errors)
                self._stats["syncs"] += 1
                if inverted:
                    self._stats["inverted_syncs"] += 1
                self.emit("sync", SyncEvent(Protocol.P25P1, "p25p1", errors, inverted, int(self._buf_index)))
            self._unit_name = None
            consumed = self._try_frame()
            if consumed is None:
                return  # need more dibits
            if self._unit_name is not None:
                self._stats["duid"][self._unit_name] = self._stats["duid"].get(self._unit_name, 0) + 1
            self._drop(max(1, consumed))

    # -- one data unit ------------------------------------------------------------

    def _try_frame(self) -> Optional[int]:
        """Decode the unit at buf[0]. -> dibits to consume, or None when more data is needed."""
        inverted, _ = self._locked  # type: ignore[misc]
        buf = self._buf
        if buf.size < fr.total_dibits(fr.HEADER_INFO_DIBITS):
            return None
        head = buf[: fr.total_dibits(fr.HEADER_INFO_DIBITS)]
        if inverted:
            head = head ^ 2
        info = fr.strip_status(head, fr.HEADER_INFO_DIBITS)
        nid_bits = fr.dibits_to_bits(info[fr.SYNC_LEN :])
        nid = bch.p25_nid_decode(fr.bits_to_int(nid_bits))
        if nid is None:
            self._stats["nid_failed"] += 1
            self._record(False)
            return 1
        nac, duid, nid_fixed = nid
        if duid not in fr.DUID_NAMES:
            self._stats["nid_failed"] += 1
            self._record(False)
            return 1
        self._set_nac(nac)

        def frame(n_info: int) -> Optional[np.ndarray]:
            total = fr.total_dibits(n_info)
            if buf.size < total:
                return None
            raw = buf[:total] ^ 2 if inverted else buf[:total]
            return fr.strip_status(raw, n_info)

        name = fr.DUID_NAMES[duid]
        self._unit_name = name  # counted in _process once the unit is complete
        if duid == fr.DUID_TDU:
            if frame(fr.TDU_INFO_DIBITS) is None:
                return None
            self._frame_ok(nid_fixed)
            self._terminator(None)
            return fr.total_dibits(fr.TDU_INFO_DIBITS)
        if duid == fr.DUID_HDU:
            info = frame(fr.HDU_INFO_DIBITS)
            if info is None:
                return None
            self._hdu(info, nid_fixed)
            return fr.total_dibits(fr.HDU_INFO_DIBITS)
        if duid in (fr.DUID_LDU1, fr.DUID_LDU2):
            info = frame(fr.LDU_INFO_DIBITS)
            if info is None:
                return None
            self._ldu(info, duid == fr.DUID_LDU1, nid_fixed)
            return fr.total_dibits(fr.LDU_INFO_DIBITS)
        if duid == fr.DUID_TDULC:
            info = frame(fr.TDULC_INFO_DIBITS)
            if info is None:
                return None
            self._tdulc(info, nid_fixed)
            return fr.total_dibits(fr.TDULC_INFO_DIBITS)
        if duid == fr.DUID_TSDU:
            return self._tsdu(frame, nid_fixed)
        return self._pdu(frame, nid_fixed)

    # -- bookkeeping -----------------------------------------------------------------

    def _record(self, ok: bool) -> None:
        self._recent.append(ok)
        del self._recent[:-32]
        if ok:
            self._fail_streak = 0
            self._burst_reported = False
        else:
            self._fail_streak += 1
            if self._fail_streak >= 5 and not self._burst_reported:
                self._burst_reported = True
                self.emit("error", ErrorEvent("fec_burst_loss", Severity.WARNING,
                                              "five consecutive P25 frames failed FEC/CRC", {"nac": self._nac}))

    def _frame_ok(self, fixed: int) -> None:
        self._stats["frames_ok"] += 1
        self._stats["fec_corrected"] += int(fixed)
        self._record(True)

    def _frame_bad(self, what: str) -> None:
        self._stats["frames_bad"] += 1
        self._stats["fec_failed"] += 1
        self._record(False)

    def _now(self) -> float:
        return (self._time_base or 0.0) + self._stream_t

    def _set_nac(self, nac: int) -> None:
        if nac != self._nac:
            self._nac = nac
            self._sysinfo["nac"] = nac
            self._emit_system()

    def _emit_system(self) -> None:
        kwargs = {k: v for k, v in self._sysinfo.items() if k in ("wacn", "system_id", "nac", "rfss_id", "site_id", "control_channel_hz")}
        info = SystemInfo(protocol=Protocol.P25P1, **kwargs)
        self.emit("system_info", info)

    # -- voice units ---------------------------------------------------------------

    def _hdu(self, info: np.ndarray, nid_fixed: int) -> None:
        bits = fr.dibits_to_bits(info[fr.HEADER_INFO_DIBITS : fr.HEADER_INFO_DIBITS + 324])
        res = words.decode_header(bits)
        if not res.ok:
            self._frame_bad("hdu")
            return
        self._frame_ok(nid_fixed + res.corrected)
        h = words.parse_header(res.data)
        st = self._ensure_call(target=h.tgid or None, source=None, kind=CallKind.GROUP if h.tgid else CallKind.UNKNOWN, service=None)
        self._apply_crypto(st, h.algid, h.kid, h.mi)
        st.last_seen = self._stream_t
        self._flush_pending(st)
        self._maybe_update(st)

    def _ldu(self, info: np.ndarray, is_ldu1: bool, nid_fixed: int) -> None:
        voice_dibits, lc_bits, lsd = fr.split_ldu(info)
        res = words.decode_lc_hamming(lc_bits) if is_ldu1 else words.decode_es(lc_bits)
        fixed = nid_fixed + (res.corrected if res.ok else 0)
        st = self._call
        if res.ok:
            if is_ldu1:
                lc = words.parse_lc(res.data)
                if lc.kind in ("group", "private"):
                    kind = CallKind.GROUP if lc.kind == "group" else CallKind.PRIVATE
                    st = self._ensure_call(target=lc.target_id, source=lc.source_id, kind=kind, service=lc.service)
                    st.protected_lc = bool(lc.service and lc.service.protected)
                elif lc.kind == "termination":
                    self._frame_ok(fixed)
                    self._terminator(lc)
                    return
                elif st is None:
                    # a vendor/unknown LC format with voice: still a call, identity unknown
                    st = self._ensure_call(target=None, source=None, kind=CallKind.UNKNOWN, service=None)
            else:
                es = words.parse_es(res.data)
                if st is None:
                    st = self._ensure_call(target=None, source=None, kind=CallKind.UNKNOWN, service=None)
                self._apply_crypto(st, es.algid, es.kid, es.mi)
            self._frame_ok(fixed)
        else:
            # The LC/ES failed but the NID was good: keep the call alive on the voice itself.
            self._frame_bad("ldu-lc" if is_ldu1 else "ldu-es")
            if st is None:
                st = self._ensure_call(target=None, source=None, kind=CallKind.UNKNOWN, service=None)
        st.last_seen = self._stream_t
        self._flush_pending(st)
        for dibits in voice_dibits:
            vr = voice.decode_voice_frame(fr.dibits_to_bits(dibits))
            self._voice(st, vr)
        for word in lsd:
            lw = int(fr.bits_to_int(word))
            r = words.lsd_decode(lw)
            if r is not None and r[0] and st.call.meta is not None:
                st.call.meta.setdefault("lsd", []).append(r[0])
                del st.call.meta["lsd"][:-16]
        self._maybe_update(st)

    def _terminator(self, lc: Optional[words.LinkControl]) -> None:
        st = self._call
        if st is None:
            return
        if lc is not None and lc.target_id and st.call.target_id is None:
            st.call.target_id = lc.target_id
        self._end_call("terminator")

    def _tdulc(self, info: np.ndarray, nid_fixed: int) -> None:
        bits = fr.dibits_to_bits(info[fr.HEADER_INFO_DIBITS : fr.HEADER_INFO_DIBITS + 144])
        res = words.decode_lc_golay(bits)
        if not res.ok:
            self._frame_bad("tdulc")
            self._terminator(None)  # the terminator itself is still a terminator
            return
        self._frame_ok(nid_fixed + res.corrected)
        lc = words.parse_lc(res.data)
        self._terminator(lc if lc.kind in ("group", "private") else None)

    # -- call management ----------------------------------------------------------

    def _call_id(self) -> str:
        self._counter += 1
        return f"p25p1-{self.config.channel}-x-{self._counter}"

    def _ensure_call(self, *, target: Optional[int], source: Optional[int], kind: CallKind, service: Optional[words.ServiceOptions]) -> _CallState:
        st = self._call
        if st is not None and target and st.call.target_id and target != st.call.target_id:
            # A different talkgroup without a terminator in between: the old call ended unseen.
            self._end_call("sync_loss")
            st = None
        now = self._now()
        if st is None:
            call = Call(
                call_id=self._call_id(), protocol=Protocol.P25P1, kind=kind, target_id=target or None,
                source_id=source or None, frequency_hz=self.config.frequency_hz, channel=self.config.channel,
                started_at=now, updated_at=now,
            )
            call.encryption = EncryptionInfo(state=EncryptionState.UNKNOWN)
            if service is not None:
                call.emergency = service.emergency
                call.priority = service.priority
                call.meta["service_options"] = service.raw
            st = _CallState(call=call, last_seen=self._stream_t, last_update=self._stream_t)
            self._call = st
            if service is not None and service.protected:
                call.encryption.state = EncryptionState.ENCRYPTED
            self.emit("call_start", call)
            if target:
                self._announce_talkgroup(target, call.encryption.state == EncryptionState.ENCRYPTED)
            return st
        c = st.call
        changed = False
        if target and c.target_id != target:
            c.target_id, changed = target, True
        if source and c.source_id != source:
            c.source_id, changed = source, True
        if kind != CallKind.UNKNOWN and c.kind != kind:
            c.kind, changed = kind, True
        if service is not None:
            if c.emergency != service.emergency:
                c.emergency, changed = service.emergency, True
            c.priority = service.priority
            c.meta["service_options"] = service.raw
            if service.protected and c.encryption.state == EncryptionState.UNKNOWN:
                c.encryption.state = EncryptionState.ENCRYPTED
                changed = True
        if target and target not in self._talkgroups:
            self._announce_talkgroup(target, c.encryption.state == EncryptionState.ENCRYPTED)
        st.dirty = st.dirty or changed
        return st

    def _announce_talkgroup(self, tgid: int, encrypted: bool) -> None:
        if tgid in self._talkgroups:
            return
        tg = Talkgroup(id=tgid, protocol=Protocol.P25P1, label=self.config.talkgroup_labels.get(tgid),
                       last_heard=self._now(), call_count=1, encrypted_calls=1 if encrypted else 0)
        self._talkgroups[tgid] = tg
        self.emit("talkgroup", tg)

    def _apply_crypto(self, st: _CallState, algid: int, kid: int, mi: bytes) -> None:
        enc = st.call.encryption
        st.algid = algid
        clear = algid == words.ALGID_CLEAR
        new_state = EncryptionState.CLEAR if clear else EncryptionState.ENCRYPTED
        mi_present = any(mi)
        if (enc.state, enc.algorithm_id, enc.key_id, enc.mi_present) != (new_state, algid, kid, mi_present):
            enc.state = new_state
            enc.algorithm_id = algid
            enc.algorithm_name = words.algorithm_name(algid)
            enc.key_id = kid
            enc.mi_present = mi_present
            st.dirty = True
        if not clear:
            enc.audio_decoded = False

    def _maybe_update(self, st: _CallState, force: bool = False) -> None:
        if st.call.voice_frames:
            st.call.ber = st.corrected_bits / max(1, st.voice_bits)
        if (st.dirty or force) and (force or self._stream_t - st.last_update >= _UPDATE_INTERVAL_S):
            st.call.updated_at = self._now()
            st.last_update = self._stream_t
            st.dirty = False
            self.emit("call_update", st.call)

    def _end_call(self, reason: str) -> None:
        st = self._call
        if st is None:
            return
        if st.pending:
            self._stats["dropped_pending_voice"] += len(st.pending)
            st.call.meta["voice_frames_withheld"] = len(st.pending)
            st.pending.clear()
        if st.voice_bits:
            st.call.ber = st.corrected_bits / st.voice_bits
        st.call.ended_at = self._now()
        st.call.updated_at = st.call.ended_at
        st.call.end_reason = reason
        self._call = None
        if st.vocoder is not None:
            try:
                st.vocoder.close()
            except Exception:
                pass
        self.emit("call_end", st.call)

    # -- voice frames ----------------------------------------------------------------

    def _voice(self, st: _CallState, vr: voice.VoiceFrameResult) -> None:
        c = st.call
        c.voice_frames += 1
        self._stats["voice_frames"] += 1
        st.voice_bits += 144
        st.corrected_bits += vr.errors
        if vr.erased:
            c.voice_frames_bad += 1
            self._stats["voice_frames_bad"] += 1
        state = c.encryption.state
        if state == EncryptionState.ENCRYPTED:
            c.encryption.audio_decoded = False
            return  # reported, never decoded
        if state == EncryptionState.UNKNOWN:
            st.pending.append((vr.bits, vr.errors, vr.erased))
            del st.pending[:-18]
            return
        self._emit_voice(st, vr.bits, vr.errors, vr.erased)

    def _flush_pending(self, st: _CallState) -> None:
        state = st.call.encryption.state
        if state == EncryptionState.UNKNOWN or not st.pending:
            return
        held, st.pending = st.pending, []
        if state == EncryptionState.ENCRYPTED:
            self._stats["dropped_pending_voice"] += len(held)
            st.call.meta["voice_frames_withheld"] = st.call.meta.get("voice_frames_withheld", 0) + len(held)
            return
        for bits, errors, erased in held:
            self._emit_voice(st, bits, errors, erased)

    def _vocoder_for(self, st: _CallState) -> Optional[Vocoder]:
        if st.vocoder_checked:
            return st.vocoder
        st.vocoder_checked = True
        try:
            voc = self.vocoders(_VOCODER)
        except Exception as exc:
            self.emit("error", ErrorEvent("vocoder_unavailable", Severity.WARNING, f"IMBE vocoder could not be created: {exc}", {"call_id": st.call.call_id}))
            return None
        if isinstance(voc, UnavailableVocoder):
            self.emit("error", ErrorEvent("vocoder_unavailable", Severity.INFO,
                                          "No IMBE vocoder installed: calls and metadata are decoded without audio",
                                          {"call_id": st.call.call_id, "reason": getattr(voc.why, "reason", "")}))
            try:
                voc.close()
            except Exception:
                pass
            return None
        st.vocoder = voc
        return voc

    def _emit_voice(self, st: _CallState, bits: np.ndarray, errors: int, erased: bool) -> None:
        voc = self._vocoder_for(st)
        pcm = None
        if voc is not None:
            try:
                pcm = voc.decode(VocoderFrame(vocoder=_VOCODER, bits=bits, protocol=Protocol.P25P1, fec_errors=errors, erased=erased))
                st.call.encryption.audio_decoded = True
            except Exception as exc:
                self.emit("error", ErrorEvent("vocoder_error", Severity.WARNING, str(exc), {"call_id": st.call.call_id}))
        st.call.encryption.audio_decoded = st.call.encryption.audio_decoded and pcm is not None
        st.seq += 1
        self.emit("voice", VoiceFrame(call_id=st.call.call_id, seq=st.seq, vocoder=_VOCODER, pcm=pcm, errors=errors, timestamp=self._now()))

    # -- trunking signalling ---------------------------------------------------------

    def _tsdu(self, frame, nid_fixed: int) -> Optional[int]:
        """One to three trellis-coded blocks; the last-block flag says how many.

        Nothing is emitted until every block of the unit has arrived, so waiting for
        more dibits never produces duplicate events."""
        parsed: list[tuple[tsbk_mod.Tsbk, int]] = []
        used = 0
        bad = False
        for block in range(3):
            n_info = fr.HEADER_INFO_DIBITS + fr.TSBK_BLOCK_DIBITS * (block + 1)
            info = frame(n_info)
            if info is None:
                return None
            used = fr.total_dibits(n_info)
            start = fr.HEADER_INFO_DIBITS + fr.TSBK_BLOCK_DIBITS * block
            res = trellis.p25_trellis_half_decode(info[start : start + fr.TSBK_BLOCK_DIBITS])
            if not res.ok or not crc.crc_check("crc16_p25_dmr_header", res.data):
                bad = True
                break
            t = tsbk_mod.parse_tsbk(res.data)
            parsed.append((t, res.corrected))
            if t.last_block:
                break
        for i, (t, fixed) in enumerate(parsed):
            self._frame_ok(fixed + (nid_fixed if i == 0 else 0))
            self._tsbk(t)
        if bad:
            self._stats["crc_failed"] += 1
            self._frame_bad("tsbk")
        return used

    def _tsbk(self, t: tsbk_mod.Tsbk) -> None:
        self._last_tsbk_t = self._stream_t
        if not self._cc_acquired:
            self._cc_acquired = True
            self.emit("trunk", TrunkEvent("cc_acquired", frequency_hz=self.config.frequency_hz, detail={"nac": self._nac}))
        f = t.fields
        op = t.opcode
        if not t.standard:
            return
        if op == tsbk_mod.OP_IDEN_UP or op == tsbk_mod.OP_IDEN_UP_VU:
            self.bandplan.update_from_fields(f)
            self.emit("trunk", TrunkEvent("system_update", detail={"opcode": t.name, "iden_fields": dict(f)}))
        elif op == tsbk_mod.OP_GRP_V_CH_GRANT:
            self._grant(t, f["channel"], f["group"], f["source"], f["service_options"])
        elif op == tsbk_mod.OP_GRP_V_CH_GRANT_UPDT:
            for u in f["updates"]:
                if u["group"]:
                    self._grant(t, u["channel"], u["group"], None, None, update=True)
        elif op == tsbk_mod.OP_GRP_V_CH_GRANT_UPDT_EXP:
            self._grant(t, f["tx"]["channel"], f["group"], None, f["service_options"], update=True, extra={"rx_channel": f["rx"]["channel"]})
        elif op in (tsbk_mod.OP_UU_V_CH_GRANT, tsbk_mod.OP_UU_V_CH_GRANT_UPDT):
            hz = self.bandplan.frequency(f["channel"])
            self.emit("trunk", TrunkEvent("grant", frequency_hz=hz, talkgroup=None, source_id=f["source"],
                                          detail={"opcode": t.name, "channel": f["channel"], "iden": f["iden"],
                                                  "private": True, "target_unit": f["target"], "update": op == tsbk_mod.OP_UU_V_CH_GRANT_UPDT}))
        elif op == tsbk_mod.OP_RFSS_STS_BCST:
            self._sysinfo.update(system_id=f["system_id"], rfss_id=f["rfss_id"], site_id=f["site_id"])
            hz = self.bandplan.frequency(f["channel"])
            if hz:
                self._sysinfo["control_channel_hz"] = hz
            self._emit_system()
        elif op == tsbk_mod.OP_NET_STS_BCST:
            self._sysinfo.update(wacn=f["wacn"], system_id=f["system_id"])
            hz = self.bandplan.frequency(f["channel"])
            if hz:
                self._sysinfo["control_channel_hz"] = hz
            self._emit_system()
        elif op == tsbk_mod.OP_ADJ_STS_BCST:
            self.emit("trunk", TrunkEvent("system_update", frequency_hz=self.bandplan.frequency(f["channel"]),
                                          detail={"opcode": t.name, "system_id": f["system_id"], "rfss_id": f["rfss_id"],
                                                  "site_id": f["site_id"], "channel": f["channel"], "cfva": f["cfva"]}))

    def _grant(self, t: tsbk_mod.Tsbk, channel: int, group: int, source: Optional[int], service: Optional[int],
               update: bool = False, extra: Optional[dict] = None) -> None:
        svc = words.ServiceOptions.parse(service) if service is not None else None
        hz = self.bandplan.frequency(channel)
        detail = {"opcode": t.name, "channel": channel, "iden": channel >> 12, "number": channel & 0xFFF, "update": update}
        if svc is not None:
            detail.update(service_options=svc.raw, emergency=svc.emergency, priority=svc.priority)
        if extra:
            detail.update(extra)
        self.emit("trunk", TrunkEvent("grant", frequency_hz=hz, talkgroup=group, source_id=source,
                                      encrypted=bool(svc and svc.protected), detail=detail))

    # -- packet data -----------------------------------------------------------------

    def _pdu(self, frame, nid_fixed: int) -> Optional[int]:
        n_hdr = fr.HEADER_INFO_DIBITS + fr.TSBK_BLOCK_DIBITS
        info = frame(n_hdr)
        if info is None:
            return None
        res = trellis.p25_trellis_half_decode(info[fr.HEADER_INFO_DIBITS : n_hdr])
        if not res.ok or not crc.crc_check("crc16_p25_dmr_header", res.data):
            self._stats["crc_failed"] += 1
            self._frame_bad("pdu-header")
            return fr.total_dibits(n_hdr)
        h = tsbk_mod.parse_pdu_header(res.data)
        n_blocks = h.blocks_to_follow
        n_info = n_hdr + fr.TSBK_BLOCK_DIBITS * n_blocks
        if frame(n_info) is None:
            return None  # wait for the whole packet
        self._frame_ok(nid_fixed + res.corrected)
        if self.config.decode_data:
            now = self._now()
            call = Call(call_id=self._call_id(), protocol=Protocol.P25P1, kind=CallKind.DATA, target_id=h.llid,
                        frequency_hz=self.config.frequency_hz, channel=self.config.channel, started_at=now, updated_at=now,
                        ended_at=now, end_reason="terminator",
                        meta={"sap": h.sap, "mfid": h.mfid, "blocks": n_blocks, "format": h.kind, "outbound": h.outbound})
            self.emit("call_start", call)
            self.emit("call_end", call)
        return fr.total_dibits(n_info)

    # -- timers ------------------------------------------------------------------------

    def _housekeeping(self) -> None:
        st = self._call
        if st is not None and self._stream_t - st.last_seen > max(self.config.hang_time_s, 0.4):
            self._end_call("timeout")
        elif st is not None:
            self._maybe_update(st)
        if self._cc_acquired and self._last_tsbk_t is not None and self._stream_t - self._last_tsbk_t > _CC_TIMEOUT_S:
            self._cc_acquired = False
            self.emit("trunk", TrunkEvent("cc_lost", frequency_hz=self.config.frequency_hz, detail={"nac": self._nac}))
