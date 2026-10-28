"""M17 link layer: addresses, TYPE, LSF CRC, META, sync words, frame round trips (WP-E).

Sources (M17-Project/M17_spec, revision 2.0.0): address encoding appendix, "Dibit, Symbol, and
Frequency-shift" (byte 0xB4 -> symbols -1 -3 +3 +1), "Synchronization Burst" table, "CRC" test
vectors, "LSF" / "Stream Mode TYPE Field" / "Packet Mode LSF TYPE", GNSS layout, text-block rules.

The specification does not print a complete worked LSF or frame, so there is no published frame
vector to check against: the frame tests below are round trips against an independent encoder
(testing/m17_encoder.py) plus the published vectors for each sub-piece (callsign, CRC, sync words,
randomizer and interleaver tables, which WP-A's FEC tests pin to the spec).
"""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.fec import crc as fec_crc
from sigsense_api.dvoice.protocols.m17 import frames as fr
from sigsense_api.dvoice.protocols.m17 import lsf as L
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS
from sigsense_api.dvoice.testing import m17_encoder as enc


class AddressTests(unittest.TestCase):
    def test_spec_worked_example_ab1cd(self):
        self.assertEqual(enc.encode_callsign("AB1CD"), 0x9FDD51)  # appendix "Callsign Encoding"
        self.assertEqual(L.decode_address(0x9FDD51), ("AB1CD", "standard"))

    def test_trailing_spaces_do_not_change_the_address(self):
        self.assertEqual(enc.encode_callsign("ABC"), enc.encode_callsign("ABC   "))

    def test_address_space_boundaries(self):
        self.assertEqual(L.decode_address(0)[1], "reserved")
        self.assertEqual(L.decode_address(1), ("A", "standard"))
        self.assertEqual(L.decode_address(0xEE6B27FFFFFF), (".........", "standard"))  # last standard address
        self.assertEqual(L.decode_address(0xEE6B28000000)[1], "extended")
        self.assertEqual(L.decode_address(0xFFFFFFFFFFFE)[1], "extended")
        self.assertEqual(L.decode_address(0xFFFFFFFFFFFF)[1], "broadcast")
        self.assertEqual(enc.encode_callsign("........."), 0xEE6B27FFFFFF)

    def test_round_trip_random_callsigns(self):
        rng = np.random.default_rng(1)
        alphabet = L.M17_ALPHABET[1:]  # no leading space: a callsign starts with a letter or digit
        for _ in range(500):
            n = int(rng.integers(1, 10))
            cs = "".join(alphabet[int(i)] for i in rng.integers(0, len(alphabet), n))
            self.assertEqual(L.decode_address(enc.encode_callsign(cs)), (cs, "standard"))

    def test_labels(self):
        self.assertEqual(L.address_label(0xFFFFFFFFFFFF), "BROADCAST")
        self.assertEqual(L.address_label(0xFF0000000001), "0xFF0000000001")
        self.assertEqual(L.address_label(enc.encode_callsign("W2FBI")), "W2FBI")


class TypeTests(unittest.TestCase):
    def test_independent_packing_matches_the_parser(self):
        for stream in (True, False):
            for data_type in range(4):
                for enc_type in range(4):
                    for sub in range(4):
                        for can in (0, 1, 5, 8, 15):
                            for signed in (False, True):
                                raw = enc.make_type(
                                    stream=stream, data_type=data_type, enc_type=enc_type, enc_subtype=sub, can=can, signed=signed
                                )
                                t = L.parse_type(raw)
                                self.assertEqual(t.stream, stream)
                                self.assertEqual(t.can, can)
                                if stream:
                                    self.assertEqual((t.data_type, t.enc_type, t.enc_subtype, t.signed), (data_type, enc_type, sub, signed))
                                self.assertEqual(L.build_type(
                                    stream=stream, data_type=data_type, enc_type=enc_type, enc_subtype=sub, can=can, signed=signed
                                ), raw)

    def test_byte_layout_from_the_spec_table(self):
        # voice (10), no encryption, CAN 3: byte 0 = 0x01 (CAN[3:1]), byte 1 = 0x80|0x04|0x01
        self.assertEqual(enc.make_type(stream=True, data_type=2, can=3), 0x0185)
        self.assertEqual(enc.make_type(stream=True, data_type=2, can=0), 0x0005)
        self.assertEqual(enc.make_type(stream=True, data_type=2, signed=True), 0x0805)

    def test_encryption_reporting(self):
        none = L.encryption_info(L.parse_type(enc.make_type()))
        self.assertEqual(none.state.value, "clear")
        scr = L.encryption_info(L.parse_type(enc.make_type(enc_type=1, enc_subtype=1)))
        self.assertEqual((scr.state.value, scr.algorithm_name), ("encrypted", "M17 scrambler (16-bit LFSR)"))
        aes = L.encryption_info(L.parse_type(enc.make_type(enc_type=2, enc_subtype=2)))
        self.assertEqual((aes.state.value, aes.algorithm_name, aes.mi_present), ("encrypted", "AES-256-CTR", True))
        self.assertEqual(L.encryption_info(L.parse_type(enc.make_type(enc_type=3))).state.value, "unknown")


class CrcAndLsfTests(unittest.TestCase):
    def test_published_crc_vectors_with_the_encoders_own_loop(self):
        self.assertEqual(enc.crc16(b""), 0xFFFF)
        self.assertEqual(enc.crc16(b"A"), 0x206E)
        self.assertEqual(enc.crc16(b"123456789"), 0x772B)
        self.assertEqual(enc.crc16(bytes(range(256))), 0x1C31)

    def test_encoder_crc_agrees_with_the_fec_library(self):
        rng = np.random.default_rng(2)
        for _ in range(50):
            data = bytes(rng.integers(0, 256, 28, dtype=np.uint8))
            self.assertEqual(enc.crc16(data), fec_crc.crc_compute_bytes("crc16_m17", data))

    def test_whole_lsf_crc_is_zero_and_every_single_bit_flip_is_rejected(self):
        raw = enc.lsf_bytes(enc.address_bytes("W2FBI"), enc.address_bytes("N0CALL"), enc.make_type(can=2), bytes(range(14)))
        self.assertEqual(len(raw), 30)
        self.assertEqual(enc.crc16(raw), 0)  # the spec: a CRC over the whole LSF equals zero
        self.assertIsNotNone(L.parse_lsf(raw))
        for bit in range(240):
            damaged = bytearray(raw)
            damaged[bit // 8] ^= 0x80 >> (bit % 8)
            self.assertIsNone(L.parse_lsf(bytes(damaged)), f"bit {bit}")

    def test_parse_fields(self):
        raw = enc.lsf_bytes(enc.address_bytes("W2FBI"), enc.address_bytes("N0CALL"), enc.make_type(can=2), bytes(range(14)))
        lsf = L.parse_lsf(raw)
        self.assertEqual((lsf.dst_label, lsf.src_label, lsf.type.can), ("W2FBI", "N0CALL", 2))
        self.assertEqual(lsf.meta, bytes(range(14)))
        self.assertIsNone(L.parse_lsf(raw[:-1]))


class SyncTests(unittest.TestCase):
    def test_sync_words_match_the_spec_symbols(self):
        # spec table "Frame Specific Sync Bursts" (+3 -> dibit 1, +1 -> 0, -1 -> 2, -3 -> 3)
        sym = {"+3": 1, "+1": 0, "-1": 2, "-3": 3}
        table = {
            "m17_lsf": "+3 +3 +3 +3 -3 -3 +3 -3",
            "m17_bert": "-3 +3 -3 -3 +3 +3 +3 +3",
            "m17_stream": "-3 -3 -3 -3 +3 +3 -3 +3",
            "m17_packet": "+3 -3 +3 +3 -3 -3 -3 -3",
        }
        for name, symbols in table.items():
            expected = tuple(sym[s] for s in symbols.split())
            self.assertEqual(SYNC_SPECS[name].values, expected, name)
        for name, word in (("m17_lsf", 0x55F7), ("m17_bert", 0xDF55), ("m17_stream", 0xFF5D), ("m17_packet", 0x75FF)):
            self.assertEqual(tuple(enc._word_dibits(word)), SYNC_SPECS[name].values, name)

    def test_dibit_byte_example_from_the_spec(self):
        # "the four dibits contained in the byte 0xB4 (10 11 01 00) are sent as symbols -1, -3, +3, +1"
        bits = np.unpackbits(np.array([0xB4], dtype=np.uint8))
        dibits = fr.dibits_to_bits  # bits -> dibits goes the other way; check both directions
        d = enc.bits_to_dibits(bits)
        self.assertEqual(d.tolist(), [2, 3, 1, 0])  # -1, -3, +3, +1
        self.assertEqual(dibits(d).tolist(), bits.tolist())

    def test_eot_marker_is_555d(self):
        self.assertEqual(tuple(enc._word_dibits(0x555D)), (1, 1, 1, 1, 1, 1, 3, 1))  # +3 x6, -3, +3


class MetaTests(unittest.TestCase):
    def test_text_blocks_assemble_in_any_order(self):
        asm = L.TextAssembler()
        text = "Hello from M17 land, this is a fairly long message!"
        data = text.encode().ljust(52)
        blocks = [data[i : i + 13] for i in range(0, 52, 13)]
        order = [2, 0, 3, 1]
        done = None
        for n, i in enumerate(order):
            control = (0b1111 << 4) | (1 << i)
            done = asm.push(bytes([control]) + blocks[i])
            if n < 3:
                self.assertIsNone(done)
        self.assertEqual(done, text)

    def test_text_control_zero_means_no_text(self):
        self.assertIsNone(L.TextAssembler().push(bytes(14)))

    def test_gnss_decoding(self):
        meta = bytearray(14)
        meta[0] = (1 << 4) | 2  # OpenRTX, handheld
        bearing = 270
        meta[1] = (0b1110 << 4) | (3 << 1) | (bearing >> 8)  # lat/lon + altitude + velocity valid, radius 2^3
        meta[2] = bearing & 0xFF
        lat = round(45.5 / 90 * 8388607) & 0xFFFFFF
        lon = (-round(122.25 / 180 * 8388607)) & 0xFFFFFF
        meta[3:6] = lat.to_bytes(3, "big")
        meta[6:9] = lon.to_bytes(3, "big")
        meta[9:11] = int((120.5 + 500) / 0.5).to_bytes(2, "big")
        speed = int(88.5 / 0.5)
        meta[11] = speed >> 4
        meta[12] = (speed & 0xF) << 4
        g = L.parse_gnss(bytes(meta))
        self.assertAlmostEqual(g["latitude"], 45.5, places=4)
        self.assertAlmostEqual(g["longitude"], -122.25, places=4)
        self.assertEqual((g["altitude_m"], g["bearing_deg"], g["speed_kmh"], g["radius_m"]), (120.5, 270, 88.5, 8))
        self.assertEqual((g["source"], g["station_type"]), ("OpenRTX", "handheld"))

    def test_gnss_without_a_valid_position_is_ignored(self):
        meta = bytearray(14)
        meta[1] = 0b0100 << 4  # altitude valid only
        self.assertIsNone(L.parse_gnss(bytes(meta)))
        self.assertIsNone(L.parse_gnss(bytes(14)))

    def test_extended_callsigns(self):
        meta = enc.address_bytes("N0CALL") + enc.address_bytes("M17-M17 C") + bytes(2)
        self.assertEqual(L.parse_extended_callsigns(meta), ["N0CALL", "M17-M17 C"])
        self.assertEqual(L.parse_extended_callsigns(enc.address_bytes("N0CALL") + bytes(8)), ["N0CALL"])


class FrameRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(5)
        self.lsf = enc.lsf_bytes(enc.address_bytes("W2FBI"), enc.address_bytes("N0CALL"), enc.make_type(can=1), bytes(range(14)))

    def test_lsf_frame(self):
        frame = fr.decode_lsf_payload(enc.lsf_payload(self.lsf))
        self.assertEqual(frame.raw, self.lsf)
        self.assertIsNotNone(frame.lsf)
        self.assertEqual(frame.corrected, 0)

    def test_stream_frame_and_lich(self):
        for count in range(6):
            payload = bytes(self.rng.integers(0, 256, 16, dtype=np.uint8))
            frame = fr.decode_stream_payload(enc.stream_payload(self.lsf, count, 1234 + count, payload))
            self.assertTrue(frame.lich_ok)
            self.assertEqual(frame.lich_count, count)
            self.assertEqual(frame.lich_chunk, self.lsf[5 * count : 5 * count + 5])
            self.assertEqual((frame.frame_number, frame.eot, frame.payload), (1234 + count, False, payload))
        last = fr.decode_stream_payload(enc.stream_payload(self.lsf, 0, 0x7FFF, bytes(16), eot=True))
        self.assertEqual((last.frame_number, last.eot), (0x7FFF, True))

    def test_packet_frame(self):
        chunk = bytes(range(25))
        f = fr.decode_packet_payload(enc.packet_payload(chunk, False, 7))
        self.assertEqual((f.data, f.eof, f.counter, f.valid_bytes), (chunk, False, 7, 25))
        g = fr.decode_packet_payload(enc.packet_payload(chunk[:9], True, 9))
        self.assertEqual((g.eof, g.counter, g.valid_bytes, g.data[:9]), (True, 9, 9, chunk[:9]))

    def test_bert_frame_and_prbs_checker(self):
        gen = enc.Prbs9()
        checker = fr.Prbs9Checker()
        for _ in range(6):
            sent = gen.bits(197)
            frame = fr.decode_bert_payload(enc.bert_payload(sent))
            np.testing.assert_array_equal(frame.bits, sent)
            checker.push(frame.bits)
        self.assertTrue(checker.locked)
        self.assertEqual(checker.errors, 0)
        self.assertGreater(checker.bits, 800)

    def test_prbs_checker_counts_errors_after_lock(self):
        gen = enc.Prbs9()
        bits = gen.bits(2000)
        bad = bits.copy()
        bad[[600, 900, 1500]] ^= 1
        checker = fr.Prbs9Checker()
        checker.push(bad)
        self.assertTrue(checker.locked)
        self.assertEqual(checker.errors, 3)  # free-running generator: one error per bad bit
        # the register fills (9 bits) and then 18 clean bits lock it; those are not counted
        self.assertGreater(checker.bits, 2000 - 40)

    def test_prbs_checker_resynchronises_after_a_burst(self):
        gen = enc.Prbs9()
        bits = gen.bits(1200)
        bits[400:460] ^= 1  # a lost-frame burst
        checker = fr.Prbs9Checker()
        checker.push(bits)
        self.assertTrue(checker.locked)  # dropped on the burst, then locked again on the clean tail

    def test_soft_decisions_recover_far_more_frames_than_hard_slicing(self):
        # 4-level symbols (-3 -1 +1 +3) plus Gaussian noise, sliced at 0 and +-2. At sigma 0.75
        # (measured offline: hard 66/150, soft 137/150) soft values roughly double the yield.
        levels = {3: -3.0, 2: -1.0, 0: 1.0, 1: 3.0}

        def slicer(x):
            d = np.empty(x.size, dtype=np.uint8)
            d[x >= 2] = 1
            d[(x >= 0) & (x < 2)] = 0
            d[(x < 0) & (x > -2)] = 2
            d[x <= -2] = 3
            return d

        rng = np.random.default_rng(4)
        hard_ok = soft_ok = 0
        trials = 80
        for _ in range(trials):
            payload = bytes(rng.integers(0, 256, 16, dtype=np.uint8))
            dib = enc.bits_to_dibits(enc.stream_payload(self.lsf, 0, 5, payload))
            x = np.array([levels[int(d)] for d in dib]) + rng.normal(0, 0.75, dib.size)
            hard = fr.decode_stream_payload(fr.dibits_to_bits(slicer(x)))
            soft = fr.decode_stream_payload(fr.dibits_to_bits(slicer(x)), fr.symbols_to_soft_bits(x))
            hard_ok += hard.payload == payload
            soft_ok += soft.payload == payload
        self.assertGreater(soft_ok, hard_ok + trials // 4)
        self.assertGreater(soft_ok, trials * 0.8)


if __name__ == "__main__":
    unittest.main()
