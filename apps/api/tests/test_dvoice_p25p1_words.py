"""P25 Phase 1 embedded words: LC, ES, header, TDULC, low speed data (WP-C)."""

import unittest

import numpy as np

from sigsense_api.dvoice.protocols.p25p1 import words as w


class LowSpeedDataTests(unittest.TestCase):
    def test_spec_example_letter_a(self):
        # TIA-102.BAAA-A 5.6: "A" = $41 encodes to $411E
        self.assertEqual(w.lsd_encode(0x41), 0x411E)

    def test_all_octets_roundtrip_and_correct_two_errors(self):
        for x in range(256):
            word = w.lsd_encode(x)
            self.assertEqual(w.lsd_decode(word), (x, 0))
            self.assertEqual(w.lsd_decode(word ^ (1 << 3) ^ (1 << 12)), (x, 2))
            self.assertEqual(w.lsd_decode(word ^ (1 << 15)), (x, 1))

    def test_distance_five_detects_some_triple_errors(self):
        word = w.lsd_encode(0x5A)
        results = [w.lsd_decode(word ^ (1 << a) ^ (1 << b) ^ (1 << c)) for a, b, c in [(0, 5, 11), (1, 7, 14), (2, 9, 15)]]
        self.assertTrue(all(r is None or r[0] != 0x5A for r in results))  # never silently right for the wrong reason


class LinkControlTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(11)
        self.lc = w.build_lc(lco=w.LCO_GROUP_VOICE, service_options=0x80 | 0x03, target_id=0x04D2, source_id=0x123456)

    def test_group_voice_fields(self):
        r = w.decode_lc_hamming(w.encode_lc_hamming(self.lc))
        self.assertTrue(r.ok)
        lc = w.parse_lc(r.data)
        self.assertEqual((lc.kind, lc.target_id, lc.source_id), ("group", 1234, 0x123456))
        self.assertTrue(lc.service.emergency)
        self.assertEqual(lc.service.priority, 3)
        self.assertFalse(lc.service.protected)

    def test_unit_to_unit_fields(self):
        lc = w.build_lc(lco=w.LCO_UNIT_VOICE, service_options=0x40, target_id=0xAAAAAA, source_id=0x555555)
        p = w.parse_lc(w.decode_lc_hamming(w.encode_lc_hamming(lc)).data)
        self.assertEqual((p.kind, p.target_id, p.source_id), ("private", 0xAAAAAA, 0x555555))
        self.assertTrue(p.service.protected)

    def test_unknown_format_keeps_raw_octets(self):
        raw = np.unpackbits(np.frombuffer(bytes(range(1, 10)), dtype=np.uint8))
        p = w.parse_lc(raw)
        self.assertEqual(p.kind, "other")
        self.assertEqual(p.raw, bytes(range(1, 10)).hex())

    def test_manufacturer_specific_is_not_interpreted(self):
        lc = w.build_lc(lco=w.LCO_GROUP_VOICE, mfid=0x90, service_options=0, target_id=1, source_id=2)
        p = w.parse_lc(lc)
        self.assertEqual(p.kind, "other")
        self.assertFalse(p.standard)
        self.assertIsNone(p.target_id)

    def test_protected_lc_fields_are_not_trusted(self):
        lc = w.build_lc(lco=w.LCO_GROUP_VOICE, protected=True, target_id=1, source_id=2)
        self.assertEqual(w.parse_lc(lc).kind, "other")

    def test_corrects_one_bit_error_in_every_inner_word(self):
        enc = w.encode_lc_hamming(self.lc)
        bad = enc.copy()
        for i in range(24):
            bad[10 * i + int(self.rng.integers(0, 10))] ^= 1
        r = w.decode_lc_hamming(bad)
        self.assertTrue(r.ok)
        self.assertTrue((r.data == self.lc).all())
        self.assertEqual(r.corrected, 24)

    def test_double_errors_in_inner_words_become_rs_erasures(self):
        enc = w.encode_lc_hamming(self.lc)
        bad = enc.copy()
        for i in (2, 9, 17, 21):  # four inner words wrecked: RS(24,12) takes up to 12 erasures
            bad[10 * i : 10 * i + 10] ^= np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=np.uint8)
        r = w.decode_lc_hamming(bad)
        self.assertTrue(r.ok)
        self.assertTrue((r.data == self.lc).all())

    def test_heavy_damage_is_reported_not_returned_as_valid(self):
        bad = self.rng.integers(0, 2, 240).astype(np.uint8)
        results = [w.decode_lc_hamming(self.rng.integers(0, 2, 240).astype(np.uint8)) for _ in range(300)]
        accepted = sum(1 for r in results if r.ok)
        self.assertLess(accepted, 15)  # random input must (almost) never pass as a valid LC
        self.assertEqual(bad.size, 240)


class EncryptionSyncTests(unittest.TestCase):
    def test_roundtrip_and_names(self):
        es = w.build_es(mi=bytes(range(9)), algid=0x84, kid=0x1234)
        r = w.decode_es(w.encode_es(es))
        self.assertTrue(r.ok)
        p = w.parse_es(r.data)
        self.assertEqual((p.algid, p.kid, p.mi), (0x84, 0x1234, bytes(range(9))))
        self.assertFalse(p.clear)
        self.assertEqual(w.algorithm_name(0x84), "AES-256")
        self.assertEqual(w.algorithm_name(0x7E), "algorithm 0x7E")
        self.assertTrue(w.parse_es(w.build_es()).clear)

    def test_corrects_errors(self):
        es = w.build_es(mi=bytes(range(9)), algid=0x81, kid=7)
        enc = w.encode_es(es)
        enc[[5, 77, 140, 203]] ^= 1
        r = w.decode_es(enc)
        self.assertTrue(r.ok and (r.data == es).all())


class HeaderWordTests(unittest.TestCase):
    def test_roundtrip(self):
        h = w.build_header(mi=bytes(range(9)), mfid=0, algid=0x80, kid=0, tgid=777)
        enc = w.encode_header(h)
        self.assertEqual(enc.size, 648)
        r = w.decode_header(enc)
        p = w.parse_header(r.data)
        self.assertTrue(r.ok and p.tgid == 777 and p.clear)

    def test_corrects_errors_in_many_words(self):
        h = w.build_header(mi=bytes(range(9)), algid=0xAA, kid=0x55, tgid=4321)
        enc = w.encode_header(h)
        rng = np.random.default_rng(5)
        for word in range(0, 36, 2):  # three errors in every other Golay word
            for pos in rng.choice(18, 3, replace=False):
                enc[18 * word + pos] ^= 1
        r = w.decode_header(enc)
        self.assertTrue(r.ok and (r.data == h).all())


class TerminatorLinkControlTests(unittest.TestCase):
    def test_roundtrip_and_errors(self):
        lc = w.build_lc(lco=w.LCO_GROUP_VOICE, service_options=0x80, target_id=99, source_id=1000)
        enc = w.encode_lc_golay(lc)
        self.assertEqual(enc.size, 288)
        enc[[3, 40, 100, 150, 200, 270]] ^= 1
        r = w.decode_lc_golay(enc)
        self.assertTrue(r.ok and (r.data == lc).all())
        self.assertEqual(w.parse_lc(r.data).target_id, 99)


if __name__ == "__main__":
    unittest.main()
