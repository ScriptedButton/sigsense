"""LAN offload with warm local fallback, bounded deadlines and automatic recovery."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import numpy as np
from websockets.asyncio.client import connect

from sigsense_api.processing_protocol import BLOCK, DELAY, HELLO, Delay, decode_audio, valid_token


@dataclass(frozen=True)
class WorkerConfig:
    url: str
    token: str
    deadline: float = .09

    @classmethod
    def environment(cls):
        url = os.environ.get("SIGSENSE_PROCESSING_URL", "").strip().rstrip("/")
        if not url:
            return None
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError("SIGSENSE_PROCESSING_URL must be an http(s) origin, without credentials or a path")
        token = os.environ.get("SIGSENSE_PROCESSING_TOKEN", "")
        if not valid_token(token):
            raise ValueError("SIGSENSE_PROCESSING_TOKEN must have at least 32 non-whitespace characters")
        deadline = float(os.environ.get("SIGSENSE_PROCESSING_DEADLINE_MS", "90")) / 1000
        if not .02 <= deadline <= .2:
            raise ValueError("Worker deadline must be between 20 and 200 ms")
        return cls(url, token, deadline)

    @property
    def socket_url(self):
        return ("wss" if self.url.startswith("https:") else "ws") + self.url[self.url.index(":"):] + "/v1/enhance"


def public_info(info: dict):
    """An allowlist, not arbitrary metadata from a remote machine, enters browser state."""
    result = {}
    for key in ("engine", "device", "gpuName"):
        value = info.get(key)
        if isinstance(value, str):
            result[key] = value[:120]
    result["accelerated"] = info.get("accelerated") is True
    return result


def compatible(info):
    return isinstance(info, dict) and all(info.get(k) == v for k, v in HELLO.items()) and info.get("delaySamples") == DELAY and info.get("ready") is True


async def worker_health():
    try:
        config = WorkerConfig.environment()
    except (ValueError, OverflowError):
        return {"configured": True, "ready": False, "message": "Invalid server-side LAN worker configuration"}
    if config is None:
        return {"configured": False, "ready": False, "message": "Local processing; no LAN worker configured"}
    try:
        async with httpx.AsyncClient(timeout=1, follow_redirects=False, trust_env=False) as client:
            result = await client.get(config.url + "/v1/health", headers={"Authorization": "Bearer " + config.token})
        if result.status_code != 200 or len(result.content) > 16000:
            return {"configured": True, "ready": False, "message": "LAN worker unavailable or authentication refused"}
        info = result.json()
        if not compatible(info):
            raise ValueError("Incompatible worker")
        return {"configured": True, "ready": True, **public_info(info),
                "activeStreams": info.get("activeStreams"), "maxStreams": info.get("maxStreams"),
                "processingMs": info.get("processingMs"), "delayMs": DELAY / 16}
    except Exception:
        return {"configured": True, "ready": False, "message": "LAN worker unreachable or incompatible; local fallback remains available"}


class LanNeuralWorker:
    def __init__(self, local):
        self.local = local
        self.local_available = False
        self.remote = None
        self.connect_task = None
        self.closed = False
        self.config = None
        self.config_error = False
        self.warning = None
        self.retry_at = 0.
        self.round_trip = None
        self.remote_info = {}
        self.remote_blocks = 0
        self.selected_remote = False
        self.generation = 0
        self.delay_samples = 480
        try:
            self.config = WorkerConfig.environment()
        except (ValueError, OverflowError):
            self.config_error = True
            self.warning = "Invalid server-side LAN worker configuration; using local fallback"
        if self.config:
            self.delay_samples = DELAY
        self.local_delay = Delay(self.delay_samples - 480)
        self.raw_delay = Delay(self.delay_samples)

    @property
    def available(self):
        return self.local_available or self.selected_remote

    @property
    def status(self):
        return {"location": "lan" if self.selected_remote else "local" if self.local_available else "spectral",
                "configured": bool(self.config) or self.config_error,
                **(public_info(self.remote_info) if self.selected_remote else
                   {"engine": "RNNoise" if self.local_available else "Spectral", "device": "cpu", "accelerated": False}),
                "roundTripMs": round(self.round_trip, 1) if self.selected_remote and self.round_trip is not None else None,
                "fallback": (bool(self.config) or self.config_error) and not self.selected_remote,
                "warning": self.warning, "delayMs": self.delay_samples / 16}

    async def start(self):
        try:
            await self.local.start()
            self.local_available = True
        except Exception:
            await self.local.close()
            self.warning = "Local RNNoise unavailable; using spectral processing unless a LAN worker connects"
        self._schedule_connect()

    def _schedule_connect(self):
        if self.config and not self.remote and not self.connect_task and not self.closed and time.monotonic() >= self.retry_at:
            self.connect_task = asyncio.create_task(self._connect())

    async def _connect(self):
        socket = None
        try:
            socket = await connect(self.config.socket_url, additional_headers={"Authorization": "Bearer " + self.config.token},
                open_timeout=1, close_timeout=.2, max_size=16000, max_queue=1, write_limit=8192,
                compression=None, proxy=None)
            await socket.send(json.dumps(HELLO))
            info = json.loads(await asyncio.wait_for(socket.recv(), 1))
            if not compatible(info):
                raise ValueError("Incompatible worker")
            if self.closed:
                await socket.close()
                return
            self.remote, self.remote_info = socket, public_info(info)
            self.remote_blocks = 0
            self.round_trip = None
            self.warning = "LAN worker warming up; local fallback active"
        except asyncio.CancelledError:
            if socket:
                await socket.close()
            raise
        except Exception:
            if socket:
                await socket.close()
            self.retry_at = time.monotonic() + 5
            self.warning = "LAN worker unavailable, busy or authentication refused; retrying with local fallback"
        finally:
            self.connect_task = None

    async def _drop_remote(self):
        socket, self.remote = self.remote, None
        if self.selected_remote:
            self.generation += 1
        self.selected_remote = False
        self.retry_at = time.monotonic() + 5
        self.warning = "LAN worker failed or missed its real-time deadline; local fallback active, retrying"
        if socket:
            await socket.close()

    async def apply(self, audio):
        raw = self.raw_delay.apply(audio)
        local = raw
        if self.local_available:
            try:
                local = self.local_delay.apply(await self.local.apply(audio))
            except Exception:
                self.local_available = False
                self.generation += 1
                await self.local.close()
                self.warning = "Local neural worker failed; spectral fallback active"
        self._schedule_connect()
        if not self.remote:
            return local
        try:
            started = time.monotonic()
            async with asyncio.timeout(self.config.deadline):
                await self.remote.send(np.asarray(audio, dtype="<f4").tobytes())
                output = decode_audio(await self.remote.recv())
            elapsed = (time.monotonic() - started) * 1000
            self.round_trip = elapsed if self.round_trip is None else .8 * self.round_trip + .2 * elapsed
            if self.round_trip > 95:
                raise TimeoutError("Worker cannot sustain real time")
            self.remote_blocks += 1
            # Fill model lookahead/recurrent state before replacing the aligned,
            # continuously warm local branch. A model switch triggers revalidation.
            if self.remote_blocks < 5:
                return local
            if not self.selected_remote:
                self.selected_remote = True
                self.generation += 1
                self.warning = None
                mix = np.linspace(0, 1, BLOCK)
                return (local * (1 - mix) + output * mix).astype(np.float32)
            return output
        except Exception:
            await self._drop_remote()
            return local

    async def close(self):
        self.closed = True
        task = self.connect_task
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.remote:
            await self.remote.close()
            self.remote = None
        await self.local.close()
