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

```bash
# RTL stick on another machine, then:
pnpm rtl-server
# optional: pnpm rtl-server -- --listen 8081 --tcp 127.0.0.1:1234
```

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
