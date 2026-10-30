# HF display and playback processing

The browser RTL pipeline (USB, rtl_tcp and demo) now calculates overlapping
spectra continuously from incoming IQ, rather than taking just the latest FFT
when the screen refreshes. Blackman-windowed FFTs overlap by 50%; squared
magnitudes are averaged over approximately 160 ms (bounded to 48 transforms).

Zooming requests a second display spectrum. It mixes the visible region to
baseband, FIR low-pass filters it, and decimates by a power of two. With a
1.024 MS/s source and a 100 kHz view, the display FFT uses 128 kS/s and 8,192 bins:
15.625 Hz spacing. Audio tuning, raw IQ identification and the panoramic spectrum
keep their original capture clock. Each displayed row retains its own source
rate and DDC center, so axes and history map correctly while panning/zooming.
The wide spectrum is used while a newly selected detail window warms up.

The scope toolbar reports actual Hz/bin and the FFT average count. HF view
selects automatic levels, a 100 kHz span, and slow scroll. Display settings also
exposes Black level and Range; changes recolor stored history. FFT bin spacing
is not the same as equivalent noise bandwidth, nor does the display create
signals absent from the antenna input.

## Replay verification

```
pnpm --dir apps/web exec tsx scripts/hf-spectrum-compare.ts
.venv/bin/python packages/ml/scripts/verify_hf_spectrum.py
```

The seeded two-carrier-plus-noise fixture is quantized to rtl_tcp's unsigned
8-bit IQ and saved under `data/processed/hf-waterfall/`. Old snapshot, continuous
wideband and decimated display paths replay exactly the same bytes. The script
also exports a stereo PCM IQ WAV for loading in another SDR application's file
source. An existing real capture can be replayed with `--iq=/path/file.cu8`
plus `--rate=...` and `--center=...`.

On the generated fixture, wideband noise scatter fell from 5.53 to 0.65 dB;
the detail spectrum has 15.625 Hz spacing and resolves carriers 62.5 Hz apart.
An independent SciPy FIR/NumPy periodogram calculation agrees within 0.00001 dB
in the detail passband. Runtime on this host was about 242 ms per second of IQ
for both spectra together. See `hf-spectrum-validation.json`. This is a DSP
correctness/visibility test, not a captured-antenna reception benchmark. No
interactive SDR++ comparison is claimed; its importable IQ file is supplied.

## Received audio

Radio → Audio & RF contains separate Audio noise reduction and Voice isolation
controls. Settings persist locally. Both start off, providing exact original
playback when bypassed.

* Noise reduction: optional low/medium/high spectral suppression of steady hiss,
  with overlapping reconstruction and conservative noise tracking.
* Voice isolation: locally loaded RNNoise speech enhancement using
  `@jitsi/rnnoise-wasm` 0.2.1. It needs no microphone permission, server inference,
  external model service, or secure-context AudioWorklet. Incoming PCM rates
  are converted to RNNoise's 48 kHz/480-sample frames and returned at their
  original playback rate. The first enable loads the bundled model; a load
  failure is shown in the control panel and disables isolation.

Enhancement applies to local RTL playback, remote/HackRF PCM, P25 and the digital
voice player. Decoder and transcription taps retain their original samples.
Each player has independent filter history and releases model state on close.
Speech isolation can suppress CW, music, data tones and weak speech; it does not
separate simultaneous speakers or recover information lost at reception.

Audio tests cover exact bypass, non-mutation, chunk-boundary continuity,
stationary-noise attenuation, preservation of structured voice-band energy, and
neural processing at 8/12/44.1/48 kHz. A separate speech-plus-added-noise test
improved SI-SDR from 10.0 dB to 14.3 dB with medium reduction and 15.1 dB with
isolation; speech correlations were 0.982 and 0.985. Added delays were 10.7 and
30 ms respectively. These are controlled test results, not a guarantee for HF
speech. See `audio-enhancement-validation.json`.

For that optional comparison, decode the first eight seconds of the author's
`https://jmvalin.ca/demo/rnnoise/samples/clean.opus` sample to mono 48 kHz float32
at `data/processed/audio-enhancement/clean.f32`, then run:

```
pnpm --dir apps/web exec tsx scripts/audio-enhancement-compare.ts
.venv/bin/python packages/ml/scripts/verify_audio_enhancement.py
```
