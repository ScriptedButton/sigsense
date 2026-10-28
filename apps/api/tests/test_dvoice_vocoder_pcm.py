"""WP-F: 8 kHz -> 48 kHz post-processing quality."""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.vocoder.pcm import (
    FACTOR,
    PcmPostProcessor,
    Ramp,
    SoftLimiter,
    StreamingUpsampler,
    to_int16,
)


def sine(freq, seconds, rate, amp=8000.0):
    t = np.arange(int(rate * seconds)) / rate
    return amp * np.sin(2 * np.pi * freq * t)


def level_db(x):
    return 10 * np.log10(np.mean(np.asarray(x, float) ** 2) + 1e-12)


class UpsamplerTests(unittest.TestCase):
    def test_tone_comes_out_clean(self):
        up = StreamingUpsampler()
        y = up.process(sine(1000, 0.5, 8000))
        self.assertEqual(y.size, 0.5 * 8000 * FACTOR)
        steady = y[up.delay + 2000 :]
        t = (np.arange(steady.size) + up.delay + 2000) / 48000
        # Fit a sine of the right frequency; whatever is left is distortion + noise.
        basis = np.stack([np.sin(2 * np.pi * 1000 * t), np.cos(2 * np.pi * 1000 * t)], 1)
        coef, *_ = np.linalg.lstsq(basis, steady, rcond=None)
        resid = steady - basis @ coef
        self.assertGreater(level_db(basis @ coef) - level_db(resid), 60.0)
        self.assertAlmostEqual(np.hypot(*coef), 8000.0, delta=80.0)  # gain within ~1%

    def test_images_are_rejected(self):
        up = StreamingUpsampler()
        y = up.process(sine(3000, 0.5, 8000))[2000:]
        spec = np.abs(np.fft.rfft(y * np.hanning(y.size)))
        freqs = np.fft.rfftfreq(y.size, 1 / 48000)
        main = spec[np.argmin(np.abs(freqs - 3000))]
        image = spec[np.argmin(np.abs(freqs - 5000))]  # 8000 - 3000
        self.assertLess(20 * np.log10(image / main), -60.0)

    def test_chunking_does_not_change_the_output(self):
        x = sine(700, 0.4, 8000) + sine(2100, 0.4, 8000, 3000)
        whole = StreamingUpsampler().process(x)
        up = StreamingUpsampler()
        parts = np.concatenate([up.process(x[i : i + 160]) for i in range(0, x.size, 160)])
        np.testing.assert_allclose(parts, whole, atol=1e-8)

    def test_flush_completes_the_signal(self):
        x = sine(1000, 0.05, 8000)
        up = StreamingUpsampler()
        body, tail = up.process(x), up.flush()
        all_at_once = StreamingUpsampler().process(np.concatenate([x, np.zeros(len(x))]))
        np.testing.assert_allclose(np.concatenate([body, tail]), all_at_once[: body.size + tail.size], atol=1e-8)


class LimiterRampTests(unittest.TestCase):
    def test_never_exceeds_ceiling_and_leaves_quiet_audio_alone(self):
        lim = SoftLimiter(ceiling=0.8)
        loud = sine(500, 0.2, 48000, amp=40000)  # beyond full scale
        out = lim.process(loud)
        self.assertLessEqual(np.abs(out).max(), 0.8 * 32768 + 1e-6)
        quiet = SoftLimiter().process(sine(500, 0.1, 48000, amp=3000))
        np.testing.assert_allclose(quiet, sine(500, 0.1, 48000, amp=3000), atol=1e-6)

    def test_limiter_recovers_after_a_burst(self):
        lim = SoftLimiter(release_s=0.02)
        lim.process(np.full(480, 60000.0))
        later = lim.process(np.full(48000, 3000.0))
        self.assertAlmostEqual(later[-1], 3000.0, delta=30.0)

    def test_ramp_is_monotonic_and_starts_at_zero(self):
        r = Ramp(ms=4)
        y = r.fade_in(np.ones(960))
        self.assertEqual(y[0], 0.0)
        self.assertTrue(np.all(np.diff(y[: r.n]) >= 0))
        self.assertEqual(y[-1], 1.0)
        z = r.fade_out(np.ones(960))
        self.assertAlmostEqual(z[-1], 0.0, places=2)


class PostProcessorTests(unittest.TestCase):
    def test_call_starts_and_ends_without_steps(self):
        pp = PcmPostProcessor()
        frames = [to_int16(sine(900, 0.02, 8000) * 1.0) for _ in range(20)]
        # A 20 ms frame of a 900 Hz tone isn't a whole number of cycles; the stream is still continuous only
        # if the generator is, so feed one continuous tone chopped into frames.
        x = to_int16(sine(900, 0.4, 8000))
        out = np.concatenate([pp.push(x[i : i + 160]) for i in range(0, 3200, 160)] + [pp.finish()])
        self.assertEqual(out.size, 20 * 960 + 144)  # 20 frames x 960 + filter tail
        self.assertLess(abs(int(out[0])), 30)  # fade-in: first sample near zero
        self.assertLess(abs(int(out[-1])), 30)  # fade-out: last sample near zero
        steps = np.abs(np.diff(out.astype(float)))
        self.assertLess(steps.max(), 2.0 * 2 * np.pi * 900 / 48000 * 8000)  # no click anywhere
        del frames

    def test_gain_and_clipping(self):
        pp = PcmPostProcessor(gain=4.0)
        out = np.concatenate([pp.push(to_int16(sine(500, 0.1, 8000, 20000))[i : i + 160]) for i in range(0, 800, 160)])
        self.assertLessEqual(np.abs(out).max(), int(0.89 * 32768) + 2)

    def test_to_int16_saturates(self):
        np.testing.assert_array_equal(to_int16(np.array([-1e9, 0.4, 0.6, 1e9])), [-32768, 0, 1, 32767])


if __name__ == "__main__":
    unittest.main()
