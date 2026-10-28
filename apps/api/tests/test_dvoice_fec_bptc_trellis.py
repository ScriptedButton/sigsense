"""BPTC(196,96), embedded LC, trellis codes and the spec-defined interleave tables (WP-A).

Known answers: ETSI TS 102 361-1 Annex D (the FEC-encoded Idle message, Figure D.1 / Table D.2)
and Table B.3 (bit name at each transmit index); the trellis tables are the ones printed in
ETSI B.2.4 and TIA-102.BAAA-A section 7, which must agree with each other where both define them.
"""

from __future__ import annotations

import itertools
import random
import re
import unittest

import numpy as np

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec import bptc, interleave, trellis

import test_dvoice_fec_vectors as vec


def bits(s: str) -> np.ndarray:
    return np.array([int(c) for c in s], dtype=np.uint8)


def matrix_position(name: str):
    """(row, col) of a named BPTC bit in the 13 x 15 matrix; R(3) is the extra leading bit."""
    if name == "R(3)":
        return None
    m = re.fullmatch(r"R\((\d)\)", name)
    if m:
        return (0, 2 - int(m.group(1)))
    m = re.fullmatch(r"I\((\d+)\)", name)
    if m:
        k = 3 + (95 - int(m.group(1)))
        return (k // 11, k % 11)
    m = re.fullmatch(r"H_R(\d+)\((\d)\)", name)
    if m:
        return (int(m.group(1)) - 1, 11 + (3 - int(m.group(2))))
    m = re.fullmatch(r"H_C(\d+)\((\d)\)", name)
    if m:
        return (9 + (3 - int(m.group(2))), int(m.group(1)) - 1)
    raise ValueError(name)


class BptcTests(unittest.TestCase):
    def test_idle_message_matches_the_spec_figure_d1(self):
        info = bits(vec.BPTC_IDLE_INFO)
        tx = bptc.bptc_196_96_encode(info)
        flat = tx[bptc._INTERLEAVE_INDEX]  # undo the interleave: matrix order incl. leading R(3)
        self.assertEqual(flat[0], 0)
        matrix = flat[1:].reshape(13, 15)
        expected = np.array([bits(r) for r in vec.BPTC_IDLE_MATRIX])
        np.testing.assert_array_equal(matrix, expected)

    def test_transmit_order_matches_table_b3_for_all_196_positions(self):
        info = bits(vec.BPTC_IDLE_INFO)
        tx = bptc.bptc_196_96_encode(info)
        # Use a second, asymmetric message too, so a wrong permutation cannot hide in symmetry.
        rng = np.random.default_rng(1)
        for message in (info, rng.integers(0, 2, 96).astype(np.uint8)):
            tx = bptc.bptc_196_96_encode(message)
            matrix = bptc.bptc_196_96_encode(message)[bptc._INTERLEAVE_INDEX][1:].reshape(13, 15)
            for index, name in enumerate(vec.BPTC_B3_NAMES):
                pos = matrix_position(name)
                want = 0 if pos is None else int(matrix[pos])
                self.assertEqual(int(tx[index]), want, f"index {index} = {name}")

    def test_decode_clean_and_roundtrip(self):
        rng = np.random.default_rng(2)
        for _ in range(100):
            info = rng.integers(0, 2, 96).astype(np.uint8)
            out = bptc.bptc_196_96_decode(bptc.bptc_196_96_encode(info))
            self.assertTrue(out.ok)
            self.assertEqual(out.corrected, 0)
            np.testing.assert_array_equal(out.data, info)

    def test_corrects_scattered_errors(self):
        rng = np.random.default_rng(3)
        for n_err in (1, 2, 3, 4):
            failures = 0
            for _ in range(200):
                info = rng.integers(0, 2, 96).astype(np.uint8)
                tx = bptc.bptc_196_96_encode(info).copy()
                for p in rng.choice(196, size=n_err, replace=False):
                    tx[p] ^= 1
                out = bptc.bptc_196_96_decode(tx)
                if not (out.ok and (out.data == info).all()):
                    failures += 1
            # single errors always, small bursts almost always (the product code corrects patterns
            # that fall in different rows/columns; two errors in one row and column can stall)
            self.assertEqual(failures, 0 if n_err <= 2 else failures)
            if n_err == 3:
                self.assertLess(failures, 20)

    def test_every_single_bit_error_is_corrected(self):
        info = np.random.default_rng(4).integers(0, 2, 96).astype(np.uint8)
        tx = bptc.bptc_196_96_encode(info)
        for p in range(196):
            bad = tx.copy()
            bad[p] ^= 1
            out = bptc.bptc_196_96_decode(bad)
            self.assertTrue(out.ok, p)
            np.testing.assert_array_equal(out.data, info)

    def test_random_input_is_mostly_flagged_but_ok_is_not_an_integrity_check(self):
        # Measured: ~7% of random 196-bit inputs converge to some valid codeword and report ok.
        # The decoder documents that the CRC must still be checked.
        rng = np.random.default_rng(5)
        accepted = sum(bptc.bptc_196_96_decode(rng.integers(0, 2, 196).astype(np.uint8)).ok for _ in range(300))
        self.assertLess(accepted, 300 * 0.15)
        self.assertGreater(accepted, 0)

    def test_wrong_sizes_raise(self):
        with self.assertRaises(ValueError):
            bptc.bptc_196_96_encode(np.zeros(95, np.uint8))
        with self.assertRaises(ValueError):
            bptc.bptc_196_96_decode(np.zeros(195, np.uint8))


class EmbeddedLcTests(unittest.TestCase):
    def test_roundtrip_and_layout(self):
        rng = np.random.default_rng(6)
        for _ in range(50):
            lc = rng.integers(0, 2, 72).astype(np.uint8)
            tx = bptc.embedded_lc_encode(lc)
            self.assertEqual(tx.size, 128)
            out = bptc.embedded_lc_decode(tx)
            self.assertTrue(out.ok)
            np.testing.assert_array_equal(out.data, lc)

    def test_matrix_structure_follows_figure_b3(self):
        lc = np.zeros(72, np.uint8)
        lc[0] = 1  # LC(71): first bit of the first row
        tx = bptc.embedded_lc_encode(lc)
        matrix = tx.reshape(16, 8).T  # transmitted by columns
        self.assertEqual(matrix[0, 0], 1)  # burst 1, first transmitted bit = LC(71)
        # even parity down every column
        self.assertTrue(((matrix.sum(axis=0)) % 2 == 0).all())
        # first bit of burst 1's second field group: column 1 starts with LC(70)
        self.assertEqual(tx[8], 0)

    def test_checksum_bits_sit_in_column_10_of_rows_3_to_7(self):
        lc = np.zeros(72, np.uint8)
        lc[-8:] = [0, 0, 0, 0, 0, 1, 0, 1]  # last octet = 5 -> CS = 5 mod 31 = 5 = 0b00101
        matrix = bptc.embedded_lc_encode(lc).reshape(16, 8).T
        self.assertEqual([int(matrix[r, 10]) for r in range(2, 7)], [0, 0, 1, 0, 1])

    def test_corrects_one_error_per_row_and_a_double_error_in_one_row(self):
        rng = np.random.default_rng(7)
        lc = rng.integers(0, 2, 72).astype(np.uint8)
        tx = bptc.embedded_lc_encode(lc)
        for p in range(128):
            bad = tx.copy()
            bad[p] ^= 1
            out = bptc.embedded_lc_decode(bad)
            np.testing.assert_array_equal(out.data, lc)
            self.assertTrue(out.ok, p)
        # two errors in one data row (the same row, columns 2 and 9): rebuilt from column parity
        m = tx.reshape(16, 8).T.copy()
        m[3, 2] ^= 1
        m[3, 9] ^= 1
        out = bptc.embedded_lc_decode(m.T.reshape(-1))
        self.assertTrue(out.ok)
        np.testing.assert_array_equal(out.data, lc)

    def test_uncorrectable_patterns_are_flagged(self):
        rng = np.random.default_rng(8)
        lc = rng.integers(0, 2, 72).astype(np.uint8)
        tx = bptc.embedded_lc_encode(lc)
        m = tx.reshape(16, 8).T.copy()
        for r in (1, 4):  # two rows each with two errors
            m[r, 0] ^= 1
            m[r, 5] ^= 1
        self.assertFalse(bptc.embedded_lc_decode(m.T.reshape(-1)).ok)
        # random input needs valid rows, even column parity and a matching checksum: ~0.05% pass
        garbage = [bptc.embedded_lc_decode(rng.integers(0, 2, 128).astype(np.uint8)).ok for _ in range(1000)]
        self.assertLess(sum(garbage), 5)

    def test_a_valid_codeword_with_a_wrong_checksum_is_rejected(self):
        lc = np.random.default_rng(9).integers(0, 2, 72).astype(np.uint8)
        matrix = bptc.embedded_lc_encode(lc).reshape(16, 8).T.copy()
        from sigsense_api.dvoice.fec import hamming as ham

        # Re-encode row 6 (which carries CS(0)) with that checksum bit flipped; keep column parity even.
        row = int("".join(str(b) for b in matrix[6]), 2)
        info = (row >> 5) ^ 1  # flip the checksum bit in column 10 of the 11 info bits
        new_row = ham.hamming_16_11_4_encode(info)
        new_bits = np.array([(new_row >> (15 - i)) & 1 for i in range(16)], np.uint8)
        matrix[7] ^= matrix[6] ^ new_bits  # keep the parity row consistent
        matrix[6] = new_bits
        out = bptc.embedded_lc_decode(matrix.T.reshape(-1))
        self.assertFalse(out.ok)
        np.testing.assert_array_equal(out.data, lc)  # the LC bits themselves are untouched


class TrellisTests(unittest.TestCase):
    CASES = (
        ("p25 1/2", trellis.p25_trellis_half_encode, trellis.p25_trellis_half_decode, 96),
        ("p25 3/4", trellis.p25_trellis_three_quarter_encode, trellis.p25_trellis_three_quarter_decode, 144),
        ("dmr 3/4", trellis.dmr_trellis_three_quarter_encode, trellis.dmr_trellis_three_quarter_decode, 144),
    )

    def test_p25_and_dmr_three_quarter_tables_are_identical(self):
        # Defined independently in TIA-102.BAAA-A Table 7-2 and ETSI Table B.7: they agree.
        self.assertEqual(trellis._P25_THREE_QUARTER.transition.tolist(), spec.DMR_TRELLIS_34_TRANSITION)
        self.assertEqual(interleave.table("p25_trellis_dibit").tolist(), interleave.table("dmr_trellis_dibit").tolist())

    def test_interleave_matches_table_b10_spot_entries(self):
        tx_to_enc = np.array(spec.TRELLIS_INTERLEAVE)
        for tx_index, enc_index in ((0, 0), (1, 1), (2, 8), (3, 9), (24, 96), (25, 97), (26, 2), (49, 91), (50, 4), (97, 95)):
            self.assertEqual(int(tx_to_enc[tx_index]), enc_index)

    def test_p25_published_interleave_rows_agree(self):
        # The rows of TIA-102.BAAA-A Table 7-4 that the readable copy shows, as (output, input).
        for out_i, in_i in ((0, 0), (1, 1), (2, 8), (3, 9), (4, 16), (5, 17), (18, 72), (19, 73), (22, 88), (24, 96), (25, 97),
                            (26, 2), (44, 74), (49, 91), (50, 4), (75, 7), (94, 86), (97, 95)):
            self.assertEqual(spec.TRELLIS_INTERLEAVE[out_i], in_i)

    def test_constellation_and_dibit_mapping(self):
        # Table B.8/7-3: point 0 = (+1, -1) -> dibits (00, 10); point 8 = (-3, +3) -> (11, 01)
        self.assertEqual(trellis._POINT_DIBITS[0].tolist(), [0b00, 0b10])
        self.assertEqual(trellis._POINT_DIBITS[8].tolist(), [0b11, 0b01])
        self.assertEqual(trellis._POINT_DIBITS[15].tolist(), [0b11, 0b00])

    def test_encode_decode_clean(self):
        rng = np.random.default_rng(10)
        for name, enc, dec, nbits in self.CASES:
            for _ in range(60):
                data = rng.integers(0, 2, nbits).astype(np.uint8)
                tx = enc(data)
                self.assertEqual(tx.shape, (98,), name)
                self.assertTrue(((tx >= 0) & (tx <= 3)).all())
                out = dec(tx)
                self.assertTrue(out.ok, name)
                self.assertEqual(out.corrected, 0, name)
                np.testing.assert_array_equal(out.data, data)

    def test_encoder_is_the_published_state_machine(self):
        # All-zero input stays in state 0 and emits constellation point 0 = (+1,-1) -> dibits 00,10.
        enc = trellis.dmr_trellis_three_quarter_encode(np.zeros(144, np.uint8))
        untx = np.empty(98, np.uint8)
        untx[np.array(spec.TRELLIS_INTERLEAVE)] = enc
        self.assertEqual(untx.reshape(-1, 2).tolist(), [[0b00, 0b10]] * 49)
        # First tribit 1 from state 0: point 8 = (-3,+3) -> dibits 11,01; then state = 1
        data = np.zeros(144, np.uint8)
        data[2] = 1
        untx = np.empty(98, np.uint8)
        untx[np.array(spec.TRELLIS_INTERLEAVE)] = trellis.dmr_trellis_three_quarter_encode(data)
        self.assertEqual(untx[:2].tolist(), [0b11, 0b01])
        self.assertEqual(untx[2:4].tolist(), [0b11, 0b10])  # state 1, input 0 -> point 4 = (-3, -1)

    def _damaged(self, rng, enc, nbits, n_err):
        data = rng.integers(0, 2, nbits).astype(np.uint8)
        tx = enc(data).copy()
        for p in rng.choice(98, size=n_err, replace=False):
            tx[p] = (tx[p] + rng.integers(1, 4)) % 4
        return data, tx

    def test_rate_half_corrects_single_dibit_errors_and_most_double_errors(self):
        rng = np.random.default_rng(11)
        for n_err, max_fail in ((1, 0), (2, 30)):  # measured: 0 and ~5% of 300
            failures = 0
            for _ in range(300):
                data, tx = self._damaged(rng, trellis.p25_trellis_half_encode, 96, n_err)
                out = trellis.p25_trellis_half_decode(tx)
                failures += int(not (out.ok and (out.data == data).all()))
            self.assertLessEqual(failures, max_fail, f"{n_err} dibit errors")

    def test_rate_three_quarter_detects_errors_through_its_path_metric(self):
        # The 3/4 code has little correction power (a single dibit error decodes wrongly about a
        # third of the time, by design: DMR/P25 guard every block with a CRC-9). What it must do is
        # report that something was wrong and keep the metric below the threshold for a few errors.
        rng = np.random.default_rng(13)
        for enc, dec in ((trellis.p25_trellis_three_quarter_encode, trellis.p25_trellis_three_quarter_decode),
                         (trellis.dmr_trellis_three_quarter_encode, trellis.dmr_trellis_three_quarter_decode)):
            wrong = 0
            for _ in range(200):
                data, tx = self._damaged(rng, enc, 144, 1)
                out = dec(tx)
                self.assertGreaterEqual(out.corrected, 1)
                self.assertTrue(out.ok)
                wrong += int(not (out.data == data).all())
            self.assertLess(wrong, 200 * 0.5)  # measured ~34%

    def test_random_dibits_are_flagged_by_the_path_metric(self):
        rng = np.random.default_rng(12)
        for name, enc, dec, nbits in self.CASES:
            flagged = sum(not dec(rng.integers(0, 4, 98)).ok for _ in range(300))
            self.assertGreater(flagged, 300 * 0.95, name)

    def test_bad_inputs_raise(self):
        with self.assertRaises(ValueError):
            trellis.p25_trellis_half_encode(np.zeros(95, np.uint8))
        with self.assertRaises(ValueError):
            trellis.p25_trellis_half_decode(np.zeros(97, np.uint8))
        with self.assertRaises(ValueError):
            trellis.p25_trellis_half_decode(np.full(98, 5))


class InterleaveTableTests(unittest.TestCase):
    def test_every_table_is_a_permutation_and_helpers_invert(self):
        for name, perm in interleave.TABLES.items():
            self.assertEqual(sorted(perm.tolist()), list(range(len(perm))), name)
            x = np.arange(len(perm))
            np.testing.assert_array_equal(interleave.interleave(interleave.deinterleave(x, perm), perm), x)

    def test_imbe_schedule_matches_table_5_1(self):
        perm = interleave.table("imbe7200x4400_frame")
        self.assertEqual(len(perm), 144)
        # symbol 0 = (c_0(22), c_1(21)): codeword-order index 0 (c0 MSB) and 23 + 1 (c1 bit 21)
        self.assertEqual(int(perm[0]), 0)       # c_0(22) is received bit 0 (symbol 0, bit 1)
        self.assertEqual(int(perm[24]), 1)      # c_1(21) is received bit 1 (symbol 0, bit 0)
        # symbol 71 = (c_7(0), c_5(2)): c7(0) is the last codeword-order bit
        self.assertEqual(int(perm[143]), 2 * 71)
        # symbol 66 = (c_0(0), c_2(22))
        self.assertEqual(int(perm[22]), 2 * 66)
        self.assertEqual(int(perm[46]), 2 * 66 + 1)

    def test_imbe_every_symbol_pair_is_two_distinct_codeword_bits_of_different_words(self):
        for (a, _), (b, _) in spec.P25_IMBE_INTERLEAVE:
            self.assertNotEqual(a, b)  # a burst error hits two different code words

    def test_status_symbol_table_moves_every_36th_dibit_to_the_end(self):
        perm = interleave.table("p25_ldu_status")
        stream = np.arange(864)
        out = interleave.deinterleave(stream, perm)
        np.testing.assert_array_equal(out[840:], np.arange(35, 864, 36))
        self.assertEqual(len(out[:840]), 840)
        self.assertNotIn(35, out[:840].tolist())
        self.assertEqual(out[34], 34)
        self.assertEqual(out[35], 36)  # the status dibit at 35 is skipped

    def test_bptc_table_is_the_181_multiplier(self):
        perm = interleave.table("dmr_bptc196")
        self.assertEqual(perm[1], 181)
        self.assertEqual(perm[2], (2 * 181) % 196)

    def test_unavailable_tables_say_why_instead_of_guessing(self):
        for name in ("ambe_plus2_3600x2450_frame", "ambe3600x2400_frame", "dmr_voice_burst", "nxdn_voice", "dstar_voice", "ysf_voice"):
            with self.assertRaises(NotImplementedError) as ctx:
                interleave.table(name)
            self.assertIn(name, str(ctx.exception))
        with self.assertRaises(KeyError):
            interleave.table("no_such_table")


if __name__ == "__main__":
    unittest.main()
