# SigSense

ML-powered SDR signal analysis: classify **IQ captures** and **waterfall images**, match against an expandable library (open-set), and stream **live SDR** (SoapySDR) or synthetic demo IQ.

## Stack

- `apps/web` — Next.js UI (pnpm)
- `apps/api` — FastAPI inference + live websocket
- `packages/ml` — preprocessing, synthetic IQ, embedding CNN, FAISS library
- `models/` — exported weights, class map, FAISS index

## Quick start

```bash
# Python 3.12 recommended
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r apps/api/requirements.txt
pip install -e packages/ml

# Train a candidate + library (TorchSig + synthetic). GPU recommended.
pip install -e "packages/ml[torchsig]"
python packages/ml/scripts/train.py --source mixed --device auto --out-dir checkpoints/candidate-001
# Also: --source synthetic|torchsig|radioml|all
# Review candidate-001/evaluation.json before promoting its four model/library
# artifacts to models/. The API continues to use models/.

# API + web together
pnpm install
pnpm dev
# Or start one service: pnpm dev:api / pnpm dev:web
```

Open http://localhost:3000

**Receiver** is the browser RTL-SDR from [SDRSense](https://github.com/ScriptedButton/sdrsense): WebUSB, an `rtl_tcp` bridge, OpenWebRX+, WBFM/NBFM/AM/USB/LSB/CW, scanner, frequency lookup, shortwave schedule, Morse, RTTY, FT8, weatherfax, P25, in-browser transcription, and on-device signal identification. **Live SDR** stays the HackRF / SoapySDR path with BLE decode. Leaving Receiver stops that session.

**Find speech automatically** in Radio or Stations starts the [autonomous HF finder](docs/autonomous-finder.md): no range or channel-list setup, local neural speech checks, automatic listening/reacquisition, and remembered signal cooldowns. The receiver page must stay open; speech activity is not a guarantee of intelligibility.

**Background shortwave finder**, also in Radio and Stations, shows continuous service
scan progress, saved speech detections, and recent signal checks. Pause/resume/skip
controls manage the background search; **Listen live** transfers the receiver to
your browser. Run `pnpm autofinder-service` or install the documented user service
to scan while your browser is closed. Findings survive restarts and remain visible
when the service is offline. See [setup and UI behavior](docs/autonomous-finder.md#background-findings-in-the-ui).

The finder also runs an [adaptive backend cleaning pipeline](docs/adaptive-signal-cleaning.md):
impulse/whistle rejection, local RNNoise, adaptive spectral suppression, measured
RF corrections, and continuous speech-preserving profile selection. Cleaned audio
plays automatically on the listening receiver; the UI provides a live original/
cleaned comparison and shows optimization measurements.

An optional [LAN GPU processing worker](docs/lan-gpu-processing.md) offloads heavier
streaming neural enhancement to another machine. Radio ownership stays local;
authenticated audio transport, measured latency, UI GPU/CPU status and automatic
local fallback/recovery are built into the cleaner.

```bash
# RTL stick on another machine, then:
pnpm rtl-server
# optional: pnpm rtl-server -- --listen 8081 --tcp 127.0.0.1:1234
```

To share a receiver with another SDR++ instance, install
[SDR++](https://www.sdrpp.org/) on the machine with the radio, then run:

```bash
pnpm sdrpp-server                         # listens on 127.0.0.1:5259
SDRPP_ADDR=0.0.0.0 pnpm sdrpp-server       # allow connections over the LAN
# Optional environment overrides: SDRPP_BIN=/path/to/sdrpp SDRPP_PORT=5259
pnpm sdrpp-server -- --root /path/to/sdrpp-config
```

In the SDR++ client, select **SDR++ Server** and enter the server machine's
address and port. This uses the native SDR++ protocol; the browser Receiver's
`rtl_tcp` bridge still uses `pnpm rtl-server`. Additional arguments are forwarded
to SDR++; see the [SDR++ user guide](https://www.sdrpp.org/manual.pdf) for server setup.
Run `pnpm sdrpp-server --help` for helper options.

For direct agent inspection/control of the browser receiver, see
[Receiver MCP setup](docs/receiver-mcp.md). Run `pnpm receiver-control` for the
authenticated browser broker and `pnpm receiver-mcp` for the stdio MCP server.
The tools expose live state, tuning, gain, spectrum, raw/playback audio, native IQ,
signal discovery, schedules, display settings, and playback diagnostics.

P25 clear-voice synthesis uses a WebAssembly build of [mbelib](https://github.com/szechyjs/mbelib). Executable objects derived from that package may be covered by patents; see `apps/web/src/lib/sdr/p25/vendor/mbe/PATENT-NOTICE.md`. Metadata decode does not require the vocoder.

The web server proxies `/api` HTTP and WebSocket requests to
`http://127.0.0.1:8000`, so LAN browsers use the same host as the page.
Set `API_PROXY_URL` on the web server to use another backend, or
`NEXT_PUBLIC_API_URL` to have browsers connect directly to an accessible API URL.

The signal catalog has 52 labels. The mixed generated-data profile trains 44;
the compact profile adds captured-data classes for 49 trained labels. Checkpoints expose their own trained label list.
See the [ML guide](packages/ml/README.md) for capture ingestion and evaluation, and
the [accuracy research](docs/model-accuracy-research.md) for datasets and next steps.

## API

| Endpoint | Description |
|----------|-------------|
| `POST /analyze/image` | Waterfall PNG/JPG → classification |
| `POST /analyze/iq` | IQ file → classification (`dtype=auto|wav|complex64|int16|uint8`; stereo WAV L=I/R=Q) |
| `GET /library` | Known + enrolled classes |
| `POST /library/enroll` | Add prototype embedding |
| `WS /live` | Live spectrogram frames + labels |
| `GET/POST /sdr/tuner` | Tune CF / rate / demo class |

## Live SDR / HackRF BLE

With [SoapySDR](https://github.com/pothosware/SoapySDR) + [SoapyHackRF](https://github.com/pothosware/SoapyHackRF) installed:

1. Live SDR → **HackRF BLE ADV37** (2402 MHz, 4 MS/s, `driver=hackrf`)
2. Uncheck **Demo mode**, keep **Frame BLE 1M packets** on
3. Start stream — classified label + framed ADV PDUs (AA `0x8E89BED6`, dewhitened, CRC)

Advertising channels: **37** = 2402 MHz, **38** = 2426 MHz, **39** = 2480 MHz.

`POST /analyze/ble` also frames BLE 1M packets from an uploaded IQ/WAV capture.

Without hardware, leave **Demo mode** on with class `Bluetooth-LE` to exercise GFSK synthesize → demod → PDU framing.

### Saved Bluetooth broadcasts and device sets

In Live SDR, inspect a device and choose **Save / classify broadcast** to name an
individual payload (for example, “Lamp on”). **Save device & associated messages**
saves all received messages for that device in the current listening session,
including repeated broadcasts and messages outside the latest 100 shown or hidden
by filters. Sessions retain up to 10,000 verified messages, then stop so you can
save before starting another. Clearing the recent display does not erase the
session used for device saves; starting a new receiver session does.

**Collapse identical broadcasts** groups matching access address, PHY, header and
payload across captures and channels while retaining reception counts. Payload
changes remain separate and saved broadcast names become their type labels.

The **Saved broadcasts & devices** library persists in the capture database. It
supports inspection, renaming, JSON export, replay dry runs, and HackRF RF replay
of unique broadcasts or all saved occurrences. Replay reconstructs legacy LE1M
announcements and scan responses on channels 37–39, preserving order and channels
with reconstructed spacing. Connection data and other unsupported packets remain
saved and are reported as skipped. This does not recreate a device connection or
original over-the-air timing. Whole-window IQ captures remain in the capture
library.
