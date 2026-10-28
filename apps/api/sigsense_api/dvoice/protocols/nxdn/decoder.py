"""NXDN48 / NXDN96 decoder (WP-E). Original code from NXDN TS 1-A v1.3 (Common Air Interface).

Frame (384 bits = 192 symbols; 80 ms at NXDN48 / 2400 baud, 40 ms at NXDN96 / 4800 baud):

    FSW 10 symbols | LICH 8 symbols | body 174 symbols            (+ preamble on an inbound burst)
    body, traffic voice:   SACCH 30 | VCH 36 x4  or FACCH1 72 for each stolen half
    body, traffic data:    UDCH or FACCH2 174
    body, control outbound: CAC 150 | E 12 | Post 12
    body, control inbound:  Long CAC / Short CAC 126 | guard 48

Everything after the FSW is scrambled with the x^9 + x^4 + 1 sequence (restarted each frame); the LICH
(7 bits + an even parity bit over the four MSBs, one symbol per bit: 0 = +3, 1 = -3) says which of the
above applies. Channels are CRC-protected and decoded with the verified WP-A chain (channels.py).

What is and is not decoded
  * Decoded: LICH, SACCH (4-frame superframe -> 72-bit layer 3 message), FACCH1, UDCH/FACCH2 (headers only),
    outbound/inbound CAC; call identity from VCALL (source, destination, call type, emergency, cipher
    type, key ID), IVs reported, TX_REL/DISC/IDLE end a call; SITE_INFO -> system information;
    VCALL_ASSGN(_DUP) on the control channel -> trunk grant events (the channel NUMBER; mapping it to a
    frequency needs the system's channel plan, which is not in the air interface text).
  * NOT decoded: voice audio. The bits of a VCH are the AMBE+2 vocoder's own frame layout, which is
    DVSI-proprietary and not in the NXDN specification; no public table exists to deinterleave it
    and this package does not guess one. Voice frames are counted, the call is reported with an
    explicit `unsupported_feature` notice, and encryption is reported from the cipher type but
    never acted on. Data payloads are not decoded either.
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
    EncryptionInfo,
    EncryptionState,
    ErrorEvent,
    Modulation,
    Protocol,
    Severity,
    SyncEvent,
    SystemInfo,
    TrunkEvent,
)
from sigsense_api.dvoice.fec import whitening
from sigsense_api.dvoice.protocols.base import ProtocolConfig, ProtocolDecoder
from sigsense_api.dvoice.protocols.nxdn import channels as ch
from sigsense_api.dvoice.protocols.nxdn import l3
from sigsense_api.dvoice.protocols.nxdn.bits import bits_to_bytes, bits_to_int, dibits_to_bits, symbols_to_soft_bits
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS

FRAME = 192
FSW_LEN = 10
BODY = FRAME - FSW_LEN  # 182 symbols after the FSW (LICH 8 + 174)
SCAN_MAX_ERRORS = 2  # FSW symbol errors tolerated while searching
LOCK_MAX_ERRORS = 4  # ... while locked (the position is known)
SOFT_SYNC_MIN = 0.45  # normalised soft correlation accepted as a candidate (see m17 decoder)
MAX_MISSES = 3
UPDATE_INTERVAL_SYMBOLS_PER_S = 4  # call_update at most 4 Hz

_FSW = np.array(SYNC_SPECS["nxdn_fsw"].values, dtype=np.uint8)
# FSW symbols: -3 +1 -3 +3 -3 -3 +3 +3 -1 +3. For the soft correlation use the expected sign of each symbol.
_FSW_LEVEL = np.array([{0: 1.0, 1: 3.0, 2: -1.0, 3: -3.0}[int(v)] for v in _FSW])
_SCRAMBLE = whitening.nxdn_scramble_sequence(BODY)

RF_NAMES = {0: "RCCH", 1: "RTCH", 2: "RDCH", 3: "RTCH_C"}
LICH_PAYLOAD_LEN = {"cac": 150, "long_cac": 126, "short_cac": 126, "udch": 174}


class _Lich:
    __slots__ = ("rf", "fn", "opt", "outbound")

    def __init__(self, bits7: int) -> None:
        self.rf = (bits7 >> 5) & 3
        self.fn = (bits7 >> 3) & 3
        self.opt = (bits7 >> 1) & 3
        self.outbound = bool(bits7 & 1)


def _parse_lich(bits8: np.ndarray) -> Optional[_Lich]:
    """8 LICH bits: 7 information bits then an even parity bit over the four MSBs."""
    b = [int(x) for x in bits8]
    if (b[0] ^ b[1] ^ b[2] ^ b[3]) != b[7]:
        return None
    return _Lich(bits_to_int(np.array(b[:7], dtype=np.uint8)))


class _Call:
    def __init__(self, call: Call, ids: tuple) -> None:
        self.call = call
        self.ids = ids  # (call_type, source, dest)
        self.last_update_symbol = -10**9
        self.dirty = False
        self.rate_full = False
        self.error_sent = False


class Nxdn48Decoder(ProtocolDecoder):
    """NXDN at 2400 baud (4800 bps)."""

    protocol = Protocol.NXDN48
    symbol_rate = 2400.0
    modulation = Modulation.FSK4
    sync_specs = ("nxdn_fsw",)
    timeslots = 1

    def __init__(self, emit, vocoders, config: Optional[ProtocolConfig] = None) -> None:
        super().__init__(emit, vocoders, config)
        self._counter = 0
        self._ran: Optional[int] = None
        self._site: Optional[tuple] = None
        self._reset_state()

    # ------------------------------------------------------------------ state
    def _reset_state(self) -> None:
        self._buf = np.zeros(0, dtype=np.uint8)
        self._soft = np.zeros(0, dtype=np.float32)
        self._base = 0
        self._pos = 0
        self._next_index: Optional[int] = None
        self._locked = False
        self._inverted = False
        self._misses = 0
        self._call: Optional[_Call] = None
        self._last_frame_end = 0
        self._sacch_parts: list[Optional[np.ndarray]] = [None] * 4
        self._sacch_next = 0
        self.counters = {
            "frames_ok": 0,
            "frames_bad": 0,
            "fec_corrected": 0,
            "fec_failed": 0,
            "syncs": 0,
            "lich_bad": 0,
            "calls": 0,
            "voice_frames": 0,
            "facch1": 0,
            "cac": 0,
            "udch": 0,
            "messages": 0,
        }

    def reset(self) -> None:
        if self._call is not None:
            self._end_call("retune")
        self._reset_state()

    def stats(self) -> dict[str, Any]:
        total = self.counters["frames_ok"] + self.counters["frames_bad"]
        out: dict[str, Any] = dict(self.counters)
        out["sync_ratio"] = (self.counters["frames_ok"] / total) if total else 0.0
        return out

    # ------------------------------------------------------------------ input
    def feed(self, block: SymbolBlock) -> None:
        try:
            self._feed(block)
        except Exception as exc:
            self.emit(
                "error",
                ErrorEvent(
                    code="decoder_fault",
                    severity=Severity.WARNING,
                    message=f"NXDN decoder reset after an internal error: {type(exc).__name__}",
                    detail={"protocol": self.protocol.value},
                ),
            )
            self._reset_state()

    def _feed(self, block: SymbolBlock) -> None:
        dibits = np.asarray(block.dibits, dtype=np.uint8).ravel() & 3
        if dibits.size == 0:
            return
        if self._next_index is not None and block.first_index != self._next_index:
            self._buf = np.zeros(0, dtype=np.uint8)
            self._soft = np.zeros(0, dtype=np.float32)
            self._pos = 0
            self._base = int(block.first_index)
            self._locked = False
            self._misses = 0
            self._sacch_parts = [None] * 4
            self._sacch_next = 0
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
        drop = self._pos
        if drop > 0:
            self._buf = self._buf[drop:]
            self._soft = self._soft[drop:]
            self._base += drop
            self._pos = 0

    # ------------------------------------------------------------------ scanning
    def _candidates(self) -> list[tuple[int, bool, int]]:
        count = self._buf.size - FRAME - self._pos + 1
        if count <= 0:
            return []
        found: list[tuple[int, bool, int]] = []
        window = self._soft[self._pos : self._pos + count + FSW_LEN - 1]
        use_soft = window.size == count + FSW_LEN - 1 and not np.isnan(window).any()
        clipped = np.clip(window, -3.0, 3.0) / 3.0 if use_soft else None
        for inverted in (False, True):
            pattern = _FSW ^ 2 if inverted else _FSW
            err = np.zeros(count, dtype=np.int16)
            for k in range(FSW_LEN):
                err += self._buf[self._pos + k : self._pos + k + count] != pattern[k]
            hit = err <= SCAN_MAX_ERRORS
            if use_soft:
                expected = (-_FSW_LEVEL if inverted else _FSW_LEVEL) / 3.0
                score = np.zeros(count)
                for k in range(FSW_LEN):
                    score += expected[k] * clipped[k : k + count]
                hit = hit | (score / float(np.sum(expected**2)) >= SOFT_SYNC_MIN)
            for idx in np.flatnonzero(hit):
                found.append((self._pos + int(idx), inverted, int(err[idx])))
        found.sort(key=lambda c: (c[0], c[2]))
        return found

    def _process(self) -> None:
        guard = 0
        while guard < 100000:
            guard += 1
            if self._buf.size - self._pos < FRAME:
                if not self._locked:
                    self._pos = max(self._pos, self._buf.size - (FRAME - 1))
                return
            if self._locked:
                self._locked_step()
                continue
            cands = self._candidates()
            if not cands:
                self._pos = max(self._pos, self._buf.size - (FRAME - 1))
                return
            for pos, inverted, errors in cands:
                if self._accept(pos, inverted, errors):
                    self._pos = pos + FRAME
                    self._locked = True
                    self._inverted = inverted
                    self._misses = 0
                    break
            else:
                self._pos = cands[-1][0] + 1

    def _locked_step(self) -> None:
        pos = self._pos
        seg = self._buf[pos : pos + FSW_LEN]
        if self._inverted:
            seg = seg ^ 2
        errors = int(np.count_nonzero(seg != _FSW))
        ok = False
        if errors <= LOCK_MAX_ERRORS:
            ok = self._accept(pos, self._inverted, errors, locked=True)
        self._pos = pos + FRAME
        if ok:
            self._misses = 0
        else:
            self._misses += 1
            if self._misses >= MAX_MISSES:
                self._locked = False
                self._end_call("sync_loss")

    # ------------------------------------------------------------------ one frame
    def _body(self, pos: int, inverted: bool) -> tuple[np.ndarray, Optional[np.ndarray]]:
        """The 182 symbols after the FSW, descrambled: dibits and (if available) soft symbol levels."""
        d = self._buf[pos + FSW_LEN : pos + FRAME]
        s = self._soft[pos + FSW_LEN : pos + FRAME]
        if inverted:
            d = d ^ 2
            s = -s
        d = d ^ (_SCRAMBLE.astype(np.uint8) << 1)  # scrambling inverts the symbol sign
        soft = None if np.isnan(s).any() else np.where(_SCRAMBLE == 1, -s, s).astype(np.float64)
        return d, soft

    @staticmethod
    def _bits(d: np.ndarray, soft: Optional[np.ndarray], start: int, count: int):
        """Bits (and soft bits) of `count` symbols starting at symbol `start` of the body."""
        seg = d[start : start + count]
        return dibits_to_bits(seg), None if soft is None else symbols_to_soft_bits(soft[start : start + count])

    def _accept(self, pos: int, inverted: bool, fsw_errors: int, *, locked: bool = False) -> bool:
        d, soft = self._body(pos, inverted)
        lich = _parse_lich(d[:8] >> 1)  # one symbol per LICH bit: +3/+1 -> 0, -1/-3 -> 1
        if lich is None:
            self.counters["lich_bad"] += 1
            self.counters["frames_bad"] += 1
            return False
        index = self._base + pos
        results: list[tuple[str, ch.ChannelResult]] = []
        voice_slots = 0
        stolen = 0

        def run(name: str, start: int, count: int) -> ch.ChannelResult:
            bits, sbits = self._bits(d, soft, start, count)
            r = ch.decode_channel(name, bits, sbits)
            results.append((name, r))
            self.counters["fec_corrected"] += r.metric
            if not r.accepts(locked):
                self.counters["fec_failed"] += 1
            return r

        sacch_res: Optional[ch.ChannelResult] = None
        if lich.rf == 0:
            if lich.outbound:
                if lich.fn == 0:
                    run("cac", 8, 150)
            elif lich.fn == 1:
                run("long_cac", 8, 126)
            elif lich.fn == 3:
                run("short_cac", 8, 126)
        elif lich.fn == 1:  # UDCH / FACCH2
            if lich.opt in (0, 3):
                run("udch", 8, 174)
        else:  # SACCH (superframe, non-superframe or idle)
            sacch_res = run("sacch", 8, 30)
            first_stolen = lich.opt in (0, 1)  # steal flag: bit2 bit1 = 0 1 -> first half, 0 0 -> both
            second_stolen = lich.opt in (0, 2)
            if lich.fn != 3:  # idle frames carry no voice
                if first_stolen:
                    run("facch1", 38, 72)
                    stolen += 1
                if second_stolen:
                    run("facch1", 110, 72)
                    stolen += 1
                voice_slots = 2 - stolen  # halves of VCH data (2 x VCH72 each at half rate)

        if not any(r.accepts(locked) for _, r in results):
            self.counters["frames_bad"] += 1
            return False
        self.counters["frames_ok"] += 1
        self.counters["syncs"] += 1
        self._last_frame_end = index + FRAME
        self.emit("sync", SyncEvent(self.protocol, "nxdn_fsw", fsw_errors, inverted, index))
        for name, r in results:
            if r.accepts(locked):
                self._on_channel(name, r, lich, index)
        if voice_slots and self._call is not None and lich.rf != 0 and lich.fn != 1:
            self._count_voice(voice_slots, index)
        return True

    # ------------------------------------------------------------------ channel contents
    def _note_ran(self, sr: int) -> None:
        ran = sr & 0x3F
        if ran != self._ran:
            self._ran = ran
            self.emit("system_info", SystemInfo(protocol=self.protocol, ran=ran))

    def _on_channel(self, name: str, r: ch.ChannelResult, lich: _Lich, index: int) -> None:
        if r.sr is not None:
            self._note_ran(r.sr)
        if name == "sacch":
            self._on_sacch(r, lich, index)
        elif name == "facch1":
            self.counters["facch1"] += 1
            self._on_message(l3.parse_l3(bits_to_bytes(r.l3_bits), "rtch"), index)
        elif name == "cac":
            self.counters["cac"] += 1
            self._on_message(l3.parse_l3(bits_to_bytes(r.l3_bits), "rcch_out"), index)
        elif name in ("long_cac", "short_cac"):
            self.counters["cac"] += 1
            self._on_message(l3.parse_l3(bits_to_bytes(r.l3_bits), "rcch_in"), index)
        elif name == "udch":
            self.counters["udch"] += 1
            if self._call is not None:
                self._call.call.meta["data_frames"] = self._call.call.meta.get("data_frames", 0) + 1
            # a UDCH frame starts with an L3 header on the first frame of a data call
            self._on_message(l3.parse_l3(bits_to_bytes(r.l3_bits), "rtch"), index, from_udch=True)

    def _on_sacch(self, r: ch.ChannelResult, lich: _Lich, index: int) -> None:
        if lich.fn == 0:  # non-superframe SACCH: a single 18-bit piece, nothing to assemble
            return
        structure = (r.sr or 0) >> 6  # 11 = 1/4 (head), 10 = 2/4, 01 = 3/4, 00 = 4/4 (last)
        idx = {3: 0, 2: 1, 1: 2, 0: 3}[structure]
        if idx == 0:
            self._sacch_parts = [None] * 4
            self._sacch_next = 0
        if idx != self._sacch_next:
            self._sacch_parts = [None] * 4
            self._sacch_next = 0
            if idx != 0:
                return
        self._sacch_parts[idx] = r.l3_bits.copy()
        self._sacch_next = idx + 1
        if idx == 3 and all(p is not None for p in self._sacch_parts):
            bits = np.concatenate(self._sacch_parts)  # type: ignore[arg-type]
            self._sacch_parts = [None] * 4
            self._sacch_next = 0
            self._on_message(l3.parse_l3(bits_to_bytes(bits), "rtch"), index)

    def _on_message(self, msg: Optional[l3.L3Message], index: int, *, from_udch: bool = False) -> None:
        if msg is None:
            return
        self.counters["messages"] += 1
        f = msg.fields
        name = msg.name
        if name == "VCALL" and "source_id" in f:
            self._vcall(f, index)
        elif name == "DCALL_HEADER" and "source_id" in f:
            self._vcall(f, index, data=True)
        elif name == "VCALL_IV" and self._call is not None and f.get("iv_present"):
            self._call.call.encryption.mi_present = True
            self._call.dirty = True
        elif name in ("TX_REL", "TX_REL_EX", "DISC", "IDLE") and self._call is not None and not from_udch:
            self._end_call("terminator")
        elif name == "SITE_INFO" and "system_code" in f:
            site = (f["category"], f["system_code"], f["site_code"])
            if site != self._site:
                self._site = site
                self.emit(
                    "system_info",
                    SystemInfo(
                        protocol=self.protocol,
                        network_id=f["system_code"],
                        site_id=f["site_code"],
                        ran=self._ran,
                        extra={"location_category": f["category"]},
                    ),
                )
        elif name in ("VCALL_ASSGN", "VCALL_ASSGN_DUP") and "channel" in f:
            self.emit(
                "trunk",
                TrunkEvent(
                    kind="grant",
                    talkgroup=f["dest_id"],
                    source_id=f["source_id"],
                    encrypted=False,
                    detail={
                        "channel_number": f["channel"],
                        "call_type": f["call_type_name"],
                        "emergency": f["emergency"],
                        "duplicate": name.endswith("_DUP"),
                        "note": "channel number only: the frequency comes from the system's channel plan",
                    },
                ),
            )

    # ------------------------------------------------------------------ calls
    def _vcall(self, f: dict, index: int, data: bool = False) -> None:
        ids = (f["call_type"], f["source_id"], f["dest_id"])
        if self._call is not None:
            if self._call.ids == ids:
                self._update_encryption(self._call, f)
                return
            self._end_call("terminator")  # a different call took over
        self._counter += 1
        self.counters["calls"] += 1
        now = time.time()
        enc = self._encryption(f)
        call = Call(
            call_id=f"{self.protocol.value}-{self.config.channel}-x-{self._counter}",
            protocol=self.protocol,
            kind=CallKind.DATA if data else l3.call_kind(f["call_type"]),
            target_id=f["dest_id"],
            source_id=f["source_id"],
            frequency_hz=self.config.frequency_hz,
            channel=self.config.channel,
            started_at=now,
            updated_at=now,
            encryption=enc,
            emergency=bool(f.get("emergency")),
            priority=1 if f.get("priority_paging") else None,
            meta={
                "ran": self._ran,
                "call_type": f["call_type_name"],
                "tx_mode": f["tx_mode_name"],
                "duplex": f["duplex"],
                "cipher": f.get("cipher_name", "none"),
                "key_id": f.get("key_id"),
                "audio": "data call (payload not decoded)" if data else "not decoded (AMBE+2 frame layout is not public)",
            },
        )
        state = _Call(call, ids)
        state.rate_full = f["tx_mode"] == 3
        self._call = state
        self.emit("call_start", self._snapshot(state))
        state.last_update_symbol = index

    @staticmethod
    def _encryption(f: dict) -> EncryptionInfo:
        cipher = f.get("cipher_type", 0)
        if cipher == 0:
            return EncryptionInfo(state=EncryptionState.CLEAR)
        return EncryptionInfo(
            state=EncryptionState.ENCRYPTED,
            algorithm_id=cipher,
            algorithm_name={1: "NXDN scramble", 2: "DES", 3: "AES"}.get(cipher, "reserved"),
            key_id=f.get("key_id"),
        )

    def _update_encryption(self, state: _Call, f: dict) -> None:
        enc = self._encryption(f)
        cur = state.call.encryption
        if (enc.state, enc.algorithm_id, enc.key_id) != (cur.state, cur.algorithm_id, cur.key_id):
            enc.mi_present = cur.mi_present
            state.call.encryption = enc
            state.dirty = True

    def _snapshot(self, state: _Call) -> Call:
        call = state.call
        call.updated_at = time.time()
        return replace(call, encryption=replace(call.encryption), meta=dict(call.meta))

    def _count_voice(self, slots: int, index: int) -> None:
        state = self._call
        assert state is not None
        # half rate: 2 VCH72 per unstolen half; full rate: 1 VCH144 per unstolen half
        per_half = 1 if state.rate_full else 2
        n = slots * per_half
        state.call.voice_frames += n
        self.counters["voice_frames"] += n
        if not state.error_sent and state.call.kind != CallKind.DATA:
            state.error_sent = True
            self.emit(
                "error",
                ErrorEvent(
                    code="unsupported_feature",
                    severity=Severity.INFO,
                    message=(
                        "NXDN voice audio is not decoded: the AMBE+2 voice frame layout is not part of the public "
                        "NXDN specification. Call details are still decoded."
                    ),
                    detail={"call_id": state.call.call_id},
                ),
            )
            state.dirty = True
        interval = int(self.symbol_rate / UPDATE_INTERVAL_SYMBOLS_PER_S)
        if state.dirty and index - state.last_update_symbol >= interval:
            state.dirty = False
            state.last_update_symbol = index
            self.emit("call_update", self._snapshot(state))

    def _end_call(self, reason: str) -> None:
        state = self._call
        self._call = None
        self._sacch_parts = [None] * 4
        self._sacch_next = 0
        if state is None:
            return
        call = state.call
        call.ended_at = time.time()
        call.end_reason = reason
        self.emit("call_end", self._snapshot(state))

    def _timeout_check(self) -> None:
        if self._call is None:
            return
        hang = max(0.5, float(self.config.hang_time_s)) * self.symbol_rate
        if (self._base + self._buf.size) - self._last_frame_end > hang + 2 * FRAME and not self._locked:
            self._end_call("timeout")


class Nxdn96Decoder(Nxdn48Decoder):
    """NXDN at 4800 baud (9600 bps): same frame format, twice the symbol rate."""

    protocol = Protocol.NXDN96
    symbol_rate = 4800.0
