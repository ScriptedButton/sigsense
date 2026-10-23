"""Bluetooth LE 1M PHY helpers: GFSK IQ synthesis, demod, and PDU framing.

Supports advertising access address (0x8E89BED6) and optional channel whitening.
Intended for HackRF / wideband IQ captures around 2.4 GHz.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

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

    def to_dict(self) -> dict:
        return asdict(self)


def _mac_le(raw6: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw6[::-1])


def is_led_remote_advdata(adv_data: bytes) -> bool:
    """Known LED-remote AdvData: len=0x0d type=0xff then 55aa… or 4c00… custom blob."""
    if len(adv_data) < 4 or adv_data[0] != 0x0D or adv_data[1] != 0xFF:
        return False
    return adv_data[2:4] in (b"\x55\xaa", b"\x4c\x00")


def parse_adv_payload(pdu_type: int, payload: bytes) -> dict:
    """Parse ADV_*/SCAN_* payload: addresses + AD structures when present."""
    out: dict = {
        "adv_address": None,
        "ad_structures": [],
        "summary": None,
        "led_remote": False,
    }

    # SCAN_REQ: ScanA(6) + AdvA(6)
    if pdu_type == 0x3 and len(payload) >= 12:
        scan_a = _mac_le(payload[:6])
        adv_a = _mac_le(payload[6:12])
        out["adv_address"] = adv_a
        out["summary"] = f"ScanA {scan_a} → AdvA {adv_a}"
        return out

    if pdu_type not in (0x0, 0x1, 0x2, 0x4, 0x6, 0x7) or len(payload) < 6:
        out["summary"] = payload.hex() if payload else "(empty)"
        return out

    # AdvA is little-endian on air
    adv_address = _mac_le(payload[:6])
    out["adv_address"] = adv_address
    adv_data = payload[6:]
    out["led_remote"] = is_led_remote_advdata(adv_data)

    ads: list[dict] = []
    i = 6
    while i < len(payload):
        length = payload[i]
        i += 1
        if length == 0 or i + length - 1 > len(payload):
            break
        ad_type = payload[i]
        data = payload[i + 1 : i + length]
        i += length
        label = {
            0x01: "Flags",
            0x02: "Incomplete UUID16",
            0x03: "Complete UUID16",
            0x08: "Shortened Local Name",
            0x09: "Complete Local Name",
            0x0A: "Tx Power",
            0xFF: "Manufacturer Specific",
        }.get(ad_type, f"AD 0x{ad_type:02X}")
        detail = data.hex()
        if ad_type == 0x01 and data:
            flags = data[0]
            parts = []
            if flags & 0x01:
                parts.append("LE Limited Disc")
            if flags & 0x02:
                parts.append("LE General Disc")
            if flags & 0x04:
                parts.append("BR/EDR Not Supported")
            detail = f"0x{flags:02X} ({', '.join(parts) or 'none'})"
        elif ad_type == 0xFF:
            detail = data.hex()
            # Apple Continuity company ID is little-endian 0x004C → bytes 4c 00
            if data[:2] == b"\x4c\x00" and not out["led_remote"]:
                detail = f"{data.hex()} (Apple Continuity-like)"
            elif out["led_remote"] and len(adv_data) > 12:
                detail = f"{data.hex()} (AdvData[12]=0x{adv_data[12]:02x})"
        elif ad_type in (0x08, 0x09):
            try:
                detail = data.decode("utf-8", errors="replace")
            except Exception:
                detail = data.hex()
        ads.append({"type": ad_type, "label": label, "data_hex": data.hex(), "detail": detail})

    out["ad_structures"] = ads
    if out["led_remote"]:
        cmd = f" AdvData[12]=0x{adv_data[12]:02x}" if len(adv_data) > 12 else ""
        out["summary"] = f"{adv_address} · LED AdvData {adv_data.hex()}{cmd}"
    elif len(payload) > 6 and ads and ads[0]["type"] == 0xFF and len(ads) == 1:
        out["summary"] = f"{adv_address} · AdvData {adv_data.hex()}"
    else:
        ad_summary = "; ".join(f"{a['label']}: {a['detail']}" for a in ads) or "no AD"
        out["summary"] = f"{adv_address} · {ad_summary}"
    return out


def dedupe_packets(packets: list[BlePacket]) -> list[BlePacket]:
    """Keep unique PDU bodies (ignore offset duplicates from ring/overlap)."""
    seen: set[str] = set()
    out: list[BlePacket] = []
    for p in packets:
        key = f"{p.header_hex}:{p.payload_hex}:{p.crc_ok}"
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
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
    """Match btle_rx.c crc_init_reorder: 0x555555 → 0xAAAAAA."""
    crc_init_input = 0
    tmp = int(crc_init) & 0xFFFFFF
    for _ in range(3):
        crc_init_input = ((crc_init_input << 8) | (tmp & 0xFF)) & 0xFFFFFF
        tmp >>= 8
    crc_init_input = (crc_init_input << 1) & 0x1FFFFFF
    out = 0
    for _ in range(24):
        crc_init_input >>= 1
        out = ((out << 1) | (crc_init_input & 1)) & 0xFFFFFF
    return out


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
) -> BlePacket | None:
    """Parse an unwhitened PDU+CRC fixture (header..payload..crc)."""
    if len(pdu_crc) < 2 + 3:
        return None
    header = pdu_crc[:2]
    pdu_type = header[0] & 0x0F
    length = header[1] & 0xFF
    if length > 37:
        return None
    total = 2 + length
    if len(pdu_crc) < total + 3:
        return None
    body = pdu_crc[:total]
    crc_bytes = pdu_crc[total : total + 3]
    ok = _crc24_bytes(body) == int.from_bytes(crc_bytes, "little")
    payload = body[2:]
    fields = parse_adv_payload(pdu_type, payload)
    return BlePacket(
        access_address=f"0x{ADV_ACCESS_ADDRESS:08X}",
        channel=channel,
        pdu_type=pdu_type,
        pdu_type_name=ADV_PDU_TYPES.get(pdu_type, f"TYPE_{pdu_type}"),
        length=length,
        header_hex=header.hex(),
        payload_hex=payload.hex(),
        crc_hex=crc_bytes.hex(),
        crc_ok=ok,
        rssi_dbfs=None,
        offset_bits=0,
        dewhitened=True,
        adv_address=fields["adv_address"],
        ad_structures=fields["ad_structures"],
        summary=fields["summary"],
        led_remote=bool(fields.get("led_remote")),
    )


def decode_whitened_pdu(
    whitened_pdu_crc: bytes,
    *,
    channel: int,
) -> BlePacket | None:
    """Dewhiten then parse PDU+CRC (no AA/preamble)."""
    bits = np.unpackbits(
        np.frombuffer(whitened_pdu_crc, dtype=np.uint8), bitorder="little"
    )
    plain = bits_to_bytes(_whiten_bits(bits, channel))[: len(whitened_pdu_crc)]
    return decode_unwhitened_pdu(plain, channel=channel)


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
    crc_bytes = bytes(
        [crc_val & 0xFF, (crc_val >> 8) & 0xFF, (crc_val >> 16) & 0xFF]
    )
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
    pdu_bits = np.unpackbits(
        np.frombuffer(pdu_crc, dtype=np.uint8), bitorder="little"
    )
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
    noise = (
        rng.normal(0, 0.05, n_pad) + 1j * rng.normal(0, 0.05, n_pad)
    ).astype(np.complex64)
    packet = np.concatenate([noise, iq, noise])

    # AWGN
    power = np.mean(np.abs(packet) ** 2) + 1e-12
    noise_power = power / (10 ** (snr_db / 10.0))
    awgn = (
        rng.normal(0, np.sqrt(noise_power / 2), packet.shape)
        + 1j * rng.normal(0, np.sqrt(noise_power / 2), packet.shape)
    )
    return (packet + awgn).astype(np.complex64)


def _channel_filter(iq: np.ndarray, sample_rate: float) -> np.ndarray:
    """Band-limit to ~±700 kHz for BLE 1M."""
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if iq.size < 64:
        return iq
    cutoff = min(0.45, 700e3 / sample_rate)
    b, a = sp_signal.butter(3, cutoff, btype="low")
    return sp_signal.filtfilt(b, a, iq).astype(np.complex64)


def _fm_soft(iq: np.ndarray) -> np.ndarray:
    return np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float64)


def _soft_filtered(iq: np.ndarray, sample_rate: float) -> tuple[np.ndarray, int]:
    """FM discriminator + light LPF; returns (soft, sps)."""
    # Mild channelization — too tight smears GFSK zero-crossings
    if sample_rate > 2.5e6:
        cutoff = min(0.45, 1.1e6 / sample_rate)
        b, a = sp_signal.butter(2, cutoff, btype="low")
        iq = sp_signal.filtfilt(b, a, iq).astype(np.complex64)
    soft = _fm_soft(iq)
    sps = max(2, int(round(sample_rate / 1e6)))
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
    soft, sps = _soft_filtered(iq, sample_rate)
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


def _find_aa_offsets(bits: np.ndarray, aa_bits: np.ndarray, max_errors: int = 0) -> list[int]:
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
) -> BlePacket | None:
    start = aa_offset + 32
    if start + 40 > len(bits):
        return None
    for dewhitened, stream in (
        (True, _whiten_bits(bits[start:], channel)),
        (False, bits[start:]),
    ):
        header = bits_to_bytes(stream[:16])
        pdu_type = header[0] & 0x0F
        length = header[1] & 0xFF  # advertising Length is 8-bit (0..37)
        if length < 6 or length > 37:
            continue
        total = 16 + length * 8
        if len(stream) < total + 24:
            continue
        pdu_bits = stream[:total]
        crc_bits = stream[total : total + 24]
        if not _crc24_check(pdu_bits, crc_bits):
            continue
        payload = bits_to_bytes(stream[16:total])
        fields = parse_adv_payload(pdu_type, payload)
        return BlePacket(
            access_address=f"0x{access_address:08X}",
            channel=channel,
            pdu_type=pdu_type,
            pdu_type_name=ADV_PDU_TYPES.get(pdu_type, f"TYPE_{pdu_type}"),
            length=length,
            header_hex=header.hex(),
            payload_hex=payload.hex(),
            crc_hex=bits_to_bytes(crc_bits)[:3].hex(),
            crc_ok=True,
            rssi_dbfs=None,
            offset_bits=aa_offset,
            dewhitened=dewhitened,
            adv_address=fields["adv_address"],
            ad_structures=fields["ad_structures"],
            summary=fields["summary"],
            led_remote=bool(fields.get("led_remote")),
        )
    return None


def frame_ble_packets(
    bits: np.ndarray,
    *,
    channel: int | None = 37,
    access_address: int = ADV_ACCESS_ADDRESS,
    max_packets: int = 32,
    max_errors: int = 1,
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
                bits_try, off, channel=ch, access_address=access_address
            )
            if parsed is not None:
                packets.append(parsed)
    packets = dedupe_packets(packets)
    return [p for p in packets if p.crc_ok and p.pdu_type in ADV_PDU_TYPES]


def _burst_slices(iq: np.ndarray, sample_rate: float) -> list[tuple[int, int]]:
    """Find short energy bursts likely to be BLE advertising PDUs."""
    iq = np.asarray(iq, dtype=np.complex64).ravel()
    if iq.size < 1024:
        return [(0, iq.size)]
    # ~80 us power window
    win = max(32, int(sample_rate * 80e-6))
    power = np.abs(iq) ** 2
    ma = np.convolve(power, np.ones(win) / win, mode="same")
    noise = float(np.median(ma) + 1e-12)
    thr = max(noise * 6.0, float(np.percentile(ma, 92)))
    active = ma > thr
    slices: list[tuple[int, int]] = []
    i = 0
    n = len(active)
    # BLE adv PDU ~200-400 us + pad
    min_len = int(sample_rate * 120e-6)
    max_len = int(sample_rate * 600e-6)
    pad = int(sample_rate * 80e-6)
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j < n and active[j]:
            j += 1
        if j - i >= min_len:
            a = max(0, i - pad)
            b = min(n, max(j + pad, i + min_len))
            b = min(n, max(b, a + min_len))
            if b - a > max_len + 2 * pad:
                b = a + max_len + 2 * pad
            slices.append((a, b))
        i = j
    if not slices:
        # fallback: whole buffer in chunks
        step = int(sample_rate * 0.002)
        for a in range(0, iq.size, step):
            slices.append((a, min(iq.size, a + step)))
    return slices[:80]


def _demod_burst_packets(
    burst: np.ndarray,
    *,
    sample_rate: float,
    channel: int,
) -> list[BlePacket]:
    """CFO grid + polarity + sample-phase search on one burst."""
    burst = np.asarray(burst, dtype=np.complex64).ravel()
    if burst.size < 64:
        return []
    aa = ADV_AA_BITS.astype(np.float64) * 2.0 - 1.0
    found: list[BlePacket] = []

    for cfo in np.linspace(-200e3, 200e3, 9):
        t = np.arange(burst.size) / sample_rate
        mixed = (burst * np.exp(-1j * 2 * np.pi * cfo * t)).astype(np.complex64)
        soft, sps = _soft_filtered(mixed, sample_rate)
        for phase in range(sps):
            samples = soft[phase::sps]
            if samples.size < 48:
                continue
            for polarity in (1.0, -1.0):
                corr = np.correlate(samples * polarity, aa, mode="valid")
                if corr.size == 0:
                    continue
                peak = int(np.argmax(corr))
                if corr[peak] < 6.0:
                    continue
                bits = (samples * polarity > 0).astype(np.uint8)
                for delta in range(-3, 4):
                    off = peak + delta
                    if off < 0 or off + 40 >= len(bits):
                        continue
                    errs = int(np.sum(bits[off : off + 32] != ADV_AA_BITS))
                    if errs > 2:
                        continue
                    pkt = _parse_from_bits(bits, off, channel=channel)
                    if pkt is not None:
                        found.append(pkt)
        if found:
            break
    return dedupe_packets(found)


def decode_ble_iq(
    iq: np.ndarray,
    *,
    sample_rate: float,
    center_freq_hz: float | None = None,
    channel: int | None = None,
) -> dict:
    """Full BLE 1M decode path for an IQ buffer (burst-oriented)."""
    if channel is None and center_freq_hz is not None:
        channel = channel_from_freq_hz(center_freq_hz)
    if channel is None:
        channel = 37

    iq = np.asarray(iq, dtype=np.complex64).ravel()
    packets: list[BlePacket] = []

    # Path A: multi-phase whole-buffer demod (fast, good for demo + strong signals)
    bits = demod_gfsk_bits(iq, sample_rate=sample_rate)
    packets.extend(frame_ble_packets(bits, channel=channel, max_errors=1))
    bit_count = int(bits.size)

    # Path B: burst detector + CFO search when nothing found (real HackRF)
    if not packets:
        for a, b in _burst_slices(iq, sample_rate):
            packets.extend(
                _demod_burst_packets(
                    iq[a:b], sample_rate=sample_rate, channel=channel
                )
            )
            if len(packets) >= 16:
                break

    packets = dedupe_packets(packets)
    power = float(np.mean(np.abs(iq) ** 2) + 1e-12)
    rssi = 10.0 * np.log10(power)
    for p in packets:
        p.rssi_dbfs = rssi

    return {
        "phy": "LE1M",
        "sample_rate_hz": sample_rate,
        "center_freq_hz": center_freq_hz,
        "channel": channel,
        "bit_count": bit_count,
        "packet_count": len(packets),
        "packets": [p.to_dict() for p in packets],
    }
