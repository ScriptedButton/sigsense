"""Convolutional codes, Viterbi, the NXDN conformance vectors, M17/D-STAR parameters and the
scramblers (WP-A).

Primary references: NXDN TS 1-A v1.3 section 4.5/4.6 and the TS 2-B v1.2 conformance vectors
(complete encode chains and the scrambled outputs), M17 spec appendices B-F, JARL D-STAR spec
AP1-AP3. The NXDN vectors are published known answers: our CRC, convolutional encoder,
puncturing, interleaver and scrambler reproduce them bit for bit.
"""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.fec import crc, interleave, viterbi, whitening

import test_dvoice_fec_vectors as vec


def bits(s: str) -> np.ndarray:
    return np.array([int(c) for c in s], dtype=np.uint8)


def keep_mask(matrix):
    """Linear keep-mask from a puncturing matrix: for each column, G1 then G2."""
    if matrix is None:
        return None
    return tuple(int(row[j]) for j in range(len(matrix[0])) for row in matrix)


# channel -> (null bits appended before the CRC, CRC name, CRC width, puncturing matrix, rows)
NXDN_CHANNELS = {
    "cac": (3, "crc16_ccitt_false", 16, ("1111111", "1011101"), 25),
    "long_cac": (0, "crc16_ccitt_false", 16, ("1111111111111", "0110001110111"), 21),
    "short_cac": (2, "crc16_ccitt_false", 16, None, 21),
    "sacch": (0, "crc6_nxdn", 6, ("111111", "110110"), 5),
    "facch1": (0, "crc12_nxdn", 12, ("11", "01"), 9),
    "udch_facch2": (0, "crc15_nxdn", 15, ("1111111", "1011101"), 29),
}
NXDN_TABLES = {
    "cac": "nxdn_cac_outbound",
    "long_cac": "nxdn_cac_long",
    "short_cac": "nxdn_cac_short",
    "sacch": "nxdn_sacch",
    "facch1": "nxdn_facch1",
    "udch_facch2": "nxdn_udch",
}


def nxdn_encode(channel: str, info: np.ndarray) -> np.ndarray:
    nulls, crc_name, crc_width, matrix, rows = NXDN_CHANNELS[channel]
    data = np.concatenate([info, np.zeros(nulls, np.uint8)])
    value = crc.crc_compute(crc_name, data)
    withcrc = np.concatenate([data, np.array([int(c) for c in f"{value:0{crc_width}b}"], np.uint8)])
    code = viterbi.ConvCode(5, (viterbi._K5_G1, viterbi._K5_G2), keep_mask(matrix), 4)
    coded = viterbi.conv_encode(code, withcrc)
    return coded.reshape(rows, -1).T.reshape(-1)  # write rows, read columns


class NxdnConformanceTests(unittest.TestCase):
    def test_complete_coding_chains_reproduce_the_published_vectors(self):
        for channel, vectors in vec.NXDN_VECTORS.items():
            self.assertGreater(len(vectors), 0, channel)
            for before, after_interleave, _ in vectors:
                got = nxdn_encode(channel, bits(before))
                self.assertEqual("".join(map(str, got)), after_interleave, channel)

    def test_receive_side_tables_undo_the_published_interleave(self):
        for channel, vectors in vec.NXDN_VECTORS.items():
            table = interleave.table(NXDN_TABLES[channel])
            for before, after_interleave, _ in vectors[:2]:
                rx = bits(after_interleave)
                coded = interleave.deinterleave(rx, table)
                nulls, crc_name, crc_width, matrix, _ = NXDN_CHANNELS[channel]
                code = viterbi.ConvCode(5, (viterbi._K5_G1, viterbi._K5_G2), keep_mask(matrix), 4)
                out = viterbi.viterbi_decode(code, coded)
                info = out.data[: len(before) + nulls + crc_width]
                self.assertEqual(out.corrected, 0, channel)
                self.assertEqual("".join(map(str, info[: len(before)])), before, channel)
                self.assertTrue(crc.crc_check(crc_name, info), channel)

    def test_scrambled_vectors_follow_the_scrambler_stream(self):
        # After-interleaving vs after-scrambling differ by sign inversion (dibit ^ 0b10) wherever the
        # scrambler stream is 1; the CAC starts at symbol S8 (the 8 LICH symbols come first).
        n = whitening.nxdn_scramble_sequence(200)
        for before, after_il, after_sc in vec.NXDN_VECTORS["cac"] + vec.NXDN_VECTORS["long_cac"]:
            a, b = after_il, after_sc
            for j in range(len(a) // 2):
                d0, d1 = int(a[2 * j : 2 * j + 2], 2), int(b[2 * j : 2 * j + 2], 2)
                self.assertEqual(d0 ^ d1, int(n[8 + j]) << 1, f"symbol {j}")

    def test_lich_scrambling_vectors(self):
        n = whitening.nxdn_scramble_sequence(8)
        pairs = (("555D", "5D77"), ("5757", "5F7D"), ("5F55", "577F"), ("DDF5", "D5DF"), ("D557", "DD7D"),
                 ("D55F", "DD75"), ("DD55", "D57F"), ("DD5D", "D577"), ("D755", "DF7F"), ("D75D", "DF77"))
        for before, after in pairs:
            b = f"{int(before, 16):016b}"
            a = f"{int(after, 16):016b}"
            for j in range(8):
                self.assertEqual(int(b[2 * j : 2 * j + 2], 2) ^ int(a[2 * j : 2 * j + 2], 2), int(n[j]) << 1)

    def test_published_polynomials_and_presets(self):
        self.assertEqual(viterbi.NXDN_K5_R12.polys, (0o23, 0o35))  # G1 = 1+D^3+D^4, G2 = 1+D+D^2+D^4
        self.assertEqual(viterbi.NXDN_K5_R12.tail_bits, 4)
        self.assertEqual(sum(viterbi.NXDN_CAC_PUNCTURE), 12)  # 14 outputs per 7 inputs, 2 erased
        self.assertEqual(viterbi.NXDN_K5_R12_CAC.puncture, keep_mask(NXDN_CHANNELS["cac"][3]))


class ViterbiEngineTests(unittest.TestCase):
    CODES = {
        "nxdn": (viterbi.NXDN_K5_R12, 100),
        "nxdn_cac": (viterbi.NXDN_K5_R12_CAC, 171),
        "m17_p1": (viterbi.M17_K5_R12_P1, 240),
        "m17_p2": (viterbi.M17_K5_R12_P2, 144),
        "m17_p3": (viterbi.M17_K5_R12_P3, 206),
        "dstar": (viterbi.DSTAR_K3_R12, 100),
    }

    def test_poly_from_delays_uses_msb_for_the_newest_input(self):
        self.assertEqual(viterbi.poly_from_delays((0, 3, 4), 5), 0o23)
        self.assertEqual(viterbi.poly_from_delays((0, 1, 2, 4), 5), 0o35)
        self.assertEqual(viterbi.poly_from_delays((0, 1, 2), 3), 0b111)
        with self.assertRaises(ValueError):
            viterbi.poly_from_delays((5,), 5)

    def test_encoder_known_impulse_response(self):
        # A single 1 followed by zeros shows each generator's taps in order (G1,G2 interleaved).
        out = viterbi.conv_encode(viterbi.NXDN_K5_R12, np.array([1, 0, 0, 0, 0], np.uint8))
        g1 = out[0::2][:5].tolist()  # 1 + D^3 + D^4 -> 1 0 0 1 1
        g2 = out[1::2][:5].tolist()  # 1 + D + D^2 + D^4 -> 1 1 1 0 1
        self.assertEqual((g1, g2), ([1, 0, 0, 1, 1], [1, 1, 1, 0, 1]))
        d = viterbi.conv_encode(viterbi.DSTAR_K3_R12, np.array([1, 0, 0], np.uint8))
        self.assertEqual((d[0::2][:3].tolist(), d[1::2][:3].tolist()), ([1, 1, 1], [1, 0, 1]))

    def test_encoded_lengths(self):
        self.assertEqual(len(viterbi.conv_encode(viterbi.M17_K5_R12_P1, np.zeros(240, np.uint8))), 368)  # 488 -> 368
        # M17 stream frame: 144 bits + 4 tail -> 296 coded -> P2 keeps 272 (spec appendix E)
        self.assertEqual(len(viterbi.conv_encode(viterbi.M17_K5_R12_P2, np.zeros(144, np.uint8))), 272)
        self.assertEqual(len(viterbi.conv_encode(viterbi.M17_K5_R12, np.zeros(244 - 4, np.uint8))), 488)
        self.assertEqual(len(viterbi.conv_encode(viterbi.NXDN_K5_R12_CAC, np.zeros(171, np.uint8))), 300)

    def test_clean_roundtrip_all_named_codes(self):
        rng = np.random.default_rng(1)
        for name, (code, n) in self.CODES.items():
            for _ in range(20):
                data = rng.integers(0, 2, n).astype(np.uint8)
                out = viterbi.viterbi_decode(code, viterbi.conv_encode(code, data))
                np.testing.assert_array_equal(out.data, data, name)
                self.assertEqual(out.corrected, 0, name)
                self.assertTrue(out.ok, name)

    def test_corrects_a_few_hard_errors(self):
        rng = np.random.default_rng(2)
        for name, (code, n) in self.CODES.items():
            # within the capability of every code, including the heavily punctured M17 P1
            for n_err in (1, 2):
                for _ in range(60):
                    data = rng.integers(0, 2, n).astype(np.uint8)
                    rx = viterbi.conv_encode(code, data)
                    for p in rng.choice(len(rx), size=n_err, replace=False):
                        rx[p] ^= 1
                    out = viterbi.viterbi_decode(code, rx)
                    np.testing.assert_array_equal(out.data, data, f"{name} {n_err} errors")
                    self.assertEqual(out.corrected, n_err, name)

    def test_soft_decisions_beat_hard_decisions(self):
        rng = np.random.default_rng(3)
        code, n = viterbi.NXDN_K5_R12, 120
        hard_fail = soft_fail = 0
        for _ in range(150):
            data = rng.integers(0, 2, n).astype(np.uint8)
            tx = viterbi.conv_encode(code, data)
            soft = 1.0 - 2.0 * tx
            soft = soft + rng.normal(0, 0.75, size=soft.size)  # noisy channel
            hard = (soft < 0).astype(np.uint8)
            hard_fail += int(not (viterbi.viterbi_decode(code, hard).data == data).all())
            soft_fail += int(not (viterbi.viterbi_decode(code, hard, soft=np.clip(soft, -1, 1)).data == data).all())
        self.assertLess(soft_fail, hard_fail)

    def test_erased_positions_cost_nothing(self):
        code = viterbi.NXDN_K5_R12_CAC
        data = np.random.default_rng(4).integers(0, 2, 171).astype(np.uint8)
        rx = viterbi.conv_encode(code, data)
        soft = 1.0 - 2.0 * rx
        soft[10:14] = 0.0  # four "don't know" bits
        out = viterbi.viterbi_decode(code, rx, soft=soft)
        np.testing.assert_array_equal(out.data, data)
        self.assertEqual(out.corrected, 0)

    def test_inconsistent_lengths_raise(self):
        with self.assertRaises(ValueError):
            viterbi.viterbi_decode(viterbi.NXDN_K5_R12, np.zeros(11, np.uint8))  # odd number of coded bits
        with self.assertRaises(ValueError):
            viterbi.viterbi_decode(viterbi.NXDN_K5_R12, np.zeros(10, np.uint8), soft=np.zeros(9))


class M17Tests(unittest.TestCase):
    def test_randomizer_matches_table_b1(self):
        self.assertEqual(list(whitening._M17_RANDOMIZER_BYTES), vec.M17_RANDOMIZER_SPEC)
        seq = whitening.m17_randomizer(368)
        self.assertEqual(seq.size, 368)
        self.assertEqual("".join(map(str, seq[:16])), f"{0xD6B5:016b}")  # MSB first
        np.testing.assert_array_equal(whitening.m17_randomizer(800)[368:736], seq)  # restarts after byte 45
        self.assertEqual(int(seq[-8:].dot(1 << np.arange(7, -1, -1))), 0xC3)

    def test_qpp_interleaver_matches_appendix_f(self):
        table = interleave.table("m17_payload")
        self.assertEqual(table.tolist(), vec.M17_QPP_SPEC)
        self.assertEqual(sorted(table.tolist()), list(range(368)))
        self.assertEqual((int(table[1]), int(table[2]), int(table[92]), int(table[137])), (137, 90, 92, 1))

    def test_puncture_patterns_match_appendix_e(self):
        self.assertEqual(len(viterbi.M17_P1), 61)
        self.assertEqual(sum(viterbi.M17_P1), 46)
        self.assertEqual(viterbi.M17_P1[:13], (1, 1, 0, 1, 1, 1, 0, 1, 1, 1, 0, 1, 1))  # printed start of P1
        self.assertEqual(viterbi.M17_P2, (1,) * 11 + (0,))
        self.assertEqual(viterbi.M17_P3, (1, 1, 1, 1, 1, 1, 1, 0))
        # LSF: 240 data + 4 tail bits -> 488 coded; the 61-entry pattern is used 8 times -> 368 bits.
        self.assertEqual(len(viterbi.conv_encode(viterbi.M17_K5_R12_P1, np.zeros(240, np.uint8))), 368)
        # stream frame: 296 coded bits -> 272 (P2 drops every 12th bit)
        self.assertEqual(len(viterbi.conv_encode(viterbi.M17_K5_R12_P2, np.zeros(144, np.uint8))), 272)

    def test_interleave_then_randomize_roundtrips(self):
        rng = np.random.default_rng(5)
        payload = rng.integers(0, 2, 368).astype(np.uint8)
        table = interleave.table("m17_payload")
        tx = interleave.interleave(payload, table) ^ whitening.m17_randomizer(368)
        rx = interleave.deinterleave(tx ^ whitening.m17_randomizer(368), table)
        np.testing.assert_array_equal(rx, payload)


class DstarTests(unittest.TestCase):
    def test_code_matches_the_jarl_spec(self):
        code = viterbi.DSTAR_K3_R12
        self.assertEqual((code.constraint_length, code.tail_bits), (3, 2))
        self.assertEqual(code.polys, (0b111, 0b101))  # G1 = 1 + D + D^2, G2 = 1 + D^2

    def test_header_interleave_matches_the_660_bit_matrix(self):
        table = interleave.table("dstar_header")
        self.assertEqual(len(table), 660)
        x = np.arange(660)
        tx = interleave.interleave(x, table)
        # transmit order is row 0 across (0, 24, 48, ...), then row 1 (1, 25, ...)
        self.assertEqual(tx[:5].tolist(), [0, 24, 48, 72, 96])
        self.assertEqual(tx[28:31].tolist(), [1, 25, 49])
        # rows 12..23 have only 27 entries (660 = 24 * 27.5)
        self.assertEqual(tx[12 * 28 : 12 * 28 + 3].tolist(), [12, 36, 60])
        np.testing.assert_array_equal(interleave.deinterleave(tx, table), x)

    def test_scrambler_period_and_start(self):
        seq = whitening.dstar_scrambler(254)
        np.testing.assert_array_equal(seq[:127], seq[127:])  # period 2^7 - 1
        self.assertEqual(int(seq[:127].sum()), 64)  # maximal-length sequence: 64 ones
        # Sequence from S(x) = x^7 + x^4 + 1 seeded with ones, first byte LSB-first = 0x70.
        first = sum(int(b) << i for i, b in enumerate(seq[:8]))
        self.assertEqual(first, 0x70)


class WhiteningTests(unittest.TestCase):
    def test_lfsr_helper_unchanged(self):
        a = whitening.lfsr_sequence(511, (9, 5), 0x1FF, 9)
        self.assertEqual(int(a.sum()), 256)

    def test_nxdn_sequence_is_the_maximal_length_x9_x4_1_sequence(self):
        seq = whitening.nxdn_scramble_sequence(1022)
        np.testing.assert_array_equal(seq[:511], seq[511:])
        self.assertEqual(int(seq[:511].sum()), 256)
        self.assertEqual(seq[:9].tolist(), [0, 0, 1, 0, 0, 1, 1, 1, 0])  # preset 0 1 1 1 0 0 1 0 0, S0 first
        self.assertEqual(whitening.nxdn_scramble_sequence(0).size, 0)
        np.testing.assert_array_equal(whitening.nxdn_scramble_sequence(5), seq[:5])

    def test_p25p2_scramble_sequence(self):
        seq = whitening.p25p2_scramble_sequence(0xBEE00, 0x1C7, 0x013)
        self.assertEqual(seq.size, 4320)
        # the register is loaded MSB first, so the first 44 outputs are the seed itself
        self.assertEqual(int("".join(map(str, seq[:44])), 2), 0xBEE001C7013)
        np.testing.assert_array_equal(whitening.p25p2_scramble_sequence(0xBEE00, 0x1C7, 0x013, 100), seq[:100])
        self.assertFalse(np.array_equal(seq, whitening.p25p2_scramble_sequence(0xBEE00, 0x1C7, 0x014)))
        self.assertEqual(whitening.p25p2_scramble_sequence(0, 0, 0, 44).tolist(), [1] * 44)  # zero seed -> all ones
        self.assertGreater(int(seq.sum()), 1900)
        self.assertLess(int(seq.sum()), 2420)

    def test_sequences_without_a_readable_public_spec_refuse_to_guess(self):
        with self.assertRaises(NotImplementedError):
            whitening.ysf_whitening(100)


class VectorDataTests(unittest.TestCase):
    def test_vector_module_is_complete(self):
        self.assertEqual(set(vec.NXDN_VECTORS), set(NXDN_CHANNELS))
        for rows in vec.NXDN_VECTORS.values():
            self.assertGreater(len(rows), 0)
        self.assertEqual(len(vec.BPTC_IDLE_INFO), 96)
        self.assertEqual(len(vec.BPTC_IDLE_MATRIX), 13)
        self.assertEqual(len(vec.BPTC_B3_NAMES), 196)
        self.assertEqual(len(vec.M17_RANDOMIZER_SPEC), 46)
        self.assertEqual(len(vec.M17_QPP_SPEC), 368)


if __name__ == "__main__":
    unittest.main()
