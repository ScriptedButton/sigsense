# Adaptive backend signal cleaning

The shortwave finder automatically starts the server cleaner once it confirms
speech. The browser sends raw demodulated mono audio, independently of listening
volume and browser enhancement. The FastAPI backend continuously returns cleaned
PCM; local listening switches to that audio automatically. The dedicated headless
receiver publishes the same stream for other browsers without opening another
RTL connection. **Listen live** also starts cleanup after transferring the receiver
to the listening browser, and keeps optimizing until Stop or a manual dial/mode/
bandwidth change takes ownership.

**Clean this signal on server** (Find something to hear, on Radio and Stations) starts the same cleaner on
demand for whatever AM signal you are tuned to, 2.3–30 MHz, on an RTL-SDR source,
without running the finder. It uses the LAN worker when one is configured, with
the usual local fallback. **Stop server cleaning**, retuning, or changing mode or
bandwidth ends it. It is unavailable while the automatic finder is running.

Radio and Stations show **Adaptive signal cleaning**, including the selected
profile, optimization count, speech-spectrum similarity, measured quiet-interval
noise reduction, and active whistle filters. **Listen cleaned**, **Compare original**,
and **Stop monitor** provide a live, aligned A/B with independent monitor volume.
The normal receiver is muted during A/B, preventing doubled audio. Backend output
is played directly, bypassing additional browser enhancement. An unavailable or
stalled backend restores receiver playback and reports/retries the failure.

## Processing

1. Stateful anti-alias filtering and sample-clock conversion to 16 kHz. The clock
   and filter history persist across arbitrary network packet boundaries.
2. Repair isolated large impulsive outliers, preserving ordinary modulation peaks.
3. By default, local RNNoise in a private Node worker using the already-installed
   `@jitsi/rnnoise-wasm` model. An optional authenticated [LAN GPU worker](lan-gpu-processing.md)
   can run a larger neural model, with continuously warm local fallback.
4. Delay alignment of original and neural branches, followed by linear-phase voice
   filtering, persistent narrow-tone rejection, and overlapping Wiener suppression.
5. Bounded loudness stabilization and peak limiting. Noise estimates have bounded
   influence, and gain cannot increase without limit during a deep fade.

Every four seconds, the optimizer evaluates five processing profiles on the exact
same recent source and neural audio. Raw Silero VAD identifies speech and quiet
intervals. Candidates must preserve sufficient speech energy, high-band content,
and spectral similarity. Silence and severely attenuated speech are ineligible.
The score uses gain-normalized quiet-interval noise attenuation with distortion
penalties; stronger suppression has no unlimited reward. Hysteresis prevents
constant switching, and warmed filters crossfade when settings change. Unsafe
settings roll back to Natural. Continuous speech retains an eligible established
profile and its notches until fresh quiet intervals support a new noise estimate.
Fresh streams start with Natural and revalidate remembered processing settings
against current reception before applying stronger suppression.

Scores compare processing choices within one observed window. They are not
intelligibility measurements, station verification, or ground-truth SNR estimates.
The original receiver audio remains available for judging the actual result.

## Reception feedback

Fresh RF measurements accompany the audio. Three consistent observations are
required before applying a correction. ADC clipping can lower gain by 3 dB;
strong nearby carriers can narrow AM to 4 kHz; a stable carrier peak can correct
tuning by at most 250 Hz per adjustment, within 500 Hz of the selected carrier.
Stale spectra and featureless noise do
not produce tuning commands. Changes are limited to the receiver owned by the
finder or the explicitly selected live finding. Manual tuning cancels ownership
before delayed feedback can change the user's dial.

The finder continues checking **raw** audio for speech. Cleaner output is never
fed back into discovery, transcription taps, or the neural acceptance test.

## Operation and limits

The API hosts `/autofinder/cleaner/source/{id}` and
`/autofinder/cleaner/listen/{id}` WebSockets, plus a read-only
`/autofinder/cleaner/status/{id}` route. The web app's existing API proxy carries
these connections. Each stream uses mono PCM16; output is 16 kHz. Retuning starts
a fresh stream, discarding old audio/filter history. Sources stop on receiver
stop/disconnect and retry with visible errors if the backend becomes unavailable.

Up to two sources and eight monitors per source are supported. Source history is
bounded to six seconds and listener queues to three packets. Slow listeners drop
old packets instead of accumulating delayed audio. Node workers terminate when
their source disconnects. Audio is not stored by normal operation; up to 200
frequency profiles are retained privately in `.runtime/audio-cleaner/profiles.json`.
Demo sessions do not update those profiles. The service's saved findings include
the most recent eligible cleanup measurements.

The API process needs Node.js and the web workspace dependencies. Set
`SIGSENSE_CLEANER_NODE` to the Node executable if it is not on the API service's
PATH. The supplied API systemd unit specifies the installation's local Node path.
`SIGSENSE_CLEANER_DIR` overrides the profile directory. If RNNoise cannot start,
the UI reports the failure and the backend offers spectral cleanup without neural
profiles. The finder itself still requires its normal local Silero detector.

Deploy the API and web changes together, then restart the API and refresh/restart
the dedicated receiver browser. Existing production processes do not pick up new
Python routes or browser controllers automatically. Default operation adds no
cloud model, microphone input, transmitter operation, or new model dependency.
Optional LAN GPU processing has separate worker-only model dependencies and setup.

## Verification

```bash
PYTHONPATH=apps/api .venv/bin/python -m unittest discover -s apps/api/tests -p test_audio_cleaner.py -v
pnpm --dir apps/web exec tsx --test src/lib/sdr/autofinder/cleaner.test.ts
pnpm test:autofinder-service
pnpm --dir apps/web exec tsx scripts/autofinder-service-browser-smoke.ts
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/verify_audio_cleaner.py
```

The browser smoke uses a production build on port 3001 and synthetic receiver/
cleaner streams. It exercises actual playback of cleaned/original PCM, raw audio
upload after live handoff, and rendering returned optimization state. The backend
WebSocket test runs the real local RNNoise worker and optimizer and checks source
return audio, monitor audio, repeated optimization, and disconnect cleanup.

The replay script uses the existing speech fixture documented in
`hf-audio-processing.md`, adds seeded hiss, hum, a whistle, impulses and fading,
and runs the production streaming DSP and actual RNNoise. It exports aligned
before/after/reference WAV files and a report under
`.runtime/audio-cleaner-validation/`. It measures SI-SDR against the known clean
speech, correlation, measured latency and processing cost. This is a controlled
DSP test, not a claim of a newly received intelligible station. Successful DSP
cleanup cannot reconstruct information absent from the captured signal.

On the current host, the controlled ten-second replay improved SI-SDR from
0.64 to 8.86 dB, with speech correlation 0.941 and 94 ms of measured DSP latency.
Processing took 2.77 seconds for ten seconds of audio. Network and browser audio
buffers add transport/playback delay beyond the DSP figure. These figures apply
to the seeded fixture and do not establish intelligibility on a live RF signal.
