# Server-side speech decoder: research and decision

Goal: near-real-time, high-quality transcription of demodulated radio voice (NBFM/AM/SSB, ~3 kHz audio, PTT bursts,
open-squelch hiss, callsigns and numbers) running **locally** on the API server, usable from desktop and phone, for
any source (HackRF, RTL-SDR, OpenWebRX). Machine: RTX 4070 12 GB, 32 cores, 62 GB RAM.

## Decision

**faster-whisper (CTranslate2) with `large-v3-turbo`, fp16 on the GPU**, behind a streaming layer we wrote
(`apps/api/sigsense_api/stt.py`): VAD endpointing, greedy *partials* while a transmission is in progress, beam-search
*finals* with word timestamps when it ends, a radio vocabulary prompt, and a hallucination filter. CPU fallback is
`distil-small.en` int8 with a short prompt. Nothing leaves the machine.

## Candidates

| Approach | What the evidence says | Install risk here | Verdict |
|---|---|---|---|
| **faster-whisper, large-v3-turbo** | Turbo is commonly reported ~4x faster than large-v3 for 1-2 WER points ([faster-whisper benchmark thread](https://github.com/SYSTRAN/faster-whisper/issues/1030)). **Measured here:** 2.5-3.3% WER offline, 25x real time, 2.2 GB VRAM. Model was already in the local HF cache (no download). | Low: adds ctranslate2, onnxruntime, av, tokenizers; torch unchanged (checked with `pip --dry-run`). CTranslate2 runs on CUDA beside torch's CUDA 13. | **Chosen** |
| faster-whisper, large-v3 | Marginally fewer errors than turbo in published numbers, ~2x the size and slower; some reports of more hallucination on silence. Not downloaded (3 GB, and turbo already within noise on our corpus). | Same | Not tested |
| faster-whisper, distil-large-v3 | English-only. **Measured: 7.6 / 5.1 / 3.6%** vs turbo 3.3 / 2.9 / 2.5% at 10 / 15 / 25 dB. Worse on noisy audio. | Same (1.5 GB download, done for the comparison) | Rejected |
| distil-small.en | **Measured: loops ("we're, we're, we're...") with a long prompt (WER > 100%)**; 14.4 / 6.9 / 4.3% with a one-line prompt. 0.4 GB VRAM; 4x real time on CPU int8 (3.9% at 15 dB). | Cached already | **CPU fallback only**, with the short prompt |
| turbo, int8_float16 | **Measured: 3.6 / 4.4 / 1.8%** (within noise of fp16), 1.07 GB VRAM. | Same | Good option if VRAM is needed (`SIGSENSE_STT_COMPUTE=int8_float16`) |
| whisper-streaming / LocalAgreement-2 | Confirms the longest common prefix of consecutive overlapping decodes; ~3.3 s average latency on an A40 in the paper ([Machacek et al.](https://arxiv.org/abs/2307.14743)), and it can commit words prematurely. Designed for continuous speech. | n/a | **Not used.** Radio voice arrives as endpointed PTT bursts, so we decode the burst directly: greedy partials for liveness, one beam-search final at the end. |
| NVIDIA Parakeet-TDT / Canary (NeMo) | Strong on clean and meeting audio in vendor and blog comparisons (e.g. ~6.3% vs ~7.4% average WER on the Open ASR leaderboard); [noisy and narrowband rankings conflict between sources](https://openwhispr.com/blog/parakeet-vs-whisper-vs-nemotron). No narrowband-radio benchmark found. | **High:** NeMo pins torch/CUDA stacks and would risk the existing install. | Not tested; revisit in an isolated venv |
| sherpa-onnx streaming zipformer | True streaming, tiny, CPU-friendly; lower accuracy than Whisper-class models in general. | Medium (new runtime) | Not tested |
| Moonshine | Edge-oriented; in one third-party test 7.1% vs 4.9% for Whisper large-v3 on mostly clean clips. | Medium | Not tested |
| Vosk (Kaldi) | Old acoustic models, weak on noisy speech and names/numbers. | Low | Not tested |
| WhisperX | Adds forced alignment/diarisation for offline files; faster-whisper already provides word timestamps. | Medium | Not needed |

No published benchmark covers 12.5 kHz NBFM voice with FM-threshold noise, so the choice is decided by our own
measurements below, not by literature.

## Test method

* **Speech:** 30 radio-style phrases (callsigns, numbers, NATO alphabet, GMRS chatter; ~330 words) synthesised with
  Kokoro TTS (2 voices) via `apps/api/scripts/stt_corpus.py`.
* **Channel:** 300-3400 Hz band-limit + limiter -> NBFM (2.5 kHz deviation) at 2 MS/s, carrier 100 kHz off centre ->
  AWGN at 10 / 15 / 25 dB SNR in a 12.5 kHz channel -> **the repo's own demodulator** (`sigsense_api/demod.py`, NBFM)
  -> 48 kHz audio -> 16 kHz.
* **Two kinds of audio:** one file per transmission (offline scoring), and a ~77 s continuous stream per SNR with the
  carrier **keyed off between transmissions**, so the demodulator outputs open-squelch hiss in the gaps (this is what the
  HackRF path actually delivers). The hiss is *louder* than the speech (RMS ~0.45 vs ~0.10).
* **Streams:** stream A uses phrases 0-13 (used while tuning the front end); **stream B uses the other 16 phrases and was
  never tuned against** (held out).
* **Scoring:** WER with numbers normalised to spoken form (`10-4` = `ten four`, `KAB1234` = `kab one two three four`).
  Streaming is scored on what a user would see: finals assigned to the transmission they overlap.
* **Real time:** streaming runs fed audio at real-time pace in 100 ms chunks.

## Results

### Which model (offline, per transmission, 30 clips per SNR; band-pass + AGC front end)

| Config | WER 10 dB | 15 dB | 25 dB | RTF | VRAM |
|---|---|---|---|---|---|
| **turbo fp16, beam 5, radio prompt (shipped)** | **3.3%** | **2.9%** | **2.5%** | 0.04 | ~2.2 GB |
| turbo, greedy | 4.0% | 4.4% | 2.5% | 0.04 | 2.1 GB |
| turbo, **no prompt** | 6.2% | 4.7% | 4.0% | 0.04 | 2.1 GB |
| turbo, raw audio (no band-pass/AGC) | 4.0% | 2.9% | 2.5% | 0.04 | 2.1 GB |
| turbo int8_float16 | 3.6% | 4.4% | 1.8% | 0.037 | 1.07 GB |
| distil-large-v3 | 7.6% | 5.1% | 3.6% | 0.04 | 2.0 GB |
| distil-small.en, full prompt | >100% (loops) | >100% | >100% | 0.10 | 0.4 GB |
| distil-small.en, short prompt | 14.4% | 6.9% | 4.3% | 0.012 | 0.4 GB |
| distil-small.en, no prompt | 14.8% | 7.9% | 5.1% | 0.012 | 0.5 GB |

Reading it honestly: 330 words per column means one word is 0.3 points, so **differences under ~1 point are noise**
(e.g. int8 vs fp16, band-pass vs raw). The clear effects are the radio **prompt** (turbo 6.2% -> 3.3% at 10 dB),
turbo vs distil-large-v3, and the **catastrophic** long-prompt failure of the small model.

### Streaming front end (stream scoring, 1 session, `partials` off, fast replay)

What actually mattered was **when an utterance starts**. A first version scored 28.7% WER at 10 dB on stream A even though
the same speech scored 3.3% offline: the VAD confirmed speech late and the first words ("Kilo", "Roger that", "Stand by
one moment while I") were cut off.

| Front-end config | Stream A 10 dB | 15 dB | Held-out B 10 dB | 15 dB | 25 dB |
|---|---|---|---|---|---|
| VAD on band-passed audio, contiguous soft look-back, hiss gate (first version) | 28.7% | 15.4% | 24.1% | 7.5% | 5.3% |
| band-passed VAD, **whole 1.2 s look-back**, no gate | 11.2% | 4.2% | 6.0% | 3.0% | 3.0% |
| band-passed VAD, whole look-back, hiss gate | 9.8% | 4.9% | 8.3% | 3.0% | 3.8% |
| **max(raw VAD, band-passed VAD), whole look-back, no gate (shipped)** | **4.2%** | **4.2%** | **5.3%** | **3.0%** | **3.0%** |
| max VAD, whole look-back, hiss gate | 6.3% | 3.5% | 6.0% | 3.0% | 3.0% |
| lower VAD threshold (0.4) + 2-frame onset, band-passed VAD (held-out B) | 6.3% | 2.8% | 7.5% | 3.0% | **17.3%** |
| same, max(raw, band-passed) VAD | 25.2% | 35.0% | not run | not run | not run |

Findings: sending the **whole look-back** to the model is the big win (24% -> 5% at 10 dB); gating the hiss does not help;
an aggressive onset threshold is unstable (17.3% at 25 dB on held-out audio) and was rejected. A separate frame-level
measurement showed band-passed audio gives far fewer false triggers in the hiss (0.0% vs 5% at threshold 0.3) while raw
audio recovers slightly more speech, which is why both detectors are combined.

### Streaming latency and concurrency (shipped config, real-time pace, 0 false finals in any run)

Held-out stream B, one session each:

| SNR | WER | First partial after speech starts (p50 / p90) | Final after speech ends (p50 / p90 / max) |
|---|---|---|---|
| 10 dB | 5.3% | 1.44 / 1.92 s | 0.77 / 1.11 / 1.68 s |
| 15 dB | 3.0% | 1.37 / 1.67 s | 0.77 / 0.90 / 1.44 s |
| 25 dB | 3.0% | 1.35 / 1.53 s | 0.80 / 1.27 / 2.19 s |

Three simultaneous sessions (streams at 10 / 15 / 25 dB sharing the one model; stream A, used in tuning):
WER 2.8 / 2.8 / 0.7%; final p50 0.84 / 0.81 / 0.97 s, p90 1.3-1.6 s, max 2.03 s; first partial p50 1.4-1.6 s; peak VRAM
2.35 GB total (the model is shared); decode queue never exceeded 4.

"Final after speech ends" is dominated by the 0.7 s endpoint silence that decides a transmission is over
(`endpoint_ms`, configurable 250-3000 ms); the decode itself is ~0.1-0.2 s. First partial waits for ~0.5 s of voiced
audio and one 0.8 s interval, so text starts appearing ~1.4 s into a transmission.

### Bugs found by measuring (all fixed, all covered by tests)

1. **Late/clipped starts**: see above.
2. **Whisper repeating a whole sentence** inside one final ("...mile marker 42. All units be advised, ...mile marker
   42."). Fixed by `collapse_phrase_repeats`, which only collapses a repeat when the audio is too short to contain it
   (a genuine "say again" is kept). Took one stream from 11.2% to 0.7%.
3. **"Thank you." on silence**: the real model returns this on digital silence (a known Whisper artefact); filtered by
   `is_hallucination`, asserted by the GPU integration test.
4. **Small-model prompt loops** (above): the prompt style now depends on the model (`SttService.prompt_style`).
5. **Mode switching crashed with no browser DSP** (`Scheme "NBFM" was not registered`): an existing bug in the HackRF /
   iOS path, found by the browser test; fixed in `radio-controller.ts`.

## Front end choices

| Item | Decision | Why |
|---|---|---|
| Resample | stateful FIR + interpolation to 16 kHz | no seams between chunks; tested at 48/44.1/22.05/8 kHz |
| Band-pass 300-3400 Hz | on (model input) | cheap; offline effect within noise, kept as a safe default for wideband sources |
| VAD | Silero (bundled with faster-whisper), own streaming wrapper, run on raw **and** band-passed audio, max of the two | faster-whisper's wrapper resets state per call |
| Level | gain from voiced frames, capped at +24 dB and by peak | open-squelch hiss is louder than speech, so peak normalising scales speech down |
| Hiss gate | off | measured no gain |
| Noise suppression (RNNoise / DeepFilterNet) | **not implemented** | adds a dependency; Whisper-turbo handled 10 dB without it. Worth testing on real recordings |
| Endpoint | 0.7 s silence, 28 s hard cap; 1.2 s look-back | see tables |
| Prompt | radio glossary (+ user hints + last finals) for large models; one line for small models | prompt cut 10 dB WER from 6.2% to 3.3%, but kills small models |
| Decoding | partials: greedy, last 14 s; finals: beam 5, word timestamps, `condition_on_previous_text=False`, no-speech 0.6, logprob -1.0, compression 2.4 | |
| Hallucination filter | known sign-off phrases on short/low-confidence bursts, near-pure repetition, low-confidence no-speech, word and sentence repeat collapse | |
| Language | default English (faster and more accurate on this traffic); selectable / auto-detect | |

## Architecture

```
HackRF path (phone uploads nothing):
  /live/audio  (demod.py, 48 kHz)  --tap-->  StreamingSession --events--> same /live/audio socket (JSON text)
Any other source (RTL-SDR, OpenWebRX, demo, ...):
  browser audio tap -> downsample 16 kHz int16 -> /transcribe (binary frames) -> StreamingSession -> JSON events
StreamingSession: resample -> band-pass -> VAD (x2) -> utterance buffer -> priority queue -> ONE shared model on ONE worker
  finals (priority 0) run before partials (priority 1); one partial in flight per session.
```

Endpoints (all additive; `/live` and `/live/audio` audio contracts unchanged):

* `GET /transcribe/status`, `POST /transcribe/load`
* `WS /transcribe`: client `{"type":"config", language?, prompt?, sample_rate?, source_label?, frequency_hz?, mode?, endpoint_ms?, ...}`
  then binary little-endian int16 PCM; also `{"type":"meta",...}`, `{"type":"flush"}`. Server sends
  `{"type":"status","state":"loading|ready|error"}`, then `{"type":"partial"|"final","id","text","start_s","end_s","words"?,"confidence","language","latency_ms",...}`;
  a final with `"dropped": true` retracts a partial.
* `WS /live/audio`: client may send `{"type":"transcribe","enabled":true, language?, prompt?, frequency_hz?, mode?}` (and `transcribe_meta`);
  server adds `{"type":"transcript_status",...}` and the same partial/final events as text frames.

Browser: `RemoteTranscriptionEngine` (same interface as the on-device engine) + `TranscriptionHub` (Auto: server if
`/transcribe/status` says available, else on-device; auto-falls back if the server engine errors) + the Speech panel.

## Risks and what is not verified

* **The speech is synthetic (Kokoro TTS), not human radio operators.** Real voices, accents, clipping, over-deviation,
  squelch tails, QRM and fading are not represented. Expect real-world WER above these numbers.
* **The channel is simulated** (AWGN + FM, no fading, no adjacent-channel interference, no multipath, no real HackRF
  front-end overload). The end-to-end browser test used a fake `hackrf_transfer` replaying simulated IQ, not hardware.
* Small corpus: 30 phrases (~330 words) and 14-16 per stream. Differences under ~1 point are noise. Stream A was used to
  tune the front end; only stream B is held out.
* The radio glossary biases output: with it, "unit twelve" was once heard as "KAB12". Vocabulary hints help far more than
  they hurt here (6.2% -> 3.3%), but it is a bias, and users can clear it ("Vocabulary hints" is additive; the built-in
  glossary can be disabled with `use_glossary: false` in the config).
* Numbers/callsigns are scored by a spoken-form normaliser; remaining errors include `fifteen` -> `50` style misses.
* SSB/AM/CW and non-English were not evaluated. WBFM broadcast audio was not evaluated.
* iOS Safari WebSocket binary uploads and background throttling were not tested on a device.
* Latency was measured on an otherwise idle GPU; other GPU jobs will add to it.
* The first transcription after the API starts loads the model (~2 s from cache). `SIGSENSE_STT_PRELOAD=1` preloads it.

## How to run

* Install: `pip install -r apps/api/requirements.txt` (adds faster-whisper). Dev/eval only: `apps/api/requirements-dev-stt.txt`.
* Model: `SIGSENSE_STT_MODEL` (default `deepdml/faster-whisper-large-v3-turbo-ct2`), `SIGSENSE_STT_COMPUTE` (`float16`
  or `int8_float16`), `SIGSENSE_STT_CPU_MODEL`, `SIGSENSE_STT_PRELOAD=1`.
* Reproduce: `python apps/api/scripts/stt_corpus.py --out DIR` then
  `python apps/api/scripts/stt_eval.py offline|stream --corpus DIR`.
