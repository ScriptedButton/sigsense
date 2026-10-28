"""P25 Phase 2 decoder: MAC parsing, then fragments -> events, and the full RF chain."""

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.types import SymbolBlock
from sigsense_api.dvoice.events import CallKind, EncryptionState, Modulation
from sigsense_api.dvoice.protocols.base import ProtocolConfig
from sigsense_api.dvoice.protocols.p25p2 import mac as mac_mod
from sigsense_api.dvoice.protocols.p25p2.decoder import P25P2Decoder
from sigsense_api.dvoice.testing import p25p2_encoder as enc
from sigsense_api.dvoice.testing.p25p2_encoder import P25P2Encoder, Slot, assemble_stream
from sigsense_api.dvoice.vocoder import registry

RATE = 6000.0


class MacParseTests(unittest.TestCase):
    def test_ptt(self):
        pdu = mac_mod.parse_mac(enc.ptt_pdu(2001, 31337, 0x84, 0x55, bytes(range(9))), "facch")
        st = pdu.structures[0]
        self.assertEqual((pdu.type_name, st.name), ("PTT", "PUSH_TO_TALK"))
        self.assertEqual((st.fields["group"], st.fields["source"], st.fields["algorithm"], st.fields["key_id"]),
                         (2001, 31337, 0x84, 0x55))
        self.assertEqual(st.fields["mi"], bytes(range(9)))

    def test_end_ptt(self):
        st = mac_mod.parse_mac(enc.end_ptt_pdu(7, 9, 0x123), "facch").structures[0]
        self.assertEqual((st.name, st.fields), ("END_PUSH_TO_TALK", {"nac": 0x123, "source": 9, "group": 7}))

    def test_active_group_user_and_idle(self):
        pdu = mac_mod.parse_mac(enc.active_pdu(300, 4000, 0x80), "sacch")
        self.assertEqual(pdu.type_name, "ACTIVE")
        self.assertEqual(pdu.structures[0].fields, {"service": 0x80, "group": 300, "source": 4000})
        idle = mac_mod.parse_mac(enc.idle_pdu(), "facch")
        self.assertEqual((idle.type_name, idle.structures), ("IDLE", []))

    def test_network_status_and_grant(self):
        f = mac_mod.parse_mac(enc.network_status_pdu(0xBEE00, 0x1C7, 0x013, 0x1005), "sacch").structures[0].fields
        self.assertEqual((f["wacn"], f["system_id"], f["nac"], f["channel"]), (0xBEE00, 0x1C7, 0x013, 0x1005))
        g = mac_mod.parse_mac(enc.grant_pdu(55, 66, 0x2003, 0x80), "lcch").structures[0].fields
        self.assertEqual((g["group"], g["source"], g["channel"], g["service"]), (55, 66, 0x2003, 0x80))

    def test_two_structures_are_walked_by_length(self):
        bits = enc.active_pdu(10, 20)
        enc.put(bits, 64, 8, 0x01)  # a second GVCU right after the first (7 octets from bit 8)
        enc.put(bits, 80, 16, 11)
        enc.put(bits, 96, 24, 21)
        pdu = mac_mod.parse_mac(bits, "sacch")
        self.assertEqual([s.fields["group"] for s in pdu.structures], [10, 11])

    def test_lcch_nac(self):
        bits = enc.idle_pdu(180)
        enc.put(bits, 152, 12, 0xABC)
        self.assertEqual(mac_mod.parse_mac(bits, "lcch").nac, 0xABC)


def run(dibits, enc_=None, extra=None, block=1000, config=None):
    enc_ = enc_ or P25P2Encoder()
    events = []
    extra = {"wacn": enc_.wacn, "system_id": enc_.system_id, "nac": enc_.nac,
             "ambe_channel_decode": lambda r: (r[:49], 0), **(extra or {})}
    with registry.use_test_double():
        dec = P25P2Decoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                           config or ProtocolConfig(channel="t", extra=extra))
        for i in range(0, dibits.size, block):
            chunk = dibits[i : i + block]
            dec.feed(SymbolBlock(chunk, None, RATE, i, i / RATE))
    return dec, events


def of(events, kind):
    return [p for t, p in events if t == kind]


def stream(**kw):
    e = P25P2Encoder()
    return e, assemble_stream(e.voice_call(**kw), lead_dibits=137)


class CallTests(unittest.TestCase):
    def test_clear_call(self):
        e, d = stream(group=2001, source=31337, superframes=3)
        dec, ev = run(d, e)
        start, end = of(ev, "call_start")[0], of(ev, "call_end")[0]
        self.assertEqual((start.target_id, start.source_id, start.slot, start.kind), (2001, 31337, 0, CallKind.GROUP))
        self.assertEqual(start.encryption.state, EncryptionState.CLEAR)
        # 3 superframes: the first loses a 4V slot to the PTT => 14 + 18 + 18 frames
        self.assertEqual((end.voice_frames, end.end_reason), (50, "terminator"))
        self.assertEqual(len(of(ev, "voice")), 50)
        self.assertEqual(of(ev, "talkgroup")[0].id, 2001)
        st = dec.stats()
        self.assertEqual((st["frames_bad"], st["fec_failed"], st["fec_corrected"]), (0, 0, 0))
        self.assertEqual(st["ess_ok"], 3)

    def test_voice_frames_arrive_in_order(self):
        e, d = stream(group=1, source=2, superframes=2)
        seen = []
        run(d, e, extra={"ambe_channel_decode": lambda r: (seen.append(int("".join(map(str, r[:6])), 2)) or r[:49], 0)})
        # frame counter = 18 * superframe + 4 * slot + j; the PTT replaced slot 0 of superframe 0
        self.assertEqual(seen, [n % 64 for n in range(4, 36)])

    def test_arbitrary_block_boundaries_give_identical_events(self):
        e, d = stream(group=2001, source=31337, superframes=2)
        ref = [(t, getattr(p, "voice_frames", None)) for t, p in run(d, e, block=len(d))[1] if t != "sync"]
        for block in (1, 7, 333, 719, 721):
            got = [(t, getattr(p, "voice_frames", None)) for t, p in run(d, e, block=block)[1] if t != "sync"]
            self.assertEqual(got, ref, block)

    def test_inverted_polarity(self):
        e, d = stream(group=2001, source=31337, superframes=2)
        dec, ev = run(d ^ 2, e)
        self.assertEqual(len(of(ev, "call_start")), 1)
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 32)
        self.assertTrue(dec.stats()["inverted"])

    def test_encrypted_call_is_reported_and_never_decoded(self):
        e, d = stream(group=2001, source=31337, superframes=3, algorithm=0x84, key_id=0x1234, mi=bytes(range(9)))
        dec, ev = run(d, e)
        start = of(ev, "call_start")[0]
        self.assertEqual(start.encryption.state, EncryptionState.ENCRYPTED)
        self.assertEqual((start.encryption.algorithm_id, start.encryption.key_id), (0x84, 0x1234))
        self.assertEqual(start.encryption.algorithm_name, "AES-256")
        self.assertEqual(len(of(ev, "voice")), 0)
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 50)
        self.assertEqual(of(ev, "talkgroup")[0].encrypted_calls, 1)

    def test_late_entry_without_ptt_learns_ids_and_encryption_from_sacch_and_ess(self):
        e, d = stream(group=444, source=555, superframes=3, with_ptt=False)
        dec, ev = run(d, e)
        starts = of(ev, "call_start")
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0].meta.get("late_entry_no_ptt"))
        end = of(ev, "call_end")[0]
        self.assertEqual((end.target_id, end.source_id), (444, 555))
        # nothing is played until the first ESS says the call is clear, then the held audio is released
        self.assertEqual(len(of(ev, "voice")), 3 * 18)
        self.assertEqual(end.encryption.state, EncryptionState.CLEAR)

    def test_service_options_set_emergency_and_priority(self):
        e, d = stream(group=444, source=555, superframes=2, service=0x85)
        _, ev = run(d, e)
        call = (of(ev, "call_update") or of(ev, "call_start"))[-1]
        self.assertTrue(call.emergency)
        self.assertEqual(call.priority, 5)

    def test_both_logical_channels_at_once(self):
        e = P25P2Encoder()
        a = e.voice_superframe(0, group=100, source=1)
        b = e.voice_superframe(100, group=200, source=2)
        frags = []
        for sf in range(3):
            frags += e.superframe([a, b], sf)
        _, ev = run(assemble_stream(frags), e)
        ended = {c.slot: c for c in of(ev, "call_end")}
        self.assertEqual({s: (c.target_id, c.source_id) for s, c in ended.items()}, {0: (100, 1), 1: (200, 2)})
        by_call = {}
        for v in of(ev, "voice"):
            by_call[v.call_id] = by_call.get(v.call_id, 0) + 1
        self.assertEqual(sorted(by_call.values()), [54, 54])

    def test_hang_time_ends_a_call_that_never_terminates(self):
        e = P25P2Encoder()
        frags = []
        for sf in range(2):
            frags += e.superframe([e.voice_superframe(0, group=9, source=8), e.idle_superframe()], sf)
        for sf in range(6):
            frags += e.superframe([e.idle_superframe(), e.idle_superframe()], sf)
        _, ev = run(assemble_stream(frags), e, config=ProtocolConfig(channel="t", hang_time_s=0.3, extra={
            "wacn": e.wacn, "system_id": e.system_id, "nac": e.nac, "ambe_channel_decode": lambda r: (r[:49], 0)}))
        self.assertEqual(of(ev, "call_end")[0].end_reason, "timeout")

    def test_corrupt_isch_is_bridged_by_the_sequence_prediction(self):
        e = P25P2Encoder()
        frags = e.voice_call(group=77, source=88, superframes=3)
        # smash the I-ISCH words of fragment 5 (and keep its sync): the predicted sequence must decode it
        frags[5] = frags[5].copy()
        frags[5][0:20] = (frags[5][0:20] + 1) & 3
        frags[5][180:200] = (frags[5][180:200] + 2) & 3
        dec, ev = run(assemble_stream(frags), e)
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 50)
        self.assertGreaterEqual(dec.stats()["isch_predicted"], 1)

    def test_garbage_does_not_lock_or_crash(self):
        rng = np.random.default_rng(4)
        dec, ev = run(rng.integers(0, 4, 40_000).astype(np.uint8))
        self.assertEqual(of(ev, "sync"), [])
        self.assertEqual(of(ev, "call_start"), [])

    def test_sync_loss_is_reported_and_the_call_is_ended(self):
        e, d = stream(group=2001, source=31337, superframes=4, lead_idle=0, tail_idle=0)
        cut = 720 * 8
        d = np.concatenate([d[:cut], np.random.default_rng(2).integers(0, 4, 720 * 8).astype(np.uint8)])
        dec, ev = run(d, e)
        self.assertEqual(of(ev, "call_end")[0].end_reason in ("sync_loss", "timeout"), True)
        self.assertTrue(any(p.code == "sync_lost" for p in of(ev, "error")))

    def test_gap_in_symbol_index_resets_the_lock(self):
        e, d = stream(group=2001, source=31337, superframes=4)
        events = []
        with registry.use_test_double():
            dec = P25P2Decoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                               ProtocolConfig(channel="t", extra={"wacn": e.wacn, "system_id": e.system_id, "nac": e.nac,
                                                                  "ambe_channel_decode": lambda r: (r[:49], 0)}))
            half = 720 * 7
            dec.feed(SymbolBlock(d[:half], None, RATE, 0, 0.0))
            dec.feed(SymbolBlock(d[half + 100 :], None, RATE, half + 5000, 1.0))
        self.assertIn("sync_loss", [c.end_reason for c in of(events, "call_end")])


class ScrambleParameterTests(unittest.TestCase):
    def test_voice_without_parameters_says_why(self):
        e, d = stream(group=2001, source=31337, superframes=2)
        events = []
        dec = P25P2Decoder(lambda t, p: events.append((t, p)), lambda v: None, ProtocolConfig(channel="t"))
        for i in range(0, d.size, 1000):
            dec.feed(SymbolBlock(d[i : i + 1000], None, RATE, i, i / RATE))
        self.assertEqual(of(events, "call_start"), [])
        codes = [p.code for p in of(events, "error")]
        self.assertEqual(codes.count("p25p2_scramble_unknown"), 1)
        self.assertGreater(dec.stats()["voice_slots_unscrambled"], 0)

    def test_parameters_are_learned_from_an_unscrambled_network_status_broadcast(self):
        e = P25P2Encoder()
        nsb = Slot("sacch", payload=enc.network_status_pdu(e.wacn, e.system_id, e.nac), scrambled=False)
        sup = e.idle_superframe()
        sup[5] = nsb
        frags = e.superframe([sup, e.idle_superframe()], 0) + e.voice_call(group=12, source=34, superframes=2)
        events = []
        dec = P25P2Decoder(lambda t, p: events.append((t, p)), lambda v: None,
                           ProtocolConfig(channel="t", extra={"ambe_channel_decode": lambda r: (r[:49], 0)}))
        d = assemble_stream(frags)
        with registry.use_test_double():
            dec.vocoders = registry.get_vocoder
            for i in range(0, d.size, 1000):
                dec.feed(SymbolBlock(d[i : i + 1000], None, RATE, i, i / RATE))
        info = of(events, "system_info")[0]
        self.assertEqual((info.wacn, info.system_id, info.nac), (e.wacn, e.system_id, e.nac))
        self.assertEqual(of(events, "call_start")[0].target_id, 12)
        self.assertEqual(len(of(events, "voice")), 14 + 18)

    def test_wrong_parameters_decode_nothing(self):
        e, d = stream(group=2001, source=31337, superframes=2)
        _, ev = run(d, e, extra={"nac": e.nac ^ 1})
        self.assertEqual(of(ev, "call_start"), [])


class GrantTests(unittest.TestCase):
    def test_lcch_group_grant_is_a_trunk_event(self):
        e = P25P2Encoder()
        lcch = Slot("lcch", payload=enc.grant_pdu(55, 66, 0x2003, 0x80, size=164))
        sup = [lcch] + [Slot("lcch", payload=enc.idle_pdu(164)) for _ in range(5)]
        frags = e.superframe([sup, e.idle_superframe()], 0)
        _, ev = run(assemble_stream(frags), e)
        grants = [p for p in of(ev, "trunk") if p.kind == "grant"]
        self.assertEqual(len(grants), 1)
        g = grants[0]
        self.assertEqual((g.talkgroup, g.source_id, g.detail["channel"], g.encrypted), (55, 66, 0x2003, False))


class FullChainTests(unittest.TestCase):
    """modulate -> channel -> CQPSK symbol recovery -> decoder at 6000 baud."""

    def chain(self, snr_db, **channel):
        from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
        from sigsense_api.dvoice.testing.channel import impair
        from sigsense_api.dvoice.testing.modulator import modulate

        e, d = stream(group=2001, source=31337, superframes=3)
        iq = impair(modulate(d, RATE, Modulation.CQPSK, 48_000.0), snr_db, seed=1, sample_rate=48_000.0,
                    snr_bandwidth_hz=12_500.0, **channel)
        rec = CqpskSymbolRecovery(RATE)
        events = []
        with registry.use_test_double():
            dec = P25P2Decoder(lambda t, p: events.append((t, p)), registry.get_vocoder,
                               ProtocolConfig(channel="e2e", extra={"wacn": e.wacn, "system_id": e.system_id, "nac": e.nac,
                                                                    "ambe_channel_decode": lambda r: (r[:49], 0)}))
            for i in range(0, iq.size, 4096):
                sym = rec.process(iq[i : i + 4096])
                if sym.dibits.size:
                    dec.feed(sym)
        return dec, events

    def test_clean(self):
        _, ev = self.chain(40.0)
        self.assertEqual(of(ev, "call_end")[0].voice_frames, 50)

    def test_noise_carrier_offset_and_clock_error(self):
        dec, ev = self.chain(18.0, freq_offset_hz=600.0, timing_ppm=40.0)
        self.assertEqual(len(of(ev, "call_start")), 1, dec.stats())
        self.assertGreaterEqual(of(ev, "call_end")[0].voice_frames, 40)

    def test_inverted_carrier(self):
        _, ev = self.chain(30.0, invert=True)
        self.assertEqual(len(of(ev, "call_start")), 1)


class EngineIntegrationTests(unittest.TestCase):
    def test_decoder_is_reported_implemented(self):
        from sigsense_api.dvoice import engine as eng

        info = eng.protocol_support()["p25p2"]
        self.assertTrue(info["implemented"], info)
        self.assertEqual((info["symbol_rate"], info["modulation"], info["timeslots"]), (6000.0, "cqpsk", 2))


if __name__ == "__main__":
    unittest.main()
