# Digital voice decoding: architecture and work-package contracts

Status: phase 0 (architecture + contracts). Contract version **1**.
Code skeleton: `apps/api/sigsense_api/dvoice/`. TypeScript mirror: `apps/web/src/lib/sdr/dvoice/types.ts`.
Contract tests: `apps/api/tests/test_dvoice_contracts.py` (must stay green; it fails only on contract drift).

Reference architecture: [arancormonk/dsd-neo](https://github.com/arancormonk/dsd-neo) (inspected at its
current default branch; a read-only clone sat in a scratch directory, nothing from it was built or run).
We reuse its **structure and pipeline ideas**, not its code (section 2).

---------------------------------------------------------------------------------------------------

## 0. Read this first (all implementers)

1. **Do not copy or translate dsd-neo source.** It is GPL-3.0-or-later and carries mixed third-party
   provenance (section 2). Implement from the public air-interface specifications and from this
   document. Do not clone dsd-neo to "see how it does it": if you need to resolve a spec ambiguity,
   ask the architect, consult another independent source, or build a spec-based known-answer test.
   (Reducing derivative-work risk is the point; the architecture notes below are the sanctioned summary.)
2. **Scope policy: clear voice and signalling only.** The engine **detects and reports** encryption
   (algorithm id, key id, message-indicator presence, and the "encrypted" call flag). There is **no key
   recovery, brute force, key import, key table, or decryption** in this package, in this phase. Encrypted
   calls produce metadata and no audio. The contract test refuses file names like `crack`/`brute` and any
   key-material field on `EncryptionInfo`. If you believe a feature needs key handling, stop and ask.
3. **UI disclaimer (WP-H must show it, verbatim or equivalent):**
   *"Monitoring is legal for unencrypted public-safety and amateur traffic in many places but not all.
   You are responsible for complying with the laws that apply to you. Encrypted transmissions are
   not decoded."*
4. **Stay in your lane.** Each work package owns a disjoint set of files (section 10). Only the
   integration package (WP-G) edits shared files (`main.py`, `requirements.txt`, `sdr.py`, `hackrf.py`).
   WP-H owns `control-rail.tsx`, a marked block in `receiver.css`, and the small upload hook in
   `radio-controller.ts`.
5. **Contracts change only through the architect/integration lead**: edit `events.py` +
   `types.ts` + `test_dvoice_contracts.py` together and bump `CONTRACT_VERSION`.
6. Never touch the user's live API/HackRF at `localhost:8000`. Use your own port (suggested ranges:
   WP-A..F tests need no server; WP-G 8030-8039; WP-H web 3120-3129). Kill only PIDs you started.
7. No commits unless the user asks. Tests: `cd apps/api && ../../.venv/bin/python -m unittest discover -s tests`.

---------------------------------------------------------------------------------------------------

## 1. What the reference architecture does (and how we map it)

dsd-neo is a C/C++ DSD-family decoder. Its structure (from `docs/code_map.md`,
`docs/rtl-demod-pipeline-audit.md`, the source tree and public headers):

| dsd-neo layer | What it does | Our module |
| --- | --- | --- |
| `src/io` (rtl/soapy/airspy/tcp/iq replay) | capture -> normalised float IQ ring | existing `hackrf.py` tap + browser upload (section 5) |
| `src/dsp` demod pipeline (`demod_pipeline.cpp`, `fsk_modem.c`, `halfband`, `firdes`, `costas`, `ted`, `iq_resampler`) | channel LPF/half-band decimation; FSK path: discriminator -> PCM-like samples; CQPSK path: AGC -> band-edge FLL -> Gardner -> differential phasor -> Costas | `dvoice/dsp/` (`channel.py`, `discriminator.py`, `fsk.py`, `cqpsk.py`, `afc.py`) |
| `dsd_symbol.c`, `symbol_levels.c`, `slicer_thresholds.c`, `sync_calibration.c` | sample-domain timing, adaptive 4-level slicer centred from sync words, dibit digitisation (CQPSK uses fixed -2/0/+2 thresholds) | `dsp/slicer.py`, `FskSymbolRecovery.recalibrate_from_sync` |
| `dsd_frame_sync.c`, `sync_hamming.c`, `frame_sync_policy.c`, `core/sync_patterns.h` | correlate dibit stream against every enabled protocol's sync word, both polarities, with error tolerance; decides protocol | `dvoice/sync/` + `dsp/autodetect.py` |
| `engine/dispatch/*` (`protocol_dispatch.c`, one `dispatch_<proto>.c` each) | handler table keyed by sync type -> per-protocol frame processors | `protocols/<proto>/decoder.py` behind one `ProtocolDecoder` interface; `engine.py` dispatches |
| `src/fec` (`fec.c`, `bptc.c`, `trellis34.c`, `hamming_10_6_3`, `rs-12-9.c`, ezpwd RS, Golay24) and `protocol/*/*_crc.c` | all block/convolutional FEC, CRCs | `dvoice/fec/` (one shared library, section 6.1) |
| `core/audio`, `core/vocoder/dsd_mbe.c`, mbelib-neo | MBE frame -> PCM; per-protocol frame types `DSD_VOICE_PROTOCOL_*`; soft-bit input | `dvoice/vocoder/` (interface + pluggable backends, section 7) |
| `core/call_state.h`, `core/events.h`, `runtime/telemetry` | canonical call record (slot, target/source, service options, crypto classification, timing), event history, telemetry to UI/API | `events.py` (`Call`, `EncryptionInfo`, `SystemInfo`, `LinkStats`, ...) |
| `protocol/p25/p25_trunk_sm.c`, `dmr/dmr_trunk_sm`, `nxdn_cc_hunt`, `engine/trunk_tuning.c` | control-channel state machines, voice-grant following, hang time, return-to-CC, CC hunting | `dvoice/trunking/` (policy + `TuneRequest`) |
| `apps/dsd-cli`, `src/api`, `src/ui` | CLI/terminal/Qt UI, control API | `dvoice/api.py` + web UI (WP-G, WP-H) |
| `core/audio` UDP/net sinks, `recorder`, `dsd_events` | audio out, per-call WAV, call/event logs | `recorder.py`, binary audio frames (section 4.3) |

Protocol coverage in dsd-neo: P25 P1 (C4FM + CQPSK) and P2, DMR (BS/MS, Tier I-III, Hytera/Motorola variants),
NXDN48/96, D-STAR, YSF, M17, dPMR, X2-TDMA, ProVoice, EDACS, TETRA. **Our v1 scope:** P25 P1, P25 P2,
DMR, NXDN48/96, D-STAR, YSF, M17. dPMR / X2-TDMA / ProVoice / EDACS / TETRA are *reserved enum values*
(`Protocol`) that the UI can name but the engine reports as `unsupported_protocol` errors.

Notable design points we adopt from the reference:

* One **symbol-rate-agnostic dibit stream** after the front end, so frame sync, FEC and parsers are
  independent of modulation. Both polarities are searched; a polarity flag is carried, not "fixed up" by luck.
* The slicer is **re-centred from every sync word** (levels and offset), which is what makes weak/offset
  signals decode.
* CQPSK needs a **separate complex path** (timing + differential phase), FSK/C4FM can ride the discriminator.
* FEC returns **both data and a correction count**; the count drives BER/health metrics and the vocoder's
  erasure/repeat decision.
* **Trunking is a state machine** (on CC / hang / hunting / armed / follow / returning) separated from
  frame decoding; retuning is a request, not a side effect.
* Vocoder input is **soft-bit capable** (bit + reliability) even if v1 passes hard bits.
* A **canonical call record** with identity, timing, slot, crypto classification and a revision/epoch so
  consumers can tell updates from new calls (our `call_id`).

---------------------------------------------------------------------------------------------------

## 2. Licensing of the reference, and what that means for us

Primary sources: dsd-neo `LICENSE`, `THIRD_PARTY.md`, `legal/status.md`; mbelib-neo v2.6.2 `legal/patents.md`, `legal/status.md`.

* **dsd-neo is GPL-3.0-or-later.** Copying or translating its code into SigSense would make the combined
  work subject to GPLv3 copyleft. We therefore take **architecture and spec knowledge only** (this document)
  and write original code from public standards (TIA-102, ETSI TS 102 361-1/-2/-3, ETSI TS 102 361-4 for
  Tier III, NXDN Forum specs, JARL D-STAR spec, Yaesu YSF documentation, M17 Protocol Specification).
* **The reference itself documents unresolved licence and patent findings** (its own `legal/status.md` says
  explicitly it is "not legal clearance"): a non-commercial-restricted BCH file (EDACS), a Golay implementation
  whose source article has no explicit redistribution grant, mixed OP25-derived GPL parts in NXDN/P25 helpers,
  TETRA ETSI codebook data with unresolved permission, and open patent questions for AMBE+2/TETRA. Lesson for
  us: **FEC must be written from the mathematics** (generator polynomials, syndrome decoding, published tables
  from the standards), not adapted from existing C. Golay(23,12) in particular is a few lines from `g(x)`.
* Spec constants (sync words, field layouts) are facts, not expression; we cross-checked our sync words
  against the reference's table and against the standards' hex forms (and found one real subtlety, below).
* **Sync-table subtlety found during cross-check:** the reference stores *sign-only* sync strings (`1` =
  positive symbol, `3` = negative). That equals the exact word for P25/DMR/M17/NXDN (outer symbols only) but
  loses inner symbols for the **YSF** FSW (`0xD471C9634D`, which contains dibits 0 and 2). Our
  `sync/patterns.py` stores **exact dibits** and exposes `.signs` for tolerant matching; the contract test pins
  both forms to the reference.

---------------------------------------------------------------------------------------------------

## 3. Where it runs: decision

**Server-side Python engine (`apps/api/sigsense_api/dvoice/`), results streamed to any client.**

| Option | HackRF | RTL / OpenWebRX / rtl_tcp (browser has IQ) | Mobile | Verdict |
| --- | --- | --- | --- | --- |
| Browser (like the existing P25 `lib/sdr/p25`) | no (IQ stays on server) | yes | heavy on phones; iOS cannot run the DSP WASM | rejected as the primary home |
| **Server** | **yes (in-process IQ tap, no upload)** | **yes via a 48 kHz upload (192 KB/s IQ, or 96 KB/s discriminator)** | **yes (thin client)** | **chosen** |

Compute: numpy/scipy for block DSP; **numba 0.68 is already installed** (as is cffi), so per-sample loops
(timing recovery, Viterbi, Golay/BCH search, sync correlation) go in `_kernels.py` modules decorated
`@numba.njit(cache=True)` with a pure-numpy fallback if numba cannot import (feature-detect once; tests run
both). Warm up kernels at engine start (JIT latency would otherwise land on the first call). A C extension
via cffi is a last resort, only if profiling shows numba cannot meet the real-time budget. Machine: 32 cores,
RTX 4070 (unused by this package; the speech-to-text service uses it).

The existing browser P25 decoder stays as-is for now (it works for RTL sources). WP-H decides, after server
parity for P25 P1, whether the Decode > Trunk tab routes to the server engine. No behaviour of the browser
engine is changed in this project.

---------------------------------------------------------------------------------------------------

## 4. Data flow, sample rates, threading, latency

### 4.1 Pipeline (one `ChannelWorker` per channel)

```
HackRF IQ (2-20 MS/s, int8 -> complex64)  [server tap]      browser source IQ/discriminator [upload]
        |                                                             |
        |  Channelizer(offset_hz, bw 12.5 kHz): NCO mix + cascaded     |   INPUT_IQ16_48K: already 48 kHz
        |  stateful FIR decimation  ->  48 kHz complex baseband        |   INPUT_DISC16_48K: skips to slicer
        +--------------------------+-----------------------------------+
                                   |
                  +----------------+------------------+
                  | FSK/C4FM/GMSK path                | CQPSK path (P25 P2, P25 P1 simulcast)
                  | FmDiscriminator (Hz deviation)    | AGC -> FLL -> Gardner -> diff -> Costas
                  | FskSymbolRecovery (RRC, Gardner)  | CqpskSymbolRecovery
                  +----------------+------------------+
                                   |   SymbolBlock(dibits, soft, symbol_rate, first_index, t0, modulation, polarity)
                       SyncCorrelator (enabled protocols, both polarities) -> SyncHit -> SyncEvent
                                   |   auto-detect or forced protocol selects the decoder
                       ProtocolDecoder.feed(block)
                          frame assembly -> FEC/CRC (dvoice.fec) -> link-control / signalling parse
                          -> emit("call_start|call_update|call_end|system_info|trunk|talkgroup|error", dataclass)
                          -> voice frames -> Vocoder (per call+slot) -> VoiceFrame(PCM 8 kHz)
                                   |
      engine: JSON events (+LinkStats every 500 ms)  |  binary audio frames  |  CallRecorder  |  STT hook  |  TrunkController -> TuneRequest
```

Symbol rates: P25 P1 4800 (C4FM 4 levels, or CQPSK 10 SPS @48 k); P25 P2 6000 (CQPSK, 8 SPS @48 k); DMR 4800
(4 levels, 2 slots); NXDN48 2400, NXDN96 4800; D-STAR 4800 (GMSK, 2 levels); YSF 4800 (C4FM); M17 4800 (4FSK).
Channel filters follow the reference guidance: 12.5 kHz classes with a wide-enough sample rate (>= 24 kHz,
48 kHz preferred) for timing margin; NXDN48/dPMR-class 6.25 kHz.

Dibit convention (all protocols; see `dsp/types.py`): `+3 -> 1, +1 -> 0, -1 -> 2, -3 -> 3`; polarity inversion maps
`d -> d ^ 2`. 2-level D-STAR uses only `{1, 3}`.

### 4.2 Threading and asyncio

* The FastAPI event loop never runs DSP. `feed_iq`/`feed_upload` enqueue into a **bounded per-channel
  queue** (drop-oldest + an `input_overrun` `error` event) and return immediately.
* One **worker thread per channel** owns all state for that channel (front end, correlator, decoder, vocoder
  instances, trunk follower). No shared mutable state between channels; the vocoder registry hands out fresh
  instances per call/slot.
* Events leave through the engine's `on_event` / `on_audio` callbacks, which the API layer bridges onto the
  loop with `loop.call_soon_threadsafe` (as `/live/audio` already does for the transcript tap).
* `subscribe_audio` fans per-call PCM out to other consumers (speech-to-text) without blocking the worker.

### 4.3 Latency budget (HackRF IQ in -> audio frame out)

front end <= 40 ms; frame assembly = the protocol's frame time (P25 LDU 180 ms for 9 IMBE frames; DMR burst
30 ms/voice superframe 360 ms; NXDN 40 ms; YSF 100 ms; M17 40 ms); vocoder <= 5 ms/frame; queues <= 40 ms.
**Target: voice audible < 350 ms after the voice frame ends**; call-start metadata < 1 s after the first sync.
Real-time factor target: <= 0.15 of one core per channel (with numba).

### 4.4 Binary audio frames

16-byte header + int16 LE PCM (8 kHz, 160 samples typical): `magic 0xA7, version, slot(0xFF none), flags(end,
errors), call_num u32, seq u32, sample_rate u16, n_samples u16`. Implemented and tested in `events.py`
(`pack_audio_frame` / `unpack_audio_frame`) and mirrored in `types.ts` (`parseDvAudioFrame`). The browser
plays it through the existing player (`OpenWebRxPlayback.push(pcm, 8000)` resamples).

---------------------------------------------------------------------------------------------------

## 5. Inputs

| Source | Path | Notes |
| --- | --- | --- |
| HackRF (server) | `feed_iq(iq, fs, center_hz, channel)` from a **second IQ tap** in `hackrf.py` (WP-G adds `add_tap`, parallel to the existing audio tap; independent ring, non-consuming) | A channel is an NCO offset + decimator on the shared wideband capture, so several channels (control + voice follower, or 2-3 systems in one 8-10 MHz window) cost no retune. This is the preferred voice-follow mechanism on HackRF. |
| Browser RTL / rtl_tcp / OpenWebRX / Demo | `WS /dvoice/stream?source=upload`: first byte `kind`, then int16 LE samples | `INPUT_IQ16_48K` (complex baseband at 48 kHz, from the existing P25 DDC `P25IqReceiver` which already down-converts to ~48 kHz) for all protocols; `INPUT_DISC16_48K` for FSK-family only (half the bandwidth). OpenWebRX supplies demodulated audio, not IQ: digital voice is **not available** on that source (UI says so). |
| Replay (tests) | `engine.feed_iq` with synthetic or recorded IQ | gated real-capture tests |

Mobile: phones are display/playback clients only; no DSP on device.

---------------------------------------------------------------------------------------------------

## 6. Module tree (exact paths) and interfaces

```
apps/api/sigsense_api/dvoice/
  __init__.py            CONTRACT_VERSION = 1
  events.py              enums + dataclasses + make_event + pack/unpack_audio_frame      (architect)
  fec/                   (WP-A)  types.py crc.py golay.py hamming.py bch.py bptc.py reed_solomon.py
                                 viterbi.py trellis.py interleave.py whitening.py
  dsp/                   (WP-B)  types.py channel.py discriminator.py fsk.py cqpsk.py slicer.py afc.py autodetect.py
  sync/                  (WP-B)  patterns.py correlator.py
  protocols/
    base.py              ProtocolDecoder, ProtocolConfig, EmitFn, VocoderProvider        (architect)
    p25p1/ (WP-C) p25p2/ (WP-C2) dmr/ (WP-D) nxdn/ dstar/ ysf/ m17/ (WP-E)    each: __init__.py (DECODERS) + decoder.py (+ helpers)
  vocoder/               (WP-F)  base.py registry.py backends/{imbe_external,ambe_external,codec2}.py
  trunking/              base.py (architect)  p25.py (WP-C)  dmr.py (WP-D)
  engine.py              (WP-G)  DigitalVoiceEngine, EngineConfig, ChannelConfig, INPUT_* kinds
  recorder.py            (WP-G)  CallRecorder
  api.py                 (WP-G)  build_router(...)
  testing/               base.py (architect) modulator.py channel.py (WP-B) <proto>_encoder.py (protocol WPs)
apps/api/tests/          test_dvoice_contracts.py (architect) + each WP's own test_dvoice_<area>*.py
apps/web/src/lib/sdr/dvoice/types.ts (architect)   client.ts store.ts (WP-H)
apps/web/src/components/sdr/dvoice/*                (WP-H)
docs/digital-voice-architecture.md                  (this file; WP-I keeps it current)
```

### 6.1 Shared FEC library (WP-A) — exact signatures

Words are Python ints (MSB first) for small block codes; bit arrays are `uint8` of 0/1. Decoders return
`(data, corrected)` / `None` (uncorrectable) for word codes and `FecResult(data, corrected, ok)` for block
codes. See the stub modules (docstrings are the contract; the contract test pins names and parameters):

```
fec.crc        CRC_SPECS: dict[str, CrcSpec];  crc_compute(name, bits)->int;  crc_check(name, bits_with_crc)->bool
               (engine implemented + verified: crc16_ccitt_false 0x29B1, crc16_genibus 0xD64E, crc16_x25 0x906E;
                WP-A adds P25 TSBK/PDU/LC, DMR CRC-9/-16/-32 and CCITT masks, NXDN CRC-6/7/12/15/16, M17 LSF, ...)
fec.golay      golay_23_12_{encode,decode}  golay_24_12_{encode,decode}  golay_18_6_decode  golay_20_8_{encode,decode}
fec.hamming    hamming_{7_4,10_6_3,13_9_3,15_11_3,16_11_4,17_12_3}_{encode,decode}
fec.bch        p25_nid_{encode,decode}(BCH(63,16,23)+parity)  qr_16_7_6_{encode,decode}  bch_15_7_5_decode
fec.bptc       bptc_196_96_{decode,encode}  embedded_lc_{decode,encode}
fec.reed_solomon  ReedSolomon(m, prim_poly, n, k, fcr, prim).{encode,decode(erasures=)}
               rs_p25_24_12_13  rs_p25_24_16_9  rs_p25_36_20_17  rs_dmr_12_9_4
fec.viterbi    ConvCode  conv_encode  viterbi_decode(code, received, soft)   (NXDN, M17, D-STAR, YSF named codes)
fec.trellis    p25_trellis_{half,three_quarter}_{decode,encode}  dmr_trellis_three_quarter_{decode,encode}
fec.interleave table(name) + deinterleave/interleave/block_permutation   (named tables listed in the docstring)
fec.whitening  lfsr_sequence (implemented)  p25p2_scramble_sequence  nxdn_scramble_sequence  m17_randomizer
               dstar_scrambler  ysf_whitening
```

If a protocol WP needs a FEC function that is not listed, it **asks WP-A** (or, if WP-A is not yet done, writes
the function privately inside its own package with a `# TODO(WP-A): move to fec/` note and a test; WP-A
adopts it later). It never edits `fec/`.

### 6.2 Protocol decoder interface (architect; `protocols/base.py`)

`ProtocolDecoder(emit, vocoders, config)` with class attributes `protocol`, `symbol_rate`, `modulation`,
`sync_specs` (keys of `SYNC_SPECS`), `timeslots`, and methods `feed(SymbolBlock)`, `reset()`, `stats()`.
Decoders only call `emit(type, payload)`:

| emit type | payload | when |
| --- | --- | --- |
| `system_info` | `SystemInfo` | control/system broadcast decoded (NAC/WACN/SysID/site; DMR CC/system code; NXDN RAN) |
| `call_start` / `call_update` / `call_end` | `Call` (same `call_id` for a transmission) | first valid voice/LC; changes (source id, encryption, emergency); terminator or hang timeout |
| `voice` (internal, engine turns into audio frames) | `VoiceFrame` | one per vocoder frame |
| `sync` | `SyncEvent` | each accepted sync (rate-limited by the engine) |
| `trunk` | `TrunkEvent` | grants, CC acquired/lost, release |
| `talkgroup` | `Talkgroup` | first heard / label resolved |
| `error` | `ErrorEvent` | `vocoder_unavailable`, `fec_burst_loss`, `unsupported_feature`, ... |

`Call.encryption` is filled from the air interface (P25 ES/ALGID/KID/MI, DMR privacy/FID/feature flags, NXDN
cipher type, M17 encryption-type field). A call whose `encryption.state == encrypted` is **never** passed to a
vocoder; its `Call` is still emitted with `audio_decoded = False`.

Call lifecycle: `call_start` on the first FEC-valid LC/voice; `call_update` at most 4 Hz or on changed fields;
`call_end` with `end_reason` in `terminator | timeout | sync_loss | retune`. DMR/P25P2 slots are separate calls
(`slot` 0/1). `call_id` is `"{protocol}-{channel}-{slot|x}-{counter}"`; the engine assigns `meta["call_num"]`
(u32) for the binary audio header.

### 6.3 Front end interface (WP-B)

`Channelizer(sample_rate_in, offset_hz, bandwidth_hz, out_rate=48000).process(iq)`, `FmDiscriminator`,
`FskSymbolRecovery(symbol_rate, sample_rate, levels, modulation, timing)`, `CqpskSymbolRecovery`,
`AdaptiveSlicer`, `FrequencyTracker`, `AutoDetector`, `SyncCorrelator`. All stateful, block-size independent
(a test cuts the same signal at random boundaries and requires identical symbols), phase-continuous across
blocks. Required tolerances (acceptance tests): carrier offset +/-1.5 kHz after AFC, symbol clock +/-100 ppm,
polarity inversion, +/-20% deviation error, DC offset, 12-15 dB SNR operation for 4-level protocols.

### 6.4 Engine and API (WP-G)

`DigitalVoiceEngine(config, on_event, on_audio)`: `start/stop/configure`, `feed_iq`, `feed_upload`,
`status`, `subscribe_audio`. `EngineConfig`: channels (name, protocol or `"auto"`, offset, bandwidth,
polarity), enabled protocols, `FollowPolicy`, `record_dir` (off by default), talkgroup labels, vocoder
preferences. REST/WS surface is the docstring of `api.py` (`/dvoice/status`, `/capabilities`, `/config`, `/calls`,
`/calls/{id}/audio`, `/calls/export`, `/talkgroups`, `WS /dvoice/stream`). `/dvoice/capabilities` returns the
implemented protocols, vocoder availability **with licence notes**, numba/GPU info, the encryption policy and
the legal disclaimer text.

### 6.5 Trunking

`TrunkController.on_event(type, payload) -> list[TuneRequest]` and `on_voice_ended(freq)`. The reference's
state machine (on CC / hang / hunting / armed / follow / returning) is the model; ours is a typed, testable
class with `FollowPolicy` (hold/skip/priority lists, `skip_encrypted=True`, hang time, max followers). On HackRF
a `TuneRequest(role="voice")` is satisfied by adding a **second channelizer** on the same wideband IQ when the
voice frequency lies inside the captured span; only if it does not does WP-G retune the capture (using the
existing fast-retune path) and say so in a `trunk` event.

---------------------------------------------------------------------------------------------------

## 7. Vocoders, licences and patents (**decision needed from the user**)

Evidence: mbelib-neo v2.6.2 `legal/patents.md` and `legal/status.md` (dated 2026-10-17), dsd-neo
`legal/status.md`, this repo's `apps/web/src/lib/sdr/p25/vendor/mbe/PATENT-NOTICE.md`. None of this is legal advice;
the reference's own reviews state they are not legal clearance and that **an expiry date passing does not by
itself clear a patent issue**.

| Codec | Used by | Findings | Recommended route |
| --- | --- | --- | --- |
| **IMBE 7200x4400** | P25 Phase 1 | mbelib (original) carries an ISC notice plus a patent notice ("educational purposes... may be covered by patents"). The reference lists US 5,701,390 (regenerated-phase equations) only as a *historical algorithm reference* and declines to infer clearance from age. My understanding (unverified here) is that the 1990s IMBE patent families have expired, which is why open decoders have circulated for years; verify before relying on it. | **Default: external library.** Backend loads a library the *user installed* (`libmbe`/mbelib-neo via ctypes, path from `SIGSENSE_MBE_LIB`); not vendored. `availability()` states the patent notice. |
| **AMBE+2 3600x2450** | DMR, NXDN, YSF, P25 Phase 2 | **Patent-exposed.** mbelib-neo's review flags **US 8,359,197** (listed "Active", projected 2028-05-20; claims include decoder/tone-reconstruction claims), related family **US 8,595,002**, a voicing-smoothing application, and states it applied no exemption (educational/local use not asserted). Copyright of the library code is separate from these patent questions. | **Not shipped, off by default.** Two opt-in routes: (1) a **DVSI AMBE-3000 class USB vocoder** (licensed silicon; serial packet protocol; `Dvsi3000` backend), (2) a user-installed library as for IMBE. With neither present: calls, talkgroups and metadata are decoded, audio is reported `vocoder_unavailable`. |
| **AMBE 3600x2400** | D-STAR | same family concerns as AMBE+2 | same routes as AMBE+2 |
| **Codec2 3200** | M17 | LGPL-2.1 library; its authors state no known patent encumbrance for Codec2 | libcodec2 via ctypes (user/system installed) or `pycodec2`; shippable as an optional dependency |
| TETRA ACELP | (out of scope) | reference flags unresolved ETSI table permission | not implemented |

**Existing repo item (WP-F cleanup, no behaviour change):** the browser P25 build vendors an mbelib-derived WASM
(`p25/vendor/mbe/mbe.js`) whose directory contains only the patent notice, **no copy of mbelib's licence text**.
WP-F adds the upstream ISC licence text and a `NOTICE.md` (source, version 1.3.0, patent notice, how it was built)
next to it. Whether to keep *shipping* that WASM is the user's call (see below).

**Decisions for the user (the architecture is built so each can change later without rework):**
1. Keep the already-vendored IMBE WASM in the browser build, or switch the browser engine to the same
   "user-installed/optional" stance? (Recommendation: keep for now, add the licence/notice files, and do not
   extend vendoring to any new codec.)
2. AMBE+2/AMBE: ship nothing; support DVSI hardware and user-installed libraries only. (Recommended.) If you
   want AMBE+2 audio out of the box, that is a legal decision to take with counsel, not an engineering one.
3. Whether `/dvoice/capabilities` should show the full licence/patent text to users (recommended: yes).

---------------------------------------------------------------------------------------------------

## 8. Test strategy

1. **Known-answer FEC tests (WP-A):** vectors from the standards' appendices where published; otherwise
   algebraic properties: exhaustive single- and double-error correction for Hamming/Golay (all 2^12 words x all
   error patterns up to weight 3 for Golay), BCH(63,16,23) random patterns up to 11 errors + rejection of 12+,
   RS correction up to `t` symbols and `2t` erasures, Viterbi vs brute-force minimum distance on short blocks.
2. **Spec-based frame encoders (every protocol WP):** `testing/<proto>_encoder.py` builds complete
   transmissions (sync, NID/slot type/LICH, LC/ES/embedded LC, interleaved+FEC'd voice frames, terminator)
   from structured input (`SyntheticCall`) using the **same FEC library's encoders**. The decoder test asserts
   the reported `Call` fields equal the input. Encoders are independent code paths from the decoders, so a
   shared misunderstanding is still possible; therefore:
3. **Cross-validation against the spec text:** each WP lists, in its test file docstring, the spec section for
   every field layout and one worked example from the spec if the spec provides it. Where the standard gives a
   test vector (e.g. P25 NID examples, DMR sync/color-code tables, M17 spec's example LSF), it is used verbatim.
4. **Front-end loopback + BER sweeps (WP-B + each protocol):** dibits -> `modulate` -> `impair` (SNR sweep,
   +/-1.5 kHz, +/-100 ppm, inversion, random block boundaries) -> front end -> decoder. Report sync %, frame
   success %, and corrected/uncorrectable frames vs SNR; acceptance thresholds are in section 10.
5. **Robustness/fuzz (WP-I):** random dibit streams, truncated frames, forged length fields, enormous
   claimed PDU sizes into every decoder: no exception escapes `feed`, memory stays bounded, each `feed` call
   returns within a fixed time bound per symbol.
6. **Gated real-capture tests:** if files exist under `apps/api/tests/data/dvoice/` (not committed; the user can
   record IQ with the app's existing "Save IQ"), they are decoded and the test asserts only invariants (sync found,
   CRC-valid frames > N); otherwise `unittest.skip`. Synthetic success does not prove real-world performance:
   the final report must say which protocols were never exercised on a real signal.
7. **Contract tests** (`test_dvoice_contracts.py`) guard interfaces.
8. **Performance tests:** real-time factor per channel with numba on/off; 3 concurrent channels.

### Metrics and how errors surface

Every channel emits `LinkStats` every ~500 ms: `state` (searching/synced/voice/lost), `sync_ratio`, `ber`
(corrected bits over the window), `fec_corrected/fec_failed`, `frames_ok/bad`, `snr_db`, `freq_error_hz`, `level`,
`polarity_inverted`. The UI shows a sync lamp, BER meter, FEC counters and the call's `ber`/`voice_frames_bad`.
`ErrorEvent`s carry stable `code`s (`vocoder_unavailable`, `sync_lost`, `input_overrun`, `input_stalled`,
`unsupported_protocol`, `capture_unavailable`); the UI maps each to a one-line fix ("Install libmbe to hear
P25 audio", "Capture stopped", ...). Nothing fails silently; a missing vocoder never prevents call metadata.

---------------------------------------------------------------------------------------------------

## 9. Web client and UI contract (WP-H)

* `lib/sdr/dvoice/client.ts`: WS client with reconnect, `DvEvent` parsing, binary audio -> player, optional
  upload source (taps the existing `P25IqReceiver` 48 kHz baseband), store of calls/talkgroups/system/link stats.
* `components/sdr/dvoice/`: panel under Decode ("Digital"): protocol selector (Auto + forced), source/channel
  offset (linked to the shared frequency control), **call log** (time, protocol, slot, talkgroup label/id, source
  id, duration, BER, encryption badge), **live call card**, **talkgroup list** (hold/skip toggles, labels import),
  **link meters** (sync lamp, BER, FEC, SNR), **vocoder status with licence note**, recording toggle + export,
  settings sheet (follow policy, hang time) using `components/sdr/sheet.tsx`, the **legal disclaimer**, and an
  explicit "Encrypted - not decoded" state. Mobile: single column, 44 px targets, sheets for settings, call
  details as a bottom sheet. Works only against the API (shows the existing first-run connection card text when
  unreachable).
* Edits outside its own files (WP-H only): one Decode-tab entry in `control-rail.tsx`; a clearly marked CSS block
  at the end of `receiver.css`; the upload hook in `radio-controller.ts` (additive, behind a method
  `setDigitalVoiceTap(fn | null)` called from the existing audio/IQ tap points).

---------------------------------------------------------------------------------------------------

## 10. Work packages (strictly disjoint ownership)

Dependency order: **contracts (done) -> A, B, F in parallel -> C, D, E (use A/B stubs from day 1; integrate
as A/B land) -> C2 -> G -> H -> I.** Everything can start now because every interface is already importable.

| WP | Owns (files) | Inputs -> outputs | Depends on | Difficulty |
| --- | --- | --- | --- | --- |
| **A: FEC library** | `dvoice/fec/*`, `tests/test_dvoice_fec_*.py` | per section 6.1. Milestone **A1** (needed first by C/D): CRC specs, Hamming, Golay, P25 NID BCH, interleave tables, whitening. **A2:** Reed-Solomon, Viterbi, P25/DMR trellis, BPTC. | none | M-H (RS + trellis are the long poles) |
| **B: DSP front end + sync + test modulator** | `dvoice/dsp/*`, `dvoice/sync/*`, `dvoice/testing/modulator.py`, `testing/channel.py` (the `modulate`/`impair` bodies in `testing/base.py` move here), `tests/test_dvoice_dsp*.py` | complex 48 kHz -> `SymbolBlock`; `SyncCorrelator`; `AutoDetector`; synthetic IQ generation | none | H |
| **C: P25 Phase 1 + P25 trunking** | `protocols/p25p1/*`, `trunking/p25.py`, `testing/p25p1_encoder.py`, `tests/test_dvoice_p25p1*.py` | `SymbolBlock` -> NID/DUID, HDU, LDU1/LDU2 (IMBE frames, LC, ES), TDU/TDULC, TSBK (iden_up, grants, RFSS/site/network status, adjacent), PDU basic; `Call`/`SystemInfo`/`TrunkEvent`; `P25TrunkController` | A1, A2(RS, trellis), B (stubs ok) | M-H |
| **C2: P25 Phase 2** | `protocols/p25p2/*`, `testing/p25p2_encoder.py`, `tests/test_dvoice_p25p2*.py` | 2-slot TDMA: ISCH, scrambling (WACN/SysID/NAC), SACCH/FACCH, 4V/2V voice, MAC PDUs; AMBE+2 frames to the vocoder layer | C, A, B(CQPSK), F | H |
| **D: DMR** | `protocols/dmr/*`, `trunking/dmr.py`, `testing/dmr_encoder.py`, `tests/test_dvoice_dmr*.py` | BS/MS sync, CACH/TACT, slot type (Golay 20,8), colour code, voice superframe A-F with **embedded LC (BPTC)**, voice LC header/terminator (RS 12,9), CSBK (Tier III grants/announcements), data headers+blocks (trellis, CRC), two slots independent; privacy/encryption indicators; `DmrTrunkController` | A1, A2 (BPTC, trellis, RS), B | H |
| **E: NXDN + D-STAR + YSF + M17** | `protocols/{nxdn,dstar,ysf,m17}/*`, `testing/{nxdn,dstar,ysf,m17}_encoder.py`, `tests/test_dvoice_{nxdn,dstar,ysf,m17}*.py` | NXDN48/96: FSW, LICH, SACCH, FACCH1/2, UDCH, voice, scrambling; D-STAR: header (FEC+CRC), voice+slow data, callsigns; YSF: FICH (Golay), DN/VW/FR voice, callsigns; M17: LSF/stream/packet/BERT, Codec2 frames, encryption-type flag. **Splittable** into E1 (M17+YSF), E2 (NXDN), E3 (D-STAR) for parallel agents (each owns its subfolder). | A, B | M-H |
| **F: Vocoder layer** | `dvoice/vocoder/*`, `tests/test_dvoice_vocoder*.py`, plus (cleanup, additive files only) `apps/web/src/lib/sdr/p25/vendor/mbe/{LICENSE,NOTICE.md}` | `VocoderFrame` -> PCM; backends per section 7; ctypes loaders with known-answer vectors that auto-skip when the library is absent; DVSI serial packet codec tested against a fake serial port; Codec2 via ctypes/pycodec2; `capabilities()` text | none | M |
| **G: Engine, API, recorder, integration** | `dvoice/engine.py`, `api.py`, `recorder.py`, `tests/test_dvoice_engine*.py`; **shared edits:** `main.py` (router include), `requirements.txt`, `hackrf.py` (`add_tap`), `sdr.py` accessor | `ChannelWorker`s, queues, dispatch/auto-detect, trunk follower (second channelizer), events/audio fan-out, STT hook (`subscribe_audio` -> `stt.py` session per call), WAV recording + sqlite index, REST/WS | contracts; real modules as they land (use `FakeDecoder`s until then) | M-H |
| **H: Web UI** | `lib/sdr/dvoice/{client,store}.ts`, `components/sdr/dvoice/*`, `tests/` (node), **shared edits:** `control-rail.tsx` (one entry), end block of `receiver.css`, `radio-controller.ts` upload hook | section 9 | contracts (`types.ts`), G's WS (mock until ready) | M |
| **I: QA, security, perf, licence audit** | read-only on all; owns `tests/test_dvoice_fuzz.py`, `tests/test_dvoice_perf.py`, updates to this doc | fuzz/robustness, resource bounds, numba on/off parity, RTF numbers, header/licence scan (no copied dsd-neo code or foreign headers), encryption-policy verification (grep + tests), UI disclaimers, full-suite run, final report | all | M |

**Shared-file edit policy:** `main.py`, `requirements.txt`, `hackrf.py`, `sdr.py` — WP-G only. `control-rail.tsx`,
`receiver.css`, `radio-controller.ts` — WP-H only (other agents may be editing these files for unrelated work;
re-read right before editing, keep diffs tiny). New pip dependencies: only optional ones (`numba` is already
installed; add `pycodec2`/`pyserial` as *optional extras* in a comment block, not hard requirements).

### Acceptance tests per package (minimum bar)

* **A:** every function in 6.1 implemented; KATs + algebraic property tests above; `golay_24_12_decode` >= 200k words/s; trellis block < 1 ms (numba); numba/numpy parity test.
* **B:** loopback zero symbol errors at >= 15 dB SNR for C4FM and 4FSK, CQPSK lock at 12 dB; works across the tolerance list in 6.3; polarity inversion detected; block-size independence; `SyncCorrelator` finds every synthetic sync word with up to `max_errors` errors and no false positives in 1e6 random dibits (document the measured rate); real-time factor <= 0.15 per channel.
* **C/C2/D/E:** for each protocol: (1) clean synthetic call round-trips all fields (ids, slot, kind, emergency, encryption info), (2) `voice_frames` count right, (3) encrypted call flagged and not passed to a vocoder, (4) at the SNR where the front end is error-free, no call is dropped; BER sweep plot/table in the test log, (5) garbage-in robustness (random dibits for 10 s: no exceptions, bounded state), (6) spec cross-check list in the test docstring, (7) trunking: synthetic CC with iden/grant -> correct `TuneRequest` incl. channel-number-to-frequency maths, hang/return behaviour, skip/hold.
* **F:** capabilities show accurate availability and licence notes; with no backend present nothing crashes; loaders pass KATs when a library exists.
* **G:** synthetic end-to-end (encoder -> modulate -> impair -> `feed_iq`) yields `call_start/update/end` and audio frames (with a fake vocoder); upload path equals server path; overrun produces `input_overrun`; engine survives decoder exceptions (reports `error`, resets that channel); WS tests with a real uvicorn on your own port.
* **H:** `tsc`, eslint (no new errors), node tests with a fake WS; Playwright desktop + 390x844 screenshots viewed; disclaimer visible; encrypted state shown; vocoder-missing state shown with its licence note.
* **I:** fuzz suite green; headers scan clean; no key-handling surface; numbers reported.

---------------------------------------------------------------------------------------------------

## 11. Risks

1. **No real captures.** Everything is validated on synthetic signals built from the specs. Real-world timing,
   fading and RF impairments will need tuning; the plan asks the user for recorded IQ of an active P25/DMR/NXDN
   system (Save IQ already exists) as gated tests.
2. **Spec access.** TIA-102 documents are partly paywalled; ETSI specs are free. Where a spec is unavailable, the
   WP says so in its report and uses the published interface behaviour/tables it can verify, marking unverified
   parts. A decoder that cannot be validated against a spec must not claim support in `/dvoice/capabilities`.
3. **Python real-time performance.** Mitigated by numba kernels + block processing + per-channel threads;
   measured by WP-B/WP-I; fallback is a cffi C extension for one or two kernels.
4. **Vocoder legality/availability** (section 7). Without a backend there is no audio for AMBE+2 protocols;
   the product still delivers call/talkgroup/encryption metadata.
5. **Two sources of truth for P25.** The browser P25 engine and the new server engine overlap until WP-H
   decides; no shared code, so no coupling.
6. **GPU/CPU contention** with the speech-to-text service and the CNN: bounded by per-channel workers and
   `max channels` in `EngineConfig` (default 2).
7. **Clean-room discipline.** Independent agents must not read dsd-neo source (section 0.1). WP-I's header scan
   is the safety net.

---------------------------------------------------------------------------------------------------

## 12. Contract file locations

* `apps/api/sigsense_api/dvoice/events.py` (event dataclasses, envelope, audio framing)
* `apps/api/sigsense_api/dvoice/protocols/base.py`, `dsp/types.py`, `sync/patterns.py`, `vocoder/base.py`,
  `vocoder/registry.py`, `trunking/base.py`, `engine.py` (config + interface), `api.py` (route table),
  `fec/*.py` (signatures), `testing/base.py`
* `apps/web/src/lib/sdr/dvoice/types.ts`
* `apps/api/tests/test_dvoice_contracts.py`
