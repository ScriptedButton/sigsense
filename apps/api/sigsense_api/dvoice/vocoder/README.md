# Vocoder layer (WP-F)

Frame in (`VocoderFrame`: FEC-decoded parameter bits) -> PCM out. Everything the engine needs is in
`session.py` (`SessionPool.push(key, frame)` -> 8 kHz and 48 kHz int16 PCM, concealment, fade-out on `end`).

| Codec | Used by | Backend | Out of the box |
| --- | --- | --- | --- |
| Codec2 3200 / 1600 | M17 | `libcodec2` via ctypes (LGPL, not bundled) | works when libcodec2 is installed (`apt install libcodec2-1.2`) |
| IMBE 7200x4400 | P25 Phase 1 | adapter you install (`$SIGSENSE_MBE_LIB`, `$SIGSENSE_VOCODER_LIB`) | **off** |
| AMBE+2 3600x2450, AMBE 3600x2400 | DMR, NXDN, YSF, P25 Phase 2, D-STAR | adapter you install (`$SIGSENSE_AMBE_LIB`, `$SIGSENSE_VOCODER_LIB`) | **off** |
| AMBE / AMBE+2 | same | DVSI AMBE-3000 over serial (`$SIGSENSE_DVSI_PORT`) | **interface stub only, never reports available** |

SigSense ships no IMBE/AMBE/AMBE+2 implementation and no vocoder binary. With nothing configured, calls and
metadata are decoded and audio is reported as `vocoder_unavailable`.

## Using your own IMBE/AMBE library

Write (or obtain) a small shared library exporting the C ABI in `backends/external.py` (version 1), wrapping
whichever codec library or driver you are entitled to use, then:

    export SIGSENSE_MBE_LIB=/path/to/your-adapter.so     # IMBE (P25 Phase 1)
    export SIGSENSE_AMBE_LIB=/path/to/your-adapter.so    # AMBE / AMBE+2
    # or one library for both:  SIGSENSE_VOCODER_LIB=...

`GET /dvoice/capabilities` (WP-G) then reports it active, with the licence note. Loading runs the library's
initialisation code with this process's privileges: only use files you built or trust. Licence and patent
questions about the codec behind your adapter are yours (not legal advice).

## Testing without any codec

`registry.use_test_double()` swaps in a deterministic tone generator (not a codec) so protocol and engine tests
can run end to end.
