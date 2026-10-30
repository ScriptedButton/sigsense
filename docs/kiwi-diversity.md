# KiwiSDR reference-guided reception

In **Stations → Shortwave explorer → KiwiSDR reference reception**, paste the
address of a nearby Kiwi receiver (and its optional receiver password). Start
your local receiver below 30 MHz first, then select **Connect reference**.
The public directory link lets you choose a receiver geographically near your
antenna; there is no automatic location lookup. Only connect receivers you are
permitted to use. A connection occupies a Kiwi audio/waterfall channel until you
disconnect, leave this panel, stop the local receiver, or encounter a failure.

The second receiver follows local frequency, demodulation and bandwidth. The
normal receiver connection is not replaced. The overlaid RF spectra share an
absolute frequency axis and each has its own median noise floor removed; their
levels are **not** calibrated/comparable absolute RF power. Narrow peaks are
preserved when reducing bins for the overlay.

Choose **Kiwi-guided cleanup** to hear reference-guided suppression, or
**Delayed original** for a sample-aligned A/B. **Normal audio** restores the
existing playback/cleaner. The comparison has about two seconds of alignment
buffer plus 32 ms of overlap/add delay. The main volume control applies. Turn
other independent audio monitors off to avoid hearing multiple players. Preview
mute ownership is separate from the existing cleaner monitor, so disconnecting
this feature does not release another monitor's mute.

## What the backend does

The browser's independent `KiwiClient` sends decoded reference PCM and taps
local pre-playback-cleanup PCM to `/api/kiwi-diversity`. The FastAPI WebSocket
`/kiwi-diversity` resamples both continuously to 16 kHz using a stateful
anti-aliasing resampler independent of the neural/LAN cleaner runtime. No new dependency, microphone, recording file,
public receiver connection, or persisted password is needed.

Every second, a normalized voice-band cross-correlation searches bounded
reference history for the same 1.5-second transmission segment. Silence, pure
tones, ambiguous repeated peaks and weak correlations cannot establish a match.
Three consistent lag estimates are required, with subsequent estimates tracking
independent sample-clock drift and fractional alignment. A retune creates a
fresh processing session; no reference history crosses frequencies/modes.

For verified aligned audio, a smoothed cross-spectral coherence mask attenuates
energy unique to the local reception, with approximately 10 dB maximum
per-bin attenuation. The mask operates on the **local** Fourier coefficients:
it never adds or substitutes remote audio. Instantaneous mismatch disables
suppression; a lost alignment, reference silence or stalled stream restores
delayed local passthrough. Playback failure restores normal local reception.

This is demodulated-audio diversity, not coherent RF beamforming or guaranteed
interference cancellation. Independent receivers do not share RF phase, clocks,
AGC, antenna response or propagation paths. Nearby receivers can also hear the
same interference, and neither can recover information absent at both antennas.
CW/carrier-only signals intentionally do not establish a broadband audio match.
The attenuation reading is mean filter attenuation, **not measured SNR gain**.

## Protocol and limits

- First text message: `{"type":"configure","localRate":48000,"referenceRate":12000}`.
- Input binary: one source byte (`0` local, `1` reference), then mono little-endian
  PCM16. Allowed rates: 8–48 kHz. Maximum one second per packet; real-time input
  rate is enforced with a bounded startup allowance.
- Output binary: interleaved 16 kHz PCM16 `[clean, original]` pairs for exact A/B.
  Text status includes match confidence, signed sample-timeline alignment offset,
  drift ppm and mean spectral attenuation. The offset is not propagation delay.
- Two concurrent processing sessions, 12 seconds of history per source, bounded
  upload buffers, timeout/disconnect cleanup, and no automatic public Kiwi retry.
- Browser mixed-content restrictions still apply: an HTTPS application needs a
  WSS-capable Kiwi endpoint or a trusted HTTPS/WSS reverse proxy. Passwords go
  directly to the selected Kiwi via the existing client, not to this DSP endpoint.

Run the API and web application normally after restarting them to load the new
route and component. This feature does not change or restart deployment services.

## Validation

```sh
PYTHONPATH=apps/api .venv/bin/python -m unittest discover -s apps/api/tests -p test_kiwi_diversity.py -v
cd apps/web
pnpm exec tsx --test src/lib/sdr/kiwi/diversity.test.ts
pnpm exec tsc --noEmit --incremental false
```

Deterministic DSP tests cover delay/polarity, unrelated transmissions, tones,
ambiguous periodic material, clock drift, 48 kHz local / 12 kHz Kiwi resampling,
aligned passthrough, zero-local/no-remote-insertion, local-whistle reduction,
reference loss, bounds and WebSocket validation. Client tests cover second-source
tuning, wire format, spectrum mapping, opt-in A/B, owned mute restoration, stale
callbacks, retuning, upload backpressure and channel cleanup. These tests do not
claim live on-air improvement; that requires comparing your actual antenna and
selected nearby receiver using the A/B control.
