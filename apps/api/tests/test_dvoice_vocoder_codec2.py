"""WP-F: Codec2 backend against a real libcodec2 (auto-skips when none is installed)."""

from __future__ import annotations

import ctypes
import os
import unittest
from unittest import mock

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.vocoder.backends import codec2
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, VocoderFrame

LIB, LIB_PATH, _ = codec2.load_library()
need_lib = unittest.skipIf(LIB is None, "libcodec2 not installed")


def encode_frames(mode: int, speech: np.ndarray) -> list[np.ndarray]:
    """Encode with the library itself (test-only helper) -> list of 0/1 bit arrays, one per codec frame."""
    lib = ctypes.CDLL(LIB_PATH)
    lib.codec2_create.restype = ctypes.c_void_p
    lib.codec2_create.argtypes = [ctypes.c_int]
    lib.codec2_samples_per_frame.argtypes = [ctypes.c_void_p]
    lib.codec2_bits_per_frame.argtypes = [ctypes.c_void_p]
    lib.codec2_encode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_short)]
    lib.codec2_destroy.argtypes = [ctypes.c_void_p]
    c2 = lib.codec2_create(mode)
    spf, bpf = lib.codec2_samples_per_frame(c2), lib.codec2_bits_per_frame(c2)
    frames = []
    for i in range(0, speech.size - spf + 1, spf):
        chunk = (ctypes.c_short * spf)(*speech[i : i + spf].astype(np.int16))
        out = (ctypes.c_ubyte * ((bpf + 7) // 8))()
        lib.codec2_encode(c2, out, chunk)
        frames.append(np.unpackbits(np.frombuffer(out, dtype=np.uint8))[:bpf])
    lib.codec2_destroy(c2)
    return frames


def voiced(seconds: float, f0: float = 140.0) -> np.ndarray:
    """A harmonic 'vowel': 140 Hz fundamental with a falling spectral tilt."""
    t = np.arange(int(8000 * seconds)) / 8000
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 20))
    return (x / np.abs(x).max() * 9000).astype(np.int16)


def pitch_hz(pcm: np.ndarray) -> float:
    """Fundamental by autocorrelation (the spectral peak can sit on a harmonic, which is fine for a codec)."""
    x = pcm.astype(float) - pcm.mean()
    corr = np.correlate(x, x[: x.size // 2], mode="valid")  # lag 0 .. n/2
    lags = np.arange(30, 120)  # 67..267 Hz
    return 8000.0 / float(lags[np.argmax(corr[lags])])


@need_lib
class Codec2Tests(unittest.TestCase):
    def setUp(self):
        registry.refresh()

    def test_registered_available_with_licence_note(self):
        caps = registry.capabilities()["codec2_3200"]
        self.assertTrue(caps["available"], caps)
        self.assertIn("LGPL", caps["licence_note"])
        self.assertIsInstance(registry.get_vocoder(VocoderId.CODEC2_3200), codec2.Codec2Vocoder)

    def test_decodes_a_voiced_signal_at_the_right_pitch(self):
        bits = encode_frames(codec2.CODEC2_MODE_3200, voiced(1.0))
        self.assertEqual(len(bits), 50)
        self.assertEqual(bits[0].size, 64)
        v = codec2.Codec2Vocoder()
        pcm = np.concatenate([v.decode(VocoderFrame(VocoderId.CODEC2_3200, b)) for b in bits[5:]])
        self.assertEqual(pcm.dtype, np.int16)
        self.assertEqual(pcm.size, 45 * FRAME_SAMPLES)
        self.assertGreater(np.sqrt(np.mean(pcm.astype(float) ** 2)), 800)  # audible
        self.assertAlmostEqual(pitch_hz(pcm), 140.0, delta=14.0)  # within 10%
        v.close()

    def test_same_bits_same_audio_and_instances_are_isolated(self):
        bits = encode_frames(codec2.CODEC2_MODE_3200, voiced(0.6))
        a, b = codec2.Codec2Vocoder(), codec2.Codec2Vocoder()
        # Warm 'a' with unrelated audio first; once both have seen the same sequence from a reset, outputs match.
        for fr in bits[:5]:
            a.decode(VocoderFrame(VocoderId.CODEC2_3200, fr))
        a.reset()
        out_a = [a.decode(VocoderFrame(VocoderId.CODEC2_3200, fr)) for fr in bits]
        out_b = [b.decode(VocoderFrame(VocoderId.CODEC2_3200, fr)) for fr in bits]
        # libcodec2 draws its unvoiced phase from a process-global generator, so two instances agree to
        # within a few LSB rather than bit for bit: require > 30 dB agreement, not equality.
        x, y = np.concatenate(out_a).astype(float), np.concatenate(out_b).astype(float)
        snr = 10 * np.log10(np.sum(x**2) / max(np.sum((x - y) ** 2), 1e-9))
        self.assertGreater(snr, 30.0)
        a.close()
        b.close()

    def test_bad_or_lost_frames_are_silence_not_errors(self):
        v = codec2.Codec2Vocoder()
        ok = encode_frames(codec2.CODEC2_MODE_3200, voiced(0.1))[0]
        for frame in (
            VocoderFrame(VocoderId.CODEC2_3200, np.zeros(49, np.uint8)),  # wrong size
            VocoderFrame(VocoderId.CODEC2_3200, np.zeros(128, np.uint8)),  # a whole M17 payload by mistake
            VocoderFrame(VocoderId.CODEC2_3200, ok, erased=True),
        ):
            pcm = v.decode(frame)
            self.assertEqual(pcm.shape, (FRAME_SAMPLES,))
            self.assertFalse(pcm.any())
        v.close()

    def test_close_is_idempotent_and_silences_afterwards(self):
        v = codec2.Codec2Vocoder()
        ok = encode_frames(codec2.CODEC2_MODE_3200, voiced(0.1))[0]
        v.close()
        v.close()
        self.assertFalse(v.decode(VocoderFrame(VocoderId.CODEC2_3200, ok)).any())

    def test_1600_mode_returns_two_frames_of_audio(self):
        bits = encode_frames(codec2.CODEC2_MODE_1600, voiced(0.8))
        v = codec2.Codec2Vocoder1600()
        self.assertEqual(v.samples_per_decode, 2 * FRAME_SAMPLES)
        pcm = v.decode(VocoderFrame(VocoderId.CODEC2_1600, bits[3]))
        self.assertEqual(pcm.shape, (320,))
        v.close()

    def test_explicit_missing_path_is_reported_not_silently_replaced(self):
        with mock.patch.dict(os.environ, {codec2.ENV_LIB: "/nonexistent/libcodec2.so"}):
            avail = codec2.Codec2Vocoder.availability()
            self.assertFalse(avail.available)
            self.assertIn("/nonexistent/libcodec2.so", avail.reason)
        codec2.load_library()  # re-cache for the real environment


class Codec2WithoutLibraryTests(unittest.TestCase):
    def test_absent_library_degrades_cleanly(self):
        with mock.patch.dict(os.environ, {codec2.ENV_LIB: "/nonexistent/libcodec2.so"}):
            registry.refresh()
            avail = codec2.Codec2Vocoder.availability()
            self.assertFalse(avail.available)
            self.assertIn("LGPL", avail.licence_note)
            from sigsense_api.dvoice.vocoder.base import UnavailableVocoder

            self.assertIsInstance(registry.get_vocoder(VocoderId.CODEC2_3200), UnavailableVocoder)
        registry.refresh()
        codec2.load_library()


if __name__ == "__main__":
    unittest.main()
