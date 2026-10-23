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

# Train model + seed library (TorchSig + synthetic by default; few minutes on CPU)
pip install -e "packages/ml[torchsig]"
python packages/ml/scripts/train.py --source mixed --epochs 12
# Also: --source synthetic|torchsig|radioml|all

# API
uvicorn sigsense_api.main:app --app-dir apps/api --reload --port 8000

# Web (separate terminal)
pnpm install
pnpm --filter @sigsense/web dev
```

Open http://localhost:3000

Set `NEXT_PUBLIC_API_URL` if the API is not at `http://127.0.0.1:8000`.

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
