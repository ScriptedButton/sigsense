"""Unit tests for the building blocks: filters, discriminator, slicer, AFC tracker and the test
modulators/impairments. Owner: WP-B."""

from __future__ import annotations

import unittest

import numpy as np

from sigsense_api.dvoice.dsp.afc import FrequencyTracker
from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
from sigsense_api.dvoice.dsp.filters import gaussian_taps, lowpass_taps, rrc_taps
from sigsense_api.dvoice.dsp.slicer import AdaptiveSlicer
from sigsense_api.dvoice.events import Modulation
from sigsense_api.dvoice.testing import base as testing_base
from sigsense_api.dvoice.testing.channel import impair, multipath, rayleigh_fading, resample_ppm, signal_power
from sigsense_api.dvoice.testing.modulator import align_and_ber, group_delay_samples, modulate

LEVEL = {1: 3.0, 0: 1.0, 2: -1.0, 3: -3.0}


class FilterTests(unittest.TestCase):
    def test_rrc_pair_is_nyquist(self):
        for sps in (8.0, 10.0, 20.0):
            h = rrc_taps(sps, 0.2, 8)
            self.assertAlmostEqual(float(np.sum(h * h)), 1.0, places=9)
            rc = np.convolve(h, h)
            c = len(rc) // 2
            isi = [abs(rc[c + k * int(sps)] / rc[c]) for k in (1, 2, 3)]
            self.assertLess(max(isi), 0.01, (sps, isi))

    def test_gaussian_and_lowpass_unit_dc_gain(self):
        self.assertAlmostEqual(float(gaussian_taps(10.0, 0.5, 4).sum()), 1.0, places=9)
        lp = lowpass_taps(6250.0, 48000.0, 3750.0, 50.0)
        self.assertAlmostEqual(float(lp.sum()), 1.0, places=6)
        resp = np.abs(np.fft.rfft(lp, 4096))
        f = np.fft.rfftfreq(4096, 1 / 48000.0)
        self.assertGreater(resp[np.argmin(abs(f - 3000))], 0.99)
        self.assertLess(resp[np.argmin(abs(f - 11000))], 0.01)


class DiscriminatorTests(unittest.TestCase):
    def test_tone_gives_its_frequency_in_hz(self):
        t = np.arange(4800) / 48000.0
        for f in (1234.0, -2500.0, 0.0):
            out = FmDiscriminator(48000.0).process(np.exp(2j * np.pi * f * t).astype(np.complex64))
            self.assertAlmostEqual(float(np.median(out)), f, delta=0.5)

    def test_prefilter_passes_in_band_and_rejects_out_of_band(self):
        t = np.arange(9600) / 48000.0
        keep = FmDiscriminator(48000.0, bandwidth_hz=12500.0).process(np.exp(2j * np.pi * 1000.0 * t).astype(np.complex64))
        self.assertAlmostEqual(float(np.median(keep[400:])), 1000.0, delta=1.0)
        # a +14 kHz interferer 20 dB above a weak in-band carrier no longer captures the discriminator
        iq = (0.1 * np.exp(2j * np.pi * 1000.0 * t) + 1.0 * np.exp(2j * np.pi * 14000.0 * t)).astype(np.complex64)
        out = FmDiscriminator(48000.0, bandwidth_hz=12500.0).process(iq)
        self.assertLess(abs(float(np.median(out[400:])) - 1000.0), 600.0)

    def test_filter_start_is_primed_not_zero(self):
        """Zero filter history makes the first samples a near-zero-amplitude ramp, and arg() of that
        explodes (measured: ~21.7 kHz spikes for a 500 Hz tone). Priming the history with the first
        sample bounds the start-up error to about the tone's own frequency."""
        t = np.arange(2000) / 48000.0
        x = np.exp(2j * np.pi * 500.0 * t).astype(np.complex64)
        primed = FmDiscriminator(48000.0, bandwidth_hz=12500.0).process(x)
        zero = FmDiscriminator(48000.0, bandwidth_hz=12500.0)
        zero._primed = True  # skip priming: keep the zero history
        unprimed = zero.process(x)
        self.assertLess(float(np.abs(primed - 500.0).max()), 700.0)
        self.assertGreater(float(np.abs(unprimed - 500.0).max()), 5000.0)
        self.assertLess(float(np.abs(primed[200:] - 500.0).max()), 1.0)  # and it settles exactly


class SlicerTests(unittest.TestCase):
    def _symbols(self, n, c, a, seed=0, noise=0.0):
        rng = np.random.default_rng(seed)
        d = rng.integers(0, 4, n).astype(np.uint8)
        x = c + a * np.array([LEVEL[int(v)] for v in d]) / 3.0 + noise * rng.standard_normal(n)
        return d, x

    def test_blind_start_follows_offset_and_gain(self):
        for c, a in ((0.0, 1800.0), (900.0, 1800.0), (-700.0, 2400.0), (300.0, 800.0)):
            d, x = self._symbols(3000, c, a, seed=int(abs(c)) + 1)
            dibits, soft = AdaptiveSlicer(4).slice(x)
            self.assertLess(float(np.mean(dibits[200:] != d[200:])), 0.002, (c, a))
            self.assertAlmostEqual(float(np.median(soft[200:][d[200:] == 1])), 3.0, delta=0.25)

    def test_update_from_sync_recentres_exactly(self):
        sl = AdaptiveSlicer(4)
        d, x = self._symbols(500, 0.0, 1800.0, seed=3)
        sl.slice(x)
        # the deviation suddenly drops 25% and a DC step appears: re-centre from a known word
        d2, x2 = self._symbols(24, 400.0, 1350.0, seed=4)
        _, soft = sl.slice(x2)
        sl.update_from_sync(soft, d2)
        # (the soft values were produced while the slicer was still adapting, so the fit is a few % off)
        self.assertAlmostEqual(sl.center, 400.0, delta=40.0)
        self.assertAlmostEqual(sl.amplitude, 1350.0, delta=80.0)
        _, after = sl.slice(self._symbols(200, 400.0, 1350.0, seed=6)[1])
        self.assertAlmostEqual(float(np.abs(after[100:]).mean()), 2.0, delta=0.25)  # |levels| average 2

    def test_update_from_sync_ignores_uninformative_words(self):
        sl = AdaptiveSlicer(4)
        sl.slice(self._symbols(200, 0.0, 1000.0)[1])
        c, a = sl.center, sl.amplitude
        sl.update_from_sync(np.full(8, 3.0), np.full(8, 1, np.uint8))  # all one level: no spread to fit
        self.assertEqual((sl.center, sl.amplitude), (c, a))

    def test_two_level_uses_only_values_1_and_3(self):
        rng = np.random.default_rng(5)
        sign = rng.integers(0, 2, 2000)
        x = np.where(sign, 1200.0, -1200.0) + 40.0 * rng.standard_normal(2000)
        dibits, soft = AdaptiveSlicer(2).slice(x)
        self.assertEqual(set(np.unique(dibits).tolist()), {1, 3})
        self.assertEqual(float(np.mean(dibits[100:] != np.where(sign[100:], 1, 3))), 0.0)

    def test_bad_level_count(self):
        with self.assertRaises(ValueError):
            AdaptiveSlicer(3)


class FrequencyTrackerTests(unittest.TestCase):
    def test_closed_loop_converges_and_clamps(self):
        tr = FrequencyTracker(limit_hz=1500.0, bandwidth=0.2)
        true_offset, corr = 900.0, 0.0
        for _ in range(60):
            corr = tr.update(true_offset - corr)  # residual seen by the front end
        self.assertAlmostEqual(corr, 900.0, delta=1.0)
        tr.reset()
        for _ in range(200):
            corr = tr.update(5000.0 - corr)  # beyond the limit
        self.assertEqual(corr, 1500.0)
        self.assertEqual(FrequencyTracker(limit_hz=100.0, bandwidth=1.0).update(-300.0), -100.0)

    def test_ignores_bad_measurements_and_dead_band(self):
        tr = FrequencyTracker(limit_hz=1500.0, bandwidth=0.5, dead_band_hz=10.0)
        self.assertEqual(tr.update(float("nan")), 0.0)
        self.assertEqual(tr.update(float("inf")), 0.0)
        self.assertEqual(tr.update(8.0), 0.0)  # inside the dead band
        self.assertEqual(tr.update(20.0), 10.0)

    def test_rejects_invalid_arguments(self):
        for kw in (dict(limit_hz=0.0), dict(bandwidth=0.0), dict(bandwidth=1.5)):
            with self.assertRaises(ValueError):
                FrequencyTracker(**kw)


class ModulatorTests(unittest.TestCase):
    def test_fsk_symbol_centres_sit_at_the_nominal_deviation(self):
        rng = np.random.default_rng(1)
        d = rng.integers(0, 4, 400).astype(np.uint8)
        iq = modulate(d, 4800.0, Modulation.C4FM, 48000.0, 1800.0)
        f = FmDiscriminator(48000.0).process(iq).astype(np.float64)
        # matched filter then sample at the symbol centres: levels = dev/3 * (+/-3, +/-1)
        rx = np.convolve(f, rrc_taps(10.0, 0.2, 8), mode="full")
        delay = group_delay_samples(4800.0, Modulation.C4FM) + 40
        got = np.array([rx[delay + 10 * k] for k in range(20, 380)])
        want = np.array([LEVEL[int(v)] for v in d[20:380]]) * 600.0
        # truncated RRC (8 symbols) and the arg() discriminator leave a few % of residual ISI:
        # measured mean 38 Hz, max 133 Hz against the 600 Hz level spacing
        self.assertLess(float(np.abs(got - want).mean()), 60.0)
        self.assertLess(float(np.abs(got - want).max()), 180.0)

    def test_every_modulation_has_unit_power_and_right_length(self):
        d = np.random.default_rng(2).integers(0, 4, 500).astype(np.uint8)
        for mod in (Modulation.C4FM, Modulation.FSK4, Modulation.GMSK, Modulation.CQPSK):
            iq = modulate(d if mod != Modulation.GMSK else np.where(d & 1, 3, 1).astype(np.uint8), 4800.0, mod)
            self.assertEqual(iq.dtype, np.complex64)
            self.assertAlmostEqual(float(np.mean(np.abs(iq) ** 2)), 1.0, delta=0.02, msg=mod.value)
            self.assertGreaterEqual(len(iq), 500 * 10)
        with self.assertRaises(ValueError):
            modulate(d, 4800.0, "bogus")  # type: ignore[arg-type]
        self.assertEqual(len(modulate(np.zeros(0, np.uint8), 4800.0, Modulation.C4FM)), 0)

    def test_cqpsk_phase_steps_follow_the_dibit_map(self):
        d = np.array([1, 0, 2, 3, 1, 1, 3, 0] * 30, dtype=np.uint8)
        iq = modulate(d, 4800.0, Modulation.CQPSK)
        # a differential decode at the symbol centres recovers the steps (through the receiver chain)
        from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery

        r = align_and_ber(np.tile(d, 3), CqpskSymbolRecovery(4800.0).process(np.tile(iq, 1)).dibits, search=20, skip=40, length=120)
        self.assertEqual(r["ser"], 0.0)

    def test_base_module_delegates(self):
        d = np.random.default_rng(3).integers(0, 4, 100).astype(np.uint8)
        a = testing_base.modulate(d, 4800.0, Modulation.C4FM)
        np.testing.assert_array_equal(a, modulate(d, 4800.0, Modulation.C4FM))
        y = testing_base.impair(a, 20.0, 100.0, 10.0, True, 4)
        self.assertEqual(y.dtype, np.complex64)


class ImpairmentTests(unittest.TestCase):
    def _tone(self, n=48000):
        t = np.arange(n) / 48000.0
        return np.exp(2j * np.pi * 1000.0 * t).astype(np.complex64)

    def test_snr_is_what_was_asked(self):
        x = self._tone()
        for snr in (5.0, 15.0, 25.0):
            for bw in (None, 12500.0):
                y = impair(x, snr, seed=3, snr_bandwidth_hz=bw)
                noise = y.astype(np.complex128) - x
                bw_hz = bw or 48000.0
                measured = 10 * np.log10(signal_power(x) / (np.mean(np.abs(noise) ** 2) * bw_hz / 48000.0))
                self.assertAlmostEqual(float(measured), snr, delta=0.2, msg=(snr, bw))

    def test_frequency_offset_invert_and_determinism(self):
        x = self._tone()
        y = impair(x, np.inf, freq_offset_hz=500.0)
        f = np.angle(y[1:] * np.conj(y[:-1])) * 48000.0 / (2 * np.pi)
        self.assertAlmostEqual(float(np.median(f)), 1500.0, delta=0.5)
        f = np.angle(impair(x, np.inf, invert=True)[1:] * np.conj(impair(x, np.inf, invert=True)[:-1])) * 48000.0 / (2 * np.pi)
        self.assertAlmostEqual(float(np.median(f)), -1000.0, delta=0.5)
        np.testing.assert_array_equal(impair(x, 12.0, seed=9), impair(x, 12.0, seed=9))
        self.assertFalse(np.array_equal(impair(x, 12.0, seed=9), impair(x, 12.0, seed=10)))

    def test_timing_ppm_changes_the_length_proportionally(self):
        x = self._tone(200000)
        for ppm in (100.0, -100.0, 5000.0):
            y = resample_ppm(x, ppm)
            self.assertAlmostEqual(len(y) / len(x), 1.0 / (1.0 + ppm * 1e-6), delta=3e-5)
        # a tone read faster comes out higher in frequency by the same ratio
        y = resample_ppm(x, 5000.0)
        f = np.angle(y[1:] * np.conj(y[:-1])) * 48000.0 / (2 * np.pi)
        self.assertAlmostEqual(float(np.median(f)), 1000.0 * 1.005, delta=0.5)

    def test_drift_dc_multipath_and_fading(self):
        x = self._tone(96000)
        y = impair(x, np.inf, drift_hz_per_s=1000.0)
        f = np.angle(y[1:] * np.conj(y[:-1])) * 48000.0 / (2 * np.pi)
        self.assertGreater(float(f[-100:].mean() - f[:100].mean()), 1500.0)
        z = impair(x, np.inf, dc_offset=0.1)
        self.assertAlmostEqual(float(np.abs(z.mean() - 0.1 * np.sqrt(signal_power(x)))), 0.0, delta=0.02)
        m = multipath(x, 48000.0, [(5 / 48000.0, 0.5 + 0j)])
        self.assertGreater(float(np.mean(np.abs(m) ** 2)), 1.2)
        g = rayleigh_fading(200000, 48000.0, 30.0, seed=1)
        self.assertAlmostEqual(float(np.mean(np.abs(g) ** 2)), 1.0, delta=0.15)

    def test_align_and_ber_finds_lag_and_polarity(self):
        rng = np.random.default_rng(1)
        tx = rng.integers(0, 4, 3000).astype(np.uint8)
        rx = np.concatenate((rng.integers(0, 4, 17).astype(np.uint8), tx))  # 17 extra symbols in front
        r = align_and_ber(tx, rx, search=100, skip=300, length=2000)
        self.assertEqual((r["lag"], r["ser"], r["inverted"]), (17, 0.0, False))
        r = align_and_ber(tx, rx ^ 2, search=100, skip=300, length=2000)
        self.assertEqual((r["lag"], r["ser"], r["inverted"]), (17, 0.0, True))
        noisy = rx.copy()
        noisy[400:420] ^= 1
        r = align_and_ber(tx, noisy, search=100, skip=300, length=2000)
        self.assertEqual(r["lag"], 17)
        self.assertAlmostEqual(r["ser"], 20 / 2000, places=6)
        self.assertAlmostEqual(r["ber"], 20 / 4000, places=6)  # one bit per wrong symbol


if __name__ == "__main__":
    unittest.main()
