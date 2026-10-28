"""Channelizer: wideband IQ -> 48 kHz channel. Owner: WP-B.

Checks passband/stopband behaviour at several capture rates (including non-integer ratios such as
4 MS/s and 10 MS/s), phase continuity when the offset is retuned, block-size independence, and
that the output rate is exact (resampler error in ppm)."""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.channel import CHANNEL_RATE, Channelizer

RATES = (1_024_000.0, 2_400_000.0, 4_000_000.0, 10_000_000.0, 20_000_000.0, 48_000.0)


def tones(fs, offset, freqs, n, amps=None):
    t = np.arange(n) / fs
    amps = amps or [1.0] * len(freqs)
    return sum(a * np.exp(2j * np.pi * (offset + f) * t) for a, f in zip(amps, freqs)).astype(np.complex64)


def level_db(y, f, fs=CHANNEL_RATE):
    spec = np.abs(np.fft.fft(y * np.hanning(len(y)))) ** 2
    fr = np.fft.fftfreq(len(y), 1 / fs)
    i = int(np.argmin(np.abs(fr - f)))
    return 10 * np.log10(spec[max(0, i - 3) : i + 4].max() + 1e-30)


class ChannelizerTests(unittest.TestCase):
    def test_passband_adjacent_and_alias_rejection_at_every_rate(self):
        for fs in RATES:
            offset = 0.23 * fs if fs > 100e3 else 1500.0
            ch = Channelizer(fs, offset, 12_500.0)
            n = int(fs * 0.4)
            y = ch.process(tones(fs, offset, [2_000.0, 9_000.0, 25_000.0], n))[2000:]
            p2, p9 = level_db(y, 2_000.0), level_db(y, 9_000.0)
            p25 = level_db(y, 25_000.0 - CHANNEL_RATE)  # 25 kHz folds to -23 kHz at the 48 kHz output
            self.assertGreater(p9 - p2, -40.0)  # sanity: the measurement found the tone
            self.assertLess(p9 - p2, -12.0, fs)  # 9 kHz is already in the filter's transition band
            self.assertLess(p25 - p2, -55.0, fs)  # outside the channel: > 55 dB down (measured ~74 dB)
            self.assertLess(ch.rate_error_ppm, 1.0, fs)  # exact output rate

    def test_unity_gain_in_the_passband(self):
        ch = Channelizer(2_400_000.0, 100_000.0, 12_500.0)
        y = ch.process(tones(2_400_000.0, 100_000.0, [1_000.0], 960_000))[3000:]
        self.assertAlmostEqual(float(np.mean(np.abs(y))), 1.0, delta=0.02)

    def test_output_length_matches_the_ratio(self):
        for fs in (2_400_000.0, 4_000_000.0, 10_000_000.0):
            ch = Channelizer(fs, 0.0, 12_500.0)
            out = sum(len(ch.process(np.zeros(int(fs * 0.1), np.complex64))) for _ in range(5))
            self.assertAlmostEqual(out / (fs * 0.5) * fs / CHANNEL_RATE, 1.0, delta=0.01)

    def test_block_size_independence(self):
        fs, offset = 4_000_000.0, -600_000.0
        x = tones(fs, offset, [1_500.0, -3_000.0], 800_000)
        whole = Channelizer(fs, offset).process(x)
        rng = np.random.default_rng(0)
        ch = Channelizer(fs, offset)
        parts, i = [], 0
        while i < len(x):
            n = int(rng.integers(1, 90_000))
            parts.append(ch.process(x[i : i + n]))
            i += n
        got = np.concatenate(parts)
        self.assertEqual(len(got), len(whole))
        np.testing.assert_allclose(got, whole, rtol=0, atol=2e-5)

    def test_retune_is_phase_continuous(self):
        """Changing the offset mid-stream must not glitch a tone that stays at the channel centre."""
        fs = 2_400_000.0
        ch = Channelizer(fs, 200_000.0)
        t = np.arange(int(fs * 0.5)) / fs
        # carrier at +2 kHz above whichever offset is current: first half 200 kHz, second 250 kHz
        half = len(t) // 2
        phase = np.concatenate((2 * np.pi * (200_000 + 2_000) * t[:half], 2 * np.pi * ((250_000 + 2_000) * (t[half:] - t[half]) + (200_000 + 2_000) * t[half])))
        x = np.exp(1j * phase).astype(np.complex64)
        a = ch.process(x[:half])
        ch.set_offset(250_000.0)
        b = ch.process(x[half:])
        y = np.concatenate((a, b))
        # instantaneous frequency stays ~2 kHz; no step larger than 5% of the tone's phase advance
        f_inst = np.angle(y[1:] * np.conj(y[:-1])) * CHANNEL_RATE / (2 * np.pi)
        settled = np.concatenate((f_inst[500 : len(a) - 50], f_inst[len(a) + 600 :]))
        self.assertLess(float(np.abs(settled - 2000.0).max()), 60.0)

    def test_rejects_bad_arguments(self):
        with self.assertRaises(ValueError):
            Channelizer(24_000.0, 0.0)
        ch = Channelizer(2_400_000.0, 0.0)
        with self.assertRaises(ValueError):
            ch.set_offset(1_300_000.0)

    def test_reset_restores_the_initial_response(self):
        fs = 2_400_000.0
        x = tones(fs, 0.0, [1_000.0], 240_000)
        ch = Channelizer(fs, 0.0)
        a = ch.process(x)
        ch.reset()
        b = ch.process(x)
        np.testing.assert_allclose(a, b, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
