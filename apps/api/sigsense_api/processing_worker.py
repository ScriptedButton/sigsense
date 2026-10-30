"""Standalone authenticated LAN enhancement worker. No receiver or tuning access.

Run with one uvicorn process: model weights are shared, recurrent state is per stream.
GPU dependencies are optional and loaded only in this separate application's lifespan.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import time
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from sigsense_api.processing_protocol import BLOCK, DELAY, HELLO, decode_audio, valid_token


class DemucsEngine:
    """Optional Meta Denoiser adapter (upstream CC-BY-NC 4.0, not vendored)."""
    def __init__(self, model_name: str, device: str):
        import torch
        from denoiser import pretrained
        from denoiser.demucs import DemucsStreamer

        if device != "cpu" and not device.startswith("cuda"):
            raise ValueError("Worker device must be cpu or cuda[:index]")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; install the matching GPU PyTorch build")
        torch.set_num_threads(1)
        factory = {"demucs-dns48": pretrained.dns48, "demucs-dns64": pretrained.dns64}.get(model_name)
        if factory is None:
            raise ValueError("Unsupported processing model")
        self.torch, self.streamer_class = torch, DemucsStreamer
        self.model = factory(pretrained=False)
        url = pretrained.DNS_64_URL if model_name.endswith("64") else pretrained.DNS_48_URL
        # Only published weights, hash checked and tensor-only deserialization.
        weights = torch.hub.load_state_dict_from_url(url, map_location="cpu", check_hash=True, weights_only=True)
        self.model.load_state_dict(weights)
        self.model = self.model.eval().to(device)
        self.device = device
        self.info = {"engine": model_name, "device": str(next(self.model.parameters()).device),
                     "accelerated": device.startswith("cuda"),
                     "gpuName": torch.cuda.get_device_name(device) if device.startswith("cuda") else None,
                     "license": "CC-BY-NC-4.0"}
        # Prove that inference really runs on the selected device before reporting ready.
        stream = self.session()
        stream.apply(np.zeros(BLOCK, dtype=np.float32))
        if device.startswith("cuda"):
            torch.cuda.synchronize(device)

    def session(self):
        return DemucsSession(self)


class DemucsSession:
    def __init__(self, engine):
        self.engine = engine
        self.streamer = engine.streamer_class(engine.model, dry=0, num_frames=1)
        if self.streamer.total_length > DELAY:
            raise RuntimeError("Model lookahead exceeds the protocol's delay budget")
        self.pending = np.zeros(DELAY, dtype=np.float32)

    def apply(self, audio):
        torch = self.engine.torch
        with torch.inference_mode():
            tensor = torch.from_numpy(audio.copy()).to(self.engine.device)[None]
            output = self.streamer.feed(tensor)[0].cpu().numpy()
        self.pending = np.concatenate((self.pending, output))
        if len(self.pending) < len(audio):
            raise RuntimeError("Model lost its streaming sample clock")
        result, self.pending = self.pending[:len(audio)].copy(), self.pending[len(audio):]
        if len(self.pending) > DELAY + BLOCK:
            raise RuntimeError("Model output queue exceeded its delay budget")
        return result.astype(np.float32)


def create_app(engine_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        token = os.environ.get("SIGSENSE_PROCESSING_TOKEN", "")
        if not valid_token(token):
            raise RuntimeError("Set SIGSENSE_PROCESSING_TOKEN to at least 32 non-whitespace characters")
        app.state.token = token
        app.state.limit = int(os.environ.get("SIGSENSE_PROCESSING_MAX_STREAMS", "2"))
        if not 1 <= app.state.limit <= 8:
            raise RuntimeError("Worker stream limit must be between 1 and 8")
        # Serialize GPU work without blocking network/health requests. This also
        # prevents thread-pool queue growth when multiple radios share a worker.
        app.state.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="signal-gpu")
        app.state.active = 0
        app.state.lock = asyncio.Lock()
        app.state.failed = 0
        app.state.blocks = 0
        app.state.processing_ms = 0.
        try:
            factory = engine_factory or (lambda: DemucsEngine(
                os.environ.get("SIGSENSE_PROCESSING_MODEL", "demucs-dns64"),
                os.environ.get("SIGSENSE_PROCESSING_DEVICE", "cuda")))
            app.state.engine = await asyncio.get_running_loop().run_in_executor(app.state.pool, factory)
            yield
        finally:
            app.state.pool.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="SigSense LAN processing worker", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    async def run_work(function, *args):
        async with asyncio.timeout(.5):
            await app.state.lock.acquire()
            try:
                future = asyncio.get_running_loop().run_in_executor(app.state.pool, function, *args)
            except BaseException:
                app.state.lock.release()
                raise
            # Cancellation cannot stop a running CUDA call. Keep the reservation
            # until the actual work finishes, never enqueue behind a stuck GPU.
            def finished(job):
                app.state.lock.release()
                if not job.cancelled():
                    job.exception()  # retrieve failures even if the network request timed out
            future.add_done_callback(finished)
            return await asyncio.shield(future)

    def authorized(headers):
        return hmac.compare_digest(headers.get("authorization", "").encode(), ("Bearer " + app.state.token).encode())

    @app.get("/v1/health")
    async def health(request: Request):
        if not authorized(request.headers):
            raise HTTPException(401, "Worker authentication required")
        return {**HELLO, **app.state.engine.info, "delaySamples": DELAY, "ready": True,
                "activeStreams": app.state.active, "maxStreams": app.state.limit,
                "processedBlocks": app.state.blocks, "failedStreams": app.state.failed,
                "processingMs": round(app.state.processing_ms, 2)}

    @app.websocket("/v1/enhance")
    async def enhance(ws: WebSocket):
        # Auth is in the header, never the URL, browser storage or access log.
        if not authorized(ws.headers):
            await ws.close(1008, "Worker authentication required")
            return
        # Browser origins are never expected on this server-to-server endpoint.
        if ws.headers.get("origin"):
            await ws.close(1008, "Browser connections are not supported")
            return
        if app.state.active >= app.state.limit:
            await ws.close(1013, "Worker is at capacity")
            return
        app.state.active += 1  # no await between capacity check and reservation
        try:
            await ws.accept()
            hello = await asyncio.wait_for(ws.receive_json(), 5)
            if hello != HELLO:
                await ws.close(1008, "Incompatible processing protocol")
                return
            session = await run_work(app.state.engine.session)
            await ws.send_json({**HELLO, **app.state.engine.info, "delaySamples": DELAY, "ready": True})
            while True:
                data = await asyncio.wait_for(ws.receive_bytes(), 10)
                audio = decode_audio(data)
                # One in-flight packet per stream. Queue saturation fails closed
                # so a receiver can fall back rather than play old buffered audio.
                started = time.monotonic()
                output = await run_work(session.apply, audio)
                output = np.asarray(output, dtype="<f4")
                decode_audio(output.tobytes())
                app.state.processing_ms = (time.monotonic() - started) * 1000
                app.state.blocks += 1
                await asyncio.wait_for(ws.send_bytes(output.tobytes()), .5)
        except WebSocketDisconnect:
            pass
        except (ValueError, TypeError, KeyError):
            app.state.failed += 1
            await ws.close(1008, "Invalid processing audio or handshake")
        except Exception:
            app.state.failed += 1
            try:
                await ws.close(1011, "Processing failed or exceeded the real-time budget")
            except RuntimeError:
                pass
        finally:
            app.state.active -= 1

    return app


app = create_app()
