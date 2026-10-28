"""Synthetic M17 transmissions (WP-E test encoder). Original code from the public M17 specification.

Deliberately a separate code path from protocols/m17: callsign encoding follows the spec's
C example literally, the CRC is a plain bitwise loop, TYPE is packed by hand, and frames are
assembled in transmit order (encode -> puncture -> interleave -> randomize). It shares only the
FEC *library* primitives (convolutional encoder, Golay encoder, QPP table, randomizer) that WP-A
verified against the specification vectors.

Output is a numpy array of dibits in the dsp/types.py convention, ready to feed a decoder directly
or to modulate through WP-B's test modulators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from sigsense_api.dvoice.fec import golay, interleave, viterbi, whitening

_ALPHABET_NOTE = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/."

SYNC_WORDS = {"lsf": 0x55F7, "stream": 0xFF5D, "bert": 0xDF55, "packet": 0x75FF}
EOT_WORD = 0x555D


def encode_callsign(callsign: str) -> int:
    """Spec appendix C "Encoder Example", transcribed: last character first, base 40."""
    address = 0
    text = callsign[:9]
    for ch in reversed(text):
        if "A" <= ch <= "Z":
            val = ord(ch) - ord("A") + 1
        elif "0" <= ch <= "9":
            val = ord(ch) - ord("0") + 27
        elif ch == "-":
            val = 37
        elif ch == "/":
            val = 38
        elif ch == ".":
            val = 39
        elif "a" <= ch <= "z":
            val = ord(ch) - ord("a") + 1
        else:
            val = 0
        address = 40 * address + val
    return address


def address_bytes(callsign: str) -> bytes:
    return encode_callsign(callsign).to_bytes(6, "big")


BROADCAST = b"\xff" * 6


def crc16(data: bytes) -> int:
    """Bitwise MSB-first CRC-16, polynomial 0x5935, init 0xFFFF, no reflection (spec "CRC")."""
    reg = 0xFFFF
    for byte in data:
        for bit in range(7, -1, -1):
            top = (reg >> 15) & 1
            reg = (reg << 1) & 0xFFFF
            if top ^ ((byte >> bit) & 1):
                reg ^= 0x5935
    return reg


def make_type(
    *,
    stream: bool = True,
    data_type: int = 2,
    enc_type: int = 0,
    enc_subtype: int = 0,
    can: int = 0,
    signed: bool = False,
) -> int:
    """Pack TYPE by hand from the spec's byte/bit tables.

    byte 0 (MSB): bits 7-4 reserved, bit 3 signed, bits 2-0 = CAN[3:1]
    byte 1 (LSB): bit 7 = CAN[0], bits 6-5 enc subtype, bits 4-3 enc type, bits 2-1 data type, bit 0 P/S"""
    byte0 = (1 << 3 if (stream and signed) else 0) | ((can >> 1) & 0b111)
    byte1 = ((can & 1) << 7) | (enc_subtype & 3) << 5 | (enc_type & 3) << 3 | (data_type & 3) << 1 | (1 if stream else 0)
    if not stream:
        byte1 &= 0b10000001  # packet mode: only P/S and CAN are defined
    return (byte0 << 8) | byte1


def lsf_bytes(dst: bytes, src: bytes, type_raw: int, meta: bytes = bytes(14)) -> bytes:
    assert len(dst) == 6 and len(src) == 6 and len(meta) == 14
    body = dst + src + type_raw.to_bytes(2, "big") + meta
    return body + crc16(body).to_bytes(2, "big")


# --- bit helpers ---------------------------------------------------------------------------------


def _bits(data: bytes, nbits: Optional[int] = None) -> np.ndarray:
    out = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    return out if nbits is None else out[:nbits]


def _int_bits(value: int, width: int) -> np.ndarray:
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)


_RAND = whitening.m17_randomizer(368)
_QPP = interleave.table("m17_payload")


def _finish(type3: np.ndarray) -> np.ndarray:
    """Type 3 bits -> Type 4 (interleave) -> randomized payload bits."""
    assert type3.size == 368, type3.size
    return interleave.interleave(type3, _QPP) ^ _RAND


def lsf_payload(lsf30: bytes) -> np.ndarray:
    coded = viterbi.conv_encode(viterbi.M17_K5_R12_P1, _bits(lsf30, 240))
    return _finish(coded)


def lich_bits(lsf30: bytes, count: int) -> np.ndarray:
    """96 LICH bits for chunk `count` (0..5): 40 LSF bits + 3-bit counter + 5 reserved zero bits,
    cut into four 12-bit words, each Golay(24,12) encoded."""
    chunk = _bits(lsf30[5 * count : 5 * count + 5], 40)
    word48 = np.concatenate([chunk, _int_bits(count, 3), np.zeros(5, dtype=np.uint8)])
    out = []
    for i in range(4):
        part = int("".join(map(str, word48[12 * i : 12 * i + 12])), 2)
        out.append(_int_bits(golay.golay_24_12_encode(part), 24))
    return np.concatenate(out)


def stream_payload(lsf30: bytes, count: int, frame_number: int, payload16: bytes, eot: bool = False) -> np.ndarray:
    assert len(payload16) == 16
    fn = (frame_number & 0x7FFF) | (0x8000 if eot else 0)
    info = np.concatenate([_int_bits(fn, 16), _bits(payload16)])
    coded = viterbi.conv_encode(viterbi.M17_K5_R12_P2, info)
    assert coded.size == 272, coded.size
    return _finish(np.concatenate([lich_bits(lsf30, count), coded]))


def packet_payload(chunk: bytes, eof: bool, counter: int) -> np.ndarray:
    """25-byte chunk (zero padded) + EOF flag + 5-bit counter (frame number or valid-byte count)."""
    data = chunk.ljust(25, b"\x00")[:25]
    info = np.concatenate([_bits(data), [1 if eof else 0], _int_bits(counter & 0x1F, 5)]).astype(np.uint8)
    coded = viterbi.conv_encode(viterbi.M17_K5_R12_P3, info)
    assert coded.size == 368, coded.size
    return _finish(coded)


def bert_payload(prbs197: np.ndarray) -> np.ndarray:
    coded = viterbi.conv_encode(viterbi.M17_K5_R12_P2, np.asarray(prbs197, dtype=np.uint8))
    assert coded.size == 369, coded.size
    return _finish(coded[:368])  # the 369th punctured bit is discarded


class Prbs9:
    """x^9 + x^5 + 1, state initialised to 1; the generated bit is the output (spec "PRBS Generation")."""

    def __init__(self) -> None:
        self.state = 1

    def bits(self, n: int) -> np.ndarray:
        out = np.empty(n, dtype=np.uint8)
        for i in range(n):
            result = ((self.state >> 8) ^ (self.state >> 4)) & 1
            self.state = ((self.state << 1) | result) & 0x1FF
            out[i] = result
        return out


# --- symbols ---------------------------------------------------------------------------------------


def _word_dibits(word16: int) -> np.ndarray:
    return np.array([(word16 >> s) & 3 for s in range(14, -1, -2)], dtype=np.uint8)


def bits_to_dibits(bits: np.ndarray) -> np.ndarray:
    b = np.asarray(bits, dtype=np.uint8)
    return ((b[0::2] << 1) | b[1::2]).astype(np.uint8)


def frame_dibits(kind: str, payload_bits: np.ndarray) -> np.ndarray:
    return np.concatenate([_word_dibits(SYNC_WORDS[kind]), bits_to_dibits(payload_bits)])


def preamble(first: str = "lsf") -> np.ndarray:
    """192 symbols of alternating outer symbols: +3,-3 before an LSF; -3,+3 before BERT."""
    pair = [1, 3] if first == "lsf" else [3, 1]
    return np.array(pair * 96, dtype=np.uint8)


def eot() -> np.ndarray:
    return np.tile(_word_dibits(EOT_WORD), 24)  # 192 symbols of 0x555D


# --- transmissions ---------------------------------------------------------------------------------


@dataclass
class SyntheticM17Call:
    src: str = "W2FBI"
    dst: str = "ALL"  # "BROADCAST" or "ALL" -> 0xFFFFFFFFFFFF
    can: int = 0
    data_type: int = 2  # 1 data, 2 voice (3200), 3 voice+data (1600)
    enc_type: int = 0
    enc_subtype: int = 0
    signed: bool = False
    meta: bytes = bytes(14)
    payloads: Sequence[bytes] = field(default_factory=list)  # one 16-byte payload per stream frame
    send_eot: bool = True
    first_fn: int = 0

    def lsf(self) -> bytes:
        dst = BROADCAST if self.dst.upper() in ("ALL", "BROADCAST") else address_bytes(self.dst)
        t = make_type(
            stream=True,
            data_type=self.data_type,
            enc_type=self.enc_type,
            enc_subtype=self.enc_subtype,
            can=self.can,
            signed=self.signed,
        )
        return lsf_bytes(dst, address_bytes(self.src), t, self.meta)


def stream_transmission(call: SyntheticM17Call, *, with_preamble: bool = True, with_eot: bool = True) -> np.ndarray:
    """Preamble, LSF frame, stream frames (LICH cycling through the six chunks), EOT marker."""
    lsf = call.lsf()
    parts = []
    if with_preamble:
        parts.append(preamble("lsf"))
    parts.append(frame_dibits("lsf", lsf_payload(lsf)))
    last = len(call.payloads) - 1
    for i, payload in enumerate(call.payloads):
        fn = call.first_fn + i
        parts.append(frame_dibits("stream", stream_payload(lsf, fn % 6, fn, payload, eot=(i == last and call.send_eot))))
    if with_eot:
        parts.append(eot())
    return np.concatenate(parts)


def packet_transmission(src: str, dst: str, packet: bytes, *, can: int = 0, with_preamble: bool = True) -> np.ndarray:
    """LSF (packet mode) + packet frames. `packet` is the application data; the CRC is appended here
    (it covers the whole data including the data-type specifier, per the spec)."""
    dst_b = BROADCAST if dst.upper() in ("ALL", "BROADCAST") else address_bytes(dst)
    lsf = lsf_bytes(dst_b, address_bytes(src), make_type(stream=False, can=can))
    data = packet + crc16(packet).to_bytes(2, "big")
    chunks = [data[i : i + 25] for i in range(0, len(data), 25)]
    assert len(chunks) <= 33
    parts = [preamble("lsf")] if with_preamble else []
    parts.append(frame_dibits("lsf", lsf_payload(lsf)))
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            parts.append(frame_dibits("packet", packet_payload(chunk, True, len(chunk))))
        else:
            parts.append(frame_dibits("packet", packet_payload(chunk, False, i)))
    parts.append(eot())
    return np.concatenate(parts)


def bert_transmission(frames: int, *, with_preamble: bool = True) -> np.ndarray:
    prbs = Prbs9()
    parts = [preamble("bert")] if with_preamble else []
    for _ in range(frames):
        parts.append(frame_dibits("bert", bert_payload(prbs.bits(197))))
    parts.append(eot())
    return np.concatenate(parts)
