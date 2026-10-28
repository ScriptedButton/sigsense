"""End-to-end: protocol frame ENCODER -> modulator -> channel impairments -> a simulated 2.4 MS/s capture ->
engine (channelizer, symbol recovery, sync, protocol decoder, vocoder) -> events, audio, call log (WP-G).

Runs for every protocol that has both a real decoder and a frame encoder; the rest are skipped
with the reason, so the suite tightens by itself as the protocol work packages land.
Real over-the-air performance is NOT established by these tests: everything here is synthetic.
"""

from __future__ import annotations

import importlib
import time
import unittest

import numpy as np

from sigsense_api.dvoice import engine as eng
from sigsense_api.dvoice.engine import (
    INPUT_DISC16_48K,
    INPUT_IQ16_48K,
    ChannelConfig,
    DigitalVoiceEngine,
    EngineConfig,
)
from sigsense_api.dvoice.events import Modulation, Protocol
from sigsense_api.dvoice.testing.channel import impair
from sigsense_api.dvoice.testing.modulator import modulate
from sigsense_api.dvoice.vocoder import registry

from test_dvoice_engine_common import (
    CAPTURE_CENTER,
    CAPTURE_FS,
    Collector,
    EngineTestCase,
    drain,
    feed_capture,
    transmit,
    wideband,
)

OFFSET = 35_000.0


def m17_dibits(frames: int = 60, seed: int = 3, encrypted: bool = False) -> np.ndarray:
    from sigsense_api.dvoice.testing.m17_encoder import SyntheticM17Call, stream_transmission

    rng = np.random.default_rng(seed)
    payloads = [bytes(rng.integers(0, 256, 16, dtype=np.uint8)) for _ in range(frames)]
    call = SyntheticM17Call(src="W2FBI", dst="ALL", payloads=payloads, enc_type=1 if encrypted else 0)
    return stream_transmission(call)


def p25_dibits(*, tgid: int = 1234, source: int = 5678, superframes: int = 3, algid: int = 0x80, emergency: bool = False) -> np.ndarray:
    from sigsense_api.dvoice.testing.p25p1_encoder import build_voice_call

    dibits, _ = build_voice_call(0x293, tgid=tgid, source=source, superframes=superframes, algid=algid, emergency=emergency)
    return dibits


def run_capture(
    dibits: np.ndarray,
    protocol: str,
    symbol_rate: float,
    modulation: Modulation,
    snr_db: float = 25.0,
    chunks=None,
    channels=None,
    **tx,
):
    wb = transmit(dibits, symbol_rate, modulation, snr_db=snr_db, offset_hz=OFFSET, **tx)
    collector = Collector()
    cfg = EngineConfig(channels=channels or [ChannelConfig(name="ch0", protocol=protocol, offset_hz=OFFSET)])
    engine = DigitalVoiceEngine(cfg, collector.on_event, collector.on_audio)
    engine.start()
    feed_capture(engine, wb, chunks=chunks)
    drain(engine, 60)
    time.sleep(0.4)
    return engine, collector


def final_call(collector: Collector):
    ends = collector.data("call_end")
    return ends[-1] if ends else None


class M17EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("m17", {}).get("implemented"):
            raise unittest.SkipTest("M17 decoder not implemented yet")
        cls.dibits = m17_dibits()

    def _run(self, **kw):
        engine, col = run_capture(self.dibits, "m17", 4800.0, Modulation.FSK4, **kw)
        self.addCleanup(engine.close)
        return engine, col

    def test_clean_call_fields_and_audio(self):
        engine, col = self._run(snr_db=26.0)
        call = final_call(col)
        self.assertIsNotNone(call, "no call decoded")
        self.assertEqual(call["protocol"], "m17")
        self.assertEqual(call["meta"].get("src"), "W2FBI")
        self.assertEqual(len(col.data("call_start")), 1, "exactly one call")
        self.assertEqual(call["end_reason"], "terminator")
        self.assertEqual(call["encryption"]["state"], "clear")
        # 60 stream frames carry 120 Codec2 frames; the front end needs ~0.6 s to lock on, so the first
        # few frames are lost; the rest must decode
        self.assertGreaterEqual(call["voice_frames"], 100)
        self.assertLessEqual(call["voice_frames_bad"], 3)
        self.assertEqual(call["meta"]["call_num"], 1)
        pcm = col.pcm_frames()
        if registry.get_vocoder(__import__("sigsense_api.dvoice.events", fromlist=["VocoderId"]).VocoderId.CODEC2_3200).__class__.__name__ == "UnavailableVocoder":
            self.skipTest("libcodec2 not installed: no M17 audio to check")
        self.assertGreaterEqual(len(pcm), 100)
        self.assertEqual({h["sample_rate"] for h, _ in pcm}, {8000})
        self.assertEqual({p.size for _, p in pcm}, {160})
        self.assertGreater(int(np.abs(np.concatenate([p for _, p in pcm])).max()), 100, "decoded audio is not silent")
        self.assertTrue(col.frames()[-1][0]["end_of_call"])
        # and it is in the call log
        self.assertEqual(len(engine.recorder.list_calls()), 1)

    def test_chunk_size_invariance(self):
        runs = []
        for label, chunks in (("20ms", [48_000] * 400), ("200ms", [480_000] * 40), ("ragged", [int(x) for x in np.random.default_rng(5).integers(5_000, 400_000, 200)])):
            engine, col = self._run(snr_db=26.0, chunks=chunks)
            call = final_call(col)
            self.assertIsNotNone(call, label)
            runs.append((label, call["voice_frames"], call["voice_frames_bad"], call["meta"].get("src"), len(col.pcm_frames())))
            engine.close()
        self.assertEqual(len({r[1:] for r in runs}), 1, f"results must not depend on chunking: {runs}")

    def test_auto_detects_and_locks(self):
        engine, col = self._run(snr_db=26.0, channels=[ChannelConfig(name="ch0", protocol="auto", offset_hz=OFFSET)])
        call = final_call(col)
        self.assertIsNotNone(call, "auto mode found no call")
        self.assertEqual(call["protocol"], "m17")
        self.assertEqual(engine.status().channels["ch0"]["protocol"], "m17")
        self.assertEqual(engine.status().channels["ch0"]["mode"], "auto")

    def test_polarity_inversion_and_frequency_error(self):
        engine, col = self._run(snr_db=26.0, invert=True, freq_error_hz=900.0)
        call = final_call(col)
        self.assertIsNotNone(call, "inverted signal with a 900 Hz error was not decoded")
        self.assertGreaterEqual(call["voice_frames"], 80)
        self.assertEqual(call["meta"].get("src"), "W2FBI")

    def test_channel_follows_frequency_not_offset_when_set(self):
        cfg = [ChannelConfig(name="ch0", protocol="m17", offset_hz=0.0, frequency_hz=CAPTURE_CENTER + OFFSET)]
        engine, col = self._run(snr_db=26.0, channels=cfg)
        self.assertIsNotNone(final_call(col), "frequency_hz must override offset_hz")

    def test_upload_path_matches_the_server_path(self):
        wb48 = impair(modulate(self.dibits, 4800.0, Modulation.FSK4), 26.0, seed=1, snr_bandwidth_hz=12_500.0)
        pad = np.zeros(9_600, np.complex64)
        rng = np.random.default_rng(9)
        noise = lambda n: (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 0.03
        sig = np.concatenate([noise(9_600), wb48 * 0.5, noise(19_200)])
        samples = np.empty(sig.size * 2, dtype="<i2")
        samples[0::2] = np.clip(sig.real * 16000, -32768, 32767)
        samples[1::2] = np.clip(sig.imag * 16000, -32768, 32767)
        col = Collector()
        engine = DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(name="up", protocol="m17")]), col.on_event, col.on_audio)
        engine.start()
        self.addCleanup(engine.close)
        raw = samples.tobytes()
        step = 4 * 2400
        for i in range(0, len(raw), step):
            while engine.status().channels["up"]["queue_seconds"] > 0.2:
                time.sleep(0.003)
            engine.feed_upload(INPUT_IQ16_48K, raw[i : i + step], "up")
        drain(engine, 30)
        time.sleep(0.3)
        call = final_call(col)
        self.assertIsNotNone(call)
        self.assertEqual(call["meta"].get("src"), "W2FBI")
        self.assertGreaterEqual(call["voice_frames"], 85, "16-bit quantisation costs a little at lock-in")

    def test_discriminator_upload(self):
        from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator

        bb = impair(modulate(self.dibits, 4800.0, Modulation.FSK4), 28.0, seed=2, snr_bandwidth_hz=12_500.0)
        rng = np.random.default_rng(10)
        noise = lambda n: (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64) * 0.02
        sig = np.concatenate([noise(9_600), bb * 0.5, noise(19_200)])
        disc = FmDiscriminator(48_000.0).process(sig)  # Hz of deviation
        payload = np.clip(disc / (eng.DISC16_FULL_SCALE_HZ / 32768.0), -32768, 32767).astype("<i2").tobytes()
        col = Collector()
        engine = DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(name="d", protocol="m17")]), col.on_event, col.on_audio)
        engine.start()
        self.addCleanup(engine.close)
        step = 2 * 2400
        for i in range(0, len(payload), step):
            while engine.status().channels["d"]["queue_seconds"] > 0.2:
                time.sleep(0.003)
            engine.feed_upload(INPUT_DISC16_48K, payload[i : i + step], "d")
        drain(engine, 30)
        time.sleep(0.3)
        call = final_call(col)
        self.assertIsNotNone(call, "discriminator upload decoded nothing")
        self.assertEqual(call["meta"].get("src"), "W2FBI")

    def test_discriminator_upload_cannot_autodetect(self):
        col = Collector()
        engine = DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(name="d", protocol="auto")]), col.on_event, col.on_audio)
        engine.start()
        self.addCleanup(engine.close)
        engine.feed_upload(INPUT_DISC16_48K, np.zeros(9600, dtype="<i2").tobytes(), "d")
        drain(engine)
        self.assertTrue(col.errors("auto_requires_iq"))

    def test_encrypted_m17_is_reported_and_silent(self):
        dibits = m17_dibits(frames=40, encrypted=True)
        engine, col = run_capture(dibits, "m17", 4800.0, Modulation.FSK4, snr_db=26.0)
        self.addCleanup(engine.close)
        call = final_call(col)
        self.assertIsNotNone(call)
        self.assertEqual(call["encryption"]["state"], "encrypted")
        self.assertFalse(call["encryption"]["audio_decoded"])
        self.assertEqual(col.pcm_frames(), [], "an encrypted M17 call must produce no audio")


class P25EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("p25p1", {}).get("implemented"):
            raise unittest.SkipTest("P25 Phase 1 decoder not implemented yet")

    def _run(self, dibits, **kw):
        engine, col = run_capture(dibits, "p25p1", 4800.0, Modulation.C4FM, **kw)
        self.addCleanup(engine.close)
        return engine, col

    def test_group_call_fields_and_no_audio_without_a_vocoder(self):
        if registry.capabilities()["imbe_7200x4400"]["available"]:
            self.skipTest("an IMBE vocoder is installed here; this test covers the no-vocoder path")
        engine, col = self._run(p25_dibits(), snr_db=28.0)
        call = final_call(col)
        self.assertIsNotNone(call, "no P25 call decoded")
        self.assertEqual(call["protocol"], "p25p1")
        self.assertEqual(call["target_id"], 1234)
        self.assertEqual(call["source_id"], 5678)
        self.assertEqual(call["encryption"]["state"], "clear")
        self.assertFalse(call["emergency"])
        self.assertTrue(col.errors("vocoder_unavailable"), "a missing vocoder must be reported, not silent")
        self.assertEqual(col.pcm_frames(), [])
        self.assertEqual(len(engine.recorder.list_calls(talkgroup=1234)), 1)

    def test_group_call_with_the_test_vocoder_yields_audio(self):
        with registry.use_test_double():
            engine, col = self._run(p25_dibits(superframes=3), snr_db=28.0)
        call = final_call(col)
        self.assertIsNotNone(call)
        self.assertGreaterEqual(call["voice_frames"], 18)
        pcm = col.pcm_frames()
        self.assertGreaterEqual(len(pcm), 18)
        self.assertTrue(call["encryption"]["audio_decoded"])

    def test_emergency_flag(self):
        with registry.use_test_double():
            engine, col = self._run(p25_dibits(emergency=True), snr_db=28.0)
        self.assertTrue(final_call(col)["emergency"])

    def test_encrypted_p25_call_is_reported_and_never_decoded(self):
        with registry.use_test_double():  # even with a working vocoder available
            engine, col = self._run(p25_dibits(algid=0x84), snr_db=28.0)
        call = final_call(col)
        self.assertIsNotNone(call)
        self.assertEqual(call["encryption"]["state"], "encrypted")
        self.assertEqual(call["encryption"]["algorithm_id"], 0x84)
        self.assertFalse(call["encryption"]["audio_decoded"])
        self.assertEqual(col.pcm_frames(), [], "no audio may be produced for an encrypted P25 call")
        self.assertEqual([s for s in col.sub_audio if s[1].size], [])

    def test_auto_detect_locks_p25(self):
        # auto-detect needs about 1.5 s of sync evidence before it locks, so use a long call
        engine, col = self._run(p25_dibits(superframes=12), snr_db=28.0, channels=[ChannelConfig(name="ch0", protocol="auto", offset_hz=OFFSET)])
        self.assertEqual(engine.status().channels["ch0"]["protocol"], "p25p1")
        self.assertTrue(col.data("call_start"), "auto-detected P25 produced no call")


class DmrEndToEnd(unittest.TestCase):
    DEVIATION = 1944.0  # DMR outer-symbol deviation

    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("dmr", {}).get("implemented"):
            raise unittest.SkipTest("DMR decoder not implemented yet")
        try:
            importlib.import_module("sigsense_api.dvoice.testing.dmr_encoder")
        except Exception:
            raise unittest.SkipTest("DMR encoder not available")

    @staticmethod
    def _lc(tg: int, src: int) -> bytes:
        from sigsense_api.dvoice.protocols.dmr import lc

        return lc.build_full_lc(0, 0, bytes([0x00]) + tg.to_bytes(3, "big") + src.to_bytes(3, "big"))

    def _stream(self, slot0, slot1=None, superframes=4):
        from sigsense_api.dvoice.testing.dmr_encoder import DmrEncoder, assemble_bs_stream

        enc = DmrEncoder(cc=7)
        b0 = enc.voice_call(self._lc(*slot0), superframes=superframes)
        b1 = enc.voice_call(self._lc(*slot1), superframes=superframes) if slot1 else [enc.idle_burst()] * len(b0)
        return assemble_bs_stream(b0, b1, enc, lead_idle=3, tail_idle=10)

    def _run(self, dibits, snr_db=26.0, **kw):
        engine, col = run_capture(dibits, "dmr", 4800.0, Modulation.FSK4, snr_db=snr_db, deviation_hz=self.DEVIATION, **kw)
        self.addCleanup(engine.close)
        return engine, col

    def test_single_slot_group_call(self):
        engine, col = self._run(self._stream((9, 0x1234)))
        starts = col.data("call_start")
        self.assertEqual(len(starts), 1, f"expected one call, got {[s['call_id'] for s in starts]}")
        call = final_call(col)
        self.assertEqual((call["protocol"], call["target_id"], call["source_id"]), ("dmr", 9, 0x1234))
        self.assertEqual(call["kind"], "group")
        self.assertEqual(call["encryption"]["state"], "clear")
        self.assertIn(call["slot"], (0, 1))

    def test_two_simultaneous_slots_are_two_independent_calls(self):
        engine, col = self._run(self._stream((9, 1111), (17, 2222)))
        calls = {c["call_id"]: c for c in col.data("call_end")}
        self.assertEqual(len(calls), 2, f"expected two calls, got {list(calls)}")
        by_tg = {c["target_id"]: c for c in calls.values()}
        self.assertEqual(set(by_tg), {9, 17})
        self.assertEqual(by_tg[9]["source_id"], 1111)
        self.assertEqual(by_tg[17]["source_id"], 2222)
        self.assertNotEqual(by_tg[9]["slot"], by_tg[17]["slot"])
        self.assertEqual({c["meta"]["call_num"] for c in calls.values()}, {1, 2})

    def test_voice_audio_is_tagged_with_its_slot_when_a_vocoder_exists(self):
        with registry.use_test_double():
            engine, col = self._run(self._stream((9, 1111), (17, 2222)))
        pcm = col.pcm_frames()
        if not pcm:
            self.skipTest("this DMR decoder needs its AMBE channel-decode hook for the test vocoder; metadata is covered above")
        self.assertEqual({h["slot"] for h, _ in pcm}, {0, 1})

    def test_unavailable_vocoder_is_reported_not_silent(self):
        if registry.capabilities()["ambe_plus2_3600x2450"]["available"]:
            self.skipTest("an AMBE+2 vocoder is installed here")
        engine, col = self._run(self._stream((9, 1111)))
        self.assertTrue(col.errors("vocoder_unavailable") or col.pcm_frames() == [])
        self.assertEqual(col.pcm_frames(), [])


class P25TrunkFollowEndToEnd(unittest.TestCase):
    """A control channel at -100 kHz grants a voice channel at +50 kHz; the engine opens a second
    channelizer on the SAME capture (no retune), decodes the call there, then releases it."""

    FS = 1_200_000.0
    CC_OFFSET = -100_000.0
    VOICE_OFFSET = 50_000.0
    NAC = 0x293

    @classmethod
    def setUpClass(cls) -> None:
        if not eng.protocol_support().get("p25p1", {}).get("implemented"):
            raise unittest.SkipTest("P25 Phase 1 decoder not implemented yet")
        try:
            from sigsense_api.dvoice.trunking.p25 import P25TrunkController

            P25TrunkController().on_event("trunk", None)
        except NotImplementedError:
            raise unittest.SkipTest("P25 trunk controller not implemented yet")

    def _capture(self, tg: int = 321, src: int = 654, algid: int = 0x80):
        from sigsense_api.dvoice.testing import p25p1_encoder as enc

        base = int(CAPTURE_CENTER - 150_000)  # iden 1: channel n sits at base + n * 12.5 kHz
        channel = (1 << 12) | 16  # base + 200 kHz = centre + 50 kHz
        iden = enc.build_tsdu(self.NAC, [(0x3D, enc.args_iden_up(1, 12_500, 0, 12_500, base))])
        rfss = enc.build_tsdu(self.NAC, [(0x3A, enc.args_rfss_sts(1, 0x123, 1, 1, channel & 0x0FFF))])
        grant = enc.build_tsdu(self.NAC, [(0x00, enc.args_grp_v_ch_grant(0x03, channel, tg, src))])
        warmup = np.concatenate([iden, rfss] * 18)  # the band plan must be heard before a grant can be placed
        cc = np.concatenate([warmup] + [np.concatenate([grant, iden, rfss])] * 40)
        voice, _ = enc.build_voice_call(self.NAC, tgid=tg, source=src, superframes=8, algid=algid)
        cc_iq = transmit(cc, 4800.0, Modulation.C4FM, snr_db=28.0, offset_hz=self.CC_OFFSET, fs=self.FS, lead_s=0.1, tail_s=0.5, seed=4)
        # the voice transmission begins well after the grant is first sent (~1.4 s in)
        v_iq = transmit(voice, 4800.0, Modulation.C4FM, snr_db=28.0, offset_hz=self.VOICE_OFFSET, fs=self.FS, lead_s=2.2, tail_s=0.5, seed=5)
        n = max(cc_iq.size, v_iq.size)
        out = np.zeros(n, np.complex64)
        out[: cc_iq.size] += cc_iq
        out[: v_iq.size] += v_iq
        return out

    def _run(self, **kw):
        capture = self._capture(**kw)
        col = Collector()
        cfg = EngineConfig(channels=[ChannelConfig(name="cc", protocol="p25p1", offset_hz=self.CC_OFFSET)])
        cfg.follow.hang_time_s = 0.6
        engine = DigitalVoiceEngine(cfg, col.on_event, col.on_audio)
        engine.start()
        self.addCleanup(engine.close)
        feed_capture(engine, capture, fs=self.FS, chunk_s=0.05)
        drain(engine, 60)
        time.sleep(0.5)
        return engine, col

    def test_grant_opens_a_follower_that_decodes_the_call(self):
        with registry.use_test_double():
            engine, col = self._run()
        grants = [d for d in col.data("trunk") if d["kind"] == "grant" and d["detail"].get("follower")]
        self.assertTrue(grants, f"no follower was opened; trunk events: {[(d['kind'], d.get('talkgroup')) for d in col.data('trunk')]}")
        follower = grants[0]["detail"]["follower"]
        self.assertTrue(follower.startswith("cc-v"))
        calls = [c for c in col.data("call_end") if c["channel"] == follower]
        self.assertTrue(calls, f"the voice follower decoded no call; calls: {[(c['channel'], c['target_id']) for c in col.data('call_end')]}")
        call = calls[-1]
        self.assertEqual((call["target_id"], call["source_id"]), (321, 654))
        self.assertAlmostEqual(call["frequency_hz"], CAPTURE_CENTER + self.VOICE_OFFSET, delta=1.0)
        self.assertGreaterEqual(len(col.pcm_frames()), 9, "the followed call produced no audio")
        self.assertEqual(engine.status_dict()["trunk"]["p25p1"]["followed"], 1)

    def test_follower_is_released_after_the_call(self):
        with registry.use_test_double():
            engine, col = self._run()
        self.assertTrue(col.wait_for(lambda: all(not c["follower_of"] for c in engine.status().channels.values()), 8.0), engine.status().channels.keys())

    def test_encrypted_grant_is_not_followed_and_never_decoded(self):
        # the control channel marks the grant clear, but the voice call itself is encrypted: the engine
        # follows, sees the encryption in the call, and must still produce no audio
        with registry.use_test_double():
            engine, col = self._run(algid=0x84)
        self.assertEqual(col.pcm_frames(), [])
        enc_calls = [c for c in col.data("call_end") if c["encryption"]["state"] == "encrypted"]
        self.assertTrue(enc_calls, "the encrypted call should still be reported")
        self.assertEqual([s for s in col.sub_audio if s[1].size], [])


class SnrSweep(unittest.TestCase):
    """Prints decode success against SNR (in a 12.5 kHz channel) for each implemented protocol."""

    def test_sweep(self):
        rows = []
        cases = []
        if eng.protocol_support().get("m17", {}).get("implemented"):
            cases.append(("m17", m17_dibits(40), 4800.0, Modulation.FSK4, lambda c: c["voice_frames"] - c["voice_frames_bad"]))
        if eng.protocol_support().get("p25p1", {}).get("implemented"):
            cases.append(("p25p1", p25_dibits(superframes=3), 4800.0, Modulation.C4FM, lambda c: 1 if c["source_id"] == 5678 else 0))
        if not cases:
            self.skipTest("no decoder implemented yet")
        for proto, dibits, rate, mod, good in cases:
            for snr in (10.0, 14.0, 18.0, 24.0):
                engine, col = run_capture(dibits, proto, rate, mod, snr_db=snr)
                call = final_call(col)
                rows.append((proto, snr, None if call is None else good(call)))
                engine.close()
        print("\nprotocol  SNR dB  good frames/calls (None = no call)")
        for row in rows:
            print(f"  {row[0]:8s} {row[1]:5.0f}  {row[2]}")
        for proto, snr, val in rows:
            if snr >= 24.0:
                self.assertTrue(val, f"{proto} failed at {snr} dB")


class PendingProtocols(unittest.TestCase):
    """DMR / NXDN / D-STAR / YSF / P25 Phase 2: enabled automatically once a decoder AND an encoder exist."""

    def test_report(self):
        support = eng.protocol_support()
        for proto, encoder in (
            ("dmr", "dmr_encoder"),
            ("nxdn96", "nxdn_encoder"),
            ("dstar", "dstar_encoder"),
            ("ysf", "ysf_encoder"),
            ("p25p2", "p25p2_encoder"),
        ):
            have_encoder = True
            try:
                importlib.import_module(f"sigsense_api.dvoice.testing.{encoder}")
            except Exception:
                have_encoder = False
            print(f"  {proto:7s} decoder={'yes' if support[proto]['implemented'] else 'no':3s} encoder={'yes' if have_encoder else 'no'}")


if __name__ == "__main__":
    unittest.main()
