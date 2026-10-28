"""DMR decoder state-machine tests (WP-D): synthetic captures from testing/dmr_encoder.py."""

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import CallKind, EncryptionState
from sigsense_api.dvoice.protocols.base import ProtocolConfig
from sigsense_api.dvoice.protocols.dmr import constants as C
from sigsense_api.dvoice.protocols.dmr import csbk as csbk_mod
from sigsense_api.dvoice.protocols.dmr import data as data_mod
from sigsense_api.dvoice.protocols.dmr import lc
from sigsense_api.dvoice.protocols.dmr.decoder import DmrDecoder
from sigsense_api.dvoice.testing.dmr_encoder import (
    DmrEncoder,
    assemble_bs_stream,
    assemble_direct_stream,
    default_voice_bits,
)
from sigsense_api.dvoice.vocoder import registry


def group_lc(tg=9, src=0x1234, so=0x00, fid=0, flco=0):
    return lc.build_full_lc(flco, fid, bytes([so]) + tg.to_bytes(3, "big") + src.to_bytes(3, "big"))


def run(stream, *, chunks=None, seed=0, extra=None, config=None, first_index=0, hook=True, blocks=None):
    """Feed `stream` to a fresh decoder; returns (decoder, events, raw frames seen by the hook)."""
    events, raw = [], []

    def channel_decode(r72):
        raw.append(np.asarray(r72).copy())
        return r72[:49], 0

    cfg = config or ProtocolConfig(channel="t", extra=dict(extra or {}))
    if hook:
        cfg.extra.setdefault("ambe_channel_decode", channel_decode)
    with registry.use_test_double():
        dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder, cfg)
        rng = np.random.default_rng(seed)
        i = 0
        while i < stream.size:
            n = chunks if chunks else int(rng.integers(1, 260))
            dec.feed(SymbolBlock(stream[i : i + n], None, 4800.0, first_index + i, (first_index + i) / 4800.0))
            i += n
    return dec, events, raw


def of(events, kind):
    return [p for t, p in events if t == kind]


def voice_call_stream(enc=None, tg=9, src=0x1234, superframes=3, so=0x00, slot1=None, **kw):
    enc = enc or DmrEncoder(cc=3)
    bursts = enc.voice_call(group_lc(tg, src, so), superframes=superframes, **kw)
    other = slot1 if slot1 is not None else [enc.idle_burst()] * len(bursts)
    # C_SYS_Parms (SLCO 2) in the CACH: tiny model, NET 5, SITE 1, Reg 0, slot counter 7 (TS 102 361-4 table 7.33)
    short = (2, (0 << 22) | (((5 << 3) | 1) << 10) | (0 << 9) | 7)
    return enc, assemble_bs_stream(bursts, other, enc, short_lc=short, lead_idle=2, tail_idle=8)


class VoiceCallTests(unittest.TestCase):
    def test_group_call_metadata_frames_and_terminator(self):
        _, stream = voice_call_stream()
        dec, ev, raw = run(stream)
        starts, ends = of(ev, "call_start"), of(ev, "call_end")
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        c = starts[0]
        self.assertEqual((c.kind, c.target_id, c.source_id, c.slot), (CallKind.GROUP, 9, 0x1234, 0))
        self.assertEqual(c.encryption.state, EncryptionState.CLEAR)
        self.assertEqual(ends[0].end_reason, "terminator")
        self.assertEqual(ends[0].voice_frames, 54)  # 3 superframes x 6 bursts x 3 frames
        self.assertEqual(len(of(ev, "voice")), 54)
        self.assertEqual(dec.stats()["color_code"], 3)
        self.assertEqual(dec.stats()["frames_bad"], 0)

    def test_vocoder_frames_arrive_in_order_with_the_right_bits(self):
        _, stream = voice_call_stream(superframes=2)
        _, _, raw = run(stream)
        self.assertEqual(len(raw), 36)
        for n, frame in enumerate(raw):
            burst, k = divmod(n, 3)
            self.assertTrue(np.array_equal(frame, default_voice_bits(burst, k)), n)

    def test_chunk_invariance(self):
        _, stream = voice_call_stream()
        sigs = []
        for chunks, seed in ((1, 0), (7, 0), (None, 1), (None, 2), (10_000, 0)):
            _, ev, _ = run(stream, chunks=chunks, seed=seed)
            sigs.append([
                (t, getattr(p, "call_id", None), getattr(p, "target_id", None), getattr(p, "source_id", None),
                 getattr(p, "voice_frames", None), getattr(p, "end_reason", None))
                for t, p in ev if t != "sync"
            ])
        for s in sigs[1:]:
            self.assertEqual(s, sigs[0])

    def test_late_entry_starts_from_the_embedded_lc(self):
        enc, stream = voice_call_stream(header=False, terminator=False, superframes=3)
        dec, ev, _ = run(stream)
        starts = of(ev, "call_start")
        self.assertEqual(len(starts), 1)
        self.assertEqual((starts[0].target_id, starts[0].source_id), (9, 0x1234))
        self.assertFalse(starts[0].meta.get("late_entry_no_lc"))

    def test_call_ends_by_timeout_when_the_terminator_is_lost(self):
        enc = DmrEncoder(cc=3)
        bursts = enc.voice_call(group_lc(), superframes=2, terminator=False)
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=2, tail_idle=40)
        _, ev, _ = run(stream)
        self.assertEqual([c.end_reason for c in of(ev, "call_end")], ["timeout"])

    def test_emergency_and_private_calls(self):
        enc = DmrEncoder(cc=1)
        lc9 = lc.build_full_lc(3, 0, bytes([0x80]) + (55).to_bytes(3, "big") + (66).to_bytes(3, "big"))
        bursts = enc.voice_call(lc9, superframes=1)
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=2, tail_idle=6)
        _, ev, _ = run(stream)
        c = of(ev, "call_start")[0]
        self.assertEqual((c.kind, c.target_id, c.source_id, c.emergency), (CallKind.PRIVATE, 55, 66, True))

    def test_embedded_talker_alias_updates_the_call(self):
        enc = DmrEncoder(cc=3)
        parts = lc.encode_talker_alias("Unit 42", 0)
        extras = [lc.build_full_lc(f, 0, d) for f, d in zip(
            (lc.FLCO_TA_HEADER, lc.FLCO_TA_BLOCK1, lc.FLCO_TA_BLOCK2, lc.FLCO_TA_BLOCK3), parts)]
        bursts = enc.voice_call(group_lc(), superframes=5, extra_lcs=extras)
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=2, tail_idle=8)
        _, ev, _ = run(stream)
        aliases = [c.meta.get("talker_alias") for c in of(ev, "call_update")]
        self.assertIn("Unit 42", aliases)
        self.assertEqual(of(ev, "call_end")[0].meta.get("talker_alias"), "Unit 42")


class TwoSlotTests(unittest.TestCase):
    def test_two_simultaneous_calls_stay_separate(self):
        enc = DmrEncoder(cc=5)
        a = enc.voice_call(group_lc(100, 1), superframes=3)
        b = enc.voice_call(group_lc(200, 2), superframes=3)
        stream = assemble_bs_stream(a, b, enc, lead_idle=2, tail_idle=8)
        _, ev, _ = run(stream)
        starts = of(ev, "call_start")
        self.assertEqual(sorted((c.slot, c.target_id, c.source_id) for c in starts), [(0, 100, 1), (1, 200, 2)])
        ends = {c.slot: c for c in of(ev, "call_end")}
        self.assertEqual(ends[0].voice_frames, ends[1].voice_frames)
        self.assertEqual({c.call_id for c in of(ev, "call_start")}, {"dmr-t-0-1", "dmr-t-1-1"})
        self.assertEqual(len({v.call_id for v in of(ev, "voice")}), 2)

    def test_offset_calls_on_each_slot(self):
        enc = DmrEncoder(cc=5)
        a = enc.voice_call(group_lc(100, 1), superframes=2)
        b = [enc.idle_burst()] * 10 + enc.voice_call(group_lc(200, 2), superframes=2)
        _, ev, _ = run(assemble_bs_stream(a, b, enc, lead_idle=2, tail_idle=8))
        self.assertEqual(sorted(c.target_id for c in of(ev, "call_start")), [100, 200])

    def test_sequential_calls_on_one_slot_get_new_ids(self):
        enc = DmrEncoder(cc=2)
        first = enc.voice_call(group_lc(7, 70), superframes=1)
        gap = [enc.idle_burst()] * 4
        second = enc.voice_call(group_lc(8, 80), superframes=1)
        stream = assemble_bs_stream(first + gap + second, [enc.idle_burst()] * (len(first) + 4 + len(second)), enc,
                                    lead_idle=2, tail_idle=6)
        _, ev, _ = run(stream)
        self.assertEqual([(c.call_id, c.target_id) for c in of(ev, "call_start")], [("dmr-t-0-1", 7), ("dmr-t-0-2", 8)])


class PrivacyTests(unittest.TestCase):
    def test_privacy_flagged_call_is_reported_and_never_decoded(self):
        _, stream = voice_call_stream(so=0x40)
        dec, ev, raw = run(stream)
        c = of(ev, "call_start")[0]
        self.assertEqual(c.encryption.state, EncryptionState.ENCRYPTED)
        self.assertFalse(c.encryption.audio_decoded)
        self.assertEqual(of(ev, "voice"), [])
        self.assertEqual(raw, [])
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 54)  # frames are counted, never decoded

    def test_pi_header_marks_the_call_encrypted(self):
        enc = DmrEncoder(cc=3)
        bursts = enc.voice_call(group_lc(), superframes=2)
        pi = enc.pi_header(bytes(10))
        bursts.insert(1, pi)  # after the header, before the voice
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=2, tail_idle=6)
        _, ev, raw = run(stream)
        ends = of(ev, "call_end")
        self.assertEqual(ends[0].encryption.state, EncryptionState.ENCRYPTED)
        self.assertEqual(of(ev, "voice"), [])

    def test_manufacturer_lc_is_not_claimed_as_clear(self):
        enc = DmrEncoder(cc=3)
        lc9 = lc.build_full_lc(0, 0x10, bytes(7))
        bursts = enc.voice_call(lc9, superframes=1)
        _, ev, _ = run(assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=2, tail_idle=6))
        c = of(ev, "call_start")[0]
        self.assertEqual(c.encryption.state, EncryptionState.UNKNOWN)
        self.assertEqual(c.meta.get("mfid"), 0x10)


class VocoderRoutingTests(unittest.TestCase):
    def test_without_a_configured_hook_the_default_voice_map_decodes_the_frames(self):
        _, stream = voice_call_stream(superframes=2)
        dec, ev, _ = run(stream, hook=False)
        self.assertEqual([e for e in of(ev, "error") if e.code == "vocoder_unavailable"], [])
        self.assertEqual(len(of(ev, "voice")), 36)
        self.assertEqual(dec.stats()["voice_frames_unavailable"], 0)

    def test_without_a_vocoder_backend_there_is_an_explicit_error_and_no_audio(self):
        from sigsense_api.dvoice.vocoder.base import UnavailableVocoder

        _, stream = voice_call_stream(superframes=2)
        events = []
        cfg = ProtocolConfig(channel="t", extra={})
        dec = DmrDecoder(
            lambda t, p: events.append((t, p)),
            lambda vid: UnavailableVocoder(vocoder_id=vid),
            cfg,
        )
        dec.feed(SymbolBlock(stream, None, 4800.0, 0, 0.0))
        errs = [e for e in of(events, "error") if e.code == "vocoder_unavailable"]
        self.assertEqual(len(errs), 1)
        self.assertIn("vocoder backend", errs[0].message)
        self.assertEqual(of(events, "voice"), [])
        self.assertEqual(len(of(events, "call_start")), 1)  # metadata still decoded

    def test_test_double_produces_tones_whose_pitch_follows_the_frame_order(self):
        _, stream = voice_call_stream(superframes=1)
        _, ev, _ = run(stream)
        voice = of(ev, "voice")
        self.assertEqual([v.seq for v in voice], list(range(1, 19)))
        self.assertTrue(all(v.pcm is not None and len(v.pcm) == 160 for v in voice))

    def test_a_missing_backend_never_crashes_the_decoder(self):
        _, stream = voice_call_stream(superframes=1)
        events = []
        dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                         ProtocolConfig(channel="t", extra={"ambe_channel_decode": lambda r: (r[:49], 0)}))
        dec.feed(SymbolBlock(stream, None, 4800.0, 0, 0.0))
        self.assertEqual(len(of(events, "call_start")), 1)


class ChannelTests(unittest.TestCase):
    def test_inverted_polarity_stream(self):
        _, stream = voice_call_stream()
        _, ev, _ = run(stream ^ 2)
        self.assertEqual(len(of(ev, "call_start")), 1)
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 54)

    def test_front_end_polarity_hint(self):
        _, stream = voice_call_stream()
        events = []
        with registry.use_test_double():
            dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                             ProtocolConfig(channel="t", extra={"ambe_channel_decode": lambda r: (r[:49], 0)}))
            blk = SymbolBlock(stream ^ 2, None, 4800.0, 0, 0.0, polarity_inverted=True)
            dec.feed(blk)
        self.assertEqual(of(events, "call_end")[0].voice_frames, 54)

    def test_noise_never_creates_a_call(self):
        rng = np.random.default_rng(11)
        _, ev, _ = run(rng.integers(0, 4, 150_000).astype(np.uint8), chunks=4000)
        for kind in ("call_start", "system_info", "trunk", "talkgroup"):
            self.assertEqual(of(ev, kind), [], kind)

    def test_symbol_errors_degrade_gracefully(self):
        _, clean = voice_call_stream(superframes=4)
        rng = np.random.default_rng(5)
        results = {}
        for p in (0.0, 0.002, 0.01):
            ok = 0
            for trial in range(6):
                stream = clean.copy()
                flips = rng.random(stream.size) < p
                stream[flips] = rng.integers(0, 4, int(flips.sum()))
                _, ev, _ = run(stream, chunks=500)
                c = of(ev, "call_end")
                ok += bool(c) and c[0].target_id == 9 and c[0].source_id == 0x1234
            results[p] = ok
        self.assertEqual(results[0.0], 6)
        self.assertGreaterEqual(results[0.002], 5)  # 0.2% symbol errors: header + embedded LC redundancy
        self.assertGreaterEqual(results[0.01], 3)

    def test_wrong_colour_code_is_ignored_once_the_channel_cc_is_known(self):
        enc, stream = voice_call_stream()
        foreign = DmrEncoder(cc=9).voice_call(group_lc(555, 777), superframes=2)
        bursts_a = enc.voice_call(group_lc(), superframes=3)
        # interleave a co-channel signal on slot 1
        stream = assemble_bs_stream(bursts_a, foreign + [enc.idle_burst()] * 20, enc, lead_idle=2, tail_idle=6)
        _, ev, _ = run(stream)
        self.assertEqual([c.target_id for c in of(ev, "call_start")], [9])

    def test_forced_colour_code(self):
        _, stream = voice_call_stream()
        _, ev, _ = run(stream, extra={"color_code": 4})
        self.assertEqual(of(ev, "call_start"), [])

    def test_gap_in_the_stream_ends_calls_and_relocks(self):
        _, stream = voice_call_stream(superframes=2, terminator=False)
        half = stream.size // 2
        events = []
        with registry.use_test_double():
            dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                             ProtocolConfig(channel="t", extra={"ambe_channel_decode": lambda r: (r[:49], 0)}))
            dec.feed(SymbolBlock(stream[:half], None, 4800.0, 0, 0.0))
            dec.feed(SymbolBlock(stream[half:], None, 4800.0, half + 5000, 1.0))  # 5000 symbols missing
        self.assertIn("sync_loss", [c.end_reason for c in of(events, "call_end")])

    def test_direct_mode_ms_stream_with_gaps(self):
        enc = DmrEncoder(cc=4, source="ms")
        bursts = enc.voice_call(group_lc(31, 41), superframes=3)
        _, ev, _ = run(assemble_direct_stream(bursts))
        c = of(ev, "call_start")
        self.assertEqual((c[0].target_id, c[0].source_id), (31, 41))
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 54)

    def test_direct_mode_slot_comes_from_the_sync_pattern(self):
        for src, slot in (("dm1", 0), ("dm2", 1)):
            enc = DmrEncoder(cc=4, source=src)
            bursts = enc.voice_call(group_lc(31, 41), superframes=2)
            _, ev, _ = run(assemble_direct_stream(bursts))
            self.assertEqual(of(ev, "call_start")[0].slot, slot, src)


class SystemInfoTests(unittest.TestCase):
    def test_short_lc_in_the_cach_reports_the_network(self):
        _, stream = voice_call_stream(superframes=2)
        _, ev, _ = run(stream)
        infos = [s for s in of(ev, "system_info") if s.network_id is not None]
        self.assertTrue(infos)
        s = infos[0]
        self.assertEqual((s.color_code, s.network_id, s.site_id, s.extra["model"]), (3, 5, 1, "tiny"))

    def test_c_aloha_and_grants_on_a_control_channel(self):
        enc = DmrEncoder(cc=2)
        sysc = (1 << 14) | (((5 << 5) | 9) << 2) | 1  # small model: NET 7 bits, SITE 5 bits
        aloha = csbk_mod.Csbk(1, 0, 0b011001, 0, bytes([0b0_1_1_010_1_1, 0, 0, sysc >> 8, sysc & 0xFF, 0, 0, 1]))
        lpcn = (0x0AB << 4) | (1 << 3) | (1 << 1)
        grant = csbk_mod.Csbk(1, 0, 0b110001, 0, lpcn.to_bytes(2, "big") + (300).to_bytes(3, "big") + (400).to_bytes(3, "big"))
        bursts = [enc.csbk(aloha), enc.csbk(grant), enc.csbk(aloha), enc.csbk(grant)]
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * len(bursts), enc, lead_idle=2, tail_idle=4)
        _, ev, _ = run(stream)
        kinds = [t.kind for t in of(ev, "trunk")]
        self.assertEqual(kinds.count("cc_acquired"), 1)
        grants = [t for t in of(ev, "trunk") if t.kind == "grant"]
        self.assertTrue(grants)
        g = grants[0]
        self.assertEqual((g.talkgroup, g.source_id, g.slot, g.detail["logical_channel"]), (300, 400, 1, 0x0AB))
        sys = [s for s in of(ev, "system_info") if s.network_id is not None][0]
        self.assertEqual((sys.network_id, sys.site_id, sys.extra["model"]), (5, 9, "small"))

    def test_corrupted_csbk_is_dropped(self):
        enc = DmrEncoder(cc=2)
        grant = csbk_mod.Csbk(1, 0, 0b110001, 0, bytes(8))
        bits = csbk_mod.encode_csbk_block(grant)
        bits[::3] ^= 1  # destroy it
        burst = enc.data_burst(C.DataType.CSBK, bits)
        stream = assemble_bs_stream([enc.idle_burst()] * 3 + [burst] * 3, [enc.idle_burst()] * 6, enc, tail_idle=4)
        _, ev, _ = run(stream)
        self.assertEqual(of(ev, "trunk"), [])


class DataTests(unittest.TestCase):
    def _data_call(self, blocks_fn, header_kw=None, decode=True):
        enc = DmrEncoder(cc=6)
        payload = b"Hello DMR data!"  # 15 bytes -> pad to even
        payload += b"\x00" * (len(payload) % 2)
        from sigsense_api.dvoice.fec import crc

        msg = payload + crc.crc32_dmr_message(payload)
        bursts = blocks_fn(enc, msg)
        return enc, bursts

    def test_rate_half_unconfirmed_message(self):
        enc = DmrEncoder(cc=6)
        payload = b"Hello DMR data!\x00"
        from sigsense_api.dvoice.fec import crc

        msg = payload + crc.crc32_dmr_message(payload)  # 20 bytes -> two 12-octet blocks (4 padding)
        msg += b"\x00" * (24 - len(msg))
        # the message CRC must sit at the end of the data: rebuild with the CRC last in the final block
        body = payload + b"\x00" * (24 - 4 - len(payload))
        msg = body + crc.crc32_dmr_message(body)
        hdr = data_mod.build_data_header(dpf=0b0010, sap=0b1010, dst=1001, src=2002, blocks=2, group=False)
        bursts = [enc.data_header(hdr), enc.data_block_half(msg[:12]), enc.data_block_half(msg[12:])]
        stream = assemble_bs_stream(bursts, [enc.idle_burst()] * 3, enc, lead_idle=2, tail_idle=10)
        _, ev, _ = run(stream, config=ProtocolConfig(channel="t", decode_data=True))
        c = of(ev, "call_end")[0]
        self.assertEqual((c.kind, c.target_id, c.source_id), (CallKind.DATA, 1001, 2002))
        self.assertEqual(c.meta["dpf"], "unconfirmed")
        self.assertEqual(c.meta["sap"], "short data")
        self.assertTrue(c.meta["message_crc_ok"])
        self.assertEqual(bytes.fromhex(c.meta["payload_hex"]), body)

    def test_payload_is_withheld_unless_data_decoding_is_enabled(self):
        enc = DmrEncoder(cc=6)
        from sigsense_api.dvoice.fec import crc

        body = bytes(range(20))
        msg = body + crc.crc32_dmr_message(body)
        hdr = data_mod.build_data_header(dpf=0b0010, sap=0b0100, dst=1, src=2, blocks=2)
        bursts = [enc.data_header(hdr), enc.data_block_half(msg[:12]), enc.data_block_half(msg[12:])]
        _, ev, _ = run(assemble_bs_stream(bursts, [enc.idle_burst()] * 3, enc, lead_idle=2, tail_idle=10))
        c = of(ev, "call_end")[0]
        self.assertTrue(c.meta["message_crc_ok"])
        self.assertNotIn("payload_hex", c.meta)

    def test_rate_three_quarter_blocks(self):
        enc = DmrEncoder(cc=6)
        from sigsense_api.dvoice.fec import crc

        body = bytes(range(32))
        msg = body + crc.crc32_dmr_message(body)  # 36 bytes = two 18-octet blocks
        hdr = data_mod.build_data_header(dpf=0b0010, sap=0b0100, dst=5, src=6, blocks=2)
        bursts = [enc.data_header(hdr), enc.data_block_three_quarter(msg[:18]), enc.data_block_three_quarter(msg[18:])]
        _, ev, _ = run(assemble_bs_stream(bursts, [enc.idle_burst()] * 3, enc, lead_idle=2, tail_idle=10),
                       config=ProtocolConfig(channel="t", decode_data=True))
        c = of(ev, "call_end")[0]
        self.assertTrue(c.meta["message_crc_ok"])
        self.assertEqual(bytes.fromhex(c.meta["payload_hex"]), body)

    def test_a_bad_block_fails_the_message_crc_and_is_reported(self):
        enc = DmrEncoder(cc=6)
        from sigsense_api.dvoice.fec import crc

        body = bytes(range(20))
        msg = body + crc.crc32_dmr_message(body)
        hdr = data_mod.build_data_header(dpf=0b0010, sap=0b0100, dst=1, src=2, blocks=2)
        bad = bytearray(msg[12:])
        bad[3] ^= 0x55
        # a wrong payload that is still a valid BPTC block: the message CRC must catch it
        bursts = [enc.data_header(hdr), enc.data_block_half(msg[:12]), enc.data_block_half(bytes(bad))]
        _, ev, _ = run(assemble_bs_stream(bursts, [enc.idle_burst()] * 3, enc, lead_idle=2, tail_idle=10),
                       config=ProtocolConfig(channel="t", decode_data=True))
        c = of(ev, "call_end")[0]
        self.assertFalse(c.meta["message_crc_ok"])
        self.assertNotIn("payload_hex", c.meta)


class ResetTests(unittest.TestCase):
    def test_reset_ends_calls_and_clears_state(self):
        _, stream = voice_call_stream(superframes=2, terminator=False)
        events = []
        with registry.use_test_double():
            dec = DmrDecoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                             ProtocolConfig(channel="t", extra={"ambe_channel_decode": lambda r: (r[:49], 0)}))
            dec.feed(SymbolBlock(stream[: int(stream.size * 0.6)], None, 4800.0, 0, 0.0))
            self.assertEqual(len(of(events, "call_start")), 1)
            dec.reset()
        self.assertEqual([c.end_reason for c in of(events, "call_end")], ["retune"])
        self.assertEqual(dec.stats()["calls"], 0)


if __name__ == "__main__":
    unittest.main()
