"""WP-F: per-call sessions, concealment, state isolation and the session pool."""

from __future__ import annotations

import threading
import unittest

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.vocoder.backends.tone import ToneVocoder, tone_backend_for, tone_params
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, Vocoder, VocoderFrame
from sigsense_api.dvoice.vocoder.session import (
    STATUS_CONCEALED,
    STATUS_MUTED,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ConcealmentPolicy,
    SessionPool,
    VocoderSession,
)

VID = VocoderId.IMBE_7200X4400


def frame(seed=1, n=88, **kw):
    rng = np.random.default_rng(seed)
    return VocoderFrame(VID, (rng.random(n) < 0.4).astype(np.uint8), **kw)


class ToneVocoderTests(unittest.TestCase):
    def test_deterministic_and_phase_continuous(self):
        a, b = ToneVocoder(), ToneVocoder()
        f = frame(3)
        x = np.concatenate([a.decode(f) for _ in range(5)])
        y = np.concatenate([b.decode(f) for _ in range(5)])
        np.testing.assert_array_equal(x, y)
        # Phase continuity: the largest sample-to-sample step stays near a sine's, no jump at frame joins.
        steps = np.abs(np.diff(x.astype(float)))
        self.assertLess(steps.max(), 1.2 * (2 * np.pi * tone_params(f.bits)[0] / 8000) * np.abs(x).max())

    def test_reset_restarts_the_phase(self):
        v = ToneVocoder()
        first = v.decode(frame(5))
        v.decode(frame(5))
        v.reset()
        np.testing.assert_array_equal(v.decode(frame(5)), first)

    def test_different_frames_different_pitch(self):
        self.assertNotEqual(tone_params(np.array([0] * 6))[0], tone_params(np.array([1] * 6))[0])

    def test_erased_is_silence(self):
        self.assertFalse(ToneVocoder().decode(frame(erased=True)).any())

    def test_one_backend_class_per_codec(self):
        self.assertIs(tone_backend_for(VID), tone_backend_for(VID))
        self.assertEqual(tone_backend_for(VocoderId.CODEC2_3200).id, VocoderId.CODEC2_3200)


class SessionTests(unittest.TestCase):
    def test_unavailable_backend_still_yields_well_formed_output(self):
        with_nothing = VocoderSession(VocoderId.IMBE_7200X4400)  # no adapter configured in this process
        if with_nothing.available:
            self.skipTest("an IMBE adapter is configured on this machine")
        out = with_nothing.push(frame())
        self.assertFalse(out.available)
        self.assertEqual(out.status, STATUS_UNAVAILABLE)
        self.assertEqual(out.pcm8k.shape, (FRAME_SAMPLES,))
        self.assertEqual(out.pcm48k.shape, (6 * FRAME_SAMPLES,))
        self.assertFalse(out.pcm8k.any())
        self.assertEqual(with_nothing.stats.frames, 1)

    def make(self, **kw):
        return VocoderSession(VID, backend=ToneVocoder(), **kw)

    def test_good_frames_flow_through_both_rates(self):
        s = self.make()
        out = [s.push(frame(i)) for i in range(10)]
        self.assertTrue(all(o.status == STATUS_OK and o.available for o in out))
        self.assertTrue(all(o.pcm48k.shape == (960,) for o in out))
        self.assertEqual(s.stats.good, 10)
        self.assertGreater(np.abs(out[-1].pcm48k).max(), 1000)

    def test_lost_frames_repeat_quieter_then_mute_then_recover(self):
        s = self.make(policy=ConcealmentPolicy(repeat_limit=2, decay=0.5))
        good = s.push(frame(1))
        a = s.push(frame(erased=True))
        b = s.push(frame(erased=True))
        c = s.push(frame(erased=True))
        self.assertEqual([a.status, b.status, c.status], [STATUS_CONCEALED, STATUS_CONCEALED, STATUS_MUTED])
        np.testing.assert_array_equal(a.pcm8k, np.round(good.pcm8k * 0.5).astype(np.int16))
        np.testing.assert_array_equal(b.pcm8k, np.round(good.pcm8k * 0.25).astype(np.int16))
        self.assertFalse(c.pcm8k.any())
        again = s.push(frame(2))
        self.assertEqual(again.status, STATUS_OK)
        self.assertEqual((s.stats.concealed, s.stats.muted, s.stats.good), (2, 1, 2))

    def test_loss_before_any_good_frame_is_muted(self):
        out = self.make().push(frame(erased=True))
        self.assertEqual(out.status, STATUS_MUTED)

    def test_heavily_damaged_frames_are_treated_as_lost(self):
        s = self.make(policy=ConcealmentPolicy(max_fec_errors=4))
        s.push(frame(1))
        self.assertEqual(s.push(frame(2, fec_errors=4)).status, STATUS_OK)
        self.assertEqual(s.push(frame(3, fec_errors=5)).status, STATUS_CONCEALED)
        self.assertEqual(s.stats.fec_errors, 9)

    def test_backend_exception_is_concealed_not_raised(self):
        class Flaky(Vocoder):
            id = VID

            @classmethod
            def availability(cls):
                raise AssertionError

            def decode(self, f):
                if f.bits[0]:
                    raise RuntimeError("fault")
                return np.full(FRAME_SAMPLES, 1000, np.int16)

        s = VocoderSession(VID, backend=Flaky())
        ok = s.push(VocoderFrame(VID, np.zeros(88, np.uint8)))
        bad = s.push(VocoderFrame(VID, np.ones(88, np.uint8)))
        self.assertEqual((ok.status, bad.status), (STATUS_OK, STATUS_CONCEALED))
        self.assertEqual(s.stats.bad, 1)

    def test_sessions_do_not_share_synthesis_state(self):
        """Two calls (or two TDMA slots) interleaved must sound exactly like each decoded alone."""
        seq_a = [frame(i) for i in range(1, 8)]
        seq_b = [frame(i + 100) for i in range(1, 8)]

        def alone(seq):
            s = self.make()
            return [s.push(f).pcm48k for f in seq]

        solo_a, solo_b = alone(seq_a), alone(seq_b)
        a, b = self.make(), self.make()
        mixed_a, mixed_b = [], []
        for fa, fb in zip(seq_a, seq_b):
            mixed_a.append(a.push(fa).pcm48k)
            mixed_b.append(b.push(fb).pcm48k)
        for x, y in zip(solo_a + solo_b, mixed_a + mixed_b):
            np.testing.assert_array_equal(x, y)

    def test_close_returns_a_fade_out_and_blocks_reuse(self):
        s = self.make()
        for i in range(5):
            s.push(frame(i))
        tail = s.close()
        self.assertGreater(tail.size, 0)
        self.assertLess(abs(int(tail[-1])), 50)  # ends near zero, not mid-cycle
        self.assertEqual(s.close().size, 0)  # idempotent
        with self.assertRaises(RuntimeError):
            s.push(frame())

    def test_reset_makes_the_session_start_clean(self):
        s = self.make()
        first = s.push(frame(1)).pcm48k
        for i in range(4):
            s.push(frame(i))
        s.reset()
        np.testing.assert_array_equal(s.push(frame(1)).pcm48k, first)

    def test_concurrent_pushes_on_separate_sessions(self):
        errors = []

        def work(seed):
            try:
                s = self.make()
                for i in range(200):
                    assert s.push(frame(seed * 1000 + i)).pcm48k.shape == (960,)
                s.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(k,)) for k in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])


class PoolTests(unittest.TestCase):
    def pool(self, **kw):
        self.now = [0.0]
        return SessionPool(clock=lambda: self.now[0], factory=lambda vid: tone_backend_for(vid)(), **kw)

    def test_same_key_same_session_other_key_other_session(self):
        p = self.pool()
        a1 = p.session(("ch0", 1, 0), VID)
        self.assertIs(a1, p.session(("ch0", 1, 0), VID))
        self.assertIsNot(a1, p.session(("ch0", 1, 1), VID))  # the other TDMA slot of the same call
        self.assertEqual(len(p), 2)

    def test_slots_stay_independent_through_the_pool(self):
        p = self.pool()
        slot0 = [p.push(("c", 7, 0), frame(i)).pcm48k for i in range(1, 6)]
        p2 = self.pool()
        solo = [p2.push(("c", 7, 0), frame(i)).pcm48k for i in range(1, 6)]
        # Interleave slot 1 into a third pool: slot 0's audio must be unchanged.
        p3 = self.pool()
        mixed = []
        for i in range(1, 6):
            mixed.append(p3.push(("c", 7, 0), frame(i)).pcm48k)
            p3.push(("c", 7, 1), frame(i + 50))
        for x, y, z in zip(slot0, solo, mixed):
            np.testing.assert_array_equal(x, y)
            np.testing.assert_array_equal(x, z)

    def test_codec_change_on_a_key_replaces_the_session(self):
        p = self.pool()
        first = p.session("k", VID)
        second = p.session("k", VocoderId.CODEC2_3200)
        self.assertIsNot(first, second)
        self.assertEqual(len(p), 1)
        with self.assertRaises(RuntimeError):
            first.push(frame())  # the old one was closed

    def test_idle_sessions_are_reaped(self):
        p = self.pool(idle_s=10.0)
        old = p.session("old", VID)
        self.now[0] = 5.0
        p.session("fresh", VID)
        self.now[0] = 12.0
        p.session("fresh", VID)  # touching the pool reaps what's idle
        self.assertEqual(len(p), 1)
        with self.assertRaises(RuntimeError):
            old.push(frame())

    def test_lru_cap(self):
        p = self.pool(max_sessions=3)
        keys = [f"k{i}" for i in range(5)]
        for i, k in enumerate(keys):
            self.now[0] = float(i)
            p.session(k, VID)
        self.assertEqual(len(p), 3)

    def test_end_returns_tail_and_unknown_key_is_harmless(self):
        p = self.pool()
        p.push("c", frame(1))
        self.assertGreater(p.end("c").size, 0)
        self.assertEqual(p.end("c").size, 0)
        self.assertEqual(len(p), 0)

    def test_pool_with_registry_test_double(self):
        with registry.use_test_double():
            p = SessionPool()
            out = p.push("x", frame(2))
            self.assertTrue(out.available)
            p.close_all()


if __name__ == "__main__":
    unittest.main()
