"""P25 Phase 1 frame ENCODERS for tests and synthetic captures (WP-C). Original code.

Builds bit-exact data units (frame sync, NID with BCH, payload with the real FEC
and interleaving, status symbols) from fields. The decoder in protocols/p25p1 is
written from the same specification text; the encoder shares the field layouts
in protocols/p25p1/{words,tsbk}.py, so round-trips prove consistency, and the
known-answer checks in the tests cover the parts the spec gives examples for.
Never use this for transmission: it exists to produce test signals.
"""

from __future__ import annotations

from typing import Iterator, Optional, Sequence

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.fec import bch, crc, trellis
from sigsense_api.dvoice.protocols.p25p1 import framing as fr
from sigsense_api.dvoice.protocols.p25p1 import tsbk as tsbk_mod
from sigsense_api.dvoice.protocols.p25p1 import voice, words

STATUS_DIBIT = 2  # "unknown, use for inbound or outbound" (Table 8-2)


def encode_nid(nac: int, duid: int) -> np.ndarray:
    return fr.bits_to_dibits(fr.int_to_bits(bch.p25_nid_encode(nac & 0xFFF, duid & 0xF), 64))


def _wrap(duid: int, nac: int, payload: np.ndarray, n_info: int, status: int = STATUS_DIBIT) -> np.ndarray:
    """sync + NID + payload dibits, padded with null dibits to the unit length, status symbols inserted."""
    info = np.concatenate((fr.SYNC_DIBITS, encode_nid(nac, duid), np.asarray(payload, dtype=np.uint8)))
    if info.size > n_info:
        raise ValueError("payload longer than the data unit")
    info = np.concatenate((info, np.zeros(n_info - info.size, dtype=np.uint8)))
    return fr.insert_status(info, status)


def build_hdu(nac: int, *, tgid: int = 0, mi: bytes = bytes(9), mfid: int = 0, algid: int = words.ALGID_CLEAR, kid: int = 0) -> np.ndarray:
    bits = words.encode_header(words.build_header(mi=mi, mfid=mfid, algid=algid, kid=kid, tgid=tgid))
    return _wrap(fr.DUID_HDU, nac, fr.bits_to_dibits(bits), fr.HDU_INFO_DIBITS)


def random_voice_bits(rng: np.random.Generator, n: int = 9) -> list[np.ndarray]:
    return [rng.integers(0, 2, 88).astype(np.uint8) for _ in range(n)]


def _ldu(duid: int, nac: int, word_bits240: np.ndarray, voice_frames: Sequence[np.ndarray], lsd: Sequence[int]) -> np.ndarray:
    if len(voice_frames) != 9:
        raise ValueError("an LDU carries nine voice frames")
    chunks = fr.bits_to_dibits(word_bits240)  # 120 dibits, 20 per LC segment
    lsd_dibits = [fr.bits_to_dibits(fr.int_to_bits(words.lsd_encode(o & 0xFF), 16)) for o in (list(lsd) + [0, 0])[:2]]
    parts: list[np.ndarray] = []
    vi = li = si = 0
    for kind, n in fr.LDU_SEGMENTS:
        if kind == "vc":
            parts.append(fr.bits_to_dibits(voice.encode_voice_frame(voice_frames[vi])))
            vi += 1
        elif kind == "lc":
            parts.append(chunks[20 * li : 20 * (li + 1)])
            li += 1
        else:
            parts.append(lsd_dibits[si])
            si += 1
    return _wrap(duid, nac, np.concatenate(parts), fr.LDU_INFO_DIBITS)


def build_ldu1(nac: int, lc_bits72: np.ndarray, voice_frames: Sequence[np.ndarray], lsd: Sequence[int] = (0, 0)) -> np.ndarray:
    return _ldu(fr.DUID_LDU1, nac, words.encode_lc_hamming(lc_bits72), voice_frames, lsd)


def build_ldu2(nac: int, es_bits96: np.ndarray, voice_frames: Sequence[np.ndarray], lsd: Sequence[int] = (0, 0)) -> np.ndarray:
    return _ldu(fr.DUID_LDU2, nac, words.encode_es(es_bits96), voice_frames, lsd)


def build_tdu(nac: int) -> np.ndarray:
    return _wrap(fr.DUID_TDU, nac, np.empty(0, dtype=np.uint8), fr.TDU_INFO_DIBITS)


def build_tdulc(nac: int, lc_bits72: np.ndarray) -> np.ndarray:
    return _wrap(fr.DUID_TDULC, nac, fr.bits_to_dibits(words.encode_lc_golay(lc_bits72)), fr.TDULC_INFO_DIBITS)


# --- trunking signalling -------------------------------------------------------------


def tsbk_bits(opcode: int, args64: int, *, mfid: int = 0, last_block: bool = True, protected: bool = False) -> np.ndarray:
    head = tsbk_mod.build_tsbk_bits(opcode, args64, mfid=mfid, last_block=last_block, protected=protected)
    c = crc.crc_compute("crc16_p25_dmr_header", head)
    return np.concatenate((head, fr.int_to_bits(c, 16)))


def build_tsdu(nac: int, blocks: Sequence[tuple[int, int]], *, mfid: int = 0) -> np.ndarray:
    """blocks: (opcode, args64) pairs, one to three; the last gets the last-block flag."""
    if not 1 <= len(blocks) <= 3:
        raise ValueError("a TSDU carries one to three TSBKs")
    out = []
    for i, (op, args) in enumerate(blocks):
        bits = tsbk_bits(op, args, mfid=mfid, last_block=i == len(blocks) - 1)
        out.append(trellis.p25_trellis_half_encode(bits))
    n_info = fr.HEADER_INFO_DIBITS + fr.TSBK_BLOCK_DIBITS * len(blocks)
    return _wrap(fr.DUID_TSDU, nac, np.concatenate(out), n_info)


def _pack(*fields: tuple[int, int]) -> int:
    """(value, width) pairs -> one integer, first field most significant."""
    v = 0
    total = 0
    for value, width in fields:
        v = (v << width) | (value & ((1 << width) - 1))
        total += width
    if total != 64:
        raise ValueError(f"TSBK arguments are 64 bits, got {total}")
    return v


def args_grp_v_ch_grant(service: int, channel: int, group: int, source: int) -> int:
    return _pack((service, 8), (channel, 16), (group, 16), (source, 24))


def args_grp_v_ch_grant_updt(ch_a: int, grp_a: int, ch_b: int, grp_b: int) -> int:
    return _pack((ch_a, 16), (grp_a, 16), (ch_b, 16), (grp_b, 16))


def args_grp_v_ch_grant_updt_exp(service: int, tx_channel: int, rx_channel: int, group: int) -> int:
    return _pack((service, 8), (0, 8), (tx_channel, 16), (rx_channel, 16), (group, 16))


def args_uu_v_ch_grant(channel: int, target: int, source: int) -> int:
    return _pack((channel, 16), (target, 24), (source, 24))


def args_rfss_sts(lra: int, system_id: int, rfss_id: int, site_id: int, channel: int, service_class: int = 0) -> int:
    return _pack((lra, 8), (0, 4), (system_id, 12), (rfss_id, 8), (site_id, 8), (channel, 16), (service_class, 8))


def args_net_sts(lra: int, wacn: int, system_id: int, channel: int, service_class: int = 0) -> int:
    return _pack((lra, 8), (wacn, 20), (system_id, 12), (channel, 16), (service_class, 8))


def args_adj_sts(lra: int, cfva: int, system_id: int, rfss_id: int, site_id: int, channel: int, service_class: int = 0) -> int:
    return _pack((lra, 8), (cfva, 4), (system_id, 12), (rfss_id, 8), (site_id, 8), (channel, 16), (service_class, 8))


def args_iden_up(iden: int, bandwidth_hz: int, tx_offset_hz: int, spacing_hz: int, base_hz: int) -> int:
    sign = 1 if tx_offset_hz >= 0 else 0
    toff = (sign << 8) | min(abs(tx_offset_hz) // 250_000, 0xFF)
    return _pack((iden, 4), (bandwidth_hz // 125, 9), (toff, 9), (spacing_hz // 125, 10), (base_hz // 5, 32))


def args_iden_up_vu(iden: int, bandwidth_code: int, tx_offset_raw: int, spacing_hz: int, base_hz: int) -> int:
    return _pack((iden, 4), (bandwidth_code, 4), (tx_offset_raw, 14), (spacing_hz // 125, 10), (base_hz // 5, 32))


# --- packet data (unconfirmed delivery) -------------------------------------------------


def build_pdu_unconfirmed(nac: int, *, llid: int, sap: int, payload: bytes, mfid: int = 0, outbound: bool = True) -> np.ndarray:
    n_blocks = max(1, -(-(len(payload) + 4) // 12))
    pad = 12 * n_blocks - 4 - len(payload)
    body = payload + bytes(pad)
    octets = bytearray(10)
    octets[0] = (0x20 if outbound else 0) | tsbk_mod.FORMAT_UNCONFIRMED
    octets[1] = 0x80 | 0x40 | (sap & 0x3F)
    octets[2] = mfid & 0xFF
    octets[3:6] = (llid & 0xFFFFFF).to_bytes(3, "big")
    octets[6] = 0x80 | (n_blocks & 0x7F)
    octets[7] = pad & 0x1F
    hdr_bits = np.unpackbits(np.frombuffer(bytes(octets), dtype=np.uint8))
    hdr = np.concatenate((hdr_bits, fr.int_to_bits(crc.crc_compute("crc16_p25_dmr_header", hdr_bits), 16)))
    c32 = crc.crc_compute("crc32_p25_packet", np.unpackbits(np.frombuffer(body, dtype=np.uint8)))
    data = body + c32.to_bytes(4, "big")
    parts = [trellis.p25_trellis_half_encode(hdr)]
    for i in range(n_blocks):
        blk = np.unpackbits(np.frombuffer(data[12 * i : 12 * (i + 1)], dtype=np.uint8))
        parts.append(trellis.p25_trellis_half_encode(blk))
    n_info = fr.HEADER_INFO_DIBITS + fr.TSBK_BLOCK_DIBITS * (1 + n_blocks)
    return _wrap(fr.DUID_PDU, nac, np.concatenate(parts), n_info)


# --- whole transmissions ---------------------------------------------------------------------


def build_voice_call(
    nac: int,
    *,
    tgid: int,
    source: int,
    superframes: int = 2,
    emergency: bool = False,
    algid: int = words.ALGID_CLEAR,
    kid: int = 0,
    mi: bytes = bytes(range(9)),
    with_hdu: bool = True,
    terminator: str = "tdu",  # "tdu" | "tdulc" | "none"
    rng: Optional[np.random.Generator] = None,
    voice_frames: Optional[list[np.ndarray]] = None,
    service_options: Optional[int] = None,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """A complete group voice call. Returns (dibits, the 88-bit voice parameter vectors sent in order)."""
    rng = rng or np.random.default_rng(0)
    svc = (0x80 if emergency else 0) | (0x40 if algid != words.ALGID_CLEAR and service_options is None else 0)
    if service_options is not None:
        svc = service_options
    lc = words.build_lc(lco=words.LCO_GROUP_VOICE, service_options=svc, target_id=tgid, source_id=source)
    es = words.build_es(mi=mi, algid=algid, kid=kid)
    parts = []
    if with_hdu:
        parts.append(build_hdu(nac, tgid=tgid, mi=mi, algid=algid, kid=kid))
    sent: list[np.ndarray] = []
    for _ in range(superframes):
        v1 = voice_frames[len(sent) : len(sent) + 9] if voice_frames else random_voice_bits(rng)
        sent += list(v1)
        parts.append(build_ldu1(nac, lc, v1))
        v2 = voice_frames[len(sent) : len(sent) + 9] if voice_frames else random_voice_bits(rng)
        sent += list(v2)
        parts.append(build_ldu2(nac, es, v2))
    if terminator == "tdu":
        parts.append(build_tdu(nac))
    elif terminator == "tdulc":
        parts.append(build_tdulc(nac, lc))
    return np.concatenate(parts), sent


# --- feeding a decoder ---------------------------------------------------------------------------


def to_blocks(dibits: np.ndarray, *, chunk: int = 480, start_index: int = 0, t0: float = 0.0, symbol_rate: float = 4800.0) -> Iterator[SymbolBlock]:
    """Cut a dibit stream into SymbolBlocks of `chunk` symbols (the last may be shorter)."""
    d = np.asarray(dibits, dtype=np.uint8)
    for i in range(0, d.size, chunk):
        part = d[i : i + chunk]
        yield SymbolBlock(
            dibits=part, soft=None, symbol_rate=symbol_rate, first_index=start_index + i,
            t0=t0 + (start_index + i) / symbol_rate, modulation=Modulation.C4FM,
        )
