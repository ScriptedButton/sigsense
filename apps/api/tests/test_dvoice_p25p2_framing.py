"""P25 Phase 2 framing: CRCs, DUID, I-ISCH, MAC/ESS Reed-Solomon words, scrambler, slot geometry.

The CRC reference values are SDRTrunk's published per-bit tables (CRCP25.java): the contribution of a single
set bit to the final remainder. They are independent of the polynomial constants in fec/crc.py."""

import unittest

import numpy as np

from sigsense_api.dvoice.fec import crc
from sigsense_api.dvoice.protocols.p25p2 import framing as fr
from sigsense_api.dvoice.testing.p25p2_encoder import put


def contribution(name: str, n: int, i: int) -> int:
    bits = np.zeros(n, dtype=np.uint8)
    bits[i] = 1
    return crc.crc_compute(name, bits) ^ crc.CRC_SPECS[name].xorout


class CrcTests(unittest.TestCase):
    def test_facch_crc12_matches_published_table(self):
        self.assertEqual([contribution("crc12_p25p2", 144, i) for i in range(4)], [0x256, 0x12B, 0xCDE, 0x66F])
        self.assertEqual([contribution("crc12_p25p2", 144, i) for i in range(138, 144)],
                         [0xC5A, 0x62D, 0xF5D, 0xBE5, 0x9B9, 0x897])

    def test_sacch_and_lcch_tables(self):
        self.assertEqual([contribution("crc12_p25p2", 168, i) for i in range(3)], [0x47B, 0xE76, 0x73B])
        self.assertEqual(contribution("crc12_p25p2", 168, 167), 0x897)
        self.assertEqual([contribution("crc16_p25_dmr_header", 164, i) for i in range(3)], [0xCF76, 0x67BB, 0xBBCD])
        self.assertEqual(contribution("crc16_p25_dmr_header", 164, 163), 0x1021)


class DuidTests(unittest.TestCase):
    def test_every_duid_round_trips_and_one_error_is_corrected(self):
        for duid in range(16):
            body = np.zeros(fr.BODY_BITS, dtype=np.uint8)
            fr.write_duid(body, duid)
            self.assertEqual(fr.read_duid(body), (duid, 0))
            for p in fr.DUID_BITS:
                bad = body.copy()
                bad[p] ^= 1
                self.assertEqual(fr.read_duid(bad), (duid, 1))

    def test_two_errors_are_detected_not_guessed(self):
        body = np.zeros(fr.BODY_BITS, dtype=np.uint8)
        fr.write_duid(body, int(fr.Duid.VOICE_2))
        body[fr.DUID_BITS[0]] ^= 1
        body[fr.DUID_BITS[3]] ^= 1
        self.assertIsNone(fr.read_duid(body)[0])


class IschTests(unittest.TestCase):
    def test_minimum_distance_is_sixteen(self):
        words, _ = fr._isch_book()
        ws = [int(w) for w in words]
        self.assertEqual(min(bin(a ^ b).count("1") for i, a in enumerate(ws) for b in ws[:i]), 16)

    def test_round_trip_with_up_to_seven_bit_errors(self):
        rng = np.random.default_rng(3)
        for ch in (0, 1):
            for seq in range(3):
                for sf in range(4):
                    isch = fr.Isch(0, ch, seq, 1, sf)
                    word = fr.encode_isch(isch)
                    bits = np.array([(word >> (39 - i)) & 1 for i in range(40)], dtype=np.uint8)
                    self.assertEqual(fr.decode_isch(bits, ch), isch)
                    n = int(rng.integers(1, 8))
                    bad = bits.copy()
                    bad[rng.choice(40, n, replace=False)] ^= 1
                    got = fr.decode_isch(bad, ch)
                    self.assertEqual((got.lch_type, got.channel, got.seq, got.flag, got.superframe), (0, ch, seq, 1, sf))
                    self.assertEqual(got.errors, n)

    def test_eight_errors_are_rejected(self):
        word = fr.encode_isch(fr.Isch(0, 1, 2, 0, 3))
        bits = np.array([(word >> (39 - i)) & 1 for i in range(40)], dtype=np.uint8)
        bits[:8] ^= 1
        self.assertIsNone(fr.decode_isch(bits, 1))

    def test_other_channels_word_is_not_accepted(self):
        word = fr.encode_isch(fr.Isch(0, 0, 1, 0, 0))
        bits = np.array([(word >> (39 - i)) & 1 for i in range(40)], dtype=np.uint8)
        got = fr.decode_isch(bits, 1)
        self.assertTrue(got is None or got.errors > 7 or got.channel == 1)


class SlotGeometryTests(unittest.TestCase):
    def test_channel_ownership_and_final_fragment_swap(self):
        self.assertEqual([fr.slot_channel(p, False) for p in range(4)], [0, 1, 0, 1])
        self.assertEqual([fr.slot_channel(p, True) for p in range(4)], [0, 1, 1, 0])

    def test_each_channel_gets_six_ordinals_per_superframe(self):
        for ch in (0, 1):
            ords = sorted(fr.channel_ordinal(seq, p) for seq in range(3) for p in range(4) if fr.slot_channel(p, seq == 2) == ch)
            self.assertEqual(ords, [0, 1, 2, 3, 4, 5])

    def test_hex_positions_skip_the_duid_and_the_facch_gap(self):
        self.assertEqual(fr.HEX_POSITIONS_SACCH.size, 312)
        self.assertEqual(fr.HEX_POSITIONS_FACCH.size, 270)
        self.assertFalse(set(fr.DUID_BITS) & set(fr.HEX_POSITIONS_SACCH.tolist()))
        self.assertFalse(set(range(138, 180)) & set(fr.HEX_POSITIONS_FACCH.tolist()))
        # the sixth FACCH information hex bit group straddles the gap exactly as published
        self.assertEqual(fr.HEX_POSITIONS_FACCH[132:138].tolist(), [136, 137, 180, 181, 182, 183])


class ScrambleTests(unittest.TestCase):
    def test_segments(self):
        p = (0xBEE00, 0x1C7, 0x013)
        seg = fr.scramble_segment(p, 0)
        self.assertEqual(seg.size, 320)
        full = np.concatenate([fr.scramble_segment(p, s) for s in range(12)])
        self.assertEqual(full.size, 3840)
        self.assertFalse(np.array_equal(fr.scramble_segment(p, 0), fr.scramble_segment(p, 1)))
        self.assertFalse(np.array_equal(seg, fr.scramble_segment((0xBEE00, 0x1C7, 0x014), 0)))


class MacWordTests(unittest.TestCase):
    CHANNELS = ((fr.SACCH, 168), (fr.FACCH, 144), (fr.LCCH, 164))

    def _payload(self, size, seed):
        return np.random.default_rng(seed).integers(0, 2, size).astype(np.uint8)

    def test_round_trip_all_three_channels(self):
        for channel, cover in self.CHANNELS:
            payload = self._payload(cover, cover)
            body = np.zeros(fr.BODY_BITS, dtype=np.uint8)
            fr.encode_mac(payload, channel, body)
            word = fr.decode_mac(body, channel)
            self.assertTrue(word.crc_ok, channel.name)
            self.assertEqual(word.corrected, 0)
            np.testing.assert_array_equal(word.bits[:cover], payload)

    def test_symbol_errors_are_repaired_up_to_the_code_limit(self):
        # 28 parity symbols, 6 (SACCH) / 9 (FACCH) punctured: 11 / 9 errors correctable
        for channel, cover in self.CHANNELS:
            limit = (28 - (63 - 35 - channel.parity_hex)) // 2
            payload = self._payload(cover, 7)
            clean = np.zeros(fr.BODY_BITS, dtype=np.uint8)
            fr.encode_mac(payload, channel, clean)
            rng = np.random.default_rng(11)
            body = clean.copy()
            for g in rng.choice(len(channel.positions) // 6, limit, replace=False):
                body[channel.positions[6 * g]] ^= 1
            word = fr.decode_mac(body, channel)
            self.assertTrue(word is not None and word.crc_ok, (channel.name, limit))
            self.assertEqual(word.corrected, limit)
            np.testing.assert_array_equal(word.bits[:cover], payload)

    def test_destroyed_word_fails(self):
        payload = self._payload(168, 5)
        body = np.zeros(fr.BODY_BITS, dtype=np.uint8)
        fr.encode_mac(payload, fr.SACCH, body)
        body[fr.HEX_POSITIONS_SACCH[:240]] ^= np.random.default_rng(1).integers(0, 2, 240).astype(np.uint8)
        word = fr.decode_mac(body, fr.SACCH)
        self.assertTrue(word is None or not word.crc_ok)

    def test_crc_mismatch_is_reported(self):
        payload = self._payload(144, 9)
        body = np.zeros(fr.BODY_BITS, dtype=np.uint8)
        fr.encode_mac(payload, fr.FACCH, body)
        info = fr._to_hex(fr._from_hex(fr._to_hex(body[fr.FACCH.positions])[:26]))
        info[0] ^= 1  # alter the information then re-protect it with valid RS parity but the old CRC
        parity = fr.rs_encode_shortened(info, 19)
        body[fr.FACCH.positions] = fr._from_hex(info + parity)
        word = fr.decode_mac(body, fr.FACCH)
        self.assertFalse(word.crc_ok)


class EssTests(unittest.TestCase):
    MI = bytes(range(1, 10))

    def test_round_trip(self):
        b, a = fr.encode_ess(0x84, 0x1234, self.MI)
        ess = fr.decode_ess(b, a)
        self.assertEqual((ess.algorithm, ess.key_id, ess.mi, ess.corrected), (0x84, 0x1234, self.MI, 0))

    def test_missing_bursts_are_erasures(self):
        b, a = fr.encode_ess(0x80, 0, self.MI)
        for missing in ([0], [2], [0, 3], [1, 2, 3]):
            parts = [None if i in missing else p for i, p in enumerate(b)]
            ess = fr.decode_ess(parts, a)
            self.assertIsNotNone(ess, missing)
            self.assertEqual((ess.algorithm, ess.mi), (0x80, self.MI))

    def test_needs_the_parity_burst(self):
        b, _ = fr.encode_ess(0x80, 0, self.MI)
        self.assertIsNone(fr.decode_ess(b, None))
        self.assertIsNone(fr.decode_ess([None] * 4, fr.encode_ess(0x80, 0, self.MI)[1]))

    def test_errors_are_corrected(self):
        b, a = fr.encode_ess(0xAA, 0xBEEF, self.MI)
        bad_a = a.copy()
        bad_a[:60] ^= 1  # ten hex bits
        ess = fr.decode_ess(b, bad_a)
        self.assertEqual((ess.algorithm, ess.key_id), (0xAA, 0xBEEF))
        self.assertGreaterEqual(ess.corrected, 1)


class PutHelper(unittest.TestCase):
    def test_put(self):
        b = np.zeros(16, dtype=np.uint8)
        put(b, 4, 8, 0xA5)
        self.assertEqual("".join(map(str, b)), "0000" + "10100101" + "0000")


if __name__ == "__main__":
    unittest.main()
