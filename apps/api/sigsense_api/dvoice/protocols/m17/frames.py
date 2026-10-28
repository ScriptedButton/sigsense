"""M17 frame payload decoding: 368 payload bits -> typed contents (WP-E). Original code.

Chain per the M17 specification ("Forward Error Correction", "LSF Contents ECC/FEC", "Stream
Contents ECC/FEC", "Packet Contents ECC/FEC", "BERT Contents ECC/FEC"):

  receive:  368 Type 4 bits -> XOR randomizer -> deinterleave (QPP) -> Type 3 bits
            -> (depuncture + Viterbi, K=5 rate 1/2) -> Type 1 bits

  frame   Type 1 bits                       FEC
  LSF     240 (DST SRC TYPE META CRC)       P1 puncturing, CRC-16 over the 28 bytes
  stream  LICH 48 -> 4 x Golay(24,12) = 96 (LSF chunk 40 + LICH_CNT 3 + 5 reserved)
          + 144 (FN 16 + payload 128) -> P2 -> 272
  packet  206 (25 bytes + EOF + 5-bit counter) P3 puncturing
  BERT    197 (PRBS9)                          P2 puncturing, the 369th coded bit is dropped

Bits are numpy uint8 0/1, MSB first. `soft` values (optional) use the fec.viterbi convention:
+1 = confident 0, -1 = confident 1, 0 = no information. Dibit convention: dsp/types.py
(+3 -> 01, +1 -> 00, -1 -> 10, -3 -> 11, which is exactly the M17 symbol mapping).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from sigsense_api.dvoice.fec import golay, interleave, viterbi, whitening
from sigsense_api.dvoice.protocols.m17.lsf import LSF_BYTES, Lsf, parse_lsf

FRAME_SYMBOLS = 192  # 8 sync + 184 payload
SYNC_SYMBOLS = 8
PAYLOAD_SYMBOLS = 184
PAYLOAD_BITS = 368

_RANDOMIZER = whitening.m17_randomizer(PAYLOAD_BITS)
_QPP = interleave.table("m17_payload")


def dibits_to_bits(dibits: np.ndarray) -> np.ndarray:
    """dibits (0..3, MSB sent first) -> bits."""
    d = np.asarray(dibits, dtype=np.uint8)
    out = np.empty(d.size * 2, dtype=np.uint8)
    out[0::2] = (d >> 1) & 1
    out[1::2] = d & 1
    return out


def symbols_to_soft_bits(soft: np.ndarray) -> np.ndarray:
    """Normalised symbol levels (-3..+3, sign = polarity as seen by the slicer) -> soft bits.

    MSB: 0 for positive symbols. LSB: 1 for the outer symbols (+-3), 0 for the inner ones (+-1)."""
    s = np.asarray(soft, dtype=np.float64)
    out = np.empty(s.size * 2, dtype=np.float64)
    out[0::2] = np.clip(s / 2.0, -1.0, 1.0)
    out[1::2] = np.clip(2.0 - np.abs(s), -1.0, 1.0)
    return out


def unscramble(bits: np.ndarray, soft: Optional[np.ndarray] = None) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """Type 4 -> Type 3 bits: XOR the fixed randomizer, then undo the QPP interleaver."""
    b = interleave.deinterleave(np.asarray(bits, dtype=np.uint8) ^ _RANDOMIZER, _QPP)
    if soft is None:
        return b, None
    s = np.asarray(soft, dtype=np.float64)
    s = np.where(_RANDOMIZER == 1, -s, s)  # a flipped bit flips the soft sign
    return b, interleave.deinterleave(s, _QPP)


def bits_to_bytes(bits: np.ndarray) -> bytes:
    return np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()


def _word(bits: np.ndarray) -> int:
    value = 0
    for b in bits:
        value = (value << 1) | int(b)
    return value


# --- LSF -------------------------------------------------------------------------------------------


@dataclass
class LsfFrame:
    lsf: Optional[Lsf]  # None unless the CRC verified
    raw: Optional[bytes]  # the 30 decoded bytes (even when the CRC failed; for diagnostics only)
    corrected: int
    viterbi_ok: bool


def decode_lsf_payload(bits368: np.ndarray, soft368: Optional[np.ndarray] = None) -> LsfFrame:
    bits, soft = unscramble(bits368, soft368)
    result = viterbi.viterbi_decode(viterbi.M17_K5_R12_P1, bits, soft)
    raw = bits_to_bytes(result.data[:240]) if result.data.size >= 240 else None
    lsf = parse_lsf(raw) if raw is not None and len(raw) == LSF_BYTES else None
    return LsfFrame(lsf, raw, int(result.corrected), bool(result.ok))


# --- stream ----------------------------------------------------------------------------------------


@dataclass
class StreamFrame:
    lich_ok: bool  # all four Golay words decoded and LICH_CNT is 0..5
    lich_count: Optional[int]  # LICH_CNT
    lich_chunk: Optional[bytes]  # 5 bytes of the LSF, valid when lich_ok
    lich_errors: int  # bit errors corrected in the LICH
    frame_number: int  # 15 bits
    eot: bool  # FN most significant bit
    payload: bytes  # 16 bytes
    corrected: int  # Viterbi path metric (estimated channel errors in the 272 bits)
    viterbi_ok: bool


def decode_lich(bits96: np.ndarray) -> tuple[bool, Optional[int], Optional[bytes], int]:
    data = []
    errors = 0
    for i in range(4):
        decoded = golay.golay_24_12_decode(_word(bits96[24 * i : 24 * i + 24]))
        if decoded is None:
            return False, None, None, errors
        value, fixed = decoded
        data.append(value)
        errors += fixed
    chunk_bits = np.concatenate([((v >> np.arange(11, -1, -1)) & 1) for v in data]).astype(np.uint8)  # 48 bits
    count = int(chunk_bits[40]) << 2 | int(chunk_bits[41]) << 1 | int(chunk_bits[42])
    if count > 5:
        return False, None, None, errors
    return True, count, bits_to_bytes(chunk_bits[:40]), errors


def decode_stream_payload(bits368: np.ndarray, soft368: Optional[np.ndarray] = None) -> StreamFrame:
    bits, soft = unscramble(bits368, soft368)
    lich_ok, count, chunk, lich_errors = decode_lich(bits[:96])
    soft_tail = None if soft is None else soft[96:]
    result = viterbi.viterbi_decode(viterbi.M17_K5_R12_P2, bits[96:], soft_tail)
    data = result.data
    fn_bits = data[:16]
    fn = int(_word(fn_bits))
    return StreamFrame(
        lich_ok=lich_ok,
        lich_count=count,
        lich_chunk=chunk,
        lich_errors=lich_errors,
        frame_number=fn & 0x7FFF,
        eot=bool(fn & 0x8000),
        payload=bits_to_bytes(data[16:144]),
        corrected=int(result.corrected),
        viterbi_ok=bool(result.ok),
    )


# --- packet ----------------------------------------------------------------------------------------


@dataclass
class PacketFrame:
    data: bytes  # 25 bytes (only the first `valid` are real when eof)
    eof: bool
    counter: int  # frame number 0..31 (eof=False) or the number of valid bytes 1..25 (eof=True)
    corrected: int
    viterbi_ok: bool

    @property
    def valid_bytes(self) -> int:
        return min(25, self.counter) if self.eof else 25


def decode_packet_payload(bits368: np.ndarray, soft368: Optional[np.ndarray] = None) -> PacketFrame:
    bits, soft = unscramble(bits368, soft368)
    result = viterbi.viterbi_decode(viterbi.M17_K5_R12_P3, bits, soft)
    data = result.data
    return PacketFrame(
        data=bits_to_bytes(data[:200]),
        eof=bool(data[200]),
        counter=int(_word(data[201:206])),
        corrected=int(result.corrected),
        viterbi_ok=bool(result.ok),
    )


# --- BERT ------------------------------------------------------------------------------------------


@dataclass
class BertFrame:
    bits: np.ndarray  # 197 PRBS9 bits
    corrected: int
    viterbi_ok: bool


def decode_bert_payload(bits368: np.ndarray, soft368: Optional[np.ndarray] = None) -> BertFrame:
    """The transmitter punctures 402 coded bits to 369 with P2 and drops the last, so the missing
    369th bit is restored as an erasure (soft value 0) before decoding."""
    bits, soft = unscramble(bits368, soft368)
    padded = np.concatenate([bits, np.zeros(1, dtype=np.uint8)])
    base = np.where(bits == 0, 1.0, -1.0) if soft is None else soft
    padded_soft = np.concatenate([base, np.zeros(1)])
    result = viterbi.viterbi_decode(viterbi.M17_K5_R12_P2, padded, padded_soft)
    return BertFrame(result.data[:197].copy(), int(result.corrected), bool(result.ok))


# --- PRBS9 for BERT --------------------------------------------------------------------------------


class Prbs9Checker:
    """PRBS9 (x^9 + x^5 + 1) receiver per the M17 specification ("PRBS Receiver"):

    * synchronising: each received bit is compared with the LFSR taps and then shifted in;
      eighteen consecutive agreeing bits lock it (nothing is counted before that);
    * locked: the LFSR free-runs and every received bit disagreeing with it is one bit error;
    * more than 18 errors within 128 bits drops the lock and starts over (missed frames)."""

    LOCK_COUNT = 18
    WINDOW = 128
    WINDOW_LIMIT = 18

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = 0
        self.sync_count = 0
        self.locked = False
        self.bits = 0
        self.errors = 0
        self._window: list[int] = []

    def push(self, bits: np.ndarray) -> None:
        state = self.state
        for bit in np.asarray(bits, dtype=np.uint8):
            bit = int(bit)
            expected = ((state >> 8) ^ (state >> 4)) & 1
            if not self.locked:
                state = ((state << 1) | bit) & 0x1FF
                if bit != expected:
                    self.sync_count = 0
                else:
                    self.sync_count += 1
                    if self.sync_count >= self.LOCK_COUNT:
                        self.locked = True
                        self._window = []
                continue
            state = ((state << 1) | expected) & 0x1FF  # free-running generator
            wrong = int(bit != expected)
            self.bits += 1
            self.errors += wrong
            self._window.append(wrong)
            if len(self._window) > self.WINDOW:
                self._window.pop(0)
            if len(self._window) == self.WINDOW and sum(self._window) > self.WINDOW_LIMIT:
                self.locked = False
                self.sync_count = 0
                state = ((state << 1) | bit) & 0x1FF
        self.state = state

    @property
    def ber(self) -> Optional[float]:
        return (self.errors / self.bits) if self.bits else None
