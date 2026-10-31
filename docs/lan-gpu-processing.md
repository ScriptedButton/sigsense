# LAN GPU signal processing

The receiver can offload continuous neural speech enhancement to a second machine.
The radio, finder, raw speech checks, RF corrections, adaptive profile selection,
and live audio distribution remain on the receiver host. Only demodulated mono
audio crosses an authenticated server-to-server connection. The GPU machine does
not need an SDR, browser, microphone, FAISS library, or access to receiver control.

This is wired into the existing automatic cleaner, including background findings,
**Listen live**, and on-demand **Clean this signal on server** for a manually tuned
signal. It is not a separate file upload tool. Radio and Stations
show the worker's idle health and the active stream's actual processing location,
model, GPU name and measured round-trip latency. A reachable worker is not shown
as active until its stream has warmed up. CPU processing is explicitly labeled CPU.

## GPU machine setup

Use a separate checkout of this repository and a Python 3.11+ virtual environment.
The default engine is Meta's streaming **Denoiser DNS64**, a larger waveform-domain
speech enhancement network than the receiver's RNNoise. DNS48 is also supported.
The optional upstream package and weights are **CC-BY-NC-4.0**; review the
[upstream license](https://github.com/facebookresearch/denoiser/blob/main/LICENSE)
before using them, especially commercially. They are not vendored or installed in
the ordinary receiver requirements. See the [model/streaming documentation](https://github.com/facebookresearch/denoiser).

```bash
python3 -m venv .venv-worker
.venv-worker/bin/python -m pip install -r apps/api/requirements-processing-worker.txt
# Install the GPU build of torch appropriate to this machine/driver, then:
.venv-worker/bin/python -m pip install --no-deps denoiser==0.1.5
```

Choose the torch command from the [official PyTorch installer](https://pytorch.org/get-started/locally/).
The worker explicitly requests `cuda`/`cuda:0`; it refuses to start when that device
is unavailable rather than silently claiming GPU acceleration on CPU. An explicit
`SIGSENSE_PROCESSING_DEVICE=cpu` is available for transport/model testing, but CPU
may not meet the live-stream budget. NVIDIA CUDA is the intended GPU deployment;
other GPU backends have not been validated.

Generate a random secret (do not reuse the receiver broker token):

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Set these variables on the GPU machine using a private environment file or service
manager. `deploy/processing-worker.env.example` is a template, not a usable secret.

```text
SIGSENSE_PROCESSING_TOKEN=<generated-secret>
SIGSENSE_PROCESSING_DEVICE=cuda:0
SIGSENSE_PROCESSING_MODEL=demucs-dns64
SIGSENSE_PROCESSING_MAX_STREAMS=2
```

Then launch from the checkout root with those variables in the process environment:

```bash
PYTHONPATH=apps/api .venv-worker/bin/uvicorn sigsense_api.processing_worker:app \
  --host 0.0.0.0 --port 8791 --workers 1 --ws-max-size 8192 --ws-max-queue 1
```

The initial launch downloads approximately 128 MiB of DNS64 weights from the
publisher, verifies their hash, loads tensor-only state, and runs an inference
warmup before becoming ready. Later starts use torch's cache. No audio is saved or
sent to that publisher. For an offline GPU host, provision the torch weight cache
before startup. Do not use multiple uvicorn processes: that duplicates GPU weights
and bypasses the process-local stream limit. The worker shares immutable weights
but keeps each source's recurrent/filter state separate.

`deploy/systemd/sigsense-processing-worker.service` provides a Linux deployment
template with a private environment file and writable model cache. Adjust its user,
checkout and virtualenv paths on the GPU machine before installing it. The example
expects `/etc/sigsense/processing-worker.env` and a `sigsense` service account.

## Receiver host setup

Install the updated API dependencies, then configure the receiver API process:

```text
SIGSENSE_PROCESSING_URL=http://192.168.1.50:8791
SIGSENSE_PROCESSING_TOKEN=<the-same-generated-secret>
SIGSENSE_PROCESSING_DEADLINE_MS=90
```

Replace the address with the actual worker's LAN address. Only an HTTP(S) origin
is accepted; credentials, paths, query strings and fragments are rejected. The URL
and token are backend-only settings, never `NEXT_PUBLIC_*`, browser storage, UI
responses or saved findings. Runtime browser configuration is deliberately not
provided because the existing UI has no administrative authentication boundary.

The supplied receiver API systemd unit reads the optional private file
`/etc/sigsense/processing-client.env`; see `deploy/processing-client.env.example`.
Updating an existing deployment also requires installing the updated unit (or an
equivalent systemd environment-file override), reloading systemd and restarting the
API. Build/deploy the updated web app and refresh the dedicated receiver browser.
None of those production restarts is performed by tests.

Remove `SIGSENSE_PROCESSING_URL` and restart the API to return to local-only
operation. With no URL configured, the previous local processing path and its
94 ms DSP latency remain unchanged.

## Check before enabling live offload

From the receiver checkout, with the URL and token in its environment:

```bash
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/check_processing_worker.py
PYTHONPATH=apps/api .venv/bin/python apps/api/scripts/check_processing_worker.py --stream-test
```

The second command sends three seconds of synthetic audio, checks finite output
and protocol consistency, and reports GPU/CPU identity, p95/worst round trip and
whether the configured real-time budget is met. It does not tune or interrupt the
radio. Run it when a worker slot is free. A worker with spare compute but high LAN
latency may still be unsuitable for live audio.

## Failure behavior and bounds

Audio is 16 kHz mono float32 in fixed 100 ms packets, about 64 kB/s in each direction
per stream before transport overhead. There is one request in flight per source,
a versioned handshake, strict packet validation, bounded queues, per-frame deadlines,
and a configurable 1–8 source worker limit (default two). Health checks are
authenticated. Browser-origin connections are refused. The worker exposes no
arbitrary model execution, file operations or radio commands.

Local RNNoise runs continuously as a warm fallback. A failed connection, wrong
token, busy worker, malformed result or missed deadline returns locally processed
audio on the same sample timeline, then retries the remote worker after five seconds.
If Node/RNNoise is unavailable too, local spectral processing remains active.
Connections and reconnect tasks close when the source stops. GPU jobs are serialized;
a timed-out native call retains its reservation until it actually finishes, so
reconnects cannot build an unbounded work queue behind a stuck GPU.

Remote streams and their local fallback use an identical fixed 100 ms neural delay;
the remaining local DSP adds 64 ms (164 ms total DSP delay, excluding network and
playback buffering). Worker recovery warms recurrent state and crossfades before
selecting remote output. Every model/fallback change invalidates the previous
neural profile and restarts speech-preserving validation from Natural. Raw VAD is
not offloaded or replaced by enhanced audio. A powerful model can still damage weak
shortwave speech; the existing quality constraints can reject it and use a lighter
profile. This feature does not reconstruct missing RF information or guarantee
intelligibility.

On a trusted private LAN, `http://` uses unencrypted WebSockets: the bearer token
and audio are visible to a network observer. Restrict port 8791 to the receiver's
IP in the worker firewall; do not expose it to the Internet or an untrusted Wi-Fi
network. Use `https://` with a trusted-certificate reverse proxy (WebSocket upgrade
enabled), or a private VPN, when confidentiality matters. Client proxy auto-discovery
and redirects are disabled; normal TLS certificate verification remains enabled.

## Verification

```bash
PYTHONPATH=apps/api .venv/bin/python -m unittest discover -s apps/api/tests -p test_processing_worker.py -v
PYTHONPATH=apps/api .venv/bin/python -m unittest discover -s apps/api/tests -p test_audio_cleaner.py -v
```

Tests run an actual loopback TCP worker and cover authenticated health/streaming,
bad credentials, protocol/packet validation, capacity, clock alignment, disconnect
cleanup, slow/failing worker fallback and reconnection. The real DNS64 adapter can
be tested on CPU, but loopback and CPU results are not evidence of performance on
an unconnected remote GPU. Validate your target hardware with the stream check.

On the development receiver's ARM CPU, the real DNS64 adapter produced finite,
correctly sized streaming output, but network testing took approximately 440–480 ms
per 100 ms packet and correctly failed the live-processing budget. This confirms
model compatibility and safe rejection of a slow worker, not GPU performance.
