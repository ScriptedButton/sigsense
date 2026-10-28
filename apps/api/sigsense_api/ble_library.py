"""Canonical packet identities and reconstruction of saved advertising sequences."""
from __future__ import annotations

import json
import numpy as np
from sigsense_ml.ble import BLE_CHANNELS_MHZ, modulate_ble_packet_iq

MAX_MESSAGES = 10_000


def broadcast_key(packet):
    return ":".join(str(packet.get(k, default)).lower() for k, default in (
        ("access_address", "0x8e89bed6"), ("phy", "LE1M"),
        ("header_hex", ""), ("payload_hex", ""),
    ))


def device_key(packet):
    """A saved device association requires an address and its address type.

    Never infer ownership from a user-entered name or from anonymous advertising
    access addresses, which are shared by unrelated devices.
    """
    address = packet.get("adv_address")
    if not isinstance(address, str) or not address.strip():
        return None
    address_type = packet.get("adv_address_type")
    if address_type not in ("random", "public"):
        if packet.get("packet_kind") == "data":
            return None
        header = int(packet["header_hex"][:2], 16)
        pdu_type = header & 15
        address_type = "random" if header & (0x80 if pdu_type in (3, 5) else 0x40) else "public"
    return f"{address.strip().upper()}:{address_type}"


def device_keys(packets):
    return sorted({key for packet in packets if (key := device_key(packet)) is not None})


def validate_packets(packets):
    if not 1 <= len(packets) <= MAX_MESSAGES:
        raise ValueError(f"Save between 1 and {MAX_MESSAGES:,} received messages")
    result = []
    for original in packets:
        packet = dict(original)
        if packet.get("crc_ok") is not True:
            raise ValueError("Only checksum-verified messages can be saved")
        for key, size in (("header_hex", 2), ("payload_hex", None), ("crc_hex", 3)):
            try:
                raw = bytes.fromhex(packet[key])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"Invalid {key}") from exc
            if (size is not None and len(raw) != size) or len(raw) > 255:
                raise ValueError(f"Invalid {key} length")
            packet[key] = raw.hex()
        channel = packet.get("channel")
        if channel is not None and (type(channel) is not int or not 0 <= channel <= 39):
            raise ValueError("Channel must be 0–39 or unknown")
        if len(json.dumps(packet, allow_nan=False)) > 16_384:
            raise ValueError("Message metadata is too large")
        result.append(packet)
    return result


def replayable(packet):
    return (
        packet.get("crc_ok") is True
        and packet.get("packet_kind", "advertising") != "data"
        and packet.get("phy", "LE1M") == "LE1M"
        and packet.get("channel") in (37, 38, 39)
        and str(packet.get("access_address", "")).lower() == "0x8e89bed6"
        and (int(packet["header_hex"][:2], 16) & 15) in (0, 1, 2, 4, 6)
        and len(packet["payload_hex"]) <= 74
    )


def prepare_replay(packets, *, unique=True, repeats=1):
    """Preflight the entire sequence before any transmitter is called.

    Preserve receive order and channels, reconstructing packet spacing. Capture
    timestamps are offsets into independent dwells, not a continuous RF timeline.
    """
    packets = validate_packets(packets)
    if not 1 <= repeats <= 20:
        raise ValueError("Repeats must be 1–20")
    playable = [p for p in packets if replayable(p)]
    skipped = len(packets) - len(playable)
    if unique:
        seen = set()
        distinct = []
        for packet in playable:
            key = broadcast_key(packet)
            if key not in seen:
                seen.add(key)
                distinct.append(packet)
        playable = distinct
    if not playable:
        raise ValueError("No replayable legacy LE1M announcements or scan responses on channels 37–39")
    chunks = []
    samples = 0
    rate = 4e6
    for _ in range(repeats):
        for packet in playable:
            iq = modulate_ble_packet_iq(
                header_hex=packet["header_hex"], payload_hex=packet["payload_hex"],
                channel=packet["channel"], sample_rate=rate, repeats=1,
            )
            samples += len(iq)
            if samples > 10_000_000:
                raise ValueError("Replay exceeds 10 million samples; choose unique broadcasts or fewer repeats")
            if not chunks or chunks[-1][0] != packet["channel"]:
                chunks.append((packet["channel"], []))
            chunks[-1][1].append(iq)
    blocks = [(np.concatenate(parts), {
        "center_freq_hz": BLE_CHANNELS_MHZ[channel] * 1e6,
        "sample_rate_hz": rate,
    }) for channel, parts in chunks]
    return blocks, {
        "messages": len(playable) * repeats, "skipped": skipped,
        "samples": samples, "unique": unique,
        "timing": "Reconstructed packet spacing; original RF timing is not preserved",
    }
