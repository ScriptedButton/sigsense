#!/usr/bin/env python3
"""Build SigSense's local IMBE/AMBE codec library on YOUR machine (opt-in).

SigSense ships no voice codec. This helper, which you run yourself, downloads
the open "mbelib" source from its public repository at one pinned version,
compiles it together with SigSense's own adapter (apps/api/sigsense_api/dvoice/
vocoder/adapter/sigsense_mbe_adapter.c) into a private library, and installs
that into your data directory where the API finds it automatically.

    python3 apps/api/scripts/build_mbe_adapter.py --i-understand     # build + install
    python3 apps/api/scripts/build_mbe_adapter.py --status
    python3 apps/api/scripts/build_mbe_adapter.py --uninstall

Nothing is downloaded or built until you have read the warning and confirmed
(`--i-understand`, or typing the confirmation phrase when run interactively).
The download is only ever the one pinned URL below, and its SHA-256 is checked
before a single byte is unpacked. The downloaded project's own build files
(CMake, scripts, tests) are never executed: only its C source files are
compiled, with a fixed command line, as your normal user.

Standard library only.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

# ---- the one upstream we are willing to fetch -------------------------------
MBELIB_REPO = "https://github.com/szechyjs/mbelib"
MBELIB_COMMIT = "9a04ed5c78176a9965f3d43f7aa1b1f5330e771f"  # master at the time of writing; v1.3.0 + later fixes
MBELIB_URL = f"https://codeload.github.com/szechyjs/mbelib/tar.gz/{MBELIB_COMMIT}"
MBELIB_SHA256 = "c7d6ebbbf3ca44bc10ee0755dede7f74fd65c31d1568d4174f29a68ae2f92278"
ALLOWED_HOST = "codeload.github.com"
# Only these files are compiled. Everything else in the archive is ignored.
MBELIB_SOURCES = (
    "mbelib.c",
    "imbe7200x4400.c",
    "imbe7100x4400.c",
    "ambe3600x2400.c",
    "ambe3600x2450.c",
    "ecc.c",
)

CONFIRM_FLAG = "--i-understand"
CONFIRM_PHRASE = "I UNDERSTAND"
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 40 * 1024 * 1024
MAX_MEMBERS = 2000
LIB_NAME = "libsigsense_mbe.so"
ADAPTER_REL = Path("sigsense_api/dvoice/vocoder/adapter/sigsense_mbe_adapter.c")
ABI_VERSION = 1

WARNING = f"""
============================================================================
 READ THIS BEFORE YOU CONTINUE
============================================================================
 This builds a voice codec for P25 Phase 1 (IMBE) and for DMR, NXDN, YSF,
 D-STAR and P25 Phase 2 (AMBE / AMBE+2) so SigSense can play decoded calls.

 * These codecs may be covered by patents. Check the law where you live
   before you download, compile or use them.
 * The source comes from a separate open project ({MBELIB_REPO}),
   fetched at commit {MBELIB_COMMIT[:12]}. It has its own licence and
   copyright notice; the names MBE, IMBE and AMBE belong to their owners.
 * SigSense does not ship or vouch for it. The library built here is for your
   own use on this machine: do not redistribute it.
 * Encrypted calls stay encrypted. SigSense does not decrypt anything.
 * This is not legal advice. You are responsible for how you use it.

 What will happen: it downloads one file from {ALLOWED_HOST}, checks its
 SHA-256, compiles it locally, and installs {LIB_NAME} under your data directory.
 Remove it any time with --uninstall.
============================================================================
"""


class BuildError(Exception):
    """A failure the user should read, not a bug."""


# ---- locations (same rule as sigsense_api.dvoice.vocoder.local_codec) --------
def data_home() -> Path:
    override = os.environ.get("SIGSENSE_CODEC_HOME", "").strip()
    return Path(override).expanduser() if override else Path.home() / ".local" / "share" / "sigsense"


def codec_dir(home: Optional[Path] = None) -> Path:
    return (home or data_home()) / "codecs"


def build_root(home: Optional[Path] = None) -> Path:
    return (home or data_home()) / "codec-build"


# ---- confirmation ------------------------------------------------------------
def confirmed(args: argparse.Namespace, stdin=None, stdout=None) -> bool:
    """True only if the user explicitly agreed. Never assumes."""
    if args.i_understand:
        return True
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if not stdin.isatty():
        return False
    stdout.write(f'Type "{CONFIRM_PHRASE}" to continue, anything else to cancel: ')
    stdout.flush()
    return stdin.readline().strip() == CONFIRM_PHRASE


# ---- download and verification ----------------------------------------------
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if actual != expected.lower():
        raise BuildError(
            "the downloaded file does not match the pinned SHA-256, so it was NOT unpacked.\n"
            f"  expected {expected}\n  got      {actual}\n"
            "Either the download was corrupted or tampered with, or upstream re-generated the archive. "
            "Do not work around this by editing the hash unless you have checked the new file yourself."
        )


def download(url: str, dest: Path, timeout: float = 60.0) -> None:
    """Fetch the pinned URL (and nothing else) to `dest`, with a size cap."""
    host = urllib.request.urlparse(url).hostname
    if urllib.request.urlparse(url).scheme != "https" or host != ALLOWED_HOST:
        raise BuildError(f"refusing to download from {url}: only https://{ALLOWED_HOST}/ is allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "sigsense-build-mbe-adapter/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, "wb") as out:
            total = 0
            while True:
                block = response.read(1 << 16)
                if not block:
                    break
                total += len(block)
                if total > MAX_ARCHIVE_BYTES:
                    raise BuildError("download is larger than expected; stopped")
                out.write(block)
    except (urllib.error.URLError, OSError) as exc:
        raise BuildError(f"could not download {url}: {exc}") from exc


def safe_extract(archive: Path, dest: Path) -> Path:
    """Unpack into `dest` (an empty directory we just made), refusing anything odd.

    Returns the directory that contains mbelib.h. Links, devices, absolute paths
    and '..' components are rejected; ownership and modes are not applied.
    """
    if any(dest.iterdir()):
        raise BuildError(f"{dest} is not empty")
    root = dest.resolve()
    total = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            if len(members) > MAX_MEMBERS:
                raise BuildError("archive has too many entries")
            for member in members:
                name = member.name
                if name.startswith("/") or ".." in Path(name).parts:
                    raise BuildError(f"unsafe path in archive: {name}")
                if not (member.isfile() or member.isdir()):
                    # symlinks, hardlinks, devices, fifos: none are needed for C sources
                    if member.name.endswith("pax_global_header") or member.type == tarfile.XHDTYPE or member.type == tarfile.XGLTYPE:
                        continue
                    raise BuildError(f"archive contains a non-file entry: {name}")
                target = (dest / name).resolve()
                if root not in target.parents and target != root:
                    raise BuildError(f"path escapes the build directory: {name}")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise BuildError("archive unpacks to more than expected")
            for member in members:
                if member.isdir():
                    (dest / member.name).mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target = dest / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
    except (tarfile.TarError, OSError) as exc:
        raise BuildError(f"could not read the archive: {exc}") from exc
    headers = sorted(dest.rglob("mbelib.h"), key=lambda p: len(p.parts))
    if not headers:
        raise BuildError("the archive does not contain mbelib.h; it is not the expected source")
    return headers[0].parent


# ---- compile ----------------------------------------------------------------
def find_compiler() -> str:
    for name in ("gcc", "cc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    raise BuildError("no C compiler found. Install one (for example: sudo apt install build-essential) and run again.")


def compile_library(
    src_dir: Path,
    adapter_c: Path,
    out_path: Path,
    *,
    sources: Sequence[str] = MBELIB_SOURCES,
) -> tuple[list[str], str]:
    """Compile the adapter plus mbelib's C files into one shared library.

    Fixed command line. Only the files named in `sources` are compiled; the
    project's build system is never run. A linker version script exports only
    the sigsense_vocoder_* ABI so mbelib's own symbols cannot clash with other
    libraries in the process. Returns (command, compiler version line).
    """
    missing = [s for s in sources if not (src_dir / s).is_file()]
    if missing:
        raise BuildError(f"expected source files are missing from the archive: {', '.join(missing)}")
    if not adapter_c.is_file():
        raise BuildError(f"SigSense adapter source not found at {adapter_c}")
    compiler = find_compiler()
    version_script = out_path.parent / "exports.map"
    version_script.write_text("{ global: sigsense_vocoder_*; local: *; };\n")
    command = [
        compiler,
        "-O2",
        "-fPIC",
        "-shared",
        "-std=gnu99",
        "-fstack-protector-strong",
        "-D_FORTIFY_SOURCE=2",
        "-Wl,-z,relro,-z,now",
        f"-Wl,--version-script={version_script}",
        f"-I{src_dir}",
        str(adapter_c),
        *[str(src_dir / s) for s in sources],
        "-lm",
        "-o",
        str(out_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False, cwd=out_path.parent)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BuildError(f"the compiler could not run: {exc}") from exc
    if result.returncode != 0 or not out_path.is_file():
        raise BuildError("compiling failed:\n" + (result.stderr or result.stdout)[-1500:])
    ver = subprocess.run([compiler, "--version"], capture_output=True, text=True, check=False).stdout.splitlines()
    return command, (ver[0] if ver else compiler)


def self_test(library: Path) -> str:
    """Load the built library and prove it speaks ABI v1 and decodes every codec without crashing."""
    try:
        lib = ctypes.CDLL(str(library))
        lib.sigsense_vocoder_abi_version.restype = ctypes.c_int
        lib.sigsense_vocoder_create.restype = ctypes.c_void_p
        lib.sigsense_vocoder_create.argtypes = [ctypes.c_int]
        lib.sigsense_vocoder_destroy.argtypes = [ctypes.c_void_p]
        lib.sigsense_vocoder_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
        ]
        lib.sigsense_vocoder_decode.restype = ctypes.c_int
        lib.sigsense_vocoder_describe.restype = ctypes.c_char_p
    except (OSError, AttributeError) as exc:
        raise BuildError(f"the built library cannot be loaded: {exc}") from exc
    if lib.sigsense_vocoder_abi_version() != ABI_VERSION:
        raise BuildError("the built library reports an unexpected ABI version")
    for codec, nbits in ((1, 88), (2, 88), (3, 49), (4, 49)):
        handle = lib.sigsense_vocoder_create(codec)
        if not handle:
            raise BuildError(f"the built library does not support codec {codec}")
        out = (ctypes.c_int16 * 160)()
        written = lib.sigsense_vocoder_decode(handle, bytes(nbits), nbits, out, 160)
        lib.sigsense_vocoder_destroy(handle)
        if written != 160:
            raise BuildError(f"self-test decode of codec {codec} returned {written}, expected 160")
    return (lib.sigsense_vocoder_describe() or b"").decode("utf-8", "replace")


def read_header_version(src_dir: Path) -> str:
    try:
        for line in (src_dir / "mbelib.h").read_text(errors="replace").splitlines():
            if "MBELIB_VERSION" in line and '"' in line:
                return line.split('"')[1]
    except OSError:
        pass
    return "unknown"


def licence_summary(src_dir: Path) -> dict:
    """What the upstream COPYRIGHT file says, recorded (not interpreted) for the manifest."""
    for name in ("COPYRIGHT", "LICENSE", "LICENSE.md", "COPYING"):
        path = src_dir / name
        if path.is_file():
            text = path.read_text(errors="replace")
            isc_like = "Permission to use, copy, modify, and/or distribute" in text
            return {
                "file": name,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "looks_like": "ISC (permissive: keep the copyright notice)" if isc_like else "unrecognised: read the file",
            }
    return {"file": None, "sha256": None, "looks_like": "no licence file found in the archive"}


# ---- install / uninstall / status -------------------------------------------
def write_last_error(home: Path, message: str) -> None:
    try:
        directory = codec_dir(home)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "last_error.txt").write_text(message + "\n")
    except OSError:
        pass


def install(
    *,
    confirmed_by_user: bool,
    home: Optional[Path] = None,
    archive: Optional[Path] = None,
    url: str = MBELIB_URL,
    expected_sha256: str = MBELIB_SHA256,
    adapter_c: Optional[Path] = None,
    sources: Sequence[str] = MBELIB_SOURCES,
    log=print,
) -> dict:
    """Download (unless `archive` is given), verify, compile, self-test, install. Returns the manifest."""
    if not confirmed_by_user:
        raise BuildError("not confirmed: nothing was downloaded or built")
    home = home or data_home()
    adapter_c = adapter_c or (Path(__file__).resolve().parents[1] / ADAPTER_REL)
    build_root(home).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="build-", dir=build_root(home)))  # our own new, empty directory
    try:
        if archive is None:
            archive_path = work / "mbelib.tar.gz"
            log(f"Downloading {url}")
            download(url, archive_path)
        else:
            archive_path = Path(archive)
            log(f"Using local archive {archive_path}")
            if not archive_path.is_file():
                raise BuildError(f"{archive_path} does not exist")
        log("Checking SHA-256 ...")
        verify_sha256(archive_path, expected_sha256)
        src_root = work / "src"
        src_root.mkdir()
        src_dir = safe_extract(archive_path, src_root)
        log(f"Unpacked mbelib {read_header_version(src_dir)}; compiling (only its C files; its build scripts are not run) ...")
        out = work / LIB_NAME
        command, compiler_version = compile_library(src_dir, adapter_c, out, sources=sources)
        description = self_test(out)
        log(f"Self-test passed: {description}")

        target_dir = codec_dir(home)
        target_dir.mkdir(parents=True, exist_ok=True)
        final = target_dir / LIB_NAME
        staged = target_dir / (LIB_NAME + ".new")
        shutil.copyfile(out, staged)
        os.replace(staged, final)  # atomic: a running API never sees a half-written file
        manifest = {
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "library": str(final),
            "library_sha256": sha256_file(final),
            "abi_version": ABI_VERSION,
            "describe": description,
            "mbelib": {
                "repo": MBELIB_REPO,
                "commit": MBELIB_COMMIT if archive is None or expected_sha256 == MBELIB_SHA256 else "custom",
                "archive_sha256": expected_sha256,
                "header_version": read_header_version(src_dir),
                "licence": licence_summary(src_dir),
            },
            "adapter_sha256": sha256_file(adapter_c),
            "compiler": compiler_version,
            "command": " ".join(command),
            "notes": [
                "Built locally for your own use. Do not redistribute this library.",
                "SigSense does not ship, vouch for or license this codec. Patent and licence questions are yours.",
            ],
        }
        (target_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        try:
            (target_dir / "last_error.txt").unlink()
        except FileNotFoundError:
            pass
        return manifest
    except BuildError as exc:
        write_last_error(home, str(exc))
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)  # no downloaded source is left behind


def uninstall(home: Optional[Path] = None, log=print) -> list[str]:
    """Remove only what this helper creates. Returns the paths removed."""
    home = home or data_home()
    removed: list[str] = []
    directory = codec_dir(home)
    for name in (LIB_NAME, LIB_NAME + ".new", "manifest.json", "last_error.txt"):
        path = directory / name
        if path.exists():
            path.unlink()
            removed.append(str(path))
    for path in (directory, build_root(home)):
        if path.is_dir():
            if path == build_root(home):
                shutil.rmtree(path, ignore_errors=True)
                removed.append(str(path))
            elif not any(path.iterdir()):
                path.rmdir()
                removed.append(str(path))
    log("Removed: " + (", ".join(removed) if removed else "nothing (it was not installed)"))
    return removed


def status(home: Optional[Path] = None) -> dict:
    home = home or data_home()
    library = codec_dir(home) / LIB_NAME
    info: dict = {"installed": library.is_file(), "path": str(library), "data_home": str(home)}
    manifest = codec_dir(home) / "manifest.json"
    if manifest.is_file():
        try:
            info["manifest"] = json.loads(manifest.read_text())
        except ValueError:
            info["manifest"] = "unreadable"
    if library.is_file():
        try:
            info["describe"] = self_test(library)
            info["works"] = True
        except BuildError as exc:
            info["works"] = False
            info["error"] = str(exc)
    error = codec_dir(home) / "last_error.txt"
    if error.is_file():
        info["last_error"] = error.read_text().strip()
    return info


# ---- command line -----------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build SigSense's local IMBE/AMBE codec library (opt-in).")
    parser.add_argument(CONFIRM_FLAG, dest="i_understand", action="store_true", help="I have read the warning; go ahead")
    parser.add_argument("--status", action="store_true", help="show whether the library is installed and works")
    parser.add_argument("--uninstall", action="store_true", help="remove the installed library and any build files")
    parser.add_argument("--archive", type=Path, help="use this already-downloaded mbelib tarball (still SHA-256 checked)")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(status(), indent=2))
        return 0
    if args.uninstall:
        uninstall()
        return 0

    print(WARNING)
    if not confirmed(args):
        print(f"Not confirmed, so nothing was downloaded or built. Run again with {CONFIRM_FLAG} when you are ready.")
        return 2
    try:
        manifest = install(confirmed_by_user=True, archive=args.archive)
    except BuildError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1
    print(f"\nInstalled {manifest['library']}")
    print("SigSense finds it automatically; restart the API to pick it up.")
    print("(To use a different library instead, set SIGSENSE_MBE_LIB / SIGSENSE_AMBE_LIB / SIGSENSE_VOCODER_LIB.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
