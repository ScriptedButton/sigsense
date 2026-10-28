"""Passive Bluetooth LE 1M/2M framing plus legacy 1M test-waveform helpers.

Supports advertising access address (0x8E89BED6) and optional channel whitening.
Intended for HackRF / wideband IQ captures around 2.4 GHz.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import uuid4

import numpy as np
from scipy import signal as sp_signal

# Advertising channel access address (on-air, LSB first)
ADV_ACCESS_ADDRESS = 0x8E89BED6
ADV_AA_BITS = np.unpackbits(
    np.array(
        [
            ADV_ACCESS_ADDRESS & 0xFF,
            (ADV_ACCESS_ADDRESS >> 8) & 0xFF,
            (ADV_ACCESS_ADDRESS >> 16) & 0xFF,
            (ADV_ACCESS_ADDRESS >> 24) & 0xFF,
        ],
        dtype=np.uint8,
    ),
    bitorder="little",
)

# BLE CRC-24 (BLE poly reflected form used with LSB-first bit processing)
CRC_POLY = 0x00065B
# Advertised CRCInit 0x555555 is bit-reversed internally by BTLE/core to 0xAAAAAA
# before the byte CRC engine (see btle_rx.c crc_init_reorder).
CRC_INIT_ADVERTISING = 0xAAAAAA
CRC_INIT_ADVERTISING_INPUT = 0x555555

BLE_CHANNELS_MHZ = {
    37: 2402,
    38: 2426,
    39: 2480,
    **{i: 2404 + i * 2 for i in range(0, 11)},
    **{i: 2428 + (i - 11) * 2 for i in range(11, 37)},
}


@dataclass
class BlePacket:
    access_address: str
    channel: int | None
    pdu_type: int
    pdu_type_name: str
    length: int
    header_hex: str
    payload_hex: str
    crc_hex: str
    crc_ok: bool
    rssi_dbfs: float | None
    offset_bits: int
    dewhitened: bool
    adv_address: str | None = None
    ad_structures: list | None = None
    summary: str | None = None
    led_remote: bool = False

    capture_id: str | None = None
    phy: str = "LE1M"
    packet_kind: str = "advertising"
    sample_offset: int | None = None
    timestamp_seconds: float | None = None
    cfo_hz: float | None = None
    aa_errors: int = 0
    protocol_fields: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _mac_le(raw6: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw6[::-1])


def is_led_remote_advdata(adv_data: bytes) -> bool:
    """Known LED-remote AdvData: len=0x0d type=0xff then 55aa… or 4c00… custom blob."""
    if len(adv_data) < 4 or adv_data[0] != 0x0D or adv_data[1] != 0xFF:
        return False
    return adv_data[2:4] in (b"\x55\xaa", b"\x4c\x00")


def parse_adv_payload(pdu_type: int, payload: bytes, header0: int = 0) -> dict:
    """Decode legacy, directed, initiating and extended advertising separately."""
    from sigsense_ml.ble_protocol import (
        parse_ad_structures,
        parse_connect_ind,
        parse_extended_payload,
    )

    out = {
        "adv_address": None,
        "ad_structures": [],
        "summary": None,
        "led_remote": False,
        "protocol_fields": {},
        "warnings": [],
    }
    if pdu_type == 5:
        context = parse_connect_ind(payload, header0)
        out.update(
            adv_address=context["advertiser_address"],
            protocol_fields={"connection": context},
            summary=f"CONNECT {context['initiator_address']} → {context['advertiser_address']} · {context['access_address']}",
        )
        if not context["valid_parameters"]:
            out["warnings"].append(
                "Invalid connection parameters; connection will not be tracked"
            )
        return out
    if pdu_type in (1, 3):
        if len(payload) != 12:
            raise ValueError("Directed advertising/scan request requires 12 bytes")
        first, second = _mac_le(payload[:6]), _mac_le(payload[6:12])
        out["adv_address"] = second if pdu_type == 3 else first
        out["protocol_fields"] = {
            "scanner_address" if pdu_type == 3 else "target_address": (
                first if pdu_type == 3 else second
            )
        }
        out["summary"] = f"{first} → {second}"
        return out
    if pdu_type in (7, 8):
        extended = parse_extended_payload(payload)
        out["protocol_fields"] = {"extended": extended}
        out["adv_address"] = extended.get("advertiser_address")
        adv_data = bytes.fromhex(extended["adv_data_hex"])
    elif pdu_type in (0, 2, 4, 6) and len(payload) >= 6:
        out["adv_address"] = _mac_le(payload[:6])
        adv_data = payload[6:]
    else:
        out["summary"] = payload.hex()
        return out
    ads, warnings = parse_ad_structures(adv_data)
    out["ad_structures"], out["warnings"] = ads, warnings
    out["led_remote"] = is_led_remote_advdata(adv_data)
    if out["led_remote"]:
        cmd = f" AdvData[12]=0x{adv_data[12]:02x}" if len(adv_data) > 12 else ""
        out["summary"] = f"{out['adv_address']} · LED AdvData {adv_data.hex()}{cmd}"
    else:
        summary = "; ".join(f"{a['label']}: {a['detail']}" for a in ads) or "no AD"
        out["summary"] = f"{out['adv_address'] or 'Extended advertisement'} · {summary}"
    return out


def dedupe_packets(packets: list[BlePacket]) -> list[BlePacket]:
    """Remove alternate demodulation hypotheses, preserving separate RF events."""
    out = []
    for packet in sorted(
        packets,
        key=lambda p: (
            p.sample_offset if p.sample_offset is not None else p.offset_bits
        ),
    ):
        position = (
            packet.sample_offset
            if packet.sample_offset is not None
            else packet.offset_bits
        )
        if any(
            p.access_address == packet.access_address
            and p.channel == packet.channel
            and p.phy == packet.phy
            and p.header_hex == packet.header_hex
            and p.payload_hex == packet.payload_hex
            and (
                (
                    p.timestamp_seconds is not None
                    and packet.timestamp_seconds is not None
                    and abs(p.timestamp_seconds - packet.timestamp_seconds) < 2e-6
                )
                or (
                    p.timestamp_seconds is None
                    and packet.timestamp_seconds is None
                    and abs(p.offset_bits - packet.offset_bits) <= 1
                )
            )
            for p in out[-8:]
        ):
            continue
        out.append(packet)
    return out


ADV_PDU_TYPES = {
    0x0: "ADV_IND",
    0x1: "ADV_DIRECT_IND",
    0x2: "ADV_NONCONN_IND",
    0x3: "SCAN_REQ",
    0x4: "SCAN_RSP",
    0x5: "CONNECT_IND",
    0x6: "ADV_SCAN_IND",
    0x7: "ADV_EXT_IND",
    0x8: "AUX_CONNECT_RSP",
}


def channel_from_freq_hz(freq_hz: float) -> int | None:
    mhz = freq_hz / 1e6
    best = None
    best_err = 1e9
    for ch, f in BLE_CHANNELS_MHZ.items():
        err = abs(f - mhz)
        if err < best_err:
            best_err = err
            best = ch
    return best if best_err < 1.1 else None


def _whiten_bits(bits: np.ndarray, channel: int) -> np.ndarray:
    """BLE data whitening LFSR (x^7 + x^4 + 1); seed = channel | 0x40."""
    lfsr = 0x40 | (int(channel) & 0x3F)
    out = np.empty(len(bits), dtype=np.uint8)
    for i, b in enumerate(bits.astype(np.uint8)):
        out[i] = int(b) ^ (lfsr & 1)
        feedback = lfsr & 1
        lfsr >>= 1
        if feedback:
            lfsr ^= 0x44
    return out


def crc_init_reorder(crc_init: int = CRC_INIT_ADVERTISING_INPUT) -> int:
    """Convert protocol CRCInit to the reflected 24-bit CRC register order.

    Reversing each octet alone happens to work for advertising's 0x555555,
    but fails for the arbitrary CRCInit used by connected packets.
    """
    return int(f"{int(crc_init) & 0xffffff:024b}"[::-1], 2)


def _crc24_bytes(data: bytes, init: int = CRC_INIT_ADVERTISING) -> int:
    """BLE CRC-24 (LSB-first) over PDU bytes; poly 0x00065B reflected.

    Default init is 0xAAAAAA for advertising (BTLE/JiaoXianjun convention).
    """
    crc = int(init) & 0xFFFFFF
    for byte in data:
        for bit_i in range(8):
            bit = (byte >> bit_i) & 1
            crc_lsb = crc & 1
            crc >>= 1
            if crc_lsb ^ bit:
                crc ^= 0xDA6000  # reflected 0x00065B
    return crc & 0xFFFFFF


def _crc24(bits: np.ndarray, init: int = CRC_INIT_ADVERTISING) -> int:
    return _crc24_bytes(bits_to_bytes(bits), init=init)


def _crc24_check(
    pdu_bits: np.ndarray,
    crc_bits: np.ndarray,
    init: int = CRC_INIT_ADVERTISING,
) -> bool:
    """Return True on CRC pass (matches btle_rx crc_flag==0 / CRC0)."""
    expected = _crc24(pdu_bits, init=init)
    got = int.from_bytes(bits_to_bytes(crc_bits[:24])[:3], "little")
    return got == expected


def decode_unwhitened_pdu(
    pdu_crc: bytes,
    *,
    channel: int | None = None,
    access_address: int = ADV_ACCESS_ADDRESS,
    crc_init: int | None = None,
) -> BlePacket | None:
    """Parse dewhitened LL PDU+CRC; data channels require their actual CRCInit."""
    if channel is not None and not 0 <= channel <= 39:
        raise ValueError("BLE channel must be 0..39")
    if len(pdu_crc) < 5:
        return None
    advertising = access_address == ADV_ACCESS_ADDRESS
    if not advertising and crc_init is None:
        raise ValueError("Data PDU decoding requires the connection CRCInit")
    header_size = 2 if advertising else 2 + bool(pdu_crc[0] & 0x20)
    length = pdu_crc[1]
    if advertising:
        kind = pdu_crc[0] & 15
        bounds = {
            0: (6, 37),
            1: (12, 12),
            2: (6, 37),
            3: (12, 12),
            4: (6, 37),
            5: (34, 34),
            6: (6, 37),
            7: (1, 255),
            8: (1, 255),
        }
        if kind not in bounds or not bounds[kind][0] <= length <= bounds[kind][1]:
            return None
    else:
        kind = pdu_crc[0] & 3
        if kind == 0:
            return None
    total = header_size + length
    if len(pdu_crc) < total + 3:
        return None
    body, crc_bytes = pdu_crc[:total], pdu_crc[total : total + 3]
    raw_init = CRC_INIT_ADVERTISING_INPUT if crc_init is None else crc_init
    ok = _crc24_bytes(body, crc_init_reorder(raw_init)) == int.from_bytes(
        crc_bytes, "little"
    )
    payload = body[header_size:]
    if advertising:
        try:
            fields = parse_adv_payload(kind, payload, body[0])
        except ValueError:
            return None
        name = ADV_PDU_TYPES[kind]
    else:
        fields = {
            "adv_address": None,
            "ad_structures": [],
            "led_remote": False,
            "warnings": [],
            "protocol_fields": {
                "llid": kind,
                "nesn": (body[0] >> 2) & 1,
                "sn": (body[0] >> 3) & 1,
                "md": bool(body[0] & 16),
                "cp": bool(body[0] & 32),
            },
            "summary": f"{length} payload bytes · encryption state unknown",
        }
        if header_size == 3:
            fields["protocol_fields"]["cte_info"] = body[2]
        name = "LL_CONTROL" if kind == 3 else "LL_DATA" if payload else "LL_EMPTY"
    return BlePacket(
        access_address=f"0x{access_address:08X}",
        channel=channel,
        pdu_type=kind,
        pdu_type_name=name,
        length=length,
        header_hex=body[:header_size].hex(),
        payload_hex=payload.hex(),
        crc_hex=crc_bytes.hex(),
        crc_ok=ok,
        rssi_dbfs=None,
        offset_bits=0,
        dewhitened=True,
        adv_address=fields["adv_address"],
        ad_structures=fields["ad_structures"],
        summary=fields["summary"],
        led_remote=fields["led_remote"],
        packet_kind="advertising" if advertising else "data",
        protocol_fields=fields.get("protocol_fields", {}),
        warnings=fields.get("warnings", []),
    )


def decode_whitened_pdu(
    whitened_pdu_crc: bytes,
    *,
    channel: int,
    access_address: int = ADV_ACCESS_ADDRESS,
    crc_init: int | None = None,
) -> BlePacket | None:
    if not 0 <= channel <= 39:
        raise ValueError("BLE channel must be 0..39")
    bits = np.unpackbits(
        np.frombuffer(whitened_pdu_crc, dtype=np.uint8), bitorder="little"
    )
    plain = bits_to_bytes(_whiten_bits(bits, channel))[: len(whitened_pdu_crc)]
    return decode_unwhitened_pdu(
        plain, channel=channel, access_address=access_address, crc_init=crc_init
    )


def bits_to_bytes(bits: np.ndarray) -> bytes:
    bits = bits.astype(np.uint8)
    pad = (-len(bits)) % 8
    if pad:
        bits = np.concatenate([bits, np.zeros(pad, dtype=np.uint8)])
    return np.packbits(bits, bitorder="little").tobytes()


def encode_adv_pdu_crc(*, header_hex: str, payload_hex: str) -> bytes:
    """Build unwhitened PDU+CRC from captured header/payload hex (CRC recomputed)."""
    header = bytes.fromhex(header_hex.replace(" ", ""))
    payload = bytes.fromhex(payload_hex.replace(" ", ""))
    if len(header) < 1:
        raise ValueError("header_hex must contain at least the PDU type byte")
    if len(payload) > 37:
        raise ValueError("BLE advertising payload max is 37 bytes")
    pdu = bytes([header[0], len(payload) & 0xFF]) + payload
    crc_val = _crc24_bytes(pdu, init=CRC_INIT_ADVERTISING)
    crc_bytes = bytes([crc_val & 0xFF, (crc_val >> 8) & 0xFF, (crc_val >> 16) & 0xFF])
    return pdu + crc_bytes


def _gfsk_modulate_bits(
    bits: np.ndarray,
    *,
    sample_rate: float,
    amplitude: float = 0.9,
) -> np.ndarray:
    """GFSK modulate bit stream → complex baseband (no noise)."""
    sps = max(2, int(round(sample_rate / 1e6)))
    nrz = bits.astype(np.float64) * 2.0 - 1.0
    up = np.repeat(nrz, sps)
    bt = 0.5
    span = 4
    t = np.arange(-span * sps, span * sps + 1) / sps
    gauss = np.exp(-2 * (np.pi * bt * t) ** 2 / (np.log(2) * 2))
    gauss /= gauss.sum()
    filtered = np.convolve(up, gauss, mode="same")
    h = 0.5
    phase = np.cumsum(np.pi * h * filtered / sps)
    iq = np.exp(1j * phase).astype(np.complex64)
    return (iq * float(amplitude)).astype(np.complex64)


def packet_bits_for_tx(
    *,
    header_hex: str,
    payload_hex: str,
    channel: int,
) -> np.ndarray:
    """Preamble + AA + whitened PDU/CRC bits for BLE 1M TX."""
    pdu_crc = encode_adv_pdu_crc(header_hex=header_hex, payload_hex=payload_hex)
    pdu_bits = np.unpackbits(np.frombuffer(pdu_crc, dtype=np.uint8), bitorder="little")
    whitened = _whiten_bits(pdu_bits, channel)
    # BLE 1M: preamble 0xAA when AA LSB is 0 → alternating 0,1,…
    preamble = np.tile([0, 1], 4).astype(np.uint8)
    return np.concatenate([preamble, ADV_AA_BITS, whitened])


def modulate_ble_packet_iq(
    *,
    header_hex: str,
    payload_hex: str,
    channel: int,
    sample_rate: float = 4e6,
    repeats: int = 3,
    gap_us: float = 300.0,
    pad_us: float = 200.0,
    amplitude: float = 0.9,
) -> np.ndarray:
    """Synthesize clean BLE 1M IQ for HackRF TX replay (no AWGN)."""
    bits = packet_bits_for_tx(
        header_hex=header_hex, payload_hex=payload_hex, channel=channel
    )
    packet = _gfsk_modulate_bits(bits, sample_rate=sample_rate, amplitude=amplitude)
    gap_n = max(0, int(round(sample_rate * gap_us * 1e-6)))
    pad_n = max(0, int(round(sample_rate * pad_us * 1e-6)))
    gap = np.zeros(gap_n, dtype=np.complex64)
    pad = np.zeros(pad_n, dtype=np.complex64)
    reps = max(1, int(repeats))
    chunks: list[np.ndarray] = [pad]
    for i in range(reps):
        chunks.append(packet)
        if i + 1 < reps:
            chunks.append(gap)
    chunks.append(pad)
    return np.concatenate(chunks).astype(np.complex64)


def generate_ble_adv_iq(
    *,
    sample_rate: float = 4e6,
    channel: int = 37,
    payload: bytes | None = None,
    snr_db: float = 20.0,
    seed: int | None = None,
    n_pad: int = 2000,
) -> np.ndarray:
    """Synthesize a single ADV_IND-like BLE 1M packet as complex baseband IQ."""
    rng = np.random.default_rng(seed)
    if payload is None:
        # AdvA (6) + flags AD structure
        adv_a = bytes(rng.integers(0, 256, 6, dtype=np.uint8))
        payload = adv_a + bytes([0x02, 0x01, 0x06])

    # PDU header: ADV_IND (0), ChSel/TxAdd/RxAdd=0, length
    length = len(payload)
    header0 = 0x00  # ADV_IND
    header_hex = bytes([header0, length & 0xFF]).hex()
    iq = modulate_ble_packet_iq(
        header_hex=header_hex,
        payload_hex=payload.hex(),
        channel=channel,
        sample_rate=sample_rate,
        repeats=1,
        gap_us=0,
        pad_us=0,
        amplitude=1.0,
    )

    # Pad with noise floor
    noise = (rng.normal(0, 0.05, n_pad) + 1j * rng.normal(0, 0.05, n_pad)).astype(
        np.complex64
    )
    packet = np.concatenate([noise, iq, noise])

    # AWGN
    power = np.mean(np.abs(packet) ** 2) + 1e-12
    noise_power = power / (10 ** (snr_db / 10.0))
    awgn = rng.normal(0, np.sqrt(noise_power / 2), packet.shape) + 1j * rng.normal(
        0, np.sqrt(noise_power / 2), packet.shape
    )
    return (packet + awgn).astype(np.complex64)


def _fm_soft(iq: np.ndarray) -> np.ndarray:
    return np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float64)


def _soft_filtered(
    iq: np.ndarray, sample_rate: float, symbol_rate: float = 1e6
) -> tuple[np.ndarray, int]:
    """FM discriminator + light LPF; returns (soft, sps)."""
    # Mild channelization — too tight smears GFSK zero-crossings
    if sample_rate > 2.5 * symbol_rate:
        cutoff = min(0.45, 1.1 * symbol_rate / sample_rate)
        b, a = sp_signal.butter(2, cutoff, btype="low")
        iq = sp_signal.filtfilt(b, a, iq).astype(np.complex64)
    soft = _fm_soft(iq)
    sps = max(2, int(round(sample_rate / symbol_rate)))
    b2, a2 = sp_signal.butter(2, min(0.45, 0.85 / sps))
    soft = sp_signal.filtfilt(b2, a2, soft)
    return soft, sps


def demod_gfsk_bits(
    iq: np.ndarray,
    *,
    sample_rate: float,
    symbol_rate: float = 1e6,
) -> np.ndarray:
    """Frequency-discriminate GFSK IQ → hard bits (best AA-correlated phase)."""
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if iq.size < 16:
        return np.zeros(0, dtype=np.uint8)
    from fractions import Fraction

    if symbol_rate not in (1e6, 2e6) or sample_rate < 2 * symbol_rate:
        raise ValueError(
            "GFSK demodulation needs LE1M/LE2M and at least two samples/symbol"
        )
    ratio = Fraction(4 * symbol_rate / sample_rate).limit_denominator(4096)
    iq = sp_signal.resample_poly(iq, ratio.numerator, ratio.denominator)
    soft, sps = _soft_filtered(iq, 4 * symbol_rate, symbol_rate)
    aa = ADV_AA_BITS.astype(np.float64) * 2.0 - 1.0
    best_bits = np.zeros(0, dtype=np.uint8)
    best_score = -1e9
    for phase in range(sps):
        samples = soft[phase::sps]
        if samples.size < 48:
            continue
        for pol in (1.0, -1.0):
            corr = np.correlate(samples * pol, aa, mode="valid")
            score = float(np.max(corr)) if corr.size else -1e9
            if score > best_score:
                best_score = score
                best_bits = (samples * pol > 0).astype(np.uint8)
    return best_bits


def _find_aa_offsets(
    bits: np.ndarray, aa_bits: np.ndarray, max_errors: int = 0
) -> list[int]:
    n = len(aa_bits)
    if len(bits) < n + 16:
        return []
    hits: list[int] = []
    aa = aa_bits.astype(np.uint8)
    for i in range(len(bits) - n + 1):
        errs = int(np.sum(bits[i : i + n] != aa))
        if errs > max_errors:
            continue
        # Prefer positions with alternating preamble (BLE 1M)
        if i >= 8:
            pre = bits[i - 8 : i]
            alt = int(np.sum(pre[1:] != pre[:-1]))
            if alt < 5 and max_errors == 0:
                continue
        hits.append(i)
    pruned: list[int] = []
    for h in hits:
        if not pruned or h - pruned[-1] > n // 2:
            pruned.append(h)
    return pruned


def _parse_from_bits(
    bits: np.ndarray,
    aa_offset: int,
    *,
    channel: int,
    access_address: int = ADV_ACCESS_ADDRESS,
    crc_init: int | None = None,
) -> BlePacket | None:
    start = aa_offset + 32
    if start + 40 > len(bits):
        return None
    for dewhitened, stream in (
        (True, _whiten_bits(bits[start : start + 2088], channel)),
        (False, bits[start : start + 2088]),
    ):
        header = bits_to_bytes(stream[:16])
        header_size = 2 + (
            access_address != ADV_ACCESS_ADDRESS and bool(header[0] & 32)
        )
        required = (header_size + header[1] + 3) * 8
        if len(stream) < required:
            continue
        packet = decode_unwhitened_pdu(
            bits_to_bytes(stream[:required]),
            channel=channel,
            access_address=access_address,
            crc_init=crc_init,
        )
        if packet is not None and packet.crc_ok:
            packet.offset_bits, packet.dewhitened = aa_offset, dewhitened
            return packet
    return None


def frame_ble_packets(
    bits: np.ndarray,
    *,
    channel: int | None = 37,
    access_address: int = ADV_ACCESS_ADDRESS,
    max_packets: int = 32,
    max_errors: int = 1,
    crc_init: int | None = None,
) -> list[BlePacket]:
    """Search bit stream for AA (+ preamble) and parse PDU + CRC."""
    ch = 37 if channel is None else channel
    aa_bits = np.unpackbits(
        np.array(
            [
                access_address & 0xFF,
                (access_address >> 8) & 0xFF,
                (access_address >> 16) & 0xFF,
                (access_address >> 24) & 0xFF,
            ],
            dtype=np.uint8,
        ),
        bitorder="little",
    )
    packets: list[BlePacket] = []
    for bits_try in (bits, (1 - bits).astype(np.uint8)):
        offsets = _find_aa_offsets(bits_try, aa_bits, max_errors=max_errors)
        for off in offsets[:max_packets]:
            parsed = _parse_from_bits(
                bits_try,
                off,
                channel=ch,
                access_address=access_address,
                crc_init=crc_init,
            )
            if parsed is not None:
                packets.append(parsed)
    packets = dedupe_packets(packets)
    return [p for p in packets if p.crc_ok][:max_packets]


def _decode_soft_packets(
    iq: np.ndarray,
    *,
    sample_rate: float,
    symbol_rate: float,
    channel: int,
    access_address: int,
    crc_init: int | None,
    max_packets: int,
) -> list[BlePacket]:
    """Fractional-rate resampling; normalized AA matching; per-packet CFO fit."""
    from fractions import Fraction

    sps = 4
    target_rate = symbol_rate * sps
    ratio = Fraction(target_rate / sample_rate).limit_denominator(4096)
    samples = (
        sp_signal.resample_poly(iq, ratio.numerator, ratio.denominator)
        if ratio.numerator != ratio.denominator
        else iq
    )
    if len(samples) < 64:
        return []
    sos = sp_signal.butter(3, 0.9 * symbol_rate, fs=target_rate, output="sos")
    samples = sp_signal.sosfiltfilt(sos, samples)
    soft = np.convolve(_fm_soft(samples), np.ones(sps) / sps, mode="same")
    reference = (
        np.unpackbits(
            np.frombuffer(access_address.to_bytes(4, "little"), dtype=np.uint8),
            bitorder="little",
        ).astype(float)
        * 2
        - 1
    )
    reference_centered = reference - reference.mean()
    reference_power = np.dot(reference_centered, reference_centered)
    found = []
    for phase in range(sps):
        symbols = soft[phase::sps]
        if len(symbols) < 72:
            continue
        sums = np.convolve(symbols, np.ones(32), mode="valid")
        powers = (
            np.convolve(symbols * symbols, np.ones(32), mode="valid") - sums * sums / 32
        )
        correlation = np.correlate(symbols, reference_centered, mode="valid")
        scores = np.abs(correlation) / np.sqrt(
            np.maximum(powers, 1e-18) * reference_power
        )
        peaks = np.flatnonzero(
            (scores >= 0.72)
            & (scores >= np.r_[0, scores[:-1]])
            & (scores >= np.r_[scores[1:], 0])
        )
        peaks = peaks[np.argsort(-scores[peaks])[: max(64, max_packets * 4)]]
        for offset in peaks:
            window = symbols[offset : offset + 32]
            slope = correlation[offset] / reference_power
            bias = window.mean() - slope * reference.mean()
            polarity = 1 if slope >= 0 else -1
            bits = (
                (symbols[offset : offset + 2400] * 1.0 - bias) * polarity > 0
            ).astype(np.uint8)
            errors = int(np.count_nonzero(bits[:32] != (reference > 0)))
            if errors > 2:
                continue
            packet = _parse_from_bits(
                bits,
                0,
                channel=channel,
                access_address=access_address,
                crc_init=crc_init,
            )
            if packet is None:
                continue
            # Resampling ratio is rational; use its actual rate for timestamping.
            actual_rate = sample_rate * ratio.numerator / ratio.denominator
            packet.sample_offset = round(
                (phase + int(offset) * sps) * sample_rate / actual_rate
            )
            packet.timestamp_seconds = packet.sample_offset / sample_rate
            packet.offset_bits = round(packet.timestamp_seconds * symbol_rate)
            packet.phy = "LE2M" if symbol_rate == 2e6 else "LE1M"
            packet.cfo_hz = float(bias * actual_rate / (2 * np.pi))
            packet.aa_errors = errors
            end = min(
                len(iq),
                packet.sample_offset
                + round(
                    (
                        32
                        + (len(bytes.fromhex(packet.header_hex)) + packet.length + 3)
                        * 8
                    )
                    * sample_rate
                    / symbol_rate
                ),
            )
            power = (
                np.mean(np.abs(iq[packet.sample_offset : end]) ** 2)
                if end > packet.sample_offset
                else 0
            )
            packet.rssi_dbfs = float(10 * np.log10(power + 1e-18))
            found.append(packet)
    return dedupe_packets(found)


def decode_ble_iq(
    iq: np.ndarray,
    *,
    sample_rate: float,
    center_freq_hz: float | None = None,
    channel: int | None = None,
    phy: str = "auto",
    connections: list[dict] | None = None,
    max_packets: int = 256,
) -> dict:
    """Passively frame captured LE 1M/2M advertising and known-connection PDUs.

    This function decodes samples already captured on one channel. It does not
    follow RF channel hops, decode LE Coded IQ, or decrypt encrypted payloads.
    """
    if not np.isfinite(sample_rate) or sample_rate <= 0:
        raise ValueError("sample_rate must be finite and positive")
    if phy not in {"auto", "LE1M", "LE2M"}:
        raise ValueError(
            "IQ PHY must be auto, LE1M or LE2M; import sniffer PCAP for LE Coded"
        )
    if not 1 <= max_packets <= 4096:
        raise ValueError("max_packets must be 1..4096")
    if center_freq_hz is not None and not np.isfinite(center_freq_hz):
        raise ValueError("center_freq_hz must be finite")
    warnings = []
    if channel is None:
        channel = (
            channel_from_freq_hz(center_freq_hz) if center_freq_hz is not None else 37
        )
        if center_freq_hz is None:
            warnings.append(
                "No channel/frequency provided; assuming advertising channel 37"
            )
    if channel is None or not 0 <= channel <= 39:
        raise ValueError(
            "Specify a BLE channel 0..39 or a matching BLE center frequency"
        )
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if not np.isfinite(iq).all():
        raise ValueError("IQ contains non-finite samples")
    digital_offset = (
        BLE_CHANNELS_MHZ[channel] * 1e6 - center_freq_hz
        if center_freq_hz is not None
        else 0
    )
    if abs(digital_offset) + 0.6e6 > sample_rate / 2:
        raise ValueError("Requested BLE channel falls outside the captured bandwidth")
    if digital_offset:
        iq = (
            iq * np.exp(-2j * np.pi * digital_offset * np.arange(len(iq)) / sample_rate)
        ).astype(np.complex64)
    rates = (
        [1e6, 2e6]
        if phy == "auto" and channel < 37
        else [2e6 if phy == "LE2M" else 1e6]
    )
    rates = [rate for rate in rates if sample_rate >= 2 * rate]
    if not rates:
        raise ValueError(
            "Capture rate must be at least twice the selected PHY symbol rate"
        )
    contexts = {}
    for context in connections or []:
        aa = (
            int(context["access_address"], 0)
            if isinstance(context["access_address"], str)
            else int(context["access_address"])
        )
        init = (
            int(context["crc_init"], 0)
            if isinstance(context["crc_init"], str)
            else int(context["crc_init"])
        )
        if (
            not 0 < aa < 0xFFFFFFFF
            or aa == ADV_ACCESS_ADDRESS
            or not 0 <= init <= 0xFFFFFF
        ):
            raise ValueError("Invalid connection access address or CRCInit")
        contexts[aa] = {**context, "access_address": f"0x{aa:08X}", "crc_init": init}
    if len(contexts) > 32:
        raise ValueError("At most 32 connection contexts may be supplied")
    packets = []
    for rate in rates:
        packets.extend(
            _decode_soft_packets(
                iq,
                sample_rate=sample_rate,
                symbol_rate=rate,
                channel=channel,
                access_address=ADV_ACCESS_ADDRESS,
                crc_init=None,
                max_packets=max_packets,
            )
        )
    for packet in packets:
        context = packet.protocol_fields.get("connection")
        if packet.crc_ok and context and context["valid_parameters"]:
            aa = int(context["access_address"], 0)
            contexts.pop(aa, None)
            if len(contexts) >= 32:
                contexts.pop(next(iter(contexts)))
            contexts[aa] = context
    if channel < 37:
        for aa, context in contexts.items():
            for rate in rates:
                packets.extend(
                    _decode_soft_packets(
                        iq,
                        sample_rate=sample_rate,
                        symbol_rate=rate,
                        channel=channel,
                        access_address=aa,
                        crc_init=context["crc_init"],
                        max_packets=max_packets,
                    )
                )
    packets = dedupe_packets(packets)
    truncated = len(packets) > max_packets
    packets = packets[:max_packets]
    capture_id = uuid4().hex
    for packet in packets:
        packet.capture_id = capture_id
    phys = sorted({p.phy for p in packets})
    return {
        "phy": phys[0] if len(phys) == 1 else "auto" if not phys else "mixed",
        "sample_rate_hz": sample_rate,
        "center_freq_hz": center_freq_hz,
        "channel": channel,
        "bit_count": int(len(iq) / sample_rate * rates[0]),
        "packet_count": len(packets),
        "packets": [p.to_dict() for p in packets],
        "connections": list(contexts.values()),
        "warnings": warnings,
        "truncated": truncated,
        "clock": "sample-relative",
        "scope": "Captured-channel LE1M/LE2M; no automatic RF connection following or encrypted payload decryption",
    }
