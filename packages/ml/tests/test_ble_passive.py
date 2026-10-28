"""Passive BLE regression tests, including independent Bluetooth SIG vectors.

Core Vol 6 Part C, sections 1, 3 and 4 (v6.3):
https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core_v6.3/out/en/low-energy-controller/sample-data.html
"""

import importlib.util
import shutil
import struct
import unittest

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import resample_poly

from sigsense_ml.ble import (
    ADV_ACCESS_ADDRESS,
    _crc24_bytes,
    _whiten_bits,
    bits_to_bytes,
    crc_init_reorder,
    decode_ble_iq,
    decode_unwhitened_pdu,
    decode_whitened_pdu,
    encode_adv_pdu_crc,
    generate_ble_adv_iq,
    modulate_ble_packet_iq,
    parse_adv_payload,
)
from sigsense_ml.ble_capture import export_ble_pcap, dissect_capture
from sigsense_ml.ble_protocol import (
    data_channel,
    parse_ad_structures,
    parse_extended_payload,
)


def pdu_crc(header, payload, crc_init=0x555555):
    body = bytes([header, len(payload)]) + payload
    return body + _crc24_bytes(body, crc_init_reorder(crc_init)).to_bytes(3, "little")


def rf_waveform(
    pdu, *, aa=ADV_ACCESS_ADDRESS, channel=37, symbol_rate=1e6, sample_rate=4e6
):
    # Independent rectangular-frequency reference with Gaussian pulse shaping.
    bits = np.unpackbits(
        np.frombuffer(aa.to_bytes(4, "little"), dtype=np.uint8), bitorder="little"
    )
    payload = np.unpackbits(np.frombuffer(pdu, dtype=np.uint8), bitorder="little")
    preamble = np.tile([aa & 1, 1 - (aa & 1)], 8 if symbol_rate == 2e6 else 4)
    bits = np.r_[preamble, bits, _whiten_bits(payload, channel)]
    sps = round(sample_rate / symbol_rate)
    shaped = gaussian_filter1d(np.repeat(bits.astype(float) * 2 - 1, sps), sps * 0.35)
    signal = np.exp(1j * np.cumsum(np.pi * 0.5 * shaped / sps))
    return np.r_[
        np.zeros(120, dtype=complex), signal, np.zeros(120, dtype=complex)
    ].astype(np.complex64)


def connect_payload():
    return (
        bytes.fromhex("010203040506112233445566")
        + (0xAA08192B).to_bytes(4, "little")
        + (0xC4C181).to_bytes(3, "little")
        + bytes([1])
        + (0).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(2, "little")
        + (200).to_bytes(2, "little")
        + ((1 << 37) - 1).to_bytes(5, "little")
        + bytes([5])
    )


class PassiveBleTests(unittest.TestCase):
    def test_sig_connected_crc_whitening_vector(self):
        raw = bytes.fromhex("1605010203040545d0d2")
        packet = decode_unwhitened_pdu(
            raw, channel=16, access_address=0xAA08192B, crc_init=0xC4C181
        )
        self.assertTrue(packet.crc_ok)
        self.assertEqual(packet.payload_hex, "0102030405")
        # Independent on-air sequence from SIG sample 4.2.
        packet = decode_whitened_pdu(
            bytes.fromhex("c69b5231dbbe9d4df419"),
            channel=16,
            access_address=0xAA08192B,
            crc_init=0xC4C181,
        )
        self.assertTrue(packet.crc_ok)
        with self.assertRaises(ValueError):
            decode_unwhitened_pdu(raw, access_address=0xAA08192B)

    def test_sig_cte_control_crc_vector(self):
        packet = decode_unwhitened_pdu(
            bytes.fromhex("3b088501dbffffff01214339e294"),
            channel=29,
            access_address=0xAA173C42,
            crc_init=0xCD3F6C,
        )
        self.assertTrue(packet.crc_ok)
        self.assertEqual(packet.header_hex, "3b0885")
        self.assertEqual(packet.length, 8)
        self.assertEqual(packet.protocol_fields["cte_info"], 0x85)

    def test_sig_channel_selection_vectors(self):
        self.assertEqual(
            [
                data_channel(i, access_address=0x8E89BED6, channel_map=(1 << 37) - 1)
                for i in range(4)
            ],
            [25, 20, 6, 21],
        )
        channel_map = sum(1 << i for i in [9, 10, 21, 22, 23, 33, 34, 35, 36])
        self.assertEqual(
            [
                data_channel(i, access_address=0x8E89BED6, channel_map=channel_map)
                for i in [6, 7, 8]
            ],
            [23, 9, 34],
        )
        channel_map = (1 << 1) | (1 << 2) | (1 << 8)
        self.assertEqual(
            [
                data_channel(i, access_address=0x8E89BED6, channel_map=channel_map)
                for i in [11, 12, 13]
            ],
            [1, 2, 2],
        )

    def test_connection_and_directed_advertisements_are_not_ad_data(self):
        connection = decode_unwhitened_pdu(pdu_crc(5, connect_payload()), channel=37)
        fields = connection.protocol_fields["connection"]
        self.assertTrue(fields["valid_parameters"])
        self.assertEqual(fields["access_address"], "0xAA08192B")
        self.assertEqual(fields["crc_init"], 0xC4C181)
        self.assertEqual(connection.adv_address, "66:55:44:33:22:11")
        directed = parse_adv_payload(1, bytes.fromhex("010203040506112233445566"))
        self.assertEqual(directed["ad_structures"], [])
        self.assertEqual(
            directed["protocol_fields"]["target_address"], "66:55:44:33:22:11"
        )

    def test_extended_advertisement_long_payload_and_aux_pointer(self):
        # flags AdvA+ADI+AuxPtr; extended header = flags + 6 + 2 + 3.
        extended = (
            bytes([12, 0x19])
            + bytes.fromhex("010203040506")
            + bytes.fromhex("23a1")
            + bytes([5, 10, 0x20])
        )
        payload = extended + bytes([100, 0xFF]) + bytes(range(99))
        decoded = decode_unwhitened_pdu(pdu_crc(7, payload), channel=37)
        self.assertTrue(decoded.crc_ok)
        self.assertGreater(decoded.length, 37)
        fields = decoded.protocol_fields["extended"]
        self.assertEqual(fields["advertiser_address"], "06:05:04:03:02:01")
        self.assertEqual(fields["adi"], {"sid": 10, "did": 0x123})
        self.assertEqual(fields["aux_ptr"]["phy"], "LE2M")
        self.assertEqual(fields["aux_ptr"]["offset_us"], 300)
        with self.assertRaises(ValueError):
            parse_extended_payload(bytes([12, 1, 2]))
        with self.assertRaises(ValueError):
            parse_extended_payload(bytes([1, 1]))

    def test_malformed_ad_structures_and_uuid_service_data(self):
        ads, warnings = parse_ad_structures(bytes.fromhex("03030d1804160f1801"))
        self.assertEqual(ads[0]["detail"], "180d")
        self.assertIn("180f", ads[1]["detail"])
        self.assertFalse(warnings)
        ads, warnings = parse_ad_structures(bytes.fromhex("05094142"))
        self.assertTrue(warnings)
        self.assertEqual(ads, [])

    def test_fractional_rates_cfo_and_iq_inversion(self):
        original = generate_ble_adv_iq(
            sample_rate=4e6, channel=38, snr_db=12, seed=9, n_pad=400
        )
        for up, down in [(3, 5), (1, 1), (2, 1)]:
            rate = 4e6 * up / down
            x = resample_poly(original, up, down)
            for offset in [-250000, 250000]:
                shifted = np.conj(x) * np.exp(
                    2j * np.pi * offset * np.arange(len(x)) / rate
                )
                result = decode_ble_iq(shifted, sample_rate=rate, channel=38)
                self.assertEqual(result["packet_count"], 1, (rate, offset))
                self.assertTrue(result["packets"][0]["crc_ok"])

    def test_repeated_packets_preserved(self):
        x = modulate_ble_packet_iq(
            header_hex="42", payload_hex="387800de68be020106", channel=39, repeats=3
        )
        result = decode_ble_iq(x, sample_rate=4e6, channel=39)
        self.assertEqual(result["packet_count"], 3)
        self.assertEqual(len({p["sample_offset"] for p in result["packets"]}), 3)
        self.assertEqual(len({p["capture_id"] for p in result["packets"]}), 1)

    def test_known_connection_le2m_uses_actual_crcinit(self):
        pdu = bytes.fromhex("1605010203040545d0d2")
        x = rf_waveform(
            pdu, aa=0xAA08192B, channel=16, symbol_rate=2e6, sample_rate=8e6
        )
        result = decode_ble_iq(
            x,
            sample_rate=8e6,
            channel=16,
            connections=[{"access_address": "0xAA08192B", "crc_init": 0xC4C181}],
        )
        self.assertEqual(result["packet_count"], 1)
        self.assertEqual(result["packets"][0]["phy"], "LE2M")
        self.assertEqual(result["packets"][0]["packet_kind"], "data")
        self.assertEqual(result["packets"][0]["payload_hex"], "0102030405")
        wrong = decode_ble_iq(
            x,
            sample_rate=8e6,
            channel=16,
            connections=[{"access_address": "0xAA08192B", "crc_init": 0x555555}],
        )
        self.assertEqual(wrong["packet_count"], 0)

    def test_long_extended_iq_pdu(self):
        payload = bytes([0]) + bytes([250, 0xFF]) + bytes(range(249))
        x = rf_waveform(pdu_crc(7, payload))
        result = decode_ble_iq(x, sample_rate=4e6, channel=37)
        self.assertEqual(result["packet_count"], 1)
        self.assertEqual(result["packets"][0]["length"], 252)

    def test_no_false_packets_from_noise_and_input_validation(self):
        rng = np.random.default_rng(3)
        x = (rng.normal(size=50000) + 1j * rng.normal(size=50000)).astype(np.complex64)
        self.assertEqual(
            decode_ble_iq(x, sample_rate=4e6, channel=37)["packet_count"], 0
        )
        for kwargs in [
            {"sample_rate": 0},
            {"sample_rate": float("nan")},
            {"sample_rate": 4e6, "channel": 99},
            {"sample_rate": 4e6, "channel": 38, "center_freq_hz": 2402e6},
        ]:
            with self.assertRaises(ValueError):
                decode_ble_iq(x, **kwargs)

    @unittest.skipUnless(shutil.which("tshark"), "TShark optional integration")
    def test_pcap_export_independent_dissector_and_metadata(self):
        packet = decode_unwhitened_pdu(
            bytes.fromhex("4214387800de68be0dff55aa018a24be06c0b406080267c8a2"),
            channel=39,
        )
        capture = export_ble_pcap([packet])
        self.assertEqual(struct.unpack_from("<I", capture, 20)[0], 256)
        # RSSI is dBFS, not calibrated dBm; do not mark a fake dBm value valid.
        self.assertFalse(struct.unpack_from("<H", capture, 48)[0] & 2)
        result = dissect_capture(capture)
        self.assertEqual(result["packet_count"], 1)
        fields = result["packets"][0]["layers"]["btle"]
        self.assertEqual(fields["btle.advertising_address"], "be:68:de:00:78:38")

    @unittest.skipUnless(shutil.which("tshark"), "TShark optional integration")
    def test_connected_att_and_smp_dissection(self):
        packets = []
        connection = decode_unwhitened_pdu(pdu_crc(5, connect_payload()), channel=37)
        packets.append(connection.to_dict())
        # ATT Exchange MTU Request over fixed CID 0x0004.
        for cid, payload in [
            (4, bytes.fromhex("02f700")),
            (6, bytes.fromhex("01030005100707")),
        ]:
            l2cap = (
                len(payload).to_bytes(2, "little") + cid.to_bytes(2, "little") + payload
            )
            p = decode_unwhitened_pdu(
                pdu_crc(2, l2cap, 0xC4C181),
                channel=16,
                access_address=0xAA08192B,
                crc_init=0xC4C181,
            )
            row = p.to_dict()
            row["direction"] = "central_to_peripheral"
            row["timestamp_seconds"] = len(packets) * 0.01
            packets.append(row)
        result = dissect_capture(export_ble_pcap(packets))
        self.assertIn("btatt", result["protocols"])
        self.assertIn("btsmp", result["protocols"])


@unittest.skipUnless(
    importlib.util.find_spec("cryptography"), "optional sigsense-ml[ble]"
)
class BleCryptoTests(unittest.TestCase):
    def test_sig_session_key_and_both_directions(self):
        from sigsense_ml.ble_crypto import derive_session_key, decrypt_payload

        key = derive_session_key(
            bytes.fromhex("4c68384139f574d836bcf34e9dfb01bf"),
            bytes.fromhex("1302f1e0dfcebdac7968574635241302"),
        )
        self.assertEqual(key.hex(), "99ad1b5226a37e3e058e3b8e27c2c666")
        for header, cipher, direction in [
            ("0f05", "9fcda7f448", "central_to_peripheral"),
            ("0705", "a34c13a415", "peripheral_to_central"),
        ]:
            kwargs = dict(
                session_key=key,
                iv_on_air=bytes.fromhex("24abdcbabebaafde"),
                header=bytes.fromhex(header),
                ciphertext_mic=bytes.fromhex(cipher),
                packet_counter=0,
                direction=direction,
            )
            self.assertEqual(decrypt_payload(**kwargs), b"\x06")
            kwargs["packet_counter"] = 1
            with self.assertRaisesRegex(ValueError, "MIC verification failed"):
                decrypt_payload(**kwargs)


if __name__ == "__main__":
    unittest.main()
