# Autonomous shortwave speech finder

In Radio or Stations, press **Find speech automatically**. An idle receiver
connects to the local RTL bridge; an existing RTL/WebUSB connection is reused.
No frequency range, channel list, or squelch programming is required. The first
version searches AM shortwave speech from 2.3 to 30 MHz, not the entire VHF/UHF
receiver range or every modulation. It uses 1.024 MS/s automatically.

The controller—not a mounted panel—owns a continuous closed-loop search:

1. Scout broadcast bands, interleaving discovery tiles covering every gap in HF.
2. Confirm carrier candidates at two hardware centers to reject moving receiver spurs.
3. Select an AM bandwidth from measured neighboring carriers and apply light speech cleanup.
4. Assess four seconds of **raw demodulated PCM**, independently of volume and cleanup,
   with the local Silero ONNX neural voice-activity detector. Require two successive
   speech-positive samples before unmuting. A positive is speech activity, never a
   claim of clear words, station identity, or a verified transcript.
5. Listen while monitoring. Two quiet checks resume searching. Productive tiles
   move earlier in future passes, but discovery continues without starvation.

Rejected candidates cool down for three minutes; **Skip signal** avoids a
frequency for fifteen minutes. Expiring frequency cooldowns and band yields are
saved locally, bounded to 300 entries. No audio is stored by automatic assessment,
and no cloud speech service or LLM key is needed. The neural model is bundled with
the existing faster-whisper installation. See the upstream
[Silero VAD documentation](https://github.com/snakers4/silero-vad) for its distinction
between speech detection and speech recognition.

**Stay here** disables automatic departure until **Resume automatic search**.
**Stop & restore dial** cancels the loop and restores the original dial, sample
rate, mode, bandwidth, squelch, AFC, and audio-cleanup settings. Manual tuning,
mode, or bandwidth changes take ownership without a delayed restore overwriting
them. Starting the memory-bank scanner stops the autonomous finder first; stopping
the receiver stops both. Searches are muted without changing the user's volume.

The page must remain open. Switching panels is safe; closing/reloading the page
stops this receiver's search. The saved learning survives reload, but automatic
hardware access does not restart without another explicit Start. The dedicated
receiver browser can run the same engine through MCP while kept open, but its
headless audio is not forwarded to a different listening browser.

For unattended operation, `pnpm autofinder-service` supervises the dedicated
`?receiverAgent=1` browser and continuously runs this same finder against the real
`rtl_tcp` bridge (never the demo source). It reconnects and restarts after failures,
writes atomic status to `.runtime/autofinder-service.json`, and yields the SDR when
another browser starts real RTL playback. Install and enable
`deploy/systemd/sigsense-autofinder.service` to run it as a user service. Override
the bridge with `SIGSENSE_AUTOFINDER_RTL_URL` when it is not
`http://127.0.0.1:8081`.

Confirmed speech also starts the [adaptive backend cleaner](adaptive-signal-cleaning.md).
It continuously compares processing settings on the same audio, applies measured
RF corrections, and streams the cleaned result back for listening. Radio and
Stations include live cleaned/original comparison and optimization measurements.

### Background findings in the UI

**Radio → Background shortwave finder** and **Stations → Background shortwave
finder** show the unattended receiver's current band, dial, pass, tile count,
speech activity, and last update. Results refresh every three seconds. Confirmed
signals include their last detection time, AM bandwidth, and confirmation count;
the recent-check list also shows rejected candidates. The service keeps up to
200 distinct findings and 500 checks across browser reloads and service restarts.
It samples a detected station for at most 30 seconds before continuing discovery.

**Pause background search** releases the hardware and persists the pause across
restarts. **Resume background search** enables scanning when the receiver is free.
**Skip background signal** applies the finder's 15-minute avoidance cooldown.
**Listen live** releases the background connection before connecting this browser,
then tunes the measured frequency using the detected AM bandwidth. The background
search waits while a paired browser is using RTL and resumes after disconnect.
Normal RTL connections also request this handoff automatically. A 30-second
reservation covers the interval before the new browser connects.

**Background RF gain** changes only the dedicated scanning receiver. Enter
0–50 dB and press **Apply manual gain**, or press **Auto gain** to return to
automatic RF level control. Updates apply without restarting an active scan.
While paused or yielding to live listening, the setting is saved and applied
when background scanning resumes. The preference survives browser reconnects and
service restarts. The panel also reports actual receiver gain and warns about
ADC clipping; increasing gain does not by itself guarantee speech detection.

Saved results remain visible if the service is offline; scan status is marked
stale after 45 seconds without updates. Findings older than 30 minutes are marked
as older reception. Speech activity is a measured fraction of audio, not a
confidence in station identity or intelligible content. No assessment audio is
stored.

Deploy the updated web build and refresh the dedicated receiver browser before
starting the supervisor; an already-open browser keeps its older controller code.
With the API, web app, RTL bridge and `rtl_tcp` running, install the user services:

```bash
mkdir -p "$HOME/.config/systemd/user"
install -m 644 deploy/systemd/sigsense-receiver-control.service deploy/systemd/sigsense-receiver-browser.service deploy/systemd/sigsense-autofinder.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user restart sigsense-receiver-browser.service
systemctl --user enable --now sigsense-autofinder.service
```

The supplied units use `/home/cole/sigsense`; adapt those paths for another host.
The supervisor listens on loopback port 8790, authenticated with the broker's
receiver token. The same-origin `/api/autofinder-service` route proxies status and
bounded control actions without returning the token. Override the port with
`SIGSENSE_AUTOFINDER_PORT` on both the web app and supervisor, or the data file with
`SIGSENSE_AUTOFINDER_STATUS` on both. A second supervisor cannot claim the same
port. The service needs the dedicated browser but does not need a user browser
to remain open.

Verification: `pnpm test:autofinder-service` exercises persistence, the authenticated
daemon and UI API, receiver release, retry, source provenance, pause recovery, and
process shutdown. With an updated web app on port 3001 and the receiver-control
broker running, `pnpm --dir apps/web exec tsx scripts/autofinder-service-browser-smoke.ts`
exercises the rendered UI, failed and successful handoffs, actual controller
tuning, panel changes, offline/stale states, and mobile layout. Its findings and
RTL stream are synthetic; it does not access the physical receiver. Override
`SIGSENSE_TEST_URL` to test another app URL.

Local API: `GET /autofinder/status`, `POST /autofinder/assess` (mono PCM16 WAV,
1–6 seconds, 8–48 kHz, actual body cap 600 kB, at most two outstanding assessments).
Unavailable or failed inference stops with a visible error, not an energy-only
fallback masquerading as AI. MCP tools: `autofinder_start`, `autofinder_state`,
`autofinder_skip`, `autofinder_stop`.

Tests cover complete HF tile coverage at multiple capture rates, two-window
acceptance, noise rejection, fading/reacquisition, stay/skip cooldowns, cancellation
during capture, manual takeover, AI failure, and bounded WAV validation. A known
speech fixture produced 98.4% speech activity; a recent real 9.490 MHz noise
recording produced 0%. Neither fixture result establishes a new received station.

## Assistant

The receiver has a command bar (press `/` or Ctrl/⌘+K) that drives the receiver from plain language. Set `ANTHROPIC_API_KEY` on the web server to enable it; `SIGSENSE_ASSISTANT_MODEL` overrides the model. Tool calls run in the browser through the same validated executor as the receiver-control broker.
