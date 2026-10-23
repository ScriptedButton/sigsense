#!/usr/bin/env python3
"""Regression: decode known-good BTLE LED-remote fixtures (JiaoXianjun/BTLE)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sigsense_ml.ble import (  # noqa: E402
    CRC_INIT_ADVERTISING,
    crc_init_reorder,
    decode_unwhitened_pdu,
    decode_whitened_pdu,
    encode_adv_pdu_crc,
    generate_ble_adv_iq,
    decode_ble_iq,
    modulate_ble_packet_iq,
)

# Remote A ON — unwhitened PDU+CRC from btle_tx
REMOTE_A_UNWHITENED = bytes.fromhex(
    "4214387800de68be0dff55aa018a24be06c0b406080267c8a2"
)
# Channel-39 whitened PDU+CRC
REMOTE_A_WHITENED_39 = bytes.fromhex(
    "5d2372278528f424cc2990ee21d3fa5f89db11a94a792905c2"
)
# Full PHY including preamble+AA
REMOTE_A_PHY = bytes.fromhex(
    "aad6be898e5d2372278528f424cc2990ee21d3fa5f89db11a94a792905c2"
)
# Remote B OFF unwhitened
REMOTE_B_UNWHITENED = bytes.fromhex(
    "42144273555555550dff4c00016024be0ec042044c00dcd459"
)


def main() -> None:
    assert crc_init_reorder(0x555555) == CRC_INIT_ADVERTISING == 0xAAAAAA

    a = decode_unwhitened_pdu(REMOTE_A_UNWHITENED, channel=39)
    assert a and a.crc_ok, a
    assert a.pdu_type == 2 and a.pdu_type_name == "ADV_NONCONN_IND"
    assert a.adv_address and a.adv_address.lower() == "be:68:de:00:78:38"
    assert a.payload_hex.endswith("0dff55aa018a24be06c0b4060802")
    print("OK Remote A unwhitened", a.summary)

    aw = decode_whitened_pdu(REMOTE_A_WHITENED_39, channel=39)
    assert aw and aw.crc_ok and aw.payload_hex == a.payload_hex
    print("OK Remote A whitened ch39", aw.summary)

    # PHY: skip preamble(1)+AA(4)=5 bytes → whitened PDU+CRC
    phy_pdu = REMOTE_A_PHY[5:]
    assert phy_pdu == REMOTE_A_WHITENED_39
    print("OK Remote A PHY layout")

    b = decode_unwhitened_pdu(REMOTE_B_UNWHITENED, channel=37)
    assert b and b.crc_ok, b
    assert b.adv_address and b.adv_address.lower() == "55:55:55:55:73:42"
    assert "0dff4c00016024be0ec042044c00" in b.payload_hex
    print("OK Remote B unwhitened", b.summary)

    # Round-trip synthetic IQ still decodes with corrected CRC
    iq = generate_ble_adv_iq(sample_rate=4e6, channel=37, snr_db=25, seed=1, n_pad=800)
    r = decode_ble_iq(iq, sample_rate=4e6, center_freq_hz=2402e6, channel=37)
    assert r["packet_count"] >= 1 and r["packets"][0]["crc_ok"]
    print("OK synthetic IQ decode", r["packets"][0]["summary"])

    # Replay encode matches Remote A fixture CRC body
    rebuilt = encode_adv_pdu_crc(
        header_hex=a.header_hex, payload_hex=a.payload_hex
    )
    assert rebuilt == REMOTE_A_UNWHITENED, (rebuilt.hex(), REMOTE_A_UNWHITENED.hex())
    print("OK encode_adv_pdu_crc Remote A")

    tx = modulate_ble_packet_iq(
        header_hex=a.header_hex,
        payload_hex=a.payload_hex,
        channel=39,
        sample_rate=4e6,
        repeats=2,
        amplitude=0.9,
    )
    assert tx.size > 1000
    rt = decode_ble_iq(tx, sample_rate=4e6, center_freq_hz=2480e6, channel=39)
    assert rt["packet_count"] >= 1 and rt["packets"][0]["crc_ok"]
    assert rt["packets"][0]["payload_hex"] == a.payload_hex
    print("OK TX modulate → decode Remote A", rt["packets"][0]["summary"])

    print("All BTLE fixtures passed.")


if __name__ == "__main__":
    main()
