"""DMR layer-2 / link-control / CSBK field tests (WP-D): spec constants and round trips."""

import itertools
import unittest

import numpy as np

from sigsense_api.dvoice.events import CallKind
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr import csbk, data, layer2, lc
from sigsense_api.dvoice.protocols.dmr.bits import bytes_to_bits, dibits_to_bits
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS


class SyncWordTests(unittest.TestCase):
    def test_match_the_architect_table_for_the_four_traffic_syncs(self):
        for table, name in (
            ("dmr_bs_voice", "bs_voice"), ("dmr_bs_data", "bs_data"),
            ("dmr_ms_voice", "ms_voice"), ("dmr_ms_data", "ms_data"),
        ):
            self.assertEqual("".join(map(str, C.SYNC_BY_NAME[name].dibits)), SYNC_SPECS[table].dibits)

    def test_hex_values_from_table_9_2(self):
        # ETSI TS 102 361-1 table 9.2, read from the specification text.
        expected = {
            "bs_voice": 0x755FD7DF75F7, "bs_data": 0xDFF57D75DF5D, "ms_voice": 0x7F7D5DD57DFD,
            "ms_data": 0xD5D7F77FD757, "rc": 0x77D55F7DFD77, "dm1_voice": 0x5D577F7757FF,
            "dm1_data": 0xF7FDD5DDFD55, "dm2_voice": 0x7DFFD5F55D5F, "dm2_data": 0xD7557F5FF7F5,
        }
        for name, value in expected.items():
            self.assertEqual(int(C.SYNC_BY_NAME[name].hex48, 16), value, name)

    def test_voice_and_data_syncs_are_symbol_complements(self):
        """The spec's note: data sync = voice sync with every symbol complemented (dibit ^ 2)."""
        for fam in ("bs", "ms", "dm1", "dm2"):
            v, d = C.SYNC_BY_NAME[f"{fam}_voice"].dibits, C.SYNC_BY_NAME[f"{fam}_data"].dibits
            self.assertTrue(np.array_equal(v ^ 2, d), fam)

    def test_words_are_well_separated(self):
        for a, b in itertools.combinations(C.SYNC_WORDS, 2):
            self.assertGreaterEqual(int(np.count_nonzero(a.dibits != b.dibits)), 8, (a.name, b.name))


class CachTests(unittest.TestCase):
    def test_tact_round_trip_and_single_bit_correction(self):
        payload = np.random.default_rng(1).integers(0, 2, 17).astype(np.uint8)
        for at, tc, lcss in itertools.product((0, 1), (0, 1), (0, 1, 2, 3)):
            bits = layer2.encode_cach(at, tc, lcss, payload)
            tact, got = layer2.decode_cach(bits)
            self.assertEqual((tact.at, tact.tc, tact.lcss, tact.corrected), (at, tc, lcss, 0))
            self.assertTrue(np.array_equal(got, payload))
            for pos in C.CACH_TACT_POSITIONS:  # any single TACT bit error is corrected
                bad = bits.copy()
                bad[pos] ^= 1
                t2, _ = layer2.decode_cach(bad)
                self.assertEqual((t2.at, t2.tc, t2.lcss, t2.corrected), (at, tc, lcss, 1))

    def test_bit_positions_follow_figure_b9(self):
        bits = layer2.encode_cach(1, 0, 3, np.arange(17) % 2)
        self.assertEqual(int(bits[0]), 1)  # AT first
        self.assertEqual(sorted(C.CACH_TACT_POSITIONS + C.CACH_PAYLOAD_POSITIONS), list(range(24)))

    def test_short_lc_round_trip_with_burst_errors(self):
        rows = layer2.encode_short_lc(2, 0xABCDEF)
        got = layer2.decode_short_lc(rows)
        self.assertEqual((got.slco, got.data), (2, 0xABCDEF))
        for r in range(3):  # one error in each Hamming row (matrix row r) is corrected
            bad = [x.copy() for x in rows]
            idx = 5 * 4 + r  # column-major position of matrix[r][5]
            bad[idx // 17][idx % 17] ^= 1
            res = layer2.decode_short_lc(bad)
            self.assertEqual(res.data, 0xABCDEF)
            self.assertEqual(res.corrected, 1)
        bad = [x.copy() for x in rows]
        bad[0][:6] ^= 1  # a burst of errors: must be rejected, never returned wrong
        res = layer2.decode_short_lc(bad)
        self.assertTrue(res is None or res.data == 0xABCDEF)

    def test_short_lc_fragments_make_the_column_major_matrix_of_figure_b6(self):
        rows = layer2.encode_short_lc(0, 0)
        flat = np.concatenate(rows)
        matrix = flat.reshape(17, 4).T
        self.assertTrue(np.array_equal(matrix[3], matrix[:3].sum(axis=0) & 1))  # even column parity row


class SlotTypeEmbTests(unittest.TestCase):
    def test_slot_type_round_trip_and_correction(self):
        for cc, dt in itertools.product((0, 5, 15), range(12)):
            bits = layer2.encode_slot_type(cc, dt)
            st = layer2.decode_slot_type(bits)
            self.assertEqual((st.cc, st.data_type, st.corrected), (cc, dt, 0))
            bad = bits.copy()
            bad[[1, 8, 17]] ^= 1
            st = layer2.decode_slot_type(bad)
            self.assertEqual((st.cc, st.data_type), (cc, dt))

    def test_emb_round_trip_and_correction(self):
        for cc, pi, lcss in itertools.product((0, 9, 15), (0, 1), range(4)):
            bits = layer2.encode_emb(cc, pi, lcss)
            e = layer2.decode_emb(bits)
            self.assertEqual((e.cc, e.pi, e.lcss, e.corrected), (cc, pi, lcss, 0))
            bad = bits.copy()
            bad[[2, 11]] ^= 1
            e = layer2.decode_emb(bad)
            self.assertEqual((e.cc, e.pi, e.lcss), (cc, pi, lcss))


class LinkControlTests(unittest.TestCase):
    def setUp(self):
        self.lc9 = lc.build_full_lc(0, 0, bytes([0x80]) + (4000).to_bytes(3, "big") + (123456).to_bytes(3, "big"))

    def test_header_and_terminator_round_trip(self):
        for dt in (C.DataType.VOICE_LC_HEADER, C.DataType.TERMINATOR_WITH_LC):
            res = lc.decode_lc_burst(lc.encode_lc_burst(self.lc9, dt), dt)
            self.assertEqual(res[0].raw, self.lc9)
            vu = lc.parse_voice_user(res[0])
            self.assertEqual((vu.kind, vu.target, vu.source, vu.service.emergency), (CallKind.GROUP, 4000, 123456, True))

    def test_the_data_type_mask_is_enforced(self):
        bits = lc.encode_lc_burst(self.lc9, C.DataType.VOICE_LC_HEADER)
        self.assertIsNone(lc.decode_lc_burst(bits, C.DataType.TERMINATOR_WITH_LC))

    def test_corrects_channel_errors_and_rejects_heavy_damage(self):
        rng = np.random.default_rng(3)
        bits = lc.encode_lc_burst(self.lc9, C.DataType.VOICE_LC_HEADER)
        bad = bits.copy()
        bad[rng.choice(196, 3, replace=False)] ^= 1
        res = lc.decode_lc_burst(bad, C.DataType.VOICE_LC_HEADER)
        self.assertIsNotNone(res)
        self.assertEqual(res[0].raw, self.lc9)
        wrong = 0
        for _ in range(300):
            noise = rng.integers(0, 2, 196).astype(np.uint8)
            got = lc.decode_lc_burst(noise, C.DataType.VOICE_LC_HEADER)
            wrong += got is not None
        self.assertEqual(wrong, 0)  # random data never passes BPTC + RS(12,9)

    def test_service_options_table_7_11(self):
        so = lc.parse_service_options(0b11001011)
        self.assertEqual((so.emergency, so.privacy, so.broadcast, so.ovcm, so.priority), (True, True, True, False, 3))
        self.assertEqual(so.raw, 0b11001011)

    def test_unit_to_unit_and_manufacturer_lcs(self):
        uu = lc.parse_full_lc(lc.build_full_lc(3, 0, bytes([0]) + bytes([0, 0, 7]) + bytes([0, 0, 8])))
        self.assertEqual(lc.parse_voice_user(uu).kind, CallKind.PRIVATE)
        mfid = lc.parse_full_lc(lc.build_full_lc(0, 0x10, bytes(7)))
        self.assertIsNone(lc.parse_voice_user(mfid))
        self.assertIn("MFID_10", mfid.name)

    def test_talker_alias_formats(self):
        for text, fmt in (("Base 7", 0), ("Müller-1", 1), ("Ünit 99 ☎", 2), ("Raj-København", 3)):
            asm = lc.TalkerAliasAssembler()
            parts = lc.encode_talker_alias(text, fmt)
            flcos = [lc.FLCO_TA_HEADER, lc.FLCO_TA_BLOCK1, lc.FLCO_TA_BLOCK2, lc.FLCO_TA_BLOCK3]
            out = None
            for flco, d in zip(flcos, parts):
                out = asm.add(lc.parse_full_lc(lc.build_full_lc(flco, 0, d))) or out
            self.assertEqual(out, text, (text, fmt))

    def test_gps_two_complement_fields(self):
        lat_raw, lon_raw = (-(1 << 22)) & 0xFFFFFF, (1 << 23) & 0x1FFFFFF
        value = (0 << 52) | (3 << 49) | (lon_raw << 24) | lat_raw
        gps = lc.parse_gps(lc.parse_full_lc(lc.build_full_lc(8, 0, value.to_bytes(7, "big"))))
        self.assertAlmostEqual(gps["latitude"], -45.0, places=6)
        self.assertAlmostEqual(gps["longitude"], 90.0, places=6)
        self.assertEqual(gps["position_error_code"], 3)


class SpecWorkedExampleTests(unittest.TestCase):
    def test_idle_message_bptc_matches_figure_d1(self):
        """ETSI TS 102 361-1 annex D: the 96 Idle bits encode to the printed 13 x 15 matrix."""
        from sigsense_api.dvoice.fec import bptc

        info = bytes_to_bits(bytes.fromhex(C.IDLE_INFO_HEX))
        tx = bptc.bptc_196_96_encode(info)
        flat = np.empty(196, np.uint8)
        flat[(np.arange(196) * 181) % 196] = 0  # the matrix position of every transmitted bit
        matrix = tx[(np.arange(196) * 181) % 196][1:].reshape(13, 15)
        rows = tuple("".join(str(int(b)) for b in row) for row in matrix)
        self.assertEqual(rows, C.IDLE_BPTC_MATRIX)

    def test_idle_burst_decodes_as_an_idle_data_burst(self):
        from sigsense_api.dvoice.testing.dmr_encoder import DmrEncoder

        enc = DmrEncoder(cc=9)
        bits = dibits_to_bits(enc.idle_burst())
        st = layer2.decode_slot_type(np.concatenate([bits[C.SLOT_TYPE_A], bits[C.SLOT_TYPE_B]]))
        self.assertEqual((st.cc, st.data_type), (9, C.DataType.IDLE))
        self.assertEqual(bits.size, 264)
        self.assertTrue(np.array_equal(bits[C.CENTER], C.SYNC_BY_NAME["bs_data"].bits))


class CsbkTests(unittest.TestCase):
    def test_round_trip_and_crc_mask(self):
        c = csbk.Csbk(1, 0, 0b000100, 0, bytes([0x00, 0, 0, 0, 9, 0, 0, 5]))
        bits = csbk.encode_csbk_block(c)
        got, corrected = csbk.decode_csbk_block(bits)
        self.assertEqual((got.csbko, got.fid, got.data, corrected), (c.csbko, 0, c.data, 0))
        self.assertIsNone(csbk.decode_block(bits, C.DataType.DATA_HEADER))  # wrong data-type mask

    def test_errors_corrected_and_corruption_rejected(self):
        c = csbk.Csbk(1, 0, 0b110001, 0, bytes(8))
        bits = csbk.encode_csbk_block(c)
        bad = bits.copy()
        bad[[5, 77, 150]] ^= 1
        self.assertIsNotNone(csbk.decode_csbk_block(bad))
        rng = np.random.default_rng(4)
        bad = bits.copy()
        bad[rng.choice(196, 30, replace=False)] ^= 1
        self.assertIsNone(csbk.decode_csbk_block(bad))

    def test_tier_two_requests(self):
        c = csbk.Csbk(1, 0, 0b000100, 0, bytes([0x00, 0, 0, 0, 1, 0, 0, 2]))
        info = csbk.describe_csbk(c)
        self.assertEqual(info.name, "UU_V_Req")
        self.assertEqual((info.fields["target"], info.fields["source"]), (1, 2))

    def test_tier_three_group_grant_fields(self):
        # LPCN 0x123 | LCN 1 | late entry 0 | emergency 1 | offset 0, then target and source (table 7.11)
        lpcn = (0x123 << 4) | (1 << 3) | (0 << 2) | (1 << 1)
        c = csbk.Csbk(1, 0, 0b110001, 0, lpcn.to_bytes(2, "big") + (777).to_bytes(3, "big") + (4242).to_bytes(3, "big"))
        info = csbk.describe_csbk(c)
        self.assertEqual(info.name, "TV_GRANT")
        self.assertEqual(
            (info.grant["logical_channel"], info.grant["tdma_slot"], info.grant["emergency"], info.grant["target"], info.grant["source"]),
            (0x123, 1, True, 777, 4242),
        )

    def test_c_aloha_system_identity(self):
        sysc = (1 << 14) | (3 << 7 << 2)  # small model: NET 7 bits, SITE 5 bits
        syscode = (1 << 14) | (5 << 7) | (9 << 2) | 1  # net 5? see below
        d = bytes([0b0_1_1_010_1_1, 0, 0, syscode >> 8, syscode & 0xFF, 0, 0, 1])
        info = csbk.describe_csbk(csbk.Csbk(1, 0, 0b011001, 0, d))
        f = info.fields["syscode_fields"]
        self.assertEqual((info.name, f["model"], f["net"], f["site"], f["par"]), ("C_ALOHA", 1, 5 >> 0, 9, 1))
        self.assertEqual(info.fields["version"], 2)
        self.assertTrue(info.fields["offset_timing"])

    def test_syscode_models(self):
        for model, (net_bits, site_bits) in enumerate(((9, 3), (7, 5), (4, 8), (2, 10))):
            code = (model << 14) | (1 << (site_bits + 2)) | (1 << 2) | 2
            f = csbk.syscode_fields(code)
            self.assertEqual((f["net_bits"], f["site_bits"], f["net"], f["site"], f["par"]), (net_bits, site_bits, 1, 1, 2))

    def test_mbc_assembly_with_crc(self):
        from sigsense_api.dvoice.testing.dmr_encoder import DmrEncoder

        enc = DmrEncoder()
        header = csbk.Csbk(0, 0, 0b110001, 0, bytes(range(8)))
        blocks = [bytes(range(12)), bytes(range(20, 32))]
        bursts = enc.mbc(header, blocks)
        asm = csbk.MbcAssembler()
        done = None
        for i, burst in enumerate(bursts):
            bits = dibits_to_bits(burst)
            info = np.concatenate([bits[C.DATA_INFO_A], bits[C.DATA_INFO_B]])
            if i == 0:
                out = csbk.decode_block(info, C.DataType.MBC_HEADER)
                self.assertIsNotNone(out)
                asm.add_header(out[0])
            else:
                from sigsense_api.dvoice.fec import bptc
                from sigsense_api.dvoice.protocols.dmr.bits import bits_to_bytes

                done = asm.add_continuation(bits_to_bytes(bptc.bptc_196_96_decode(info).data)) or done
        self.assertIsNotNone(done)
        self.assertEqual(done[0].csbko, 0b110001)


class DataHeaderTests(unittest.TestCase):
    def test_round_trip(self):
        raw = data.build_data_header(dpf=0b0010, sap=0b0100, dst=100, src=200, blocks=3, group=True)
        info = csbk.encode_block(raw, C.DataType.DATA_HEADER)
        octets, _ = csbk.decode_block(info, C.DataType.DATA_HEADER)
        h = data.parse_data_header(octets)
        self.assertEqual((h.group, h.dpf_name, h.sap_name, h.dst, h.src, h.blocks_to_follow, h.confirmed),
                         (True, "unconfirmed", "IP", 100, 200, 3, False))


if __name__ == "__main__":
    unittest.main()
