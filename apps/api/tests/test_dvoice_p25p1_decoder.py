"""P25 Phase 1 decoder: call lifecycle, voice delivery, robustness (WP-C)."""

import unittest

import numpy as np

from sigsense_api.dvoice.events import CallKind, EncryptionState, VocoderId
from sigsense_api.dvoice.protocols.base import ProtocolConfig
from sigsense_api.dvoice.protocols.p25p1 import framing as fr
from sigsense_api.dvoice.protocols.p25p1 import words as w
from sigsense_api.dvoice.protocols.p25p1.decoder import P25P1Decoder
from sigsense_api.dvoice.testing import p25p1_encoder as enc
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.vocoder.base import Availability, UnavailableVocoder, Vocoder, VocoderFrame

NAC = 0x293


class SpyVocoder(Vocoder):
    """Records every frame it is given; stands in for an installed IMBE codec."""

    id = VocoderId.IMBE_7200X4400
    frames: list = []
    instances = 0

    def __init__(self):
        type(self).instances += 1
        self.closed = False

    @classmethod
    def availability(cls):
        return Availability(True, "spy", "test", "spy")

    def decode(self, frame: VocoderFrame):
        type(self).frames.append(np.array(frame.bits))
        return np.full(160, 7, dtype=np.int16)

    def close(self):
        self.closed = True


def run(dibits, *, chunk=480, config=None, vocoder="spy", t0=0.0, decoder=None):
    events: list[tuple[str, object]] = []
    SpyVocoder.frames = []
    SpyVocoder.instances = 0
    if vocoder == "spy":
        provider = lambda vid: SpyVocoder()
    elif vocoder == "none":
        provider = lambda vid: UnavailableVocoder(vid)
    else:
        provider = registry.get_vocoder
    d = decoder or P25P1Decoder(lambda t, p: events.append((t, p)), provider, config or ProtocolConfig(channel="ch0", hang_time_s=1.0))
    if decoder is not None:
        d.emit = lambda t, p: events.append((t, p))
    for b in enc.to_blocks(dibits, chunk=chunk, t0=t0):
        d.feed(b)
    return d, events


def kinds(events, name):
    return [p for t, p in events if t == name]


def call_dibits(**kw):
    rng = np.random.default_rng(kw.pop("seed", 4))
    return enc.build_voice_call(NAC, tgid=kw.pop("tgid", 1234), source=kw.pop("source", 0xABCDE), rng=rng, **kw)


class LifecycleTests(unittest.TestCase):
    def test_clear_group_call(self):
        dib, sent = call_dibits(superframes=2)
        cfg = ProtocolConfig(channel="ch0", talkgroup_labels={1234: "Dispatch"})
        d, ev = run(dib, config=cfg)
        starts, ends = kinds(ev, "call_start"), kinds(ev, "call_end")
        self.assertEqual((len(starts), len(ends)), (1, 1))
        c = ends[0]
        self.assertEqual((c.target_id, c.source_id, c.kind), (1234, 0xABCDE, CallKind.GROUP))
        self.assertEqual(c.end_reason, "terminator")
        self.assertEqual(c.encryption.state, EncryptionState.CLEAR)
        self.assertEqual(c.voice_frames, 36)
        self.assertEqual(c.call_id, "p25p1-ch0-x-1")
        self.assertEqual(kinds(ev, "talkgroup")[0].label, "Dispatch")
        self.assertEqual(kinds(ev, "system_info")[0].nac, NAC)
        self.assertEqual(d.stats()["frames_bad"], 0)
        self.assertEqual(d.stats()["duid"], {"HDU": 1, "LDU1": 2, "LDU2": 2, "TDU": 1})

    def test_voice_bits_reach_the_vocoder_exactly_and_in_order(self):
        dib, sent = call_dibits(superframes=2)
        _, ev = run(dib)
        self.assertEqual(len(SpyVocoder.frames), 36)
        for got, want in zip(SpyVocoder.frames, sent):
            self.assertTrue((got == want).all())
        voices = kinds(ev, "voice")
        self.assertEqual([v.seq for v in voices], list(range(1, 37)))
        self.assertTrue(all(v.pcm is not None and v.pcm.size == 160 for v in voices))
        self.assertEqual(SpyVocoder.instances, 1)  # one vocoder per call

    def test_vocoder_closed_when_call_ends(self):
        spy_instances = []
        dib, _ = call_dibits(superframes=1)
        d = P25P1Decoder(lambda t, p: None, lambda vid: spy_instances.append(SpyVocoder()) or spy_instances[-1])
        for b in enc.to_blocks(dib):
            d.feed(b)
        self.assertTrue(spy_instances and all(s.closed for s in spy_instances))

    def test_tone_test_double_produces_audio_frames(self):
        dib, _ = call_dibits(superframes=1)
        with registry.use_test_double():
            _, ev = run(dib, vocoder="registry")
        voices = kinds(ev, "voice")
        self.assertEqual(len(voices), 18)
        self.assertTrue(all(v.pcm is not None and v.pcm.size == 160 and v.pcm.any() for v in voices))

    def test_no_vocoder_still_reports_calls_and_one_error(self):
        dib, _ = call_dibits(superframes=2)
        _, ev = run(dib, vocoder="none")
        self.assertEqual(len(kinds(ev, "call_end")), 1)
        errs = [p for p in kinds(ev, "error") if p.code == "vocoder_unavailable"]
        self.assertEqual(len(errs), 1)
        self.assertTrue(all(v.pcm is None for v in kinds(ev, "voice")))
        self.assertEqual(kinds(ev, "call_end")[0].voice_frames, 36)

    def test_emergency_flag_and_priority(self):
        dib, _ = call_dibits(superframes=1, emergency=True, service_options=0x80 | 0x05)
        _, ev = run(dib)
        c = kinds(ev, "call_end")[0]
        self.assertTrue(c.emergency)
        self.assertEqual(c.priority, 5)

    def test_tdulc_terminator_carries_the_lc(self):
        dib, _ = call_dibits(superframes=1, terminator="tdulc")
        _, ev = run(dib)
        c = kinds(ev, "call_end")[0]
        self.assertEqual((c.end_reason, c.target_id), ("terminator", 1234))

    def test_missing_terminator_ends_by_timeout(self):
        dib, _ = call_dibits(superframes=1, terminator="none")
        d, ev = run(dib)
        self.assertEqual(kinds(ev, "call_end"), [])
        quiet = np.zeros(9600, dtype=np.uint8)  # two seconds of nothing
        d.emit = lambda t, p: ev.append((t, p))
        start = dib.size
        for b in enc.to_blocks(quiet, start_index=start, t0=start / 4800.0):
            d.feed(b)
        ends = kinds(ev, "call_end")
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0].end_reason, "timeout")

    def test_two_calls_back_to_back(self):
        a, _ = call_dibits(tgid=10, source=1, superframes=1)
        b, _ = call_dibits(tgid=20, source=2, superframes=1, seed=9)
        _, ev = run(np.concatenate((a, b)))
        ends = kinds(ev, "call_end")
        self.assertEqual([(c.target_id, c.source_id) for c in ends], [(10, 1), (20, 2)])
        self.assertEqual(len({c.call_id for c in ends}), 2)

    def test_talker_change_without_terminator_ends_the_old_call(self):
        a, _ = call_dibits(tgid=10, source=1, superframes=1, terminator="none", with_hdu=False)
        b, _ = call_dibits(tgid=20, source=2, superframes=1, seed=9, with_hdu=False)
        _, ev = run(np.concatenate((a, b)))
        ends = kinds(ev, "call_end")
        self.assertEqual([c.end_reason for c in ends], ["sync_loss", "terminator"])

    def test_reset_ends_an_active_call_as_retune(self):
        dib, _ = call_dibits(superframes=1, terminator="none")
        d, ev = run(dib)
        d.emit = lambda t, p: ev.append((t, p))
        d.reset()
        self.assertEqual(kinds(ev, "call_end")[-1].end_reason, "retune")

    def test_call_update_rate_is_bounded(self):
        dib, _ = call_dibits(superframes=20)
        _, ev = run(dib)
        self.assertLessEqual(len(kinds(ev, "call_update")), 4)


class EncryptionTests(unittest.TestCase):
    def test_encrypted_call_is_reported_and_never_decoded(self):
        dib, _ = call_dibits(superframes=2, algid=0x84, kid=0x0102)
        _, ev = run(dib)
        c = kinds(ev, "call_end")[0]
        self.assertEqual(c.encryption.state, EncryptionState.ENCRYPTED)
        self.assertEqual((c.encryption.algorithm_id, c.encryption.key_id), (0x84, 0x0102))
        self.assertEqual(c.encryption.algorithm_name, "AES-256")
        self.assertTrue(c.encryption.mi_present)
        self.assertFalse(c.encryption.audio_decoded)
        self.assertEqual(kinds(ev, "voice"), [])
        self.assertEqual(SpyVocoder.frames, [])
        self.assertEqual(SpyVocoder.instances, 0)

    def test_join_mid_call_without_header_holds_voice_until_the_ES_is_known(self):
        dib, sent = call_dibits(superframes=2, with_hdu=False)
        _, ev = run(dib)
        self.assertEqual(len(SpyVocoder.frames), 36)  # LDU1's nine were held, then released by the LDU2
        c = kinds(ev, "call_end")[0]
        self.assertEqual(c.encryption.state, EncryptionState.CLEAR)
        self.assertNotIn("voice_frames_withheld", c.meta)

    def test_join_mid_call_encrypted_voice_is_never_released(self):
        dib, _ = call_dibits(superframes=2, with_hdu=False, algid=0x81, service_options=0x00)  # LC does not flag protection
        _, ev = run(dib)
        self.assertEqual(SpyVocoder.frames, [])
        self.assertEqual(SpyVocoder.instances, 0)
        c = kinds(ev, "call_end")[0]
        self.assertEqual(c.encryption.state, EncryptionState.ENCRYPTED)
        self.assertEqual(c.meta.get("voice_frames_withheld"), 9)

    def test_lc_protected_flag_marks_encrypted_without_an_ES(self):
        dib, _ = call_dibits(superframes=1, with_hdu=False, algid=0x80, service_options=0x40, terminator="none")
        # only LDU1 reaches the decoder: cut the LDU2 off
        ldu1_len = fr.total_dibits(fr.LDU_INFO_DIBITS)
        _, ev = run(dib[:ldu1_len])
        self.assertEqual(SpyVocoder.frames, [])
        self.assertEqual(kinds(ev, "call_start")[0].encryption.state, EncryptionState.ENCRYPTED)


class StreamingTests(unittest.TestCase):
    def signature(self, ev):
        out = []
        for t, p in ev:
            if t in ("call_start", "call_end"):
                out.append((t, p.target_id, p.source_id, p.voice_frames if t == "call_end" else 0, p.end_reason))
            elif t == "voice":
                out.append((t, p.seq))
        return out

    def test_chunk_size_does_not_change_the_result(self):
        dib, _ = call_dibits(superframes=2)
        baseline = None
        bits = None
        for chunk in (1, 7, 100, 479, 5000, dib.size):
            _, ev = run(dib, chunk=chunk)
            sig = self.signature(ev)
            if baseline is None:
                baseline, bits = sig, [b.tolist() for b in SpyVocoder.frames]
            else:
                self.assertEqual(sig, baseline, f"chunk={chunk}")
                self.assertEqual([b.tolist() for b in SpyVocoder.frames], bits)

    def test_inverted_polarity_decodes_the_same(self):
        dib, sent = call_dibits(superframes=1)
        _, ev = run(dib ^ 2)
        self.assertEqual(kinds(ev, "call_end")[0].voice_frames, 18)
        self.assertTrue(all((a == b).all() for a, b in zip(SpyVocoder.frames, sent)))
        self.assertTrue(all(s.inverted for s in kinds(ev, "sync")))

    def test_garbage_before_and_after_is_ignored(self):
        rng = np.random.default_rng(3)
        dib, _ = call_dibits(superframes=1)
        stream = np.concatenate((rng.integers(0, 4, 777).astype(np.uint8), dib, rng.integers(0, 4, 500).astype(np.uint8)))
        _, ev = run(stream)
        self.assertEqual(len(kinds(ev, "call_end")), 1)

    def test_random_dibits_never_make_a_call_or_raise(self):
        rng = np.random.default_rng(8)
        d, ev = run(rng.integers(0, 4, 60_000).astype(np.uint8), chunk=997)
        self.assertEqual(kinds(ev, "call_start"), [])
        self.assertLess(len(kinds(ev, "trunk")), 1)

    def test_gap_in_the_stream_drops_the_partial_frame_and_recovers(self):
        a, _ = call_dibits(superframes=1, terminator="tdu")
        events = []
        d = P25P1Decoder(lambda t, p: events.append((t, p)), lambda vid: SpyVocoder())
        half = a.size // 2
        for b in enc.to_blocks(a[:half], chunk=300):
            d.feed(b)
        b2, _ = call_dibits(superframes=1, seed=12)
        for b in enc.to_blocks(b2, chunk=300, start_index=half + 5000, t0=(half + 5000) / 4800.0):
            d.feed(b)
        ends = kinds(events, "call_end")
        self.assertGreaterEqual(len(ends), 1)
        self.assertEqual(ends[-1].end_reason, "terminator")

    def test_buffer_does_not_grow_without_sync(self):
        d = P25P1Decoder(lambda t, p: None, lambda vid: SpyVocoder())
        for b in enc.to_blocks(np.zeros(100_000, dtype=np.uint8), chunk=1000):
            d.feed(b)
        self.assertLess(d._buf.size, 100)


class NoiseTests(unittest.TestCase):
    def corrupt(self, dibits, p, rng):
        d = dibits.copy()
        hit = rng.random(d.size) < p
        d[hit] = (d[hit] + rng.integers(1, 4, int(hit.sum()))) & 3
        return d

    def test_frame_success_falls_off_gracefully_and_never_invents_identity(self):
        rng = np.random.default_rng(31)
        dib, _ = call_dibits(superframes=6)
        results = {}
        for p in (0.0, 0.005, 0.01, 0.02, 0.05, 0.15):
            ok = []
            ids_ok = True
            for trial in range(6):
                d, ev = run(self.corrupt(dib, p, rng))
                ok.append(d.stats()["frames_ok"] / 14.0)  # HDU + 12 LDU + TDU
                for c in kinds(ev, "call_start") + kinds(ev, "call_end"):
                    ids_ok &= c.target_id in (None, 1234)
            results[p] = float(np.mean(ok))
            self.assertTrue(ids_ok, f"wrong talkgroup at error rate {p}")
        self.assertEqual(results[0.0], 1.0)
        self.assertGreaterEqual(results[0.01], 0.85)
        self.assertGreaterEqual(results[0.02], 0.5)
        self.assertLessEqual(results[0.15], results[0.02])


class TrunkingBlockTests(unittest.TestCase):
    CH = (1 << 12) | 0x0B2

    def tsdu(self, *blocks):
        return enc.build_tsdu(NAC, list(blocks))

    def iden(self):
        return (0x3D, enc.args_iden_up(1, 12_500, -45_000_000, 12_500, 851_006_250))

    def test_iden_update_then_grant_gives_a_frequency(self):
        grant = (0x00, enc.args_grp_v_ch_grant(0x03, self.CH, 4321, 0x00ABCD))
        dib = np.concatenate((self.tsdu(self.iden()), self.tsdu(grant)))
        _, ev = run(dib)
        trunk = kinds(ev, "trunk")
        self.assertEqual([t.kind for t in trunk], ["cc_acquired", "system_update", "grant"])
        g = trunk[2]
        self.assertAlmostEqual(g.frequency_hz, 851_006_250 + 0xB2 * 12_500)
        self.assertEqual((g.talkgroup, g.source_id), (4321, 0xABCD))
        self.assertFalse(g.encrypted)
        self.assertEqual(g.detail["priority"], 3)
        iden = trunk[1].detail["iden_fields"]
        self.assertEqual((iden["iden"], iden["spacing_hz"], iden["base_hz"], iden["tx_offset_hz"]), (1, 12_500, 851_006_250, -45_000_000))

    def test_grant_before_iden_has_no_frequency_but_keeps_the_channel(self):
        grant = (0x00, enc.args_grp_v_ch_grant(0, self.CH, 7, 8))
        _, ev = run(self.tsdu(grant))
        g = [t for t in kinds(ev, "trunk") if t.kind == "grant"][0]
        self.assertIsNone(g.frequency_hz)
        self.assertEqual(g.detail["channel"], self.CH)

    def test_encrypted_service_option_marks_the_grant(self):
        grant = (0x00, enc.args_grp_v_ch_grant(0x40, self.CH, 7, 8))
        _, ev = run(self.tsdu(grant))
        self.assertTrue([t for t in kinds(ev, "trunk") if t.kind == "grant"][0].encrypted)

    def test_three_block_tsdu_and_update_and_unit_to_unit(self):
        blocks = [
            self.iden(),
            (0x02, enc.args_grp_v_ch_grant_updt(self.CH, 11, self.CH + 1, 12)),
            (0x04, enc.args_uu_v_ch_grant(self.CH, 0x111111, 0x222222)),
        ]
        _, ev = run(self.tsdu(*blocks))
        grants = [t for t in kinds(ev, "trunk") if t.kind == "grant"]
        self.assertEqual([g.talkgroup for g in grants], [11, 12, None])
        self.assertEqual(grants[2].detail["target_unit"], 0x111111)
        self.assertTrue(grants[2].detail["private"])
        self.assertAlmostEqual(grants[1].frequency_hz, 851_006_250 + (0xB2 + 1) * 12_500)

    def test_explicit_grant_uses_the_transmit_channel(self):
        _, ev = run(np.concatenate((self.tsdu(self.iden()), self.tsdu((0x03, enc.args_grp_v_ch_grant_updt_exp(0, self.CH, self.CH + 5, 99))))))
        g = [t for t in kinds(ev, "trunk") if t.kind == "grant"][0]
        self.assertEqual(g.talkgroup, 99)
        self.assertEqual(g.detail["rx_channel"], self.CH + 5)

    def test_system_broadcasts(self):
        blocks = [
            self.iden(),
            (0x3B, enc.args_net_sts(1, 0xBEE00, 0x3A1, self.CH)),
            (0x3A, enc.args_rfss_sts(1, 0x3A1, 2, 3, self.CH)),
        ]
        _, ev = run(self.tsdu(*blocks))
        info = kinds(ev, "system_info")[-1]
        self.assertEqual((info.wacn, info.system_id, info.rfss_id, info.site_id, info.nac), (0xBEE00, 0x3A1, 2, 3, NAC))
        self.assertAlmostEqual(info.control_channel_hz, 851_006_250 + 0xB2 * 12_500)

    def test_adjacent_site_is_a_system_update(self):
        _, ev = run(np.concatenate((self.tsdu(self.iden()), self.tsdu((0x3C, enc.args_adj_sts(1, 0x8, 0x3A1, 4, 5, self.CH))))))
        adj = [t for t in kinds(ev, "trunk") if t.detail.get("opcode") == "ADJ_STS_BCST"][0]
        self.assertEqual((adj.detail["site_id"], adj.detail["rfss_id"], adj.detail["system_id"]), (5, 4, 0x3A1))
        self.assertIsNotNone(adj.frequency_hz)

    def test_vhf_uhf_iden_update(self):
        _, ev = run(self.tsdu((0x34, enc.args_iden_up_vu(2, 5, 0x1234, 6_250, 154_000_000))))
        f = [t for t in kinds(ev, "trunk") if t.kind == "system_update"][0].detail["iden_fields"]
        self.assertEqual((f["iden"], f["spacing_hz"], f["base_hz"]), (2, 6_250, 154_000_000))

    def test_unknown_opcode_is_reported_raw_not_guessed(self):
        _, ev = run(self.tsdu((0x2E, 0x0123456789ABCDEF)))
        self.assertEqual([t.kind for t in kinds(ev, "trunk")], ["cc_acquired"])

    def test_corrupt_crc_never_produces_a_grant(self):
        grant = (0x00, enc.args_grp_v_ch_grant(0, self.CH, 7, 8))
        dib = self.tsdu(self.iden(), grant)
        # flip enough dibits inside the second block to defeat the trellis and the CRC
        bad = dib.copy()
        start = fr.total_dibits(fr.HEADER_INFO_DIBITS + fr.TSBK_BLOCK_DIBITS) + 3
        bad[start : start + 60] ^= 3
        d, ev = run(bad)
        self.assertEqual([t for t in kinds(ev, "trunk") if t.kind == "grant"], [])
        self.assertGreaterEqual(d.stats()["crc_failed"], 1)

    def test_control_channel_lost_after_silence(self):
        dib = self.tsdu(self.iden())
        d, ev = run(dib)
        d.emit = lambda t, p: ev.append((t, p))
        quiet = np.zeros(4800 * 4, dtype=np.uint8)
        for b in enc.to_blocks(quiet, start_index=dib.size, t0=dib.size / 4800.0):
            d.feed(b)
        self.assertIn("cc_lost", [t.kind for t in kinds(ev, "trunk")])


class PacketDataTests(unittest.TestCase):
    def test_unconfirmed_header_is_decoded_when_enabled(self):
        dib = enc.build_pdu_unconfirmed(NAC, llid=0x00ABCD, sap=0x04, payload=b"hello radio world!")
        cfg = ProtocolConfig(channel="ch0", decode_data=True)
        d, ev = run(dib, config=cfg)
        c = kinds(ev, "call_end")[0]
        self.assertEqual((c.kind, c.target_id, c.meta["sap"], c.meta["blocks"], c.meta["format"]), (CallKind.DATA, 0xABCD, 4, 2, "unconfirmed"))
        self.assertEqual(d.stats()["duid"], {"PDU": 1})

    def test_data_is_off_by_default(self):
        dib = enc.build_pdu_unconfirmed(NAC, llid=1, sap=1, payload=b"x")
        _, ev = run(dib)
        self.assertEqual(kinds(ev, "call_start"), [])

    def test_corrupt_header_crc_is_rejected(self):
        dib = enc.build_pdu_unconfirmed(NAC, llid=1, sap=1, payload=b"abcdefghijklmnop")
        bad = dib.copy()
        start = fr.total_dibits(fr.HEADER_INFO_DIBITS) + 8
        bad[start : start + 70] ^= 1
        d, ev = run(bad, config=ProtocolConfig(channel="ch0", decode_data=True))
        self.assertEqual(kinds(ev, "call_start"), [])
        self.assertGreaterEqual(d.stats()["crc_failed"], 1)


if __name__ == "__main__":
    unittest.main()
