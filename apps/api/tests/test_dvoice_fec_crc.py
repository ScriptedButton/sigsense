"""CRC tests (WP-A). The bitwise engine is cross-checked against an independent table-free
reference; spec-defined CRCs are checked against published test vectors where the spec gives
them (M17 section 2.6; NXDN TS 2-B vectors exercise CRC-6/12/15/16 in test_dvoice_fec_viterbi)."""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.fec import crc


def reference_crc(width, poly, init, xorout, data: bytes) -> int:
    """Independent byte-wise MSB-first CRC (no reflection)."""
    top, mask = 1 << (width - 1), (1 << width) - 1
    reg = init
    for byte in data:
        for bit in range(7, -1, -1):
            feedback = bool(reg & top) ^ bool((byte >> bit) & 1)
            reg = (reg << 1) & mask
            if feedback:
                reg ^= poly
    return reg ^ xorout


class CatalogueTests(unittest.TestCase):
    def test_standard_check_values(self):
        bits = crc.bytes_to_bits(b"123456789")
        for name, expected in (
            ("crc16_ccitt_false", 0x29B1),
            ("crc16_genibus", 0xD64E),
            ("crc16_x25", 0x906E),
            ("crc16_m17", 0x772B),  # M17 spec Table 2.6
            ("crc16_p25_dmr_header", 0xCE3C),  # XMODEM (0x31C3) with the all-ones inversion
            ("crc32_p25_packet", 0x765E7680),  # POSIX cksum polynomial family
        ):
            self.assertEqual(crc.crc_compute(name, bits), expected, name)

    def test_m17_published_vectors(self):
        self.assertEqual(crc.crc_compute("crc16_m17", crc.bytes_to_bits(b"")), 0xFFFF)
        self.assertEqual(crc.crc_compute("crc16_m17", crc.bytes_to_bits(b"A")), 0x206E)
        self.assertEqual(crc.crc_compute("crc16_m17", crc.bytes_to_bits(bytes(range(256)))), 0x1C31)

    def test_engine_matches_an_independent_reference_for_every_unreflected_spec(self):
        data = bytes((i * 37 + 11) & 0xFF for i in range(61))
        bits = crc.bytes_to_bits(data)
        for name, spec in crc.CRC_SPECS.items():
            if spec.refin or spec.refout or spec.width < 8:
                continue
            self.assertEqual(
                crc.crc_compute(name, bits),
                reference_crc(spec.width, spec.poly, spec.init, spec.xorout, data),
                name,
            )

    def test_polynomials_are_the_ones_printed_in_the_specs(self):
        # ETSI B.3.7-B.3.13, TIA-102.BAAA-A 6.2-6.4, NXDN TS 1-A 4.5, M17 2.6 (x^n term dropped).
        expect = {
            "crc8_dmr_shortlc": (8, (2, 1, 0)),
            "crc7_dmr_rc": (7, (5, 2, 1, 0)),
            "crc9_p25_dmr": (9, (6, 4, 3, 0)),
            "crc16_p25_dmr_header": (16, (12, 5, 0)),
            "crc32_p25_packet": (32, (26, 23, 22, 16, 12, 11, 10, 8, 7, 5, 4, 2, 1, 0)),
            "crc6_nxdn": (6, (5, 2, 1, 0)),
            "crc12_nxdn": (12, (11, 3, 2, 1, 0)),
            "crc15_nxdn": (15, (14, 11, 10, 7, 6, 2, 0)),
            "crc16_m17": (16, (14, 12, 11, 8, 5, 4, 2, 0)),
        }
        for name, (width, exps) in expect.items():
            spec = crc.CRC_SPECS[name]
            self.assertEqual(spec.width, width, name)
            self.assertEqual(spec.poly, sum(1 << e for e in exps), name)

    def test_inversion_conventions(self):
        # P25/DMR CRC-CCITT, CRC-9 and the P25 packet CRC invert the result; short DMR CRCs do not.
        for name in ("crc16_p25_dmr_header", "crc9_p25_dmr", "crc32_p25_packet"):
            spec = crc.CRC_SPECS[name]
            self.assertEqual(spec.xorout, (1 << spec.width) - 1, name)
            self.assertEqual(spec.init, 0, name)
        for name in ("crc8_dmr_shortlc", "crc7_dmr_rc"):
            self.assertEqual(crc.CRC_SPECS[name].xorout, 0, name)
        for name in ("crc6_nxdn", "crc12_nxdn", "crc15_nxdn"):
            spec = crc.CRC_SPECS[name]
            self.assertEqual(spec.init, (1 << spec.width) - 1, name)  # register preset to all ones

    def test_check_detects_single_bit_errors_for_all_widths(self):
        data = crc.bytes_to_bits(b"sigsense link control")
        for name, spec in crc.CRC_SPECS.items():
            if spec.refin or spec.width < 6:
                continue
            value = crc.crc_compute(name, data)
            framed = np.concatenate([data, np.array([int(c) for c in f"{value:0{spec.width}b}"], np.uint8)])
            self.assertTrue(crc.crc_check(name, framed), name)
            for pos in (0, 7, len(framed) // 2, len(framed) - 1):
                bad = framed.copy()
                bad[pos] ^= 1
                self.assertFalse(crc.crc_check(name, bad), f"{name} bit {pos}")


class DmrHelperTests(unittest.TestCase):
    def test_crc_masks_match_table_b21(self):
        self.assertEqual(crc.DMR_CRC_MASKS_16["pi_header"], 0x6969)
        self.assertEqual(crc.DMR_CRC_MASKS_16["csbk"], 0xA5A5)
        self.assertEqual(crc.DMR_CRC_MASKS_16["mbc_header"], 0xAAAA)
        self.assertEqual(crc.DMR_CRC_MASKS_16["data_header"], 0xCCCC)
        self.assertEqual(crc.DMR_CRC_MASKS_16["unified_single_block_data"], 0x3333)
        self.assertEqual(crc.DMR_CRC_MASKS_OTHER["voice_lc_header_24"], 0x969696)
        self.assertEqual(crc.DMR_CRC_MASKS_OTHER["terminator_with_lc_24"], 0x999999)
        self.assertEqual(crc.DMR_CRC_MASKS_OTHER["rate_3_4_data_continuation_crc9"], 0x1FF)
        self.assertEqual(crc.DMR_CRC_MASKS_OTHER["reverse_channel_crc7"], 0x7A)

    def test_mask_is_an_involution_and_wrong_mask_fails_the_check(self):
        body = crc.bytes_to_bits(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a")
        value = crc.crc_compute("crc16_p25_dmr_header", body)
        masked = crc.crc_apply_mask(value, crc.DMR_CRC_MASKS_16["csbk"])
        self.assertEqual(crc.crc_apply_mask(masked, crc.DMR_CRC_MASKS_16["csbk"]), value)
        self.assertNotEqual(crc.crc_apply_mask(masked, crc.DMR_CRC_MASKS_16["data_header"]), value)

    def test_checksum5_is_sum_mod_31(self):
        self.assertEqual(crc.dmr_checksum5(bytes(9)), 0)
        self.assertEqual(crc.dmr_checksum5(bytes([255] * 9)), (255 * 9) % 31)
        self.assertEqual(crc.dmr_checksum5(bytes(range(9))), 36 % 31)
        with self.assertRaises(ValueError):
            crc.dmr_checksum5(bytes(8))

    def test_dmr_message_crc32_swaps_octet_pairs_and_sends_the_low_octet_first(self):
        data = bytes(range(1, 9))
        swapped = bytes([2, 1, 4, 3, 6, 5, 8, 7])
        value = crc.crc_compute_bytes("crc32_dmr_raw", swapped)
        self.assertEqual(crc.crc32_dmr_message(data), value.to_bytes(4, "little"))
        with self.assertRaises(ValueError):
            crc.crc32_dmr_message(b"abc")


if __name__ == "__main__":
    unittest.main()
