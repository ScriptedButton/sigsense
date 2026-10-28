"""Passive BLE capture interchange and offline Wireshark dissection.

No radio or transmission commands are executed here. Link type 256 carries the
Bluetooth LE RF pseudo-header (Wireshark packet-btle_rf.c / libpcap specification).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

MAX_CAPTURE_BYTES = 32 * 1024 * 1024


def tshark_capabilities() -> dict:
    executable = shutil.which("tshark")
    if executable:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=5
        )
        version = (
            result.stdout.splitlines()[0] if result.returncode == 0 else "unavailable"
        )
    else:
        version = "not installed"
    return {
        "available": bool(executable),
        "version": version,
        "mode": "offline passive dissection",
        "formats": ["pcap", "pcapng", "btsnoop"],
        "layers": [
            "Link Layer",
            "L2CAP",
            "ATT/GATT",
            "SMP",
            "profiles supported by installed Wireshark",
        ],
        "note": "Coverage depends on capture completeness and installed dissectors. Encrypted payloads are not plaintext without decryption keys/backend support.",
    }


def export_ble_pcap(packets: list) -> bytes:
    """Export dewhitened PDUs with CRC/PHY/direction metadata and relative times.

    No dBm signal flag is emitted for SDR dBFS measurements. Direction, decryption
    and MIC success are marked only when explicitly supplied by the capture.
    """
    out = bytearray(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 256))
    for item in packets:
        packet = item.to_dict() if hasattr(item, "to_dict") else item
        channel = packet.get("channel")
        if channel is None or not 0 <= channel <= 39:
            raise ValueError("PCAP export needs a BLE channel 0..39 for every packet")
        rf_channel = {37: 0, 38: 12, 39: 39}.get(
            channel, channel + 1 if channel < 11 else channel + 2
        )
        aa = (
            int(packet["access_address"], 0)
            if isinstance(packet["access_address"], str)
            else int(packet["access_address"])
        )
        flags = 0x0001 | 0x0010 | 0x0020 | 0x0400  # plain PDU, AA metadata, CRC checked
        flags |= 0x0800 if packet.get("crc_ok") else 0
        flags |= {"LE1M": 0, "LE2M": 1, "LECoded": 2}.get(
            packet.get("phy", "LE1M"), 0
        ) << 14
        direction = packet.get("direction")
        if packet.get("packet_kind") == "data":
            flags |= {"central_to_peripheral": 2, "peripheral_to_central": 3}.get(
                direction, 0
            ) << 7
        elif channel < 37:
            flags |= 1 << 7  # auxiliary advertising
        if packet.get("decrypted") is True:
            flags |= 0x0008
        if packet.get("mic_ok") is not None:
            flags |= 0x1000 | (0x2000 if packet["mic_ok"] else 0)
        pseudo = struct.pack(
            "<BbbBIH",
            rf_channel,
            0,
            0,
            min(255, int(packet.get("aa_errors", 0))),
            aa,
            flags,
        )
        body = aa.to_bytes(4, "little") + bytes.fromhex(
            packet["header_hex"] + packet["payload_hex"] + packet["crc_hex"]
        )
        payload = pseudo + body
        timestamp = float(packet.get("timestamp_seconds") or 0.0)
        if not math.isfinite(timestamp) or not 0 <= timestamp < 2**32 - 1:
            raise ValueError("Invalid packet timestamp")
        seconds, micros = divmod(round(timestamp * 1e6), 1_000_000)
        out.extend(struct.pack("<IIII", seconds, micros, len(payload), len(payload)))
        out.extend(payload)
    return bytes(out)


def dissect_capture(
    data: bytes, *, max_packets: int = 2000, timeout: float = 30.0
) -> dict:
    """Decode existing sniffer/HCI captures with installed TShark dissectors."""
    if not data or len(data) > MAX_CAPTURE_BYTES:
        raise ValueError("Capture must contain 1 byte to 32 MiB")
    if not 1 <= max_packets <= 10000:
        raise ValueError("max_packets must be 1..10000")
    executable = shutil.which("tshark")
    if not executable:
        raise RuntimeError(
            "TShark is not installed; install Wireshark/TShark for protocol capture analysis"
        )
    with tempfile.TemporaryDirectory(prefix="sigsense-ble-") as folder:
        path = Path(folder) / "capture.pcap"
        path.write_bytes(data)
        # -r means file input only; -n disables name resolution. Two passes let
        # Wireshark reassemble and associate connection/higher-layer context.
        result = subprocess.run(
            [
                executable,
                "-n",
                "-2",
                "-r",
                str(path),
                "-c",
                str(max_packets),
                "-Y",
                "btle || btatt || btsmp || bthci_acl",
                "-T",
                "json",
                "--no-duplicate-keys",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            message = result.stderr.decode(errors="replace").strip()[-600:]
            raise ValueError(f"TShark could not decode capture: {message}")
    decoded = json.loads(result.stdout)
    packets = []
    protocol_set = set()
    for frame in decoded:
        layers = frame.get("_source", {}).get("layers", {})
        info = layers.get("frame", {})
        protocols = str(info.get("frame.protocols", "")).split(":")
        protocol_set.update(protocols)
        summary = next(
            (p for p in reversed(protocols) if p not in {"frame", "bluetooth", "data"}),
            "BLE",
        )
        packets.append(
            {
                "number": info.get("frame.number"),
                "timestamp": info.get("frame.time_epoch"),
                "length": info.get("frame.len"),
                "protocols": protocols,
                "summary": summary,
                "layers": layers,
            }
        )
    return {
        "backend": "tshark",
        "version": tshark_capabilities()["version"],
        "packet_count": len(packets),
        "packet_limit": max_packets,
        "limit_note": "Limit applies to input frames, including non-BLE frames.",
        "protocols": sorted(protocol_set - {"", "frame"}),
        "packets": packets,
        "warnings": [
            "Decryption and protocol coverage depend on the supplied capture and installed Wireshark version."
        ],
    }
