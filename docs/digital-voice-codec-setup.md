# Digital voice audio: setting up the local codec

SigSense decodes digital voice calls (who is talking, which talkgroup, when, for how long) without any
extra software. To also **hear** the voice on P25, DMR, NXDN, YSF and D-STAR, it needs a voice codec
(a "vocoder"). SigSense does not ship one. Like SDRTrunk, which asks you to build its separate JMBE
library yourself, SigSense gives you a helper that builds the codec **on your own machine, after a
warning and your explicit confirmation**.

## What this is, and what it isn't

- The codec is the open **mbelib** project (<https://github.com/szechyjs/mbelib>). SigSense adds a small
  adapter of its own (`apps/api/sigsense_api/dvoice/vocoder/adapter/sigsense_mbe_adapter.c`) and
  compiles the two together into `libsigsense_mbe.so` in your data directory.
- Nothing from mbelib is stored in this repository. The helper downloads it only when you ask.
- Encrypted calls stay encrypted. SigSense detects and reports encryption; it does not decrypt, recover
  keys or accept keys.
- Codec2 (used by M17) is separate and needs no setup: `sudo apt install libcodec2-1.2` is enough.

## Please read: patents and licences

- The IMBE and AMBE voice codecs **may be covered by patents** (an AMBE+2 patent is listed as active
  until about 2028). Whether you may download, build or use them depends on where you live and what you
  use them for. Check the law before you go ahead. This is not legal advice, and SigSense can't tell you.
- mbelib is published under a permissive ISC-style licence. The build records the licence file it found
  in the manifest. Keep that copyright notice if you ever share it. The names MBE, IMBE and AMBE belong
  to their owners.
- The library you build is **for your own use**. Don't redistribute it, and don't commit it anywhere.
- Using it runs its code with the API's privileges. The helper only builds source that matches a pinned
  SHA-256 (below), but you are still choosing to trust it.

## Build it

You need a C compiler (`sudo apt install build-essential`) and an internet connection for the one
download.

```bash
python3 apps/api/scripts/build_mbe_adapter.py --i-understand
```

Without `--i-understand` it prints the warning and stops (when run in a terminal it asks you to type
`I UNDERSTAND` instead). It then:

1. downloads one file from `codeload.github.com` (the only host it will ever contact);
2. checks the file's SHA-256 against the pinned value and **stops if it doesn't match**;
3. unpacks it into a new, empty temporary directory, refusing links and paths that escape it;
4. compiles only the listed C files plus the SigSense adapter with a fixed command line. mbelib's own
   build scripts are never run;
5. loads the result and decodes a test frame for every codec;
6. installs it, writes `manifest.json` next to it, and deletes all the downloaded source.

If any step fails nothing is installed (an earlier working install is left alone) and the reason is
written to `last_error.txt`, which the app shows.

### What gets installed, and where

```
~/.local/share/sigsense/codecs/libsigsense_mbe.so
~/.local/share/sigsense/codecs/manifest.json     (versions, hashes, licence file, compiler command)
```

Set `SIGSENSE_CODEC_HOME` to use another directory instead of `~/.local/share/sigsense`.

### The pinned source

| | |
| --- | --- |
| Repository | <https://github.com/szechyjs/mbelib> |
| Commit | `9a04ed5c78176a9965f3d43f7aa1b1f5330e771f` (mbelib 1.3.0 plus later fixes; the last commit upstream at the time of writing) |
| Archive | `https://codeload.github.com/szechyjs/mbelib/tar.gz/9a04ed5c78176a9965f3d43f7aa1b1f5330e771f` |
| SHA-256 | `c7d6ebbbf3ca44bc10ee0755dede7f74fd65c31d1568d4174f29a68ae2f92278` |

Hashes of GitHub-generated archives are normally stable but are not guaranteed. If it ever says the hash
doesn't match, don't edit the value to make it pass. Download the archive yourself, check it against the
commit above, and pass it with `--archive path/to/file.tar.gz` (it is still hash-checked).

## Use it

Restart the API. It finds the library by itself. You can see it in `GET /dvoice/capabilities` (the
`local_codec` block) and in the app's digital voice panel. To use a different library instead, set
`SIGSENSE_MBE_LIB` (IMBE), `SIGSENSE_AMBE_LIB` (AMBE/AMBE+2) or `SIGSENSE_VOCODER_LIB` (both). Environment
variables always win.

| Protocol | Codec | Audio once built |
| --- | --- | --- |
| P25 Phase 1 | IMBE 7200x4400 | yes |
| P25 Phase 2 | AMBE+2 3600x2450 | yes |
| DMR | AMBE+2 3600x2450 | yes |
| NXDN | AMBE+2 3600x2450 | yes |
| YSF | AMBE+2 3600x2450 | yes |
| D-STAR | AMBE 3600x2400 | yes |
| M17 | Codec2 | needs libcodec2, not this helper |

Calls and metadata work without any of this; only the audio needs it.

## Check it

```bash
python3 apps/api/scripts/build_mbe_adapter.py --status
```

shows whether the library is installed and loads, and prints the manifest.

## Remove it

```bash
python3 apps/api/scripts/build_mbe_adapter.py --uninstall
```

deletes the library, the manifest, any `last_error.txt` and the build directory. It touches nothing else.

## Known limits

- This has been tested with a stand-in codec that makes a tone, and with a real build that decodes
  generated frames without crashing and produces audio. It has **not** been checked against real
  over-the-air recordings, so voice quality on real calls is unverified.
- mbelib prints a diagnostic line to the API's terminal when it sees an AMBE "tone" frame. That is
  harmless.
- The adapter passes mbelib already-corrected frames and reports no error count, so mbelib's own
  "too many errors, repeat the last frame" rule is not triggered. SigSense's protocol layer decides
  which frames are lost and sends those as erasures, which the adapter conceals by repeating, then
  muting after three in a row.

## DMR voice frame mapping (provenance and verification)

ETSI TS 102 361-1 defines only the DMR "vocoder socket": each voice burst carries three 72-bit vocoder frames.
How those 72 bits become the AMBE+2 parameters (channel interleave, Golay codes, scrambler) is the vocoder
vendor's coding and is not in the ETSI documents. SigSense implements it in
`apps/api/sigsense_api/dvoice/protocols/dmr/voice_map.py` from these sources:

| What | Source | Licence |
| --- | --- | --- |
| Channel code structure: rows of 24/23/11/14 bits, Golay(23,12) on rows 0 and 1, row 1 scrambled from row 0 (constants 173 / 13849 / 65536), 49 parameter bits | mbelib, https://github.com/szechyjs/mbelib @ `9a04ed5c78176a9965f3d43f7aa1b1f5330e771f` (`ambe3600x2450.c`, `ecc.c`, `ecc_const.h`) | ISC |
| Air-bit order -> (row, column) permutation (36 x 4 numeric indices) | original DSD, https://github.com/szechyjs/dsd @ `6605925162f412ef6d927aa1b2eab8049973f6a3`, `include/dmr_const.h` (sha256 `133427d0bd2eb0a6000bd441d6f1d0e8168c5a6ce24f768d031366b948b60bc9`) | ISC text in the file header (GitHub shows "NOASSERTION": no LICENSE file; the project has a COPYRIGHT file) |
| Consistent with the half-rate frame description (72 bits = 23 FEC + 49 voice; Golay(24,12) + Golay(23,12); 25 unprotected bits; scrambling from a key derived from the first code's data) | DVSI patent US 8,359,197 B2 (public text, no interleave table) | public patent text |

No GPL source was used for the table, and only numeric indices were taken (no code or structure). GitHub reports
the DSD repository's licence as undetected, so if you redistribute SigSense, check that note and keep the
attribution in `voice_map.py`; this is not legal advice. The 144-bit NXDN descrambling mask (`NXDN_VOICE_MASK`)
from the same file is stored for the NXDN decoder and is not verified against the NXDN specification.

**What is and is not proven.** `tests/test_dvoice_dmr_voicemap.py` proves the table is a bijection, that
encode and decode invert each other, that single/double/triple errors in the protected rows are corrected, that
the vectorised decode equals a literal port of the reference algorithm, and that a shuffled table decodes to noise.
It does **not** prove the table matches real DMR air: only a capture can.

**Verify it with a recording.** Record a minute or two of a DMR channel while there is voice traffic, e.g.
`hackrf_transfer -r dmr.iq -f <channel Hz> -s 2000000 -n 240000000` (set the centre on the channel, or
note its offset from the centre), then run
`python3 apps/api/scripts/verify_dmr_voice_map.py dmr.iq --format cs8 --rate 2000000 --offset-hz 0`.
It prints how many voice frames decoded and how many needed at most one Golay correction, next to a
shuffled-table control. With the right table a decent signal gives a large clean fraction and the control
almost none; the script exits 0 on PASS. It also tests the "bits swapped inside each dibit" alternative, so
a bit-order mismatch is reported specifically. Audio additionally needs the local codec (see above).
