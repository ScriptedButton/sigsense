"""HackRF demodulator: tones survive, chunking is seamless, audio ring behaves."""

import os
import time
import unittest

import numpy as np

from sigsense_api import hackrf
from sigsense_api.demod import AUDIO_RATE, Demodulator

SCRATCH = (
    "/tmp/claude-1000/-home-cole-Documents-sigsense/"
    "f8b41652-f1b6-4e63-95c7-1f39c714a032/scratchpad"
)


def nbfm(fs, seconds, offset, tone=1000.0, dev=2500.0, amp=0.5):
    t = np.arange(int(fs * seconds)) / fs
    phase = 2 * np.pi * offset * t - (dev / tone) * np.cos(2 * np.pi * tone * t)
    return (amp * np.exp(1j * phase)).astype(np.complex64)


def run_chunks(demod, iq, sizes):
    out, pos, i = [], 0, 0
    while pos < len(iq):
        n = sizes[i % len(sizes)]
        out.append(demod.process(iq[pos : pos + n]))
        pos += n
        i += 1
    return np.concatenate(out).astype(float)


def peak_hz(audio, skip=0.15):
    a = audio[int(skip * AUDIO_RATE) :]
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    return np.fft.rfftfreq(len(a), 1 / AUDIO_RATE)[spec.argmax()]


ODD_CHUNKS = [12_345, 40_001, 999, 77_777, 5_003]


class DemodTests(unittest.TestCase):
    def assert_seamless(self, make, iq, expected=1000.0, tol=15.0):
        chunked = run_chunks(make(), iq, ODD_CHUNKS)
        whole = run_chunks(make(), iq, [len(iq)])
        self.assertLess(abs(peak_hz(chunked) - expected), tol)
        n = min(len(chunked), len(whole))
        # Stateful processing: chunking must not change the audio at all.
        err = np.abs(chunked[:n] - whole[:n]).max() / (np.abs(whole).max() + 1)
        self.assertLess(err, 0.02, f"chunk boundaries disturb audio ({err:.4f})")
        step = np.abs(np.diff(chunked[int(0.15 * AUDIO_RATE) :])).max()
        self.assertLess(step, 0.4 * np.abs(chunked).max())

    def test_nbfm_tone_at_offset(self):
        fs = 2e6
        iq = nbfm(fs, 1.0, 100e3)
        self.assert_seamless(lambda: Demodulator(fs, "NBFM", 12_500, 100e3), iq)

    def test_nbfm_negative_offset(self):
        fs = 2e6
        iq = nbfm(fs, 0.6, -250e3)
        audio = run_chunks(Demodulator(fs, "NBFM", 12_500, -250e3), iq, ODD_CHUNKS)
        self.assertLess(abs(peak_hz(audio) - 1000), 15)

    def test_wbfm_4msps(self):
        fs = 4e6
        t = np.arange(int(fs * 0.8)) / fs
        phase = 2 * np.pi * 200e3 * t - (50e3 / 1000) * np.cos(2 * np.pi * 1000 * t)
        iq = (0.5 * np.exp(1j * phase)).astype(np.complex64)
        self.assert_seamless(lambda: Demodulator(fs, "WBFM", 180_000, 200e3), iq)

    def test_am_tone(self):
        fs = 2e6
        t = np.arange(int(fs * 0.8)) / fs
        env = 0.4 * (1 + 0.5 * np.cos(2 * np.pi * 1000 * t))
        iq = (env * np.exp(2j * np.pi * 100e3 * t)).astype(np.complex64)
        self.assert_seamless(lambda: Demodulator(fs, "AM", 10_000, 100e3), iq)

    def test_usb_and_lsb(self):
        fs = 2e6
        t = np.arange(int(fs * 0.8)) / fs
        usb = (0.2 * np.exp(2j * np.pi * (100e3 + 1000) * t)).astype(np.complex64)
        lsb = (0.2 * np.exp(2j * np.pi * (100e3 - 1000) * t)).astype(np.complex64)
        self.assert_seamless(lambda: Demodulator(fs, "USB", 2_700, 100e3), usb)
        self.assert_seamless(lambda: Demodulator(fs, "LSB", 2_700, 100e3), lsb)

    def test_sideband_rejection(self):
        fs = 2e6
        t = np.arange(int(fs * 1.0)) / fs
        # A 10x stronger signal on the wrong (lower) side at 1.5 kHz, a weak
        # wanted tone at +1 kHz: USB must keep the wanted one.
        iq = (
            1.0 * np.exp(2j * np.pi * (100e3 - 1500) * t)
            + 0.1 * np.exp(2j * np.pi * (100e3 + 1000) * t)
        ).astype(np.complex64)
        audio = run_chunks(Demodulator(fs, "USB", 2_700, 100e3), iq, [50_000])
        a = audio[int(0.2 * AUDIO_RATE) :]
        spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
        freqs = np.fft.rfftfreq(len(a), 1 / AUDIO_RATE)
        wanted = spec[np.abs(freqs - 1000) < 20].max()
        unwanted = spec[np.abs(freqs - 1500) < 20].max()
        self.assertLess(abs(peak_hz(audio, 0.2) - 1000), 15)
        self.assertGreater(wanted, 3 * unwanted)

    def test_cw_pitch(self):
        fs = 2e6
        t = np.arange(int(fs * 0.8)) / fs
        iq = (0.2 * np.exp(2j * np.pi * 100e3 * t)).astype(np.complex64)
        audio = run_chunks(Demodulator(fs, "CW", 400, 100e3), iq, ODD_CHUNKS)
        self.assertLess(abs(peak_hz(audio) - 700), 15)

    def test_offset_change_keeps_phase_and_follows(self):
        fs = 2e6
        d = Demodulator(fs, "NBFM", 12_500, 100e3)
        first = run_chunks(d, nbfm(fs, 0.4, 100e3), [50_000])
        d.reconfigure(offset_hz=-150e3)
        second = run_chunks(d, nbfm(fs, 0.6, -150e3), [50_000])
        self.assertLess(abs(peak_hz(first) - 1000), 20)
        self.assertLess(abs(peak_hz(second, skip=0.2) - 1000), 20)

    def test_pcm_is_little_endian_int16(self):
        d = Demodulator(2e6, "NBFM", 12_500, 100e3)
        pcm = d.process(nbfm(2e6, 0.2, 100e3))
        self.assertEqual(pcm.dtype, np.dtype("<i2"))
        self.assertEqual(len(pcm.tobytes()), 2 * len(pcm))


class AudioRingTests(unittest.TestCase):
    def setUp(self):
        self.fake = os.path.join(SCRATCH, "hackrf_transfer")
        if not os.path.exists(self.fake):
            self.skipTest("fake hackrf_transfer not available")
        self.saved = hackrf.HACKRF_TRANSFER
        hackrf.HACKRF_TRANSFER = self.fake

    def tearDown(self):
        hackrf.HACKRF_TRANSFER = self.saved

    def test_take_audio_iq(self):
        cap = hackrf.HackRFCapture(
            center_freq_hz=462.6e6, sample_rate_hz=2e6, gain_db=32
        )
        try:
            self.assertEqual(cap.take_audio_iq().size, 0)
            cap.start_stream()
            gen = cap.generation
            cap.enable_audio(True)
            time.sleep(0.5)
            got = cap.take_audio_iq()
            self.assertGreater(got.size, 100_000)
            self.assertLessEqual(got.size, int(0.25 * 2e6))  # backlog trimmed
            self.assertEqual(got.dtype, np.complex64)
            self.assertEqual(cap.take_audio_iq().size < got.size, True)
            cap.enable_audio(False)
            time.sleep(0.1)
            self.assertEqual(cap.take_audio_iq().size, 0)
            cap.center_freq_hz = 467.6e6
            cap.start_stream()
            self.assertEqual(cap.generation, gen + 1)
        finally:
            cap.stop_stream()


if __name__ == "__main__":
    unittest.main()
