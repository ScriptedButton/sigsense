# Receiver MCP server

SigSense exposes the browser receiver to MCP clients through a local, authenticated control path:

`MCP client ⇄ stdio ⇄ receiver MCP server ⇄ HTTP bearer auth ⇄ control broker ⇄ WebSocket ⇄ receiver page`

The MCP process never opens the SDR hardware itself and never evaluates arbitrary JavaScript. A receiver page must be open and connected to the broker. With one live page, `sessionId` is optional; with multiple pages it is required.

## Start

Start the web app and receiver-control broker, open the Receiver page, then configure an MCP client to launch:

```bash
pnpm receiver-control
# Or install deploy/systemd/sigsense-receiver-control.service as a user unit:
systemctl --user enable --now sigsense-receiver-control.service
```

The deployed broker listens on port 8787. The receiver automatically pairs through
the same-origin `/api/receiver-control` bootstrap after refreshing the page.
LAN HTTP is supported; `crypto.randomUUID()` is not required. HTTPS deployments
must provide a TLS WebSocket proxy for the broker.

```json
{
  "command": "pnpm",
  "args": ["--dir", "apps/web", "exec", "tsx", "server/receiver-mcp/index.ts"]
}
```

The server reads `.runtime/receiver-control.json`, written by the broker with mode `0600`. Overrides:

- `SIGSENSE_RECEIVER_BROKER_URL` — broker base URL, normally `http://127.0.0.1:8787`
- `SIGSENSE_RECEIVER_TOKEN_FILE` — bootstrap JSON path
- `SIGSENSE_RECEIVER_TOKEN` — explicit bearer token

The stdio server writes MCP frames only to stdout. Operational errors go to stderr.

## Tools

- `list_receivers`, `get_state`, `get_diagnostics`
- `connect`, `stop`, `tune`, `configure`
- `get_spectrum`, `get_signals`, `find_signal`
- `capture_audio`, `capture_iq`
- `get_schedule`
- `get_display`, `set_display`

Every tool validates bounds before contacting the browser. Broker errors are returned as MCP tool errors with compact text and structured error data. Audio capture returns an MCP audio content block when the browser supplies base64 WAV. IQ is never printed or returned inline: browser captures are limited to 2 MiB, written with mode `0600` below `.runtime/receiver-captures`, and returned as a file resource link. Callers cannot choose a write path.

Examples use the browser's exact setting names:

```json
{"tool":"configure","arguments":{"scheme":"AM","bandwidthHz":6000,"gainDb":0,"audioNoiseReduction":"off","voiceIsolation":false}}
{"tool":"tune","arguments":{"frequencyHz":6100000,"center":true}}
{"tool":"capture_audio","arguments":{"durationMs":3000,"stage":"raw"}}
{"tool":"capture_audio","arguments":{"durationMs":3000,"stage":"playback"}}
{"tool":"capture_iq","arguments":{"durationMs":250,"maxSamples":262144}}
```

Audio metadata includes the actual receiver state and clipping/RMS/peak metrics.
Playback captures contain the post-enhancement, post-volume signal heard by the
listener; raw captures contain demodulated PCM before playback enhancement.
IQ is interleaved complex float32 little-endian, saved with `.cf32`, with native
sample rate and hardware center frequency. Retunes invalidate captures rather
than combining different sources. Capture links can be read using MCP resources.
Spectrum bins are explicitly ordered by increasing frequency with `startHz`
and `binHz`; neither the waterfall color mapping nor cosmetic noise filter is used.

Resources `receiver://current/state` and `receiver://current/diagnostics` reflect the sole live receiver. The `troubleshoot_reception` prompt guides an agent through mode, bandwidth, clipping, gain, squelch, and interference without automatically enabling neural processing.

## Safety and capture limits

Frequency, bandwidth, gain, volume, squelch, PPM, timeout, spectrum bins, and capture duration are bounded. Audio is limited to 10 seconds/480,000 mono samples; IQ is limited to 2 seconds/262,144 complex samples (2 MiB). The requested duration can be shortened by the sample cap and the result reports the actual duration. The broker accepts only its bearer token and its enumerated receiver actions. Browser WebSockets additionally require a matching-host Origin on port 3000 or 3001. This setup is for the trusted receiver LAN; do not expose the pairing route or broker directly on the public internet.

## Verification

```bash
pnpm --dir apps/web exec tsc --noEmit
pnpm --dir apps/web exec tsx scripts/receiver-mcp-smoke.ts
pnpm --dir apps/web exec tsx scripts/receiver-browser-smoke.ts
```

The smoke test uses the official MCP client and stdio transport. It starts a real broker and runs the production browser command executor against a deterministic controller. It verifies actual configuration and tuning readback, signal coordinates, native audio, IQ resources, invalid-input rejection, and stop. Dedicated capture tests verify WAV headers and sample encoding, pool-buffer copying, native IQ encoding, and invalidation on retune. Broker tests cover authentication, Origin checks, multiple sessions, correlation, and disconnect/timeout cleanup.

The browser smoke test launches an isolated Chromium profile against the deployed
LAN HTTP app. It controls the real browser RadioController exclusively through
the official MCP client, exercises AM demo tuning and audio/IQ capture, reads an
IQ resource, changes display settings, queries schedules, and stops the stream.
It uses only the synthetic demo source and never claims the user's RTL device.
Override `CHROMIUM_BIN` or `SIGSENSE_TEST_URL` for another installation.

## Autonomous shortwave surveys

Install `deploy/systemd/sigsense-receiver-browser.service` as a user unit to keep
a dedicated, normally idle receiver page available to MCP. It does not connect
to the RTL device until commanded. Run `pnpm survey:shortwave` for a bounded,
receive-only survey of eight broadcast bands. The survey refuses to start when
another paired browser is already using rtl_tcp, and releases the stream on exit.

For continuous service-side discovery instead of a bounded survey, enable
`deploy/systemd/sigsense-autofinder.service`. It starts the browser's autonomous
finder on the dedicated receiver, verifies that the source is real `rtl_tcp`,
restarts failed searches, and yields to an interactive receiver. Current service,
receiver, and finder state is published at `.runtime/autofinder-service.json`.
The **Background shortwave finder** panel in Radio and Stations displays these
results live, keeps saved detections available offline, and provides
pause/resume/skip and live-listening handoff. See the
[background finder setup](autonomous-finder.md#background-findings-in-the-ui).

Each band is sampled at two hardware center frequencies. Only peaks that remain
at the same absolute RF frequency are retained; this rejects moving tuner/DC
artifacts, but cannot distinguish an external interferer from a broadcast. Both
carrier prominence and occupied AM sidebands affect ranking. There is no claim
of station identity or intelligible speech from peak detection alone.

Reports and native WAV captures stay under `.runtime/shortwave-survey` (private
files, gitignored). Stations displays the latest scan timestamp, candidates,
one-click speech-oriented tuning, and actual recordings. The read-only
`/api/shortwave-survey` route publishes compact results and only allowlisted clips.
Results are snapshots, not continuous monitoring or guarantees of current reception.

The optional `audioSpeechFilter` MCP setting applies a streaming 150 Hz high-pass
and 3 kHz low-pass before playback noise reduction. It is off by default, does not
alter raw decoder audio/IQ, and should stay off for music and CW. Selecting a
survey candidate enables it with 6 kHz AM bandwidth and light NR. Captured
bandwidth comparisons are successive live samples; each additional speech-filter
comparison is generated from the exact same captured PCM by the production filter.

Initial real-device survey: six frequency-stable candidates, two with appreciable
sideband energy (9.490 MHz and 15.431375 MHz), no detected ADC/audio clipping or
scheduler underruns during captures. The off-channel 15.431375 MHz signal may be
interference. On the same 6 kHz recordings, speech filtering attenuated energy
below 150 Hz by 8.3–14.2 dB and above 4 kHz by 8.8–9.1 dB; 300–2500 Hz energy
changed by 0.2–0.4 dB. These are band-energy measurements, not SNR or intelligibility
scores. Listen to the paired recordings to judge the tradeoff.

Listening order now prioritizes candidates with measurable sidebands near a
5 kHz broadcast grid, then off-grid occupied signals, then carriers with little
sideband energy. The grid tolerance is 750 Hz and is only a browsing heuristic:
off-grid signals remain visible and are never snapped to a different frequency.
A distinct neighbor 4–8 kHz away within 12 dB of the carrier recommends 4 kHz
AM instead of 6 kHz. This recommendation survives the two-position check.

Stations includes best/previous/next measured-signal navigation, stale-result
warnings, and result reload (not a rescan). Natural/Speech/Crowded presets select
9/6/4 kHz AM and progressively stronger cleanup. Bypass/restore changes only
audio cleanup, preserving the tuned frequency and RF passband for comparison.
