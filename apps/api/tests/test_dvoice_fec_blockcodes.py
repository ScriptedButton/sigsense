"""Golay, Hamming, QR, BCH (P25 NID) tests (WP-A).

Specs used as primary references: ETSI TS 102 361-1 Annex B (DMR), TIA-102.BAAA-A
5.7-5.8 and 8.5 (P25), M17 spec appendix D. Where a spec prints a generator matrix the
code is built from it and cross-checked against an independent construction (the
generator polynomial, exhaustive encode/decode, minimum-distance and error-correction
properties); nothing here is copied from another decoder.
"""

from __future__ import annotations

import itertools
import random
import unittest

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec import bch, golay, hamming
from sigsense_api.dvoice.fec._gf import gf2_poly_mod


def weight(x: int) -> int:
    return bin(x).count("1")


def flip(word: int, n: int, positions) -> int:
    for p in positions:
        word ^= 1 << (n - 1 - p)
    return word


class GolayTests(unittest.TestCase):
    G = 0xC75  # x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1

    def test_23_12_matches_the_generator_polynomial_for_every_message(self):
        for data in range(4096):
            expect = (data << 11) | gf2_poly_mod(data << 11, self.G)
            self.assertEqual(golay.golay_23_12_encode(data), expect)

    def test_24_12_is_the_23_12_codeword_plus_overall_parity(self):
        for data in range(4096):
            w23 = golay.golay_23_12_encode(data)
            w24 = golay.golay_24_12_encode(data)
            self.assertEqual(w24 >> 1, w23)
            self.assertEqual(weight(w24) % 2, 0)

    def test_minimum_distances(self):
        w23 = {golay.golay_23_12_encode(d) for d in range(4096)}
        self.assertEqual(min(weight(w) for w in w23 if w), 7)
        self.assertEqual(min(weight(golay.golay_24_12_encode(d)) for d in range(1, 4096)), 8)
        self.assertEqual(min(weight(golay.golay_18_6_encode(d)) for d in range(1, 64)), 8)
        # ETSI B.3.1 names it (20,8,7); the matrix it prints actually has distance 8 (a shortened
        # extended Golay code), which satisfies the stated bound.
        d20 = min(weight(golay.golay_20_8_encode(d)) for d in range(1, 256))
        self.assertGreaterEqual(d20, 7)
        self.assertEqual(d20, 8)

    def test_23_12_corrects_every_pattern_up_to_three_errors(self):
        rng = random.Random(1)
        for data in (0, 0xFFF, 0x5A5, rng.randrange(4096), rng.randrange(4096)):
            word = golay.golay_23_12_encode(data)
            for k in range(4):
                for pos in itertools.combinations(range(23), k):
                    self.assertEqual(golay.golay_23_12_decode(flip(word, 23, pos)), (data, k))

    def test_24_12_corrects_three_and_detects_four_errors(self):
        word = golay.golay_24_12_encode(0xABC)
        for k in range(4):
            for pos in itertools.combinations(range(24), k):
                self.assertEqual(golay.golay_24_12_decode(flip(word, 24, pos)), (0xABC, k))
        for pos in itertools.combinations(range(24), 4):
            self.assertIsNone(golay.golay_24_12_decode(flip(word, 24, pos)))

    def test_18_6_and_20_8_roundtrip_and_correct_three(self):
        for data in range(64):
            word = golay.golay_18_6_encode(data)
            self.assertEqual(golay.golay_18_6_decode(word), (data, 0))
        for data in (0, 7, 33, 63):
            word = golay.golay_18_6_encode(data)
            for k in range(4):
                for pos in itertools.combinations(range(18), k):
                    self.assertEqual(golay.golay_18_6_decode(flip(word, 18, pos)), (data, k))
            for pos in itertools.combinations(range(18), 4):
                self.assertIsNone(golay.golay_18_6_decode(flip(word, 18, pos)))
        for data in (0, 1, 0x80, 0xA5, 0xFF):
            word = golay.golay_20_8_encode(data)
            for k in range(4):
                for pos in itertools.combinations(range(20), k):
                    self.assertEqual(golay.golay_20_8_decode(flip(word, 20, pos)), (data, k))

    def test_p25_18_6_is_the_last_six_rows_of_the_24_12_code(self):
        # TIA-102.BAAA-A 5.7: the (18,6,8) code is the (24,12,8) code with the leftmost 6 bits deleted.
        for data in range(64):
            self.assertEqual(golay.golay_24_12_encode(data), golay.golay_18_6_encode(data))

    def test_m17_golay_matrix_rows_are_the_same_code(self):
        # M17 spec D.1 prints generator rows with the same parity halves (first, second, last row).
        rows = {0: "110001110101", 1: "011000111011", 11: "100011101011"}
        for i, bits in rows.items():
            self.assertEqual(golay.golay_24_12_encode(1 << (11 - i)) & 0xFFF, int(bits, 2))


class HammingTests(unittest.TestCase):
    CODES = {
        "7_4": (hamming.hamming_7_4_encode, hamming.hamming_7_4_decode, 4, 7, 3),
        "10_6": (hamming.hamming_10_6_3_encode, hamming.hamming_10_6_3_decode, 6, 10, 3),
        "13_9": (hamming.hamming_13_9_3_encode, hamming.hamming_13_9_3_decode, 9, 13, 3),
        "15_11": (hamming.hamming_15_11_3_encode, hamming.hamming_15_11_3_decode, 11, 15, 3),
        "p25_15_11": (hamming.p25_hamming_15_11_3_encode, hamming.p25_hamming_15_11_3_decode, 11, 15, 3),
        "16_11": (hamming.hamming_16_11_4_encode, hamming.hamming_16_11_4_decode, 11, 16, 4),
        "17_12": (hamming.hamming_17_12_3_encode, hamming.hamming_17_12_3_decode, 12, 17, 3),
    }

    def test_minimum_distance_and_systematic(self):
        for name, (enc, dec, k, n, dmin) in self.CODES.items():
            words = [enc(d) for d in range(1 << k)]
            self.assertEqual(len(set(words)), 1 << k, name)
            self.assertEqual(min(weight(w) for w in words if w), dmin, name)
            for d in (0, 1, (1 << k) - 1):
                self.assertEqual(enc(d) >> (n - k), d, name)  # data bits come first

    def test_every_single_error_is_corrected_for_every_word(self):
        for name, (enc, dec, k, n, dmin) in self.CODES.items():
            for d in range(1 << k):
                word = enc(d)
                self.assertEqual(dec(word), (d, 0), name)
                for pos in range(n):
                    self.assertEqual(dec(flip(word, n, [pos])), (d, 1), f"{name} d={d} pos={pos}")

    def test_extended_hamming_detects_every_double_error(self):
        enc, dec, k, n, _ = self.CODES["16_11"]
        for d in (0, 0x155, 0x7FF):
            for pos in itertools.combinations(range(n), 2):
                self.assertIsNone(dec(flip(enc(d), n, pos)), (d, pos))

    def test_hamming_generator_matrices_match_the_dmr_spec_tables(self):
        # Spot-check the first message bit and the last against Tables B.14-B.17.
        self.assertEqual(hamming.hamming_13_9_3_encode(1 << 8) & 0xF, 0b1111)
        self.assertEqual(hamming.hamming_13_9_3_encode(1) & 0xF, 0b0011)
        self.assertEqual(hamming.hamming_15_11_3_encode(1 << 10) & 0xF, 0b1001)
        self.assertEqual(hamming.hamming_16_11_4_encode(1 << 10) & 0x1F, 0b10011)
        self.assertEqual(hamming.hamming_7_4_encode(0b1000) & 0b111, 0b101)
        self.assertEqual(hamming.p25_hamming_15_11_3_encode(1 << 10) & 0xF, 0b1111)  # TIA table 5-4 row 1

    def test_dmr_and_p25_15_11_are_different_assignments_of_one_cyclic_family(self):
        # Same distance, different parity layout: a word of one is generally not a word of the other.
        self.assertNotEqual(
            {hamming.hamming_15_11_3_encode(d) for d in range(2048)},
            {hamming.p25_hamming_15_11_3_encode(d) for d in range(2048)},
        )


class QrAndBchTests(unittest.TestCase):
    def test_qr_16_7_6(self):
        words = [bch.qr_16_7_6_encode(d) for d in range(128)]
        self.assertEqual(min(weight(w) for w in words if w), 6)
        rng = random.Random(3)
        for d in (0, 1, 0x7F, 0x2A, rng.randrange(128)):
            w = bch.qr_16_7_6_encode(d)
            for k in range(3):
                for pos in itertools.combinations(range(16), k):
                    self.assertEqual(bch.qr_16_7_6_decode(flip(w, 16, pos)), (d, k))
            for pos in itertools.combinations(range(16), 3):
                got = bch.qr_16_7_6_decode(flip(w, 16, pos))
                self.assertIsNone(got)  # d=6: three errors are detected, never mis-corrected

    def test_bch_15_7_5(self):
        words = [bch.bch_15_7_5_encode(d) for d in range(128)]
        self.assertEqual(min(weight(w) for w in words if w), 5)
        for d in (0, 0x55, 0x7F):
            w = bch.bch_15_7_5_encode(d)
            for k in range(3):
                for pos in itertools.combinations(range(15), k):
                    self.assertEqual(bch.bch_15_7_5_decode(flip(w, 15, pos)), (d, k))


class P25NidTests(unittest.TestCase):
    def test_generator_matrix_rows_match_the_spec_for_all_16_data_bits(self):
        # TIA-102.BAAA-A 8.5.2: row i = data bit i set; 48 parity columns incl. the 64th bit.
        for i in range(16):
            data = 1 << (15 - i)
            word = bch.p25_nid_encode(data >> 4, data & 0xF)
            self.assertEqual(word >> 48, data)
            self.assertEqual(word & ((1 << 48) - 1), spec.P25_NID_PARITY_ROWS_48[i], f"row {i + 1}")

    def test_64th_bit_follows_table_8_4(self):
        for duid, p in ((0b0000, 0), (0b0011, 0), (0b0101, 1), (0b1010, 1), (0b1100, 0), (0b1111, 0)):
            self.assertEqual(bch.p25_nid_encode(0x293, duid) & 1, p, bin(duid))

    def test_codewords_are_a_47th_degree_bch_code_with_distance_23(self):
        g = int(spec.P25_NID_GENERATOR_OCTAL, 8)
        self.assertEqual(g.bit_length() - 1, 47)
        self.assertEqual(weight(g), 27)  # "27 non-zero terms" per the spec
        min_w = min(weight(bch.p25_nid_encode(d >> 4, d & 15) >> 1) for d in range(1, 1 << 16))
        self.assertEqual(min_w, 23)

    def test_decode_clean_and_roundtrip(self):
        rng = random.Random(4)
        for _ in range(200):
            nac, duid = rng.randrange(4096), rng.randrange(16)
            self.assertEqual(bch.p25_nid_decode(bch.p25_nid_encode(nac, duid)), (nac, duid, 0))

    def test_corrects_up_to_eleven_errors_and_reports_the_count(self):
        rng = random.Random(5)
        for _ in range(500):
            nac, duid = rng.randrange(4096), rng.randrange(16)
            word = bch.p25_nid_encode(nac, duid)
            n = rng.randint(0, 11)
            got = bch.p25_nid_decode(flip(word, 64, rng.sample(range(64), n)))
            self.assertEqual(got, (nac, duid, n))

    def test_twelve_or_more_errors_are_almost_always_rejected(self):
        rng = random.Random(6)
        rejected = wrong = 0
        trials = 1500
        for _ in range(trials):
            nac, duid = rng.randrange(4096), rng.randrange(16)
            word = bch.p25_nid_encode(nac, duid)
            got = bch.p25_nid_decode(flip(word, 64, rng.sample(range(64), rng.randint(12, 20))))
            if got is None:
                rejected += 1
            elif got[:2] != (nac, duid):
                wrong += 1
        self.assertGreater(rejected, trials * 0.97)
        self.assertLess(wrong, trials * 0.02)  # miscorrection must stay rare (measured ~0.15%)


if __name__ == "__main__":
    unittest.main()
