"""Reed-Solomon tests (WP-A). References: TIA-102.BAAA-A 5.9 (GF(2^6), generator polynomials in
octal), ETSI TS 102 361-1 B.3.6 (RS(12,9) over GF(2^8), generator matrix and exp table)."""

from __future__ import annotations

import random
import unittest

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec import reed_solomon as rs


class SpecConformanceTests(unittest.TestCase):
    def test_p25_generator_polynomials_match_the_spec(self):
        for name, factory in (
            ("hdr_36_20", rs.rs_p25_36_20_17),
            ("lc_24_12", rs.rs_p25_24_12_13),
            ("es_24_16", rs.rs_p25_24_16_9),
        ):
            self.assertEqual(factory().generator, spec.P25_RS_GENERATORS_OCTAL[name], name)

    def test_p25_field_is_gf64_with_alpha6_plus_alpha_plus_1(self):
        gf = rs.rs_p25_24_12_13().gf
        # spec examples: alpha^6 = alpha + 1 = 0o03; alpha^57 = 0o76; alpha^58 = 0o77
        self.assertEqual(gf.exp[6], 0o03)
        self.assertEqual(gf.exp[57], 0o76)
        self.assertEqual(gf.exp[58], 0o77)

    def test_dmr_field_and_generator_match_the_spec(self):
        code = rs.rs_dmr_12_9_4()
        self.assertEqual(code.gf.exp[:255], spec.DMR_GF256_EXP)  # Table B.19
        self.assertEqual(code.generator, [0x40, 0x38, 0x0E, 0x01])  # g(x) = x^3 + 0e x^2 + 38 x + 40

    def test_dmr_parity_columns_match_table_b18(self):
        code = rs.rs_dmr_12_9_4()
        for i in range(9):
            msg = [0] * 9
            msg[i] = 1
            self.assertEqual(list(code.encode(msg)[9:]), spec.DMR_RS_12_9_PARITY[i], f"row {i}")


class CorrectionTests(unittest.TestCase):
    CODES = {
        "p25_24_12": rs.rs_p25_24_12_13,
        "p25_24_16": rs.rs_p25_24_16_9,
        "p25_36_20": rs.rs_p25_36_20_17,
        "dmr_12_9": rs.rs_dmr_12_9_4,
    }

    def test_clean_roundtrip_and_systematic_layout(self):
        rng = random.Random(1)
        for name, factory in self.CODES.items():
            code = factory()
            for _ in range(20):
                msg = [rng.randrange(code.gf.size) for _ in range(code.k)]
                cw = code.encode(msg)
                self.assertEqual(list(cw[: code.k]), msg, name)
                out = code.decode(cw)
                self.assertEqual((list(out[0]), out[1]), (list(cw), 0), name)

    def test_corrects_any_mix_of_errors_and_erasures_within_capacity(self):
        rng = random.Random(2)
        for name, factory in self.CODES.items():
            code = factory()
            r = code.nroots
            for _ in range(300):
                msg = [rng.randrange(code.gf.size) for _ in range(code.k)]
                cw = list(code.encode(msg))
                n_era = rng.randint(0, r)
                n_err = rng.randint(0, (r - n_era) // 2)
                positions = rng.sample(range(code.n), n_era + n_err)
                recv = list(cw)
                for p in positions:
                    recv[p] ^= rng.randrange(1, code.gf.size)
                got = code.decode(recv, erasures=positions[:n_era])
                self.assertIsNotNone(got, f"{name} {n_err}e+{n_era}x")
                self.assertEqual(list(got[0]), cw)
                self.assertEqual(got[1], n_err + n_era)

    def test_one_more_error_than_t_is_detected_not_silently_miscorrected(self):
        rng = random.Random(3)
        for name, factory in self.CODES.items():
            code = factory()
            t = code.nroots // 2
            wrong = 0
            trials = 300
            for _ in range(trials):
                msg = [rng.randrange(code.gf.size) for _ in range(code.k)]
                cw = list(code.encode(msg))
                recv = list(cw)
                for p in rng.sample(range(code.n), t + 1):
                    recv[p] ^= rng.randrange(1, code.gf.size)
                got = code.decode(recv)
                if got is not None and list(got[0]) != cw:
                    wrong += 1
            self.assertLess(wrong, trials * 0.02, name)  # measured: 0 for the P25 codes

    def test_more_erasures_than_parity_symbols_is_rejected(self):
        code = rs.rs_dmr_12_9_4()
        cw = list(code.encode(range(1, 10)))
        self.assertIsNone(code.decode(cw, erasures=[0, 1, 2, 3]))

    def test_erasure_of_correct_symbols_costs_nothing(self):
        code = rs.rs_p25_24_12_13()
        cw = list(code.encode(range(12)))
        got = code.decode(cw, erasures=[1, 5, 20])
        self.assertEqual((list(got[0]), got[1]), (cw, 0))

    def test_non_default_first_root_and_primitive_element(self):
        rng = random.Random(4)
        for params in ((4, 0x13, 15, 9, 0, 1), (4, 0x13, 15, 9, 3, 2), (8, 0x11D, 255, 223, 112, 11)):
            code = rs.ReedSolomon(*params)
            for _ in range(40):
                msg = [rng.randrange(code.gf.size) for _ in range(code.k)]
                cw = list(code.encode(msg))
                recv = list(cw)
                for p in rng.sample(range(code.n), code.nroots // 2):
                    recv[p] ^= rng.randrange(1, code.gf.size)
                self.assertEqual(list(code.decode(recv)[0]), cw, params)

    def test_bad_arguments_raise(self):
        code = rs.rs_dmr_12_9_4()
        with self.assertRaises(ValueError):
            code.encode([1, 2])
        with self.assertRaises(ValueError):
            code.decode([0] * 11)
        with self.assertRaises(ValueError):
            code.decode([300] + [0] * 11)  # symbol larger than the field
        with self.assertRaises(ValueError):
            rs.ReedSolomon(8, 0x11D, 300, 100)


if __name__ == "__main__":
    unittest.main()
