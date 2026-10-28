"""Bounded passive BLE advertising parsers and channel-selection helpers.

Bluetooth Core Vol 6 Part B. Unknown fields remain raw, never guessed.
"""

from __future__ import annotations


def mac(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw[::-1])


def parse_ad_structures(data: bytes) -> tuple[list[dict], list[str]]:
    ads, warnings = [], []
    names = {
        1: "Flags",
        2: "Incomplete UUID16",
        3: "Complete UUID16",
        4: "Incomplete UUID32",
        5: "Complete UUID32",
        6: "Incomplete UUID128",
        7: "Complete UUID128",
        8: "Shortened Local Name",
        9: "Complete Local Name",
        10: "Tx Power",
        0x16: "Service Data UUID16",
        0x19: "Appearance",
        0x20: "Service Data UUID32",
        0x21: "Service Data UUID128",
        0xFF: "Manufacturer Specific",
    }
    pos = 0
    while pos < len(data):
        length = data[pos]
        pos += 1
        if length == 0:
            break
        if pos + length > len(data):
            warnings.append(f"Truncated AD structure at byte {pos-1}")
            break
        kind, value = data[pos], data[pos + 1 : pos + length]
        pos += length
        detail = value.hex()
        if kind in (8, 9):
            detail = value.decode("utf-8", errors="replace")
        elif kind == 1 and len(value) == 1:
            flags = [
                name
                for mask, name in [
                    (1, "LE Limited Disc"),
                    (2, "LE General Disc"),
                    (4, "BR/EDR Not Supported"),
                ]
                if value[0] & mask
            ]
            detail = f'0x{value[0]:02X} ({", ".join(flags)})'
        elif kind == 10 and len(value) == 1:
            detail = f'{int.from_bytes(value,"little",signed=True)} dBm'
        elif kind in (2, 3, 4, 5, 6, 7):
            width = 2 if kind < 4 else 4 if kind < 6 else 16
            if len(value) % width:
                warnings.append("UUID list length is not a multiple of UUID width")
            detail = ", ".join(
                f'{int.from_bytes(value[i:i+width],"little"):0{width*2}x}'
                for i in range(0, len(value) - width + 1, width)
            )
        elif kind == 0xFF and len(value) >= 2:
            detail = (
                f'company 0x{int.from_bytes(value[:2],"little"):04X}: {value[2:].hex()}'
            )
            if value[:2] == b"\x4c\x00":
                detail += " (Apple manufacturer data)"
        elif kind in (0x16, 0x20, 0x21):
            width = {0x16: 2, 0x20: 4, 0x21: 16}[kind]
            if len(value) < width:
                warnings.append("Truncated service UUID")
            else:
                detail = f'UUID {int.from_bytes(value[:width],"little"):0{width*2}x}: {value[width:].hex()}'
        ads.append(
            {
                "type": kind,
                "label": names.get(kind, f"AD 0x{kind:02X}"),
                "data_hex": value.hex(),
                "detail": detail,
            }
        )
    return ads, warnings


def parse_connect_ind(payload: bytes, header0: int = 0) -> dict:
    if len(payload) != 34:
        raise ValueError("CONNECT_IND payload must be exactly 34 bytes")
    data = payload[12:]
    channel_map = int.from_bytes(data[16:21], "little")
    used = [i for i in range(37) if channel_map & (1 << i)]
    hop = data[21] & 31
    aa = int.from_bytes(data[:4], "little")
    valid = (
        len(used) >= 2
        and not channel_map >> 37
        and 5 <= hop <= 16
        and aa not in (0, 0xFFFFFFFF, 0x8E89BED6)
    )
    return {
        "initiator_address": mac(payload[:6]),
        "advertiser_address": mac(payload[6:12]),
        "advertiser_address_type": "random" if header0 & 0x80 else "public",
        "initiator_address_type": "random" if header0 & 0x40 else "public",
        "access_address": f"0x{aa:08X}",
        "crc_init": int.from_bytes(data[4:7], "little"),
        "window_size_ms": data[7] * 1.25,
        "window_offset_ms": int.from_bytes(data[8:10], "little") * 1.25,
        "interval_ms": int.from_bytes(data[10:12], "little") * 1.25,
        "latency": int.from_bytes(data[12:14], "little"),
        "timeout_ms": int.from_bytes(data[14:16], "little") * 10,
        "channel_map_hex": data[16:21].hex(),
        "used_channels": used,
        "hop_increment": hop,
        "sleep_clock_accuracy": data[21] >> 5,
        "channel_selection": 2 if header0 & 0x20 else 1,
        "valid_parameters": valid,
    }


def parse_extended_payload(payload: bytes) -> dict:
    if not payload:
        raise ValueError("Empty extended advertising payload")
    length, mode = payload[0] & 63, payload[0] >> 6
    end = 1 + length
    if end > len(payload):
        raise ValueError("Truncated extended advertising header")
    out = {"advertising_mode": mode, "extended_header_length": length}
    flags = payload[1] if length else 0
    pos = 2 if length else 1
    for mask, size, name in [
        (1, 6, "advertiser_address"),
        (2, 6, "target_address"),
        (4, 1, "cte_info"),
        (8, 2, "adi"),
        (16, 3, "aux_ptr"),
        (32, 18, "sync_info"),
        (64, 1, "tx_power"),
    ]:
        if not flags & mask:
            continue
        if pos + size > end:
            raise ValueError(f"Truncated extended advertising {name}")
        value = payload[pos : pos + size]
        pos += size
        if name.endswith("address"):
            out[name] = mac(value)
        elif name == "adi":
            adi = int.from_bytes(value, "little")
            out[name] = {"sid": adi >> 12, "did": adi & 0xFFF}
        elif name == "aux_ptr":
            out[name] = {
                "channel": value[0] & 63,
                "clock_accuracy": (value[0] >> 6) & 1,
                "offset_us": (value[1] | ((value[2] & 31) << 8))
                * (300 if value[0] & 128 else 30),
                "phy": {0: "LE1M", 1: "LE2M", 2: "LECoded"}.get(
                    value[2] >> 5, "reserved"
                ),
            }
        elif name == "sync_info":
            out[name] = {
                "raw_hex": value.hex(),
                "interval_ms": int.from_bytes(value[2:4], "little") * 1.25,
                "access_address": f'0x{int.from_bytes(value[9:13],"little"):08X}',
                "crc_init": int.from_bytes(value[13:16], "little"),
                "event_counter": int.from_bytes(value[16:18], "little"),
            }
        else:
            out[name] = int.from_bytes(value, "little", signed=name == "tx_power")
    out["acad_hex"] = payload[pos:end].hex()
    out["adv_data_hex"] = payload[end:].hex()
    return out


def data_channel(
    event_counter: int,
    *,
    access_address: int,
    channel_map: int,
    algorithm: int = 2,
    hop_increment: int = 5,
) -> int:
    used = [i for i in range(37) if channel_map & (1 << i)]
    if len(used) < 2 or channel_map >> 37 or event_counter < 0:
        raise ValueError("Invalid channel map or event counter")
    if algorithm == 1:
        if not 5 <= hop_increment <= 16:
            raise ValueError("Invalid CSA1 hop increment")
        unmapped = ((event_counter + 1) * hop_increment) % 37
        return unmapped if unmapped in used else used[unmapped % len(used)]
    if algorithm != 2:
        raise ValueError("Channel selection algorithm must be 1 or 2")
    channel_id = ((access_address >> 16) ^ access_address) & 0xFFFF
    prn = (event_counter & 0xFFFF) ^ channel_id
    for _ in range(3):
        prn = ((prn & 0xAAAA) >> 1) | ((prn & 0x5555) << 1)
        prn = ((prn & 0xCCCC) >> 2) | ((prn & 0x3333) << 2)
        prn = ((prn & 0xF0F0) >> 4) | ((prn & 0x0F0F) << 4)
        prn = (17 * prn + channel_id) & 0xFFFF
    prn ^= channel_id
    unmapped = prn % 37
    return unmapped if unmapped in used else used[(len(used) * prn) >> 16]
