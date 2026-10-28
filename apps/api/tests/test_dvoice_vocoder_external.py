"""WP-F: the user-installed-adapter ABI, tested with a tiny adapter compiled here.

The adapter below is a TEST FAKE: it turns frame bits into a tone. It is not a
codec and contains no vocoder code of any kind. It exists to prove the loader,
the ABI checks, per-handle state, error paths and thread safety.
Auto-skips when no C compiler is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder import registry
from sigsense_api.dvoice.vocoder.backends import external
from sigsense_api.dvoice.vocoder.backends.ambe_external import ExternalAmbePlus2
from sigsense_api.dvoice.vocoder.backends.imbe_external import ExternalImbe
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, UnavailableVocoder, VocoderFrame

ADAPTER_C = r"""
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#ifndef ABI
#define ABI 1
#endif
typedef struct { int codec; double phase; } H;
int sigsense_vocoder_abi_version(void) { return ABI; }
void* sigsense_vocoder_create(int codec) {
  if (codec == 2 || codec == 3) return 0;          /* pretend IMBE-7100 and AMBE are unsupported */
  H* h = (H*)calloc(1, sizeof(H)); h->codec = codec; return h;
}
void sigsense_vocoder_destroy(void* h) { free(h); }
int sigsense_vocoder_decode(void* hv, const uint8_t* bits, int n, int16_t* pcm, int cap) {
  H* h = (H*)hv; if (n <= 0 || cap < 160) return -1;
  if (n >= 2 && bits[0] == 1 && bits[1] == 1 && bits[2] == 1) return -2;   /* injected failure */
  int k = 0; for (int i = 0; i < n; i++) k += bits[i];
  double w = 2.0 * M_PI * (300.0 + 20.0 * k) / 8000.0;
  for (int i = 0; i < 160; i++) { pcm[i] = (int16_t)(6000.0 * sin(h->phase)); h->phase += w; }
  h->phase = fmod(h->phase, 2.0 * M_PI);
  return (n == 5) ? 100 : 160;                                  /* n==5: short write */
}
#ifndef NO_CONCEAL
int sigsense_vocoder_conceal(void* hv, int16_t* pcm, int cap) {
  (void)hv; if (cap < 160) return -1; for (int i = 0; i < 160; i++) pcm[i] = 111; return 160;
}
#endif
const char* sigsense_vocoder_describe(void) { return "SigSense test adapter (not a codec)"; }
"""

CC = shutil.which("cc") or shutil.which("gcc")
ENVS = ("SIGSENSE_MBE_LIB", "SIGSENSE_AMBE_LIB", "SIGSENSE_VOCODER_LIB")


def build(tmp: str, name: str, *flags: str) -> str:
    src = os.path.join(tmp, "adapter.c")
    with open(src, "w") as fh:
        fh.write(ADAPTER_C)
    out = os.path.join(tmp, name)
    subprocess.run([CC, "-shared", "-fPIC", "-O1", *flags, src, "-o", out, "-lm"], check=True, capture_output=True)
    return out


@unittest.skipIf(CC is None, "no C compiler")
class ExternalAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sigsense-adapter-")
        cls.good = build(cls.tmp, "good.so")
        cls.badabi = build(cls.tmp, "badabi.so", "-DABI=2")
        cls.noconceal = build(cls.tmp, "noconceal.so", "-DNO_CONCEAL")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="sigsense-home-")
        env = {k: v for k, v in os.environ.items() if k not in ENVS}
        env["SIGSENSE_CODEC_HOME"] = self._home  # never pick up a library the user really built
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        registry.refresh()
        external.forget_adapters()

    def tearDown(self):
        shutil.rmtree(self._home, ignore_errors=True)
        self._env.stop()
        registry.refresh()
        external.forget_adapters()

    def use(self, path: str, var: str = "SIGSENSE_MBE_LIB"):
        os.environ[var] = path
        registry.refresh()
        external.forget_adapters()

    def frame(self, bits=None, **kw):
        return VocoderFrame(VocoderId.IMBE_7200X4400, np.array(bits if bits is not None else [0] * 88, np.uint8), **kw)

    def test_not_configured_means_unavailable_with_instructions(self):
        avail = ExternalImbe.availability()
        self.assertFalse(avail.available)
        self.assertIn("SIGSENSE_MBE_LIB", avail.reason)
        self.assertIn("ships none", avail.licence_note)

    def test_good_adapter_is_available_and_decodes(self):
        self.use(self.good)
        avail = ExternalImbe.availability()
        self.assertTrue(avail.available, avail)
        self.assertIn("test adapter", avail.reason)
        v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        self.assertIsInstance(v, ExternalImbe)
        pcm = v.decode(self.frame([1, 0, 1, 1] + [0] * 84))
        self.assertEqual((pcm.dtype, pcm.shape), (np.dtype("int16"), (FRAME_SAMPLES,)))
        self.assertGreater(np.abs(pcm).max(), 3000)
        v.close()

    def test_unsupported_codec_is_reported_per_codec(self):
        self.use(self.good, "SIGSENSE_AMBE_LIB")
        self.assertTrue(ExternalAmbePlus2.availability().available)  # codec 4 supported
        from sigsense_api.dvoice.vocoder.backends.ambe_external import ExternalAmbe

        avail = ExternalAmbe.availability()  # codec 3 refused by the adapter
        self.assertFalse(avail.available)
        self.assertIn("does not support", avail.reason)

    def test_wrong_abi_version_is_rejected(self):
        self.use(self.badabi)
        avail = ExternalImbe.availability()
        self.assertFalse(avail.available)
        self.assertIn("ABI version 2", avail.reason)

    def test_missing_and_non_library_paths(self):
        self.use("/nonexistent/adapter.so")
        self.assertFalse(ExternalImbe.availability().available)
        bogus = os.path.join(self.tmp, "not-a-library.so")
        with open(bogus, "w") as fh:
            fh.write("this is not an ELF file")
        self.use(bogus)
        avail = ExternalImbe.availability()
        self.assertFalse(avail.available)
        self.assertIn("cannot use", avail.reason)
        self.assertIsInstance(registry.get_vocoder(VocoderId.IMBE_7200X4400), UnavailableVocoder)

    def test_generic_variable_serves_both_codecs(self):
        self.use(self.good, "SIGSENSE_VOCODER_LIB")
        self.assertTrue(ExternalImbe.availability().available)
        self.assertTrue(ExternalAmbePlus2.availability().available)

    def test_adapter_error_return_is_silence_not_an_exception(self):
        self.use(self.good)
        v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        pcm = v.decode(self.frame([1, 1, 1] + [0] * 85))  # adapter returns -2
        self.assertFalse(pcm.any())
        v.close()

    def test_short_write_leaves_silence_not_stale_memory(self):
        self.use(self.good)
        v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        pcm = v.decode(self.frame([1, 0, 1, 0, 1]))  # adapter writes only 100 samples
        self.assertGreater(np.abs(pcm[:100]).max(), 0)
        self.assertFalse(pcm[100:].any())
        v.close()

    def test_adapter_concealment_used_when_present_else_silence(self):
        self.use(self.good)
        v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        np.testing.assert_array_equal(v.decode(self.frame(erased=True)), np.full(FRAME_SAMPLES, 111, np.int16))
        v.close()
        self.use(self.noconceal)
        v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        self.assertFalse(v.decode(self.frame(erased=True)).any())
        v.close()

    def test_handles_keep_independent_state(self):
        self.use(self.good)
        a = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        b = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        fr = self.frame([1, 0, 1, 1] + [0] * 84)
        a1 = a.decode(fr)
        a2 = a.decode(fr)  # phase advanced: differs from the first frame
        b1 = b.decode(fr)  # a fresh handle starts from the same phase as a's first frame
        self.assertFalse(np.array_equal(a1, a2))
        np.testing.assert_array_equal(a1, b1)
        a.reset()
        np.testing.assert_array_equal(a.decode(fr), a1)
        a.close()
        b.close()

    def test_close_twice_and_use_after_close_are_safe(self):
        self.use(self.good)
        v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
        v.close()
        v.close()
        self.assertFalse(v.decode(self.frame()).any())

    def test_threads_on_separate_handles(self):
        self.use(self.good)
        errors = []

        def work(seed):
            try:
                v = registry.get_vocoder(VocoderId.IMBE_7200X4400)
                rng = np.random.default_rng(seed)
                for _ in range(300):
                    pcm = v.decode(self.frame((rng.random(88) < 0.3).astype(np.uint8)))
                    assert pcm.shape == (FRAME_SAMPLES,)
                v.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
