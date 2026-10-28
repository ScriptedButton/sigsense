"""DMR burst / superframe encoder for tests and synthetic captures (WP-D). Original code.

Builds the on-air symbol stream described in ETSI TS 102 361-1 (clause 6, annex E):
voice bursts A-F with embedded LC, voice LC header / terminator, CSBK, data header and
rate-1/2 and rate-3/4 data blocks, CACH with TACT and short LC, for one or two slots.

It is deliberately written against the spec tables (constants.py) rather than by calling
the decoder, so a round trip exercises both directions of every field. Output is dibits
in the SymbolBlock convention (dsp/types.py); feed them to `testing.modulator` for IQ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from sigsense_api.dvoice.fec import bptc, trellis
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr import csbk as csbk_mod
from sigsense_api.dvoice.protocols.dmr import layer2, lc as lc_mod
from sigsense_api.dvoice.protocols.dmr.bits import (
    bits_to_dibits,
    bytes_to_bits,
    dibits_to_bits,
    int_to_bits,
)

VoiceBitsFn = Callable[[int, int], np.ndarray]  # (burst_counter, frame 0..2) -> 72 bits


def default_voice_bits(burst: int, frame: int) -> np.ndarray:
    """72 pseudo-random bits whose first six encode (burst*3+frame) % 64: lets tests check order."""
    rng = np.random.default_rng(1000 + burst * 3 + frame)
    bits = rng.integers(0, 2, 72).astype(np.uint8)
    bits[:6] = int_to_bits((burst * 3 + frame) % 64, 6)
    return bits


@dataclass
class DmrEncoder:
    cc: int = 1
    source: str = "bs"  # sync family: "bs" (outbound, with CACH) | "ms" | "dm1" | "dm2"
    voice_bits: VoiceBitsFn = default_voice_bits

    # ------------------------------------------------------------------ bursts

    def _sync(self, traffic: str) -> np.ndarray:
        name = f"{self.source}_{traffic}"
        return C.SYNC_BY_NAME[name].bits

    def voice_burst(
        self, idx: int, frames72: list[np.ndarray], embedded32: Optional[np.ndarray] = None,
        pi: int = 0, lcss: int = 0,
    ) -> np.ndarray:
        """Voice burst `idx` (0 = A with sync, 1..5 = B..F with EMB) -> 132 dibits."""
        vs = np.concatenate(frames72).astype(np.uint8)
        if vs.size != 216:
            raise ValueError("a voice burst carries three 72-bit frames")
        if idx == 0:
            centre = self._sync("voice")
        else:
            emb = layer2.encode_emb(self.cc, pi, lcss)
            emb32 = np.zeros(32, np.uint8) if embedded32 is None else np.asarray(embedded32, np.uint8)
            centre = np.concatenate([emb[:8], emb32, emb[8:]])
        bits = np.concatenate([vs[:108], centre, vs[108:]])
        return bits_to_dibits(bits)

    def data_burst(self, data_type: int, info196: np.ndarray) -> np.ndarray:
        info = np.asarray(info196, dtype=np.uint8)
        slot = layer2.encode_slot_type(self.cc, data_type)
        bits = np.concatenate([info[:98], slot[:10], self._sync("data"), slot[10:], info[98:]])
        return bits_to_dibits(bits)

    def idle_burst(self) -> np.ndarray:
        """Idle burst with the spec's pseudo-random fill (table D.2); all-zero fill would leave long
        single-level runs that upset a receiver's level tracking."""
        info = bytes_to_bits(bytes.fromhex(C.IDLE_INFO_HEX))
        return self.data_burst(C.DataType.IDLE, bptc.bptc_196_96_encode(info))

    # ------------------------------------------------------------------ typed data bursts

    def voice_lc_header(self, lc9: bytes) -> np.ndarray:
        return self.data_burst(C.DataType.VOICE_LC_HEADER, lc_mod.encode_lc_burst(lc9, C.DataType.VOICE_LC_HEADER))

    def terminator(self, lc9: bytes) -> np.ndarray:
        return self.data_burst(C.DataType.TERMINATOR_WITH_LC, lc_mod.encode_lc_burst(lc9, C.DataType.TERMINATOR_WITH_LC))

    def csbk(self, c: csbk_mod.Csbk) -> np.ndarray:
        return self.data_burst(C.DataType.CSBK, csbk_mod.encode_csbk_block(c))

    def pi_header(self, octets10: bytes) -> np.ndarray:
        return self.data_burst(C.DataType.PI_HEADER, csbk_mod.encode_block(octets10, C.DataType.PI_HEADER))

    def data_header(self, octets10: bytes) -> np.ndarray:
        return self.data_burst(C.DataType.DATA_HEADER, csbk_mod.encode_block(octets10, C.DataType.DATA_HEADER))

    def data_block_half(self, payload12: bytes) -> np.ndarray:
        return self.data_burst(C.DataType.RATE_12_DATA, bptc.bptc_196_96_encode(bytes_to_bits(payload12)))

    def data_block_three_quarter(self, payload18: bytes) -> np.ndarray:
        dibits = trellis.dmr_trellis_three_quarter_encode(bytes_to_bits(payload18))
        return self.data_burst(C.DataType.RATE_34_DATA, dibits_to_bits(dibits))

    def mbc(self, header: csbk_mod.Csbk, continuation_octets: list[bytes]) -> list[np.ndarray]:
        """MBC header + continuation blocks; the last block carries the CRC-16 over the continuation
        blocks (see csbk.MbcAssembler for the span). Blocks are 12 octets; the last carries 10 + CRC."""
        from sigsense_api.dvoice.fec import crc

        out = [self.data_burst(C.DataType.MBC_HEADER, csbk_mod.encode_block(header.to_bytes(), C.DataType.MBC_HEADER))]
        blocks = [bytes(b) for b in continuation_octets]
        last = bytearray(blocks[-1][:10])
        last[0] |= 0x80  # LB
        blocks[-1] = bytes(last)
        body = b"".join(blocks)
        value = crc.crc_compute("crc16_p25_dmr_header", bytes_to_bits(body))
        blocks[-1] = blocks[-1] + value.to_bytes(2, "big")
        for b in blocks:
            out.append(self.data_burst(C.DataType.MBC_CONTINUATION, bptc.bptc_196_96_encode(bytes_to_bits(b))))
        return out

    # ------------------------------------------------------------------ voice superframes

    def voice_superframe(self, lc9: bytes, first_burst: int = 0, embed_lc: bool = True) -> list[np.ndarray]:
        """Six voice bursts A-F with the embedded LC spread over B-E."""
        frags = (
            np.asarray(bptc.embedded_lc_encode(bytes_to_bits(lc9)), dtype=np.uint8).reshape(4, 32)
            if embed_lc else [None] * 4
        )
        lcss = (1, 3, 3, 2)
        out = []
        for i in range(6):
            frames = [self.voice_bits(first_burst + i, k) for k in range(3)]
            if i == 0:
                out.append(self.voice_burst(0, frames))
            elif 1 <= i <= 4:
                out.append(self.voice_burst(i, frames, frags[i - 1], 0, lcss[i - 1] if embed_lc else 0))
            else:
                out.append(self.voice_burst(5, frames, None, 0, 0))
        return out

    def voice_call(
        self, lc9: bytes, superframes: int = 3, header: bool = True, terminator: bool = True,
        extra_lcs: Optional[list[bytes]] = None,
    ) -> list[np.ndarray]:
        """Header, N superframes (embedded LC repeated or rotated through `extra_lcs`), terminator."""
        bursts: list[np.ndarray] = []
        if header:
            bursts.append(self.voice_lc_header(lc9))
        rotation = [lc9] + list(extra_lcs or [])
        for s in range(superframes):
            bursts.extend(self.voice_superframe(rotation[s % len(rotation)], first_burst=s * 6))
        if terminator:
            bursts.append(self.terminator(lc9))
        return bursts


# ---------------------------------------------------------------------- stream assembly


def _cach(tc: int, at: int, lcss: int, payload17: np.ndarray) -> np.ndarray:
    return bits_to_dibits(layer2.encode_cach(at, tc, lcss, payload17))


def assemble_bs_stream(
    slot0: list[np.ndarray], slot1: list[np.ndarray], enc: DmrEncoder,
    short_lc: Optional[tuple[int, int]] = None, lead_idle: int = 0, tail_idle: int = 0,
) -> np.ndarray:
    """Interleave two slots' bursts into the outbound 144-dibit frame stream (CACH + burst).

    Slot 0 bursts go on even frames and slot 1 bursts on odd frames, so TC alternates
    0,1,0,1. A shorter slot is padded with Idle bursts. `short_lc=(slco, data24)` is carried
    in the CACH payloads, four frames per repetition (LCSS 01, 11, 11, 10)."""
    idle = enc.idle_burst()
    s0 = [idle] * lead_idle + list(slot0) + [idle] * tail_idle
    s1 = [idle] * lead_idle + list(slot1) + [idle] * tail_idle
    n = max(len(s0), len(s1))
    s0 += [idle] * (n - len(s0))
    s1 += [idle] * (n - len(s1))
    rows = layer2.encode_short_lc(*short_lc) if short_lc else None
    out = []
    counter = 0
    for i in range(n):
        for tc, burst in ((0, s0[i]), (1, s1[i])):
            if rows is not None:
                k = counter % 4
                lcss = (1, 3, 3, 2)[k]
                payload = rows[k]
            else:
                lcss, payload = 0, np.zeros(17, np.uint8)
            out.append(_cach(tc, 1, lcss, payload))
            out.append(burst)
            counter += 1
    return np.concatenate(out).astype(np.uint8)


def assemble_direct_stream(bursts: list[np.ndarray], gap_dibits: int = 12, noise_seed: int = 0) -> np.ndarray:
    """MS / direct-mode stream: bursts every 144 symbols with an unmodulated-looking gap between."""
    rng = np.random.default_rng(noise_seed)
    out = []
    for b in bursts:
        out.append(b)
        out.append(rng.integers(0, 4, gap_dibits).astype(np.uint8))
    return np.concatenate(out).astype(np.uint8)
