"""P25 Phase 1 IMBE voice code words (TIA-102.BAAA-A 5.3) (WP-C)."""

import unittest

import numpy as np

from sigsense_api.dvoice.protocols.p25p1 import voice


class PnSequenceTests(unittest.TestCase):
    def test_mask_widths_and_first_bits_match_the_recurrence(self):
        masks = voice.pn_masks(0xABC)
        self.assertEqual([m.bit_length() <= w for m, w in zip(masks, (23, 23, 23, 15, 15, 15))], [True] * 6)
        # first PN bit by hand: p0 = 16*u0, p1 = (173*p0 + 13849) mod 65536, bit 15 of p1 leads m1
        p0 = 16 * 0xABC
        p1 = (173 * p0 + 13849) % 65536
        self.assertEqual((masks[0] >> 22) & 1, p1 >> 15)

    def test_u0_zero_gives_nonzero_masks(self):
        self.assertTrue(any(voice.pn_masks(0)))


class VoiceFrameTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(21)

    def test_clean_roundtrip(self):
        for _ in range(100):
            bits = self.rng.integers(0, 2, 88).astype(np.uint8)
            frame = voice.encode_voice_frame(bits)
            self.assertEqual(frame.size, 144)
            r = voice.decode_voice_frame(frame)
            self.assertTrue((r.bits == bits).all())
            self.assertEqual((r.errors, r.erased), (0, False))

    def test_corrects_up_to_three_errors_per_golay_word_and_one_per_hamming_word(self):
        from sigsense_api.dvoice.fec import interleave

        perm = interleave.table("imbe7200x4400_frame")  # rx index holding codeword bit j is perm[j]
        offsets = (0, 23, 46, 69, 92, 107, 122)
        widths = (23, 23, 23, 23, 15, 15, 15)
        for _ in range(40):
            bits = self.rng.integers(0, 2, 88).astype(np.uint8)
            frame = voice.encode_voice_frame(bits)
            for word in range(7):
                limit = 3 if word < 4 else 1
                for j in self.rng.choice(widths[word], limit, replace=False):
                    frame[perm[offsets[word] + j]] ^= 1
            r = voice.decode_voice_frame(frame)
            self.assertTrue((r.bits == bits).all())
            self.assertEqual(r.errors, 3 * 4 + 3)

    def test_unprotected_bits_pass_errors_through(self):
        from sigsense_api.dvoice.fec import interleave

        perm = interleave.table("imbe7200x4400_frame")
        bits = self.rng.integers(0, 2, 88).astype(np.uint8)
        frame = voice.encode_voice_frame(bits)
        frame[perm[137]] ^= 1  # a bit of c7 (uncoded)
        r = voice.decode_voice_frame(frame)
        self.assertEqual(int((r.bits != bits).sum()), 1)
        self.assertEqual(r.errors, 0)

    def test_heavy_damage_is_flagged_erased(self):
        flagged = 0
        for _ in range(100):
            frame = voice.encode_voice_frame(self.rng.integers(0, 2, 88).astype(np.uint8))
            frame ^= (self.rng.random(144) < 0.30).astype(np.uint8)
            flagged += voice.decode_voice_frame(frame).erased
        self.assertGreater(flagged, 80)

    def test_wrong_size_rejected(self):
        with self.assertRaises(ValueError):
            voice.decode_voice_frame(np.zeros(100, dtype=np.uint8))
        with self.assertRaises(ValueError):
            voice.encode_voice_frame(np.zeros(87, dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
