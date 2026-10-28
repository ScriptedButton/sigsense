"""Engine unit tests with scriptable fake decoders (WP-G).

These drive the engine's own responsibilities directly: call bookkeeping, the
ENCRYPTION POLICY (an encrypted call never yields PCM or a WAV, whatever a decoder
emits), failure containment, backpressure, trunk following, talkgroup policy and
live reconfiguration. Real air-interface decoding is covered in test_dvoice_e2e_*.py.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from sigsense_api.dvoice import engine as eng
from sigsense_api.dvoice.engine import (
    DECODER_OVERRIDES,
    INPUT_DISC16_48K,
    INPUT_IQ16_48K,
    TRUNK_OVERRIDES,
    ChannelConfig,
    DigitalVoiceEngine,
    EngineConfig,
)
from sigsense_api.dvoice.events import EncryptionState, Protocol, TrunkEvent
from sigsense_api.dvoice.trunking.base import FollowPolicy, TrunkController, TuneRequest

from test_dvoice_engine_common import (
    CAPTURE_CENTER,
    CAPTURE_FS,
    Collector,
    EngineTestCase,
    FakeDecoder,
    drain,
    make_call,
    feed_noise,
    noise_iq16,
    tone_frame,
)


def forced(name: str = "ch0") -> list[ChannelConfig]:
    return [ChannelConfig(name=name, protocol="dmr")]


class _FakeEnv(EngineTestCase):
    def setUp(self) -> None:
        self._saved = dict(DECODER_OVERRIDES)
        DECODER_OVERRIDES["dmr"] = FakeDecoder
        eng._PROBE_CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        DECODER_OVERRIDES.clear()
        DECODER_OVERRIDES.update(self._saved)
        eng._PROBE_CACHE.clear()
        FakeDecoder.script = staticmethod(lambda d, n: None)  # type: ignore[assignment]

    def run_script(self, script, seconds: float = 0.6, **config) -> DigitalVoiceEngine:
        FakeDecoder.script = staticmethod(script)  # type: ignore[assignment]
        engine = self.make_engine(forced(), **config)
        feed_noise(engine, seconds)
        self.assertTrue(drain(engine))
        return engine


class CallFlowTests(_FakeEnv):
    def test_call_lifecycle_audio_and_markers(self):
        cid = "fake-ch0-x-1"

        def script(d, n):
            if n == 1:
                d.emit("call_start", make_call(cid))
            if 1 <= n <= 3:
                d.emit("voice", tone_frame(cid, n - 1))
            if n == 4:
                d.emit("call_end", make_call(cid, ended=True))

        engine = self.run_script(script, seconds=1.0)
        starts, ends = self.collector.data("call_start"), self.collector.data("call_end")
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        self.assertEqual(starts[0]["meta"]["call_num"], 1)
        self.assertEqual(ends[0]["end_reason"], "terminator")
        frames = self.collector.frames()
        with_pcm = [(h, p) for h, p in frames if p.size]
        self.assertEqual(len(with_pcm), 3)
        self.assertTrue(frames[-1][0]["end_of_call"])  # the end marker has no samples
        self.assertEqual(frames[-1][1].size, 0)
        self.assertEqual([h["seq"] for h, _ in with_pcm], [0, 1, 2])
        self.assertEqual({h["call_num"] for h, _ in frames}, {1})
        self.assertEqual(with_pcm[0][0]["sample_rate"], 8000)
        # subscribers get the same PCM plus an empty end-of-call marker
        subs = self.collector.sub_audio
        self.assertEqual(len([s for s in subs if s[1].size]), 3)
        self.assertEqual(subs[-1][1].size, 0)
        # the call log has it, with decoded audio flagged
        rows = engine.recorder.list_calls()
        self.assertEqual(len(rows), 1)
        self.assertTrue(starts[0]["meta"]["call_num"] == rows[0]["meta"]["call_num"])
        self.assertTrue(ends[0]["encryption"]["audio_decoded"])

    def test_call_update_is_throttled_but_changes_pass(self):
        cid = "fake-ch0-x-2"

        def script(d, n):
            if n == 1:
                d.emit("call_start", make_call(cid))
            elif n <= 12:
                d.emit("call_update", make_call(cid))
            elif n == 13:
                d.emit("call_update", make_call(cid, source=999))  # source changed: must pass
            elif n == 14:
                d.emit("call_end", make_call(cid, ended=True))

        self.run_script(script, seconds=1.5)
        updates = self.collector.data("call_update")
        self.assertLess(len(updates), 6, "identical updates are rate-limited")
        self.assertTrue(any(u["source_id"] == 999 for u in updates))

    def test_unknown_call_voice_is_dropped(self):
        def script(d, n):
            if n == 1:
                d.emit("voice", tone_frame("never-started", 0))

        engine = self.run_script(script)
        self.assertEqual(self.collector.pcm_frames(), [])
        self.assertEqual(engine.metrics["voice_without_call"], 1)

    def test_stale_call_is_ended_by_the_engine(self):
        cid = "fake-ch0-x-3"
        FakeDecoder.script = staticmethod(lambda d, n: d.emit("call_start", make_call(cid)) if n == 1 else None)  # type: ignore[assignment]
        old = eng.CALL_STALE_S
        eng.CALL_STALE_S = 0.6
        self.addCleanup(setattr, eng, "CALL_STALE_S", old)
        engine = self.make_engine(forced())
        feed_noise(engine, 0.4)
        self.assertTrue(self.collector.wait_for(lambda: bool(self.collector.of("call_end")), 5.0))
        self.assertEqual(self.collector.data("call_end")[0]["end_reason"], "timeout")

    def test_calls_end_when_the_channel_resyncs(self):
        cid = "fake-ch0-x-4"
        FakeDecoder.script = staticmethod(lambda d, n: d.emit("call_start", make_call(cid)) if n == 1 else None)  # type: ignore[assignment]
        engine = self.make_engine(forced())
        feed_noise(engine, 0.4)
        self.assertTrue(self.collector.wait_for(lambda: bool(self.collector.of("call_start"))))
        engine.discontinuity()
        feed_noise(engine, 0.2, seed=50)
        self.assertTrue(self.collector.wait_for(lambda: bool(self.collector.of("call_end"))))
        self.assertEqual(self.collector.data("call_end")[0]["end_reason"], "sync_loss")


class EncryptionPolicyTests(_FakeEnv):
    """SAFETY POLICY: encrypted calls are reported, never decoded or recorded."""

    def test_encrypted_call_yields_metadata_only_and_never_pcm(self):
        cid = "fake-ch0-x-enc"

        def script(d, n):
            if n == 1:
                d.emit("call_start", make_call(cid, encrypted=True))
            if 1 <= n <= 5:
                # a misbehaving decoder that DOES emit audio for an encrypted call
                d.emit("voice", tone_frame(cid, n - 1))
            if n == 6:
                d.emit("call_end", make_call(cid, encrypted=True, ended=True))

        with tempfile.TemporaryDirectory() as tmp:
            engine = self.run_script(script, seconds=1.2, record_dir=Path(tmp))
            self.assertEqual(self.collector.pcm_frames(), [], "no PCM frame may leave the engine")
            self.assertEqual([s for s in self.collector.sub_audio if s[1].size], [], "nor reach subscribers")
            self.assertEqual(engine.metrics["encrypted_frames_dropped"], 5)
            end = self.collector.data("call_end")[0]
            self.assertEqual(end["encryption"]["state"], "encrypted")
            self.assertEqual(end["encryption"]["algorithm_id"], 0x84)  # reported, informational
            self.assertFalse(end["encryption"]["audio_decoded"])
            # logged as metadata; no WAV on disk and none served
            rows = engine.recorder.list_calls(encrypted=True)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["has_audio"])
            self.assertIsNone(engine.recorder.wav_path(cid))
            self.assertEqual(list(Path(tmp).rglob("*.wav")), [])

    def test_late_encryption_stops_audio_and_deletes_the_partial_recording(self):
        cid = "fake-ch0-x-late"

        def script(d, n):
            if n == 1:
                d.emit("call_start", make_call(cid))
            if n in (1, 2, 3):
                d.emit("voice", tone_frame(cid, n - 1))
            if n == 4:  # the encryption sync arrives after the first voice frames
                d.emit("call_update", make_call(cid, encrypted=True))
            if 4 <= n <= 6:
                d.emit("voice", tone_frame(cid, n))
            if n == 7:
                d.emit("call_end", make_call(cid, encrypted=True, ended=True))

        with tempfile.TemporaryDirectory() as tmp:
            engine = self.run_script(script, seconds=1.4, record_dir=Path(tmp))
            self.assertEqual(len(self.collector.pcm_frames()), 3, "only the frames before the encryption was known")
            self.assertEqual(list(Path(tmp).rglob("*.wav")), [], "the partial WAV is removed once the call is known encrypted")
            self.assertIsNone(engine.recorder.wav_path(cid))
            self.assertTrue(engine.recorder.list_calls(encrypted=True))

    def test_recording_never_writes_encrypted_audio_even_when_forced_directly(self):
        from sigsense_api.dvoice.recorder import CallRecorder

        with tempfile.TemporaryDirectory() as tmp:
            rec = CallRecorder(Path(tmp))
            call = make_call("enc-direct", encrypted=True)
            rec.on_call(call)
            rec.on_voice("enc-direct", np.ones(160, dtype=np.int16))
            call.ended_at = time.time()
            rec.on_call(call)
            rec.close()
            self.assertEqual(list(Path(tmp).rglob("*.wav")), [])

    def test_no_key_handling_surface(self):
        """The package must expose no way to supply or recover keys."""
        import inspect

        import sigsense_api.dvoice.engine as engine_mod
        import sigsense_api.dvoice.recorder as recorder_mod

        for mod in (engine_mod, recorder_mod):
            for name, member in inspect.getmembers(mod):
                self.assertNotRegex(name.lower(), r"(brute|crack|recover_key|set_key|load_key)", f"{mod.__name__}.{name}")
        for cls in (engine_mod.DigitalVoiceEngine, recorder_mod.CallRecorder):
            for name in dir(cls):
                self.assertNotRegex(name.lower(), r"(brute|crack|recover_key|set_key|load_key)", f"{cls.__name__}.{name}")


class TalkgroupPolicyTests(_FakeEnv):
    def _script(self, cid: str, tg: int):
        def script(d, n):
            if n == 1:
                d.emit("call_start", make_call(cid, target=tg))
            if n in (1, 2):
                d.emit("voice", tone_frame(cid, n - 1))
            if n == 3:
                d.emit("call_end", make_call(cid, target=tg, ended=True))

        return script

    def test_skipped_talkgroup_is_logged_but_silent(self):
        engine = self.run_script(self._script("fake-ch0-x-skip", 100), follow=FollowPolicy(skip_talkgroups={100}))
        self.assertEqual(self.collector.pcm_frames(), [])
        end = self.collector.data("call_end")[0]
        self.assertTrue(end["meta"].get("muted"))
        self.assertEqual(len(engine.recorder.list_calls(talkgroup=100)), 1)

    def test_hold_list_mutes_everything_else(self):
        self.run_script(self._script("fake-ch0-x-hold", 100), follow=FollowPolicy(hold_talkgroups={200}))
        self.assertEqual(self.collector.pcm_frames(), [])

    def test_hold_list_lets_its_own_talkgroup_through(self):
        self.run_script(self._script("fake-ch0-x-hold2", 100), follow=FollowPolicy(hold_talkgroups={100}))
        self.assertEqual(len(self.collector.pcm_frames()), 2)

    def test_labels_are_attached_and_talkgroups_tracked(self):
        engine = self.run_script(self._script("fake-ch0-x-lbl", 100), talkgroup_labels={100: "Dispatch"})
        end = self.collector.data("call_end")[0]
        self.assertEqual(end["meta"]["talkgroup_label"], "Dispatch")
        tgs = engine.talkgroups()
        self.assertEqual(tgs[0]["id"], 100)
        self.assertEqual(tgs[0]["label"], "Dispatch")
        self.assertEqual(tgs[0]["call_count"], 1)
        engine.set_talkgroup_labels({100: "Fire"})
        self.assertEqual(engine.talkgroups()[0]["label"], "Fire")


class ContainmentTests(_FakeEnv):
    def test_decoder_exception_is_reported_and_the_channel_survives(self):
        cid = "fake-ch0-x-boom"
        seen = []

        def script(d, n):
            seen.append(n)
            if n == 1:
                d.emit("call_start", make_call(cid))
            if n == 2:
                raise RuntimeError("synthetic decoder bug")

        engine = self.run_script(script, seconds=1.2)
        errs = self.collector.errors("decoder_error")
        self.assertEqual(len(errs), 1)
        self.assertIn("synthetic decoder bug", errs[0]["message"])
        self.assertGreater(max(seen), 2, "the channel kept feeding the decoder after the failure")
        self.assertEqual(self.collector.data("call_end")[0]["end_reason"], "sync_loss")
        self.assertFalse(engine.status().channels["ch0"]["failed"])

    def test_a_decoder_that_always_fails_disables_only_that_channel(self):
        def script(d, n):
            raise RuntimeError("always")

        old = eng.DECODER_FAILURE_LIMIT
        eng.DECODER_FAILURE_LIMIT = 5
        self.addCleanup(setattr, eng, "DECODER_FAILURE_LIMIT", old)
        FakeDecoder.script = staticmethod(script)  # type: ignore[assignment]
        engine = self.make_engine([ChannelConfig(name="ch0", protocol="dmr"), ChannelConfig(name="ch1", protocol="dmr")])
        feed_noise(engine, 1.5, "ch0")
        self.assertTrue(self.collector.wait_for(lambda: bool(self.collector.errors("channel_failed")), 8.0))
        status = engine.status().channels
        self.assertTrue(status["ch0"]["failed"])
        self.assertFalse(status["ch1"]["failed"])

    def test_event_sink_failure_does_not_stop_the_engine(self):
        cid = "fake-ch0-x-sink"
        FakeDecoder.script = staticmethod(lambda d, n: d.emit("call_start", make_call(cid)) if n == 1 else None)  # type: ignore[assignment]
        calls = []

        def bad_sink(env):
            calls.append(env["type"])
            raise RuntimeError("client went away")

        engine = DigitalVoiceEngine(EngineConfig(channels=forced()), bad_sink, None)
        engine.start()
        self.addCleanup(engine.close)
        feed_noise(engine, 0.6)
        self.assertTrue(drain(engine))
        self.assertIn("call_start", calls)

    def test_input_overrun_drops_oldest_and_reports(self):
        def slow(d, n):
            time.sleep(0.25)

        FakeDecoder.script = staticmethod(slow)  # type: ignore[assignment]
        engine = self.make_engine(forced())
        for i in range(12):
            engine.feed_upload(INPUT_IQ16_48K, noise_iq16(0.3, seed=i), "ch0")
        self.assertTrue(self.collector.wait_for(lambda: bool(self.collector.errors("input_overrun")), 5.0))
        self.assertGreater(engine.status().channels["ch0"]["dropped_items"], 0)
        self.assertLessEqual(engine.status().channels["ch0"]["queue_seconds"], eng.QUEUE_SECONDS + 0.4)

    def test_stub_decoder_is_reported_not_crashed(self):
        DECODER_OVERRIDES.clear()  # back to the real registry: DMR/NXDN may still be stubs
        eng._PROBE_CACHE.clear()
        support = eng.protocol_support()
        stubs = [p for p, v in support.items() if not v["implemented"]]
        if not stubs:
            self.skipTest("every protocol decoder is implemented")
        proto = stubs[0]
        engine = self.make_engine([ChannelConfig(name="ch0", protocol=proto)])
        feed_noise(engine, 0.5)
        self.assertTrue(drain(engine))
        self.assertTrue(self.collector.errors("unsupported_protocol"))
        snap = engine.status().channels["ch0"]
        self.assertEqual(snap["decoder"], "unavailable")
        self.assertIn("not implemented", snap["decoder_note"])


class ConfigTests(_FakeEnv):
    def test_validation(self):
        with self.assertRaises(ValueError):
            DigitalVoiceEngine(EngineConfig(channels=[]), lambda e: None)
        with self.assertRaises(ValueError):
            DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(name="a"), ChannelConfig(name="a")]), lambda e: None)
        with self.assertRaises(ValueError):
            DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(protocol="bogus")]), lambda e: None)
        with self.assertRaises(ValueError):
            DigitalVoiceEngine(EngineConfig(channels=[ChannelConfig(bandwidth_hz=100.0)]), lambda e: None)

    def test_live_reconfigure_adds_removes_and_forces(self):
        engine = self.make_engine([ChannelConfig(name="ch0", protocol="auto")])
        self.assertEqual(list(engine.status().channels), ["ch0"])
        new = EngineConfig(channels=[ChannelConfig(name="ch0", protocol="dmr"), ChannelConfig(name="ch1", protocol="dmr")])
        engine.configure(new)
        self.assertEqual(sorted(engine.status().channels), ["ch0", "ch1"])
        self.assertEqual(engine.status().channels["ch0"]["mode"], "forced")
        engine.configure(EngineConfig(channels=[ChannelConfig(name="ch1", protocol="dmr")]))
        self.assertEqual(list(engine.status().channels), ["ch1"])
        with self.assertRaises(ValueError):
            engine.configure(EngineConfig(channels=[]))

    def test_upload_kinds_and_validation(self):
        engine = self.make_engine(forced())
        with self.assertRaises(ValueError):
            engine.feed_upload(0x7F, b"\0\0\0\0", "ch0")
        engine.feed_upload(INPUT_DISC16_48K, np.zeros(4800, dtype="<i2").tobytes(), "ch0")
        engine.feed_upload(INPUT_IQ16_48K, b"\x01\x02\x03", "ch0")  # a ragged payload is trimmed, not an error
        engine.feed_upload(INPUT_IQ16_48K, noise_iq16(0.1), "no-such-channel")  # ignored
        self.assertTrue(drain(engine))

    def test_capabilities_probe_reflects_decoder_overrides(self):
        support = eng.protocol_support()
        self.assertTrue(support["dmr"]["implemented"])  # the fake
        DECODER_OVERRIDES.clear()
        eng._PROBE_CACHE.clear()
        self.assertIn("dmr", eng.protocol_support())


class _GrantController(TrunkController):
    """Trunk controller double: a 'grant' trunk event asks to follow `frequency_hz`."""

    protocol = Protocol.DMR
    requests: list[TuneRequest] = []
    ended: list[float] = []

    def on_event(self, event_type, payload):
        if event_type == "trunk" and getattr(payload, "kind", "") == "grant":
            return [TuneRequest("voice", payload.frequency_hz, "grant", payload.talkgroup)]
        return []

    def on_voice_ended(self, frequency_hz):
        type(self).ended.append(frequency_hz)
        return []

    def status(self):
        return {"double": True}


class TrunkFollowTests(_FakeEnv):
    def setUp(self) -> None:
        super().setUp()
        _GrantController.ended = []
        self._saved_trunk = dict(TRUNK_OVERRIDES)
        TRUNK_OVERRIDES[Protocol.DMR] = _GrantController
        self.addCleanup(lambda: (TRUNK_OVERRIDES.clear(), TRUNK_OVERRIDES.update(self._saved_trunk)))

    def _grant_script(self, freq: float):
        def script(d, n):
            if n == 1 and d.config.channel == "cc":
                d.emit("trunk", TrunkEvent(kind="grant", frequency_hz=freq, talkgroup=300))

        return script

    def _feed_cc(self, engine, seconds=0.5):
        iq = (np.random.default_rng(1).standard_normal(int(CAPTURE_FS * seconds)) * 0.01).astype(np.complex64)
        engine.feed_iq(iq, CAPTURE_FS, CAPTURE_CENTER, "*")

    def test_voice_grant_inside_the_capture_spawns_a_second_channelizer_then_releases_it(self):
        FakeDecoder.script = staticmethod(self._grant_script(CAPTURE_CENTER + 150_000.0))  # type: ignore[assignment]
        engine = self.make_engine(
            [ChannelConfig(name="cc", protocol="dmr", frequency_hz=CAPTURE_CENTER - 100_000.0)],
            follow=FollowPolicy(hang_time_s=0.5),
        )
        self._feed_cc(engine)
        self.assertTrue(self.collector.wait_for(lambda: any(d["detail"].get("follower") for d in self.collector.data("trunk"))))
        follower = next(d["detail"]["follower"] for d in self.collector.data("trunk") if d["detail"].get("follower"))
        status = engine.status().channels
        self.assertIn(follower, status)
        self.assertEqual(status[follower]["follower_of"], "cc")
        self.assertAlmostEqual(status[follower]["frequency_hz"], CAPTURE_CENTER + 150_000.0)
        # the follower is fed from the same wideband capture
        self._feed_cc(engine, 0.3)
        # with no call and past the grace period it is torn down and the controller is told
        old = eng.FOLLOWER_GRACE_S
        eng.FOLLOWER_GRACE_S = 0.0
        self.addCleanup(setattr, eng, "FOLLOWER_GRACE_S", old)
        info = engine._followers[follower]
        info["deadline"] = time.monotonic()
        self.assertTrue(self.collector.wait_for(lambda: follower not in engine.status().channels, 6.0))
        self.assertTrue(_GrantController.ended)

    def test_grant_outside_the_captured_span_is_reported_not_followed(self):
        FakeDecoder.script = staticmethod(self._grant_script(CAPTURE_CENTER + 5_000_000.0))  # type: ignore[assignment]
        engine = self.make_engine([ChannelConfig(name="cc", protocol="dmr")])
        self._feed_cc(engine)
        self.assertTrue(self.collector.wait_for(lambda: any(d["kind"] == "hunting" for d in self.collector.data("trunk"))))
        hunting = next(d for d in self.collector.data("trunk") if d["kind"] == "hunting")
        self.assertTrue(hunting["detail"]["retune_required"])
        self.assertEqual(list(engine.status().channels), ["cc"])

    def test_follower_cap_is_respected(self):
        def script(d, n):
            if d.config.channel == "cc" and n in (1, 2, 3):
                d.emit("trunk", TrunkEvent(kind="grant", frequency_hz=CAPTURE_CENTER + 50_000.0 * n, talkgroup=n))

        FakeDecoder.script = staticmethod(script)  # type: ignore[assignment]
        engine = self.make_engine([ChannelConfig(name="cc", protocol="dmr")], follow=FollowPolicy(max_voice_followers=1))
        for _ in range(4):
            self._feed_cc(engine, 0.3)
        drain(engine)
        followers = [n for n, c in engine.status().channels.items() if c["follower_of"]]
        self.assertEqual(len(followers), 1)

    def test_following_can_be_disabled(self):
        FakeDecoder.script = staticmethod(self._grant_script(CAPTURE_CENTER + 100_000.0))  # type: ignore[assignment]
        engine = self.make_engine([ChannelConfig(name="cc", protocol="dmr")], follow=FollowPolicy(max_voice_followers=0))
        self._feed_cc(engine)
        drain(engine)
        self.assertEqual(list(engine.status().channels), ["cc"])


class AudioSubscriberTests(_FakeEnv):
    def test_a_failing_subscriber_does_not_break_audio(self):
        cid = "fake-ch0-x-sub"

        def script(d, n):
            if n == 1:
                d.emit("call_start", make_call(cid))
            if n in (1, 2):
                d.emit("voice", tone_frame(cid, n - 1))

        FakeDecoder.script = staticmethod(script)  # type: ignore[assignment]
        engine = self.make_engine(forced())

        def bad(call_id, pcm, rate):
            raise RuntimeError("stt crashed")

        engine.subscribe_audio(bad)
        feed_noise(engine, 0.6)
        self.assertTrue(drain(engine))
        self.assertEqual(len(self.collector.pcm_frames()), 2)
        self.assertGreater(engine.metrics["audio_sub_errors"], 0)

    def test_unsubscribe(self):
        engine = self.make_engine(forced())
        got = []
        unsub = engine.subscribe_audio(lambda c, p, r: got.append(c))
        unsub()
        unsub()  # idempotent
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
