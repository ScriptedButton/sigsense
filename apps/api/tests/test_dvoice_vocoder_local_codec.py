"""WP-F: the opt-in local codec path (adapter C file, build helper, auto-discovery, status).

Everything here runs against FIXTURES/fake_mbelib, a tiny stand-in with mbelib's
function signatures that synthesises a tone. No network, no downloaded source and
no patented code are involved. The real SigSense adapter (sigsense_mbe_adapter.c)
is compiled and exercised, so this tests the actual ABI implementation.
Auto-skips when no C compiler is available.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from sigsense_api.dvoice.events import VocoderId
from sigsense_api.dvoice.vocoder import local_codec, notices, registry
from sigsense_api.dvoice.vocoder.backends import external
from sigsense_api.dvoice.vocoder.backends.ambe_external import ExternalAmbePlus2
from sigsense_api.dvoice.vocoder.backends.imbe_external import ExternalImbe, ExternalImbe7100
from sigsense_api.dvoice.vocoder.base import FRAME_SAMPLES, VocoderFrame

API = Path(__file__).resolve().parents[1]
FAKE_SRC = API / "tests" / "fixtures" / "fake_mbelib"
SCRIPT = API / "scripts" / "build_mbe_adapter.py"
ENVS = ("SIGSENSE_MBE_LIB", "SIGSENSE_AMBE_LIB", "SIGSENSE_VOCODER_LIB", "SIGSENSE_CODEC_HOME")

_spec = importlib.util.spec_from_file_location("build_mbe_adapter", SCRIPT)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)

HAVE_CC = bool(shutil.which("gcc") or shutil.which("cc") or shutil.which("clang"))


def make_archive(directory: Path, *, extra=None, name="fake.tar.gz") -> tuple[Path, str]:
    """A tarball shaped like GitHub's (one top directory) holding the fake mbelib."""
    path = directory / name
    with tarfile.open(path, "w:gz") as tar:
        for src in sorted(FAKE_SRC.iterdir()):
            tar.add(src, arcname=f"fake-mbelib-1/{src.name}")
        for member, data in (extra or []):
            tar.addfile(member, io.BytesIO(data) if data is not None else None)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def install_fake(home: Path, work: Path) -> dict:
    archive, digest = make_archive(work)
    return helper.install(
        confirmed_by_user=True,
        home=home,
        archive=archive,
        expected_sha256=digest,
        sources=("mbelib.c",),
        log=lambda *_: None,
    )


class IsolatedEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sigsense-local-codec-"))
        self.home = self.tmp / "home"
        env = {k: v for k, v in os.environ.items() if k not in ENVS}
        env["SIGSENSE_CODEC_HOME"] = str(self.home)
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        registry.refresh()
        external.forget_adapters()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        registry.refresh()
        external.forget_adapters()


class ConfirmationGateTests(IsolatedEnv):
    def test_main_refuses_without_confirmation_and_creates_nothing(self):
        out = io.StringIO()
        with mock.patch.object(helper, "download", side_effect=AssertionError("must not download")), \
             mock.patch.object(helper.sys, "stdin", io.StringIO("")), \
             mock.patch.object(helper.sys, "stdout", out):
            self.assertEqual(helper.main([]), 2)
        self.assertIn("READ THIS BEFORE YOU CONTINUE", out.getvalue())  # the warning is always shown first
        self.assertFalse(self.home.exists())

    def test_install_refuses_when_not_confirmed(self):
        with mock.patch.object(helper, "download", side_effect=AssertionError("must not download")):
            with self.assertRaises(helper.BuildError):
                helper.install(confirmed_by_user=False, home=self.home, log=lambda *_: None)
        self.assertFalse(self.home.exists())

    def test_typed_phrase_confirms_only_on_a_terminal(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        args = helper.argparse.Namespace(i_understand=False)
        self.assertTrue(helper.confirmed(args, stdin=Tty(helper.CONFIRM_PHRASE + "\n"), stdout=io.StringIO()))
        self.assertFalse(helper.confirmed(args, stdin=Tty("yes\n"), stdout=io.StringIO()))
        self.assertFalse(helper.confirmed(args, stdin=io.StringIO(helper.CONFIRM_PHRASE + "\n"), stdout=io.StringIO()))

    def test_warning_text_says_the_important_things(self):
        for phrase in ("patents", "Check the law", "do not redistribute", "not legal advice", "does not decrypt"):
            self.assertIn(phrase.lower(), helper.WARNING.lower())


class DownloadAndArchiveSafetyTests(IsolatedEnv):
    def test_only_the_pinned_host_over_https_is_allowed(self):
        for url in ("http://codeload.github.com/x", "https://example.com/x.tar.gz", "https://evil.codeload.github.com.attacker.io/x"):
            with self.subTest(url=url), mock.patch.object(helper.urllib.request, "urlopen", side_effect=AssertionError("no network")):
                with self.assertRaises(helper.BuildError):
                    helper.download(url, self.tmp / "x")

    def test_pinned_constants_are_well_formed(self):
        self.assertRegex(helper.MBELIB_SHA256, r"^[0-9a-f]{64}$")
        self.assertRegex(helper.MBELIB_COMMIT, r"^[0-9a-f]{40}$")
        self.assertIn(helper.MBELIB_COMMIT, helper.MBELIB_URL)
        self.assertEqual(helper.urllib.request.urlparse(helper.MBELIB_URL).hostname, helper.ALLOWED_HOST)

    def test_tampered_archive_aborts_and_installs_nothing(self):
        archive, digest = make_archive(self.tmp)
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 0xFF  # flip a byte after the hash was taken
        archive.write_bytes(bytes(data))
        with self.assertRaises(helper.BuildError) as ctx:
            helper.install(confirmed_by_user=True, home=self.home, archive=archive, expected_sha256=digest,
                           sources=("mbelib.c",), log=lambda *_: None)
        self.assertIn("SHA-256", str(ctx.exception))
        self.assertFalse((helper.codec_dir(self.home) / helper.LIB_NAME).exists())
        self.assertIn("SHA-256", (helper.codec_dir(self.home) / "last_error.txt").read_text())
        self.assertEqual(list(helper.build_root(self.home).iterdir()), [])  # work directory cleaned up

    def test_wrong_pin_aborts(self):
        archive, _ = make_archive(self.tmp)
        with self.assertRaises(helper.BuildError):
            helper.install(confirmed_by_user=True, home=self.home, archive=archive, expected_sha256="0" * 64,
                           sources=("mbelib.c",), log=lambda *_: None)

    def test_unsafe_archive_members_are_rejected(self):
        cases = {}
        info = tarfile.TarInfo("../escape.txt"); info.size = 1
        cases["dotdot"] = (info, b"x")
        info = tarfile.TarInfo("/abs.txt"); info.size = 1
        cases["absolute"] = (info, b"x")
        info = tarfile.TarInfo("fake-mbelib-1/link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
        cases["symlink"] = (info, None)
        for label, member in cases.items():
            with self.subTest(label):
                archive, _ = make_archive(self.tmp, extra=[member], name=f"bad-{label}.tar.gz")
                dest = self.tmp / f"dest-{label}"
                dest.mkdir()
                with self.assertRaises(helper.BuildError):
                    helper.safe_extract(archive, dest)
                self.assertFalse((self.tmp / "escape.txt").exists())

    def test_archive_without_mbelib_header_is_rejected(self):
        path = self.tmp / "empty.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo("top/readme.txt"); info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        dest = self.tmp / "d"; dest.mkdir()
        with self.assertRaises(helper.BuildError):
            helper.safe_extract(path, dest)

    def test_downloaded_build_files_are_never_executed(self):
        # A hostile 'build script' in the archive must not run: only the named .c files are compiled.
        marker = self.tmp / "PWNED"
        script = f"#!/bin/sh\ntouch {marker}\n".encode()
        info = tarfile.TarInfo("fake-mbelib-1/configure"); info.size = len(script); info.mode = 0o755
        archive, digest = make_archive(self.tmp, extra=[(info, script)], name="withscript.tar.gz")
        if not HAVE_CC:
            self.skipTest("no C compiler")
        helper.install(confirmed_by_user=True, home=self.home, archive=archive, expected_sha256=digest,
                       sources=("mbelib.c",), log=lambda *_: None)
        self.assertFalse(marker.exists())


@unittest.skipUnless(HAVE_CC, "no C compiler")
class BuiltAdapterTests(IsolatedEnv):
    def setUp(self):
        super().setUp()
        self.manifest = install_fake(self.home, self.tmp)
        self.lib_path = helper.codec_dir(self.home) / helper.LIB_NAME
        self.lib = ctypes.CDLL(str(self.lib_path))
        L = self.lib
        L.sigsense_vocoder_abi_version.restype = ctypes.c_int
        L.sigsense_vocoder_create.restype = ctypes.c_void_p
        L.sigsense_vocoder_create.argtypes = [ctypes.c_int]
        L.sigsense_vocoder_destroy.argtypes = [ctypes.c_void_p]
        L.sigsense_vocoder_decode.restype = ctypes.c_int
        L.sigsense_vocoder_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int16), ctypes.c_int]
        L.sigsense_vocoder_conceal.restype = ctypes.c_int
        L.sigsense_vocoder_conceal.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int]
        L.sigsense_vocoder_describe.restype = ctypes.c_char_p

    # --- ABI conformance ---------------------------------------------------
    def test_abi_version_and_description(self):
        self.assertEqual(self.lib.sigsense_vocoder_abi_version(), 1)
        self.assertIn(b"SigSense mbelib adapter v1", self.lib.sigsense_vocoder_describe())

    def test_only_the_abi_symbols_are_exported(self):
        import subprocess
        nm = shutil.which("nm")
        if not nm:
            self.skipTest("no nm")
        out = subprocess.run([nm, "-D", "--defined-only", str(self.lib_path)], capture_output=True, text=True).stdout
        exported = {line.split()[-1] for line in out.splitlines() if line.split() and line.split()[1] in "TtDdBb"}
        exported = {s for s in exported if not s.startswith("_")}
        self.assertTrue(exported, "nothing exported")
        self.assertTrue(all(s.startswith("sigsense_vocoder_") for s in exported), exported)  # no mbe_* leakage

    def test_create_accepts_codecs_1_to_4_only(self):
        for codec in (0, 5, -1, 99):
            self.assertFalse(self.lib.sigsense_vocoder_create(codec), codec)
        for codec in (1, 2, 3, 4):
            handle = self.lib.sigsense_vocoder_create(codec)
            self.assertTrue(handle, codec)
            self.lib.sigsense_vocoder_destroy(handle)
        self.lib.sigsense_vocoder_destroy(None)  # free(NULL) is fine

    def test_error_codes_and_buffer_rules(self):
        handle = self.lib.sigsense_vocoder_create(1)
        out = (ctypes.c_int16 * FRAME_SAMPLES)()
        self.assertEqual(self.lib.sigsense_vocoder_decode(handle, bytes(88), 88, out, FRAME_SAMPLES), 160)
        self.assertEqual(self.lib.sigsense_vocoder_decode(handle, bytes(49), 49, out, FRAME_SAMPLES), -2)  # wrong size for IMBE
        self.assertEqual(self.lib.sigsense_vocoder_decode(handle, bytes(88), 88, out, 159), -3)            # buffer too small
        self.assertEqual(self.lib.sigsense_vocoder_decode(None, bytes(88), 88, out, FRAME_SAMPLES), -1)    # no handle
        self.assertEqual(self.lib.sigsense_vocoder_decode(handle, None, 88, out, FRAME_SAMPLES), -1)       # no bits
        self.assertEqual(self.lib.sigsense_vocoder_conceal(handle, out, 159), -3)
        self.assertEqual(self.lib.sigsense_vocoder_conceal(None, out, FRAME_SAMPLES), -1)
        self.lib.sigsense_vocoder_destroy(handle)
        amb = self.lib.sigsense_vocoder_create(4)
        self.assertEqual(self.lib.sigsense_vocoder_decode(amb, bytes(88), 88, out, FRAME_SAMPLES), -2)    # wrong size for AMBE
        self.assertEqual(self.lib.sigsense_vocoder_decode(amb, bytes(49), 49, out, FRAME_SAMPLES), 160)
        self.lib.sigsense_vocoder_destroy(amb)

    def test_output_never_overruns_the_buffer(self):
        handle = self.lib.sigsense_vocoder_create(1)
        guarded = (ctypes.c_int16 * (FRAME_SAMPLES + 8))(*([12345] * (FRAME_SAMPLES + 8)))
        self.assertEqual(self.lib.sigsense_vocoder_decode(handle, bytes([1, 0] * 44), 88, guarded, FRAME_SAMPLES), 160)
        self.assertEqual(list(guarded[FRAME_SAMPLES:]), [12345] * 8)
        self.lib.sigsense_vocoder_destroy(handle)

    def test_bits_are_masked_to_zero_or_one(self):
        a = self.lib.sigsense_vocoder_create(1); b = self.lib.sigsense_vocoder_create(1)
        oa = (ctypes.c_int16 * 160)(); ob = (ctypes.c_int16 * 160)()
        bits = bytes([1, 0, 1, 1, 0, 0] + [0] * 82)
        self.lib.sigsense_vocoder_decode(a, bits, 88, oa, 160)
        self.lib.sigsense_vocoder_decode(b, bytes(x | 0xFE for x in bits), 88, ob, 160)  # 0xFF/0xFE: low bit is the bit
        self.assertEqual(list(oa), list(ob))
        self.lib.sigsense_vocoder_destroy(a); self.lib.sigsense_vocoder_destroy(b)

    def test_state_is_per_handle_and_carries_across_frames(self):
        h1 = self.lib.sigsense_vocoder_create(1); h2 = self.lib.sigsense_vocoder_create(1)
        frame = bytes([1, 0, 1, 0, 1, 0] + [0] * 82)
        seq = lambda h: [list((lambda o: (self.lib.sigsense_vocoder_decode(h, frame, 88, o, 160), list(o))[1])((ctypes.c_int16 * 160)())) for _ in range(3)]
        a = seq(h1); b = seq(h2)
        self.assertEqual(a, b)               # same input, independent handles, same output
        self.assertNotEqual(a[0], a[1])      # but phase advances from frame to frame inside one handle
        self.lib.sigsense_vocoder_destroy(h1); self.lib.sigsense_vocoder_destroy(h2)

    def test_conceal_repeats_then_mutes(self):
        h = self.lib.sigsense_vocoder_create(4)
        out = (ctypes.c_int16 * 160)()
        self.lib.sigsense_vocoder_decode(h, bytes([1, 0, 1, 0, 1, 0] + [0] * 43), 49, out, 160)
        peaks = []
        for _ in range(6):
            self.assertEqual(self.lib.sigsense_vocoder_conceal(h, out, 160), 160)
            peaks.append(max(abs(v) for v in out))
        self.assertGreater(peaks[0], 0)      # first lost frame: speech repeated
        self.assertEqual(peaks[-1], 0)       # after several lost frames in a row: muted
        self.lib.sigsense_vocoder_destroy(h)

    def test_imbe_7100_layout_differs_from_7200(self):
        h0 = self.lib.sigsense_vocoder_create(1); h1 = self.lib.sigsense_vocoder_create(2)
        o0 = (ctypes.c_int16 * 160)(); o1 = (ctypes.c_int16 * 160)()
        bits = bytes([1, 0, 1, 1, 0, 1] + [0] * 82)
        self.lib.sigsense_vocoder_decode(h0, bits, 88, o0, 160)
        self.lib.sigsense_vocoder_decode(h1, bits, 88, o1, 160)
        self.assertNotEqual(list(o0), list(o1))  # the 7100 path reorders bits first
        self.lib.sigsense_vocoder_destroy(h0); self.lib.sigsense_vocoder_destroy(h1)

    def test_threads_with_their_own_handles_match_a_sequential_run(self):
        frames = [bytes(((i * 7 + j * 3) % 2) for j in range(88)) for i in range(60)]

        def run(codec=1):
            h = self.lib.sigsense_vocoder_create(codec)
            out = (ctypes.c_int16 * 160)()
            result = []
            for f in frames:
                self.assertEqual(self.lib.sigsense_vocoder_decode(h, f, 88, out, 160), 160)
                result.append(hashlib.sha1(bytes(out)).hexdigest())
            self.lib.sigsense_vocoder_destroy(h)
            return result

        expected = run()
        results, errors = [], []

        def worker():
            try:
                results.append(run())
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r == expected for r in results))

    # --- manifest, status, discovery ------------------------------------
    def test_manifest_records_provenance(self):
        data = json.loads((helper.codec_dir(self.home) / "manifest.json").read_text())
        self.assertEqual(data["abi_version"], 1)
        self.assertEqual(data["library_sha256"], hashlib.sha256(self.lib_path.read_bytes()).hexdigest())
        self.assertIn("-Wl,-z,relro,-z,now", data["command"])
        self.assertTrue(any("redistribute" in note for note in data["notes"]))
        self.assertIn("mbelib", data)

    def test_auto_discovery_without_any_environment_variable(self):
        for cls, codec_name in ((ExternalImbe, "IMBE"), (ExternalImbe7100, "IMBE"), (ExternalAmbePlus2, "AMBE")):
            avail = cls.availability()
            self.assertTrue(avail.available, (cls, avail.reason))
            self.assertIn("default install location", avail.reason)

    def test_decode_through_the_real_loader(self):
        voc = ExternalImbe()
        bits = np.array([1, 0, 1, 0, 1, 0] + [0] * 82, np.uint8)
        a = voc.decode(VocoderFrame(VocoderId.IMBE_7200X4400, bits))
        self.assertEqual(a.dtype, np.int16)
        self.assertEqual(a.size, FRAME_SAMPLES)
        self.assertGreater(int(np.abs(a).max()), 1000)
        lost = voc.decode(VocoderFrame(VocoderId.IMBE_7200X4400, bits, erased=True))
        self.assertEqual(lost.size, FRAME_SAMPLES)
        voc.close()

    def test_environment_variable_beats_the_default_location(self):
        other = self.tmp / "other"
        other.mkdir()
        # a path that is not a library: the env var wins, so availability fails instead of falling back
        bogus = other / "nope.so"
        bogus.write_bytes(b"not a library")
        os.environ["SIGSENSE_MBE_LIB"] = str(bogus)
        registry.refresh(); external.forget_adapters()
        avail = ExternalImbe.availability()
        self.assertFalse(avail.available)
        self.assertIn(str(bogus), avail.reason)

    def test_status_reports_what_the_ui_needs(self):
        status = local_codec.local_codec_status()
        self.assertTrue(status["installed"])
        self.assertEqual(status["source"], "default install location")
        self.assertEqual(status["path"], str(self.lib_path))
        self.assertTrue(all(status["codecs"].values()))
        self.assertEqual(set(status["protocols_with_audio"]), {"P25 Phase 1", "DMR", "NXDN", "YSF", "P25 Phase 2", "D-STAR"})
        self.assertIn("--i-understand", status["build_command"])
        self.assertIn("patent", status["warning"].lower())
        self.assertEqual(status["manifest"]["abi_version"], 1)
        json.dumps(status)  # JSON-serialisable

    def test_licence_notices_include_the_local_codec_block(self):
        data = notices.licence_notices()
        self.assertTrue(data["local_codec"]["installed"])
        names = {b["name"] for b in data["backends"]}
        self.assertIn("ExternalImbe7100", names)
        self.assertFalse(data["policy"]["ships_ambe"])

    def test_uninstall_removes_only_what_it_created(self):
        keep = helper.codec_dir(self.home) / "mine.txt"
        keep.write_text("user file")
        removed = helper.uninstall(self.home, log=lambda *_: None)
        self.assertTrue(any(p.endswith(helper.LIB_NAME) for p in removed))
        self.assertFalse(self.lib_path.exists())
        self.assertFalse((helper.codec_dir(self.home) / "manifest.json").exists())
        self.assertTrue(keep.exists())                       # not ours, not touched
        self.assertFalse(helper.build_root(self.home).exists())
        registry.refresh(); external.forget_adapters()
        self.assertFalse(local_codec.local_codec_status()["installed"])
        self.assertFalse(ExternalImbe.availability().available)

    def test_status_command_works_after_install(self):
        info = helper.status(self.home)
        self.assertTrue(info["installed"] and info["works"])


class NotInstalledTests(IsolatedEnv):
    def test_status_when_nothing_is_installed(self):
        status = local_codec.local_codec_status()
        self.assertFalse(status["installed"])
        self.assertIsNone(status["path"])
        self.assertEqual(status["protocols_with_audio"], [])
        self.assertIn("build_mbe_adapter.py", status["build_command"])

    def test_unavailable_reason_points_at_the_helper(self):
        reason = ExternalImbe.availability().reason
        self.assertIn("build_mbe_adapter.py", reason)

    def test_uninstall_when_nothing_installed_is_harmless(self):
        self.assertEqual(helper.uninstall(self.home, log=lambda *_: None), [])

    def test_helper_and_backend_agree_on_the_install_path(self):
        self.assertEqual(helper.codec_dir(), local_codec.codec_dir())
        self.assertEqual(helper.codec_dir() / helper.LIB_NAME, local_codec.default_library_path())
        self.assertEqual(helper.build_root(), local_codec.build_dir())

    def test_last_error_from_a_failed_build_is_surfaced(self):
        helper.write_last_error(self.home, "compiling failed: boom")
        self.assertIn("boom", local_codec.local_codec_status()["last_error"])


@unittest.skipUnless(HAVE_CC, "no C compiler")
class BuildFailureTests(IsolatedEnv):
    def test_missing_expected_source_file_is_a_clear_error_and_leaves_nothing(self):
        archive, digest = make_archive(self.tmp)
        with self.assertRaises(helper.BuildError) as ctx:
            helper.install(confirmed_by_user=True, home=self.home, archive=archive, expected_sha256=digest,
                           sources=("mbelib.c", "does_not_exist.c"), log=lambda *_: None)
        self.assertIn("does_not_exist.c", str(ctx.exception))
        self.assertFalse((helper.codec_dir(self.home) / helper.LIB_NAME).exists())
        self.assertIn("does_not_exist.c", local_codec.local_codec_status()["last_error"])

    def test_failed_reinstall_keeps_the_previous_working_library(self):
        install_fake(self.home, self.tmp)
        before = (helper.codec_dir(self.home) / helper.LIB_NAME).read_bytes()
        archive, _ = make_archive(self.tmp, name="again.tar.gz")
        with self.assertRaises(helper.BuildError):
            helper.install(confirmed_by_user=True, home=self.home, archive=archive, expected_sha256="1" * 64,
                           sources=("mbelib.c",), log=lambda *_: None)
        self.assertEqual((helper.codec_dir(self.home) / helper.LIB_NAME).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
