"""LAN offload with warm local fallback, bounded deadlines and automatic recovery."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from sigsense_api.processing_protocol import BLOCK, DELAY, HELLO, Delay, decode_audio, valid_token

logger = logging.getLogger(__name__)
JITTER_BLOCKS = 1
MAX_LATE_BLOCKS = 3
EXCHANGE_TIMEOUT = .5


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
        self.exchange_task = None
        self.requests = None
        self.results = {}
        self.outstanding = 0
        self.sequence = 0
        self.cleanup_tasks = set()
        self.late_blocks = 0
        self.consecutive_late = 0
        self.reconnects = 0
        self.last_failure = None
        self.last_failure_at = None
        try:
            self.config = WorkerConfig.environment()
        except (ValueError, OverflowError):
            self.config_error = True
            self.warning = "Invalid server-side LAN worker configuration; using local fallback"
        if self.config:
            # The worker protocol retains its 100 ms model delay. One additional
            # local playout block absorbs jitter without changing the LAN worker.
            self.delay_samples = DELAY + BLOCK * JITTER_BLOCKS
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
                "roundTripMs": round(self.round_trip, 1) if self.round_trip is not None else None,
                "fallback": (bool(self.config) or self.config_error) and not self.selected_remote,
                "warning": self.warning, "delayMs": self.delay_samples / 16,
                "bufferMs": BLOCK * JITTER_BLOCKS / 16 if self.config else 0,
                "deadlineMs": self.config.deadline * 1000 if self.config else None,
                "lateBlocks": self.late_blocks, "consecutiveLateBlocks": self.consecutive_late,
                "reconnects": self.reconnects, "lastFailure": self.last_failure,
                "lastFailureAt": self.last_failure_at}

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
            self.consecutive_late = 0
            self.round_trip = None
            # At most three outstanding blocks, including queued and sent work.
            self.requests = asyncio.Queue(maxsize=2)
            self.outstanding = 0
            self.exchange_task = asyncio.create_task(self._exchange(socket))
            self.warning = "LAN worker warming up; local fallback active"
        except asyncio.CancelledError:
            if socket:
                await socket.close()
            raise
        except Exception as exc:
            if socket:
                await socket.close()
            self.retry_at = time.monotonic() + 5
            reason = self._reason(exc, connecting=True)
            self._record_failure(reason, exc)
            self.warning = f"LAN worker connection failed ({reason}); local fallback active, retrying in 5 seconds"
        finally:
            self.connect_task = None

    @staticmethod
    def _reason(exc, connecting=False):
        if isinstance(exc, TimeoutError):
            return "connect_timeout" if connecting else "worker_stall"
        if isinstance(exc, InvalidStatus):
            code = exc.response.status_code
            return "authentication" if code in (401, 403) else "capacity" if code == 503 else "handshake_rejected"
        if isinstance(exc, ConnectionClosed):
            code = exc.rcvd.code if exc.rcvd else None
            return {1008: "protocol_rejected", 1013: "capacity", 1011: "worker_processing"}.get(code, "connection_closed")
        if isinstance(exc, (ValueError, TypeError)):
            return "incompatible_worker" if connecting else "invalid_audio"
        return "connection_error" if connecting or isinstance(exc, OSError) else "client_processing"

    def _record_failure(self, reason, exc=None):
        self.last_failure = reason
        self.last_failure_at = time.time()
        # Never log exception text, URLs, headers, audio or bearer credentials.
        code = exc.rcvd.code if isinstance(exc, ConnectionClosed) and exc.rcvd else None
        logger.warning("LAN processing failure reason=%s exception=%s close_code=%s late_blocks=%d consecutive_late=%d round_trip_ms=%s",
                       reason, type(exc).__name__ if exc else "none", code,
                       self.late_blocks, self.consecutive_late,
                       round(self.round_trip, 1) if self.round_trip is not None else None)

    def _select(self, remote):
        if self.selected_remote != remote:
            self.selected_remote = remote
            self.generation += 1

    async def _close_socket(self, socket):
        try:
            async with asyncio.timeout(.3):
                await socket.close()
        except Exception:
            pass

    async def _drop_remote(self, reason="connection_closed", exc=None):
        socket, self.remote = self.remote, None
        self._select(False)
        self.retry_at = time.monotonic() + 5
        self._record_failure(reason, exc)
        self.warning = f"LAN worker fallback: {reason}; retrying in 5 seconds"
        task, self.exchange_task = self.exchange_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
        for future in self.results.values():
            if not future.done():
                future.set_result(None)
        self.results.clear()
        self.requests = None
        self.outstanding = 0
        if socket:
            self.reconnects += 1
            # Socket close handshakes must not delay fallback audio delivery.
            cleanup = asyncio.create_task(self._close_socket(socket))
            self.cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self.cleanup_tasks.discard)

    async def _exchange(self, socket):
        # One writer and one reader allow network requests to overlap, while the
        # unchanged worker still processes its recurrent model in sample order.
        # A high round-trip time need not reduce throughput to one block per RTT.
        sent = asyncio.Queue(maxsize=3)
        async def send():
            while True:
                _, data, future = await self.requests.get()
                started = time.monotonic()
                await sent.put((started, future))
                async with asyncio.timeout(EXCHANGE_TIMEOUT):
                    await socket.send(data)

        async def receive():
            future = None
            try:
                while True:
                    started, future = await sent.get()
                    remaining = max(0, EXCHANGE_TIMEOUT - (time.monotonic() - started))
                    async with asyncio.timeout(remaining):
                        output = decode_audio(await socket.recv())
                    self.outstanding -= 1
                    elapsed = (time.monotonic() - started) * 1000
                    self.round_trip = elapsed if self.round_trip is None else .8 * self.round_trip + .2 * elapsed
                    if not future.done():
                        future.set_result(output)
            finally:
                if future and not future.done():
                    future.set_result(None)

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(send())
                group.create_task(receive())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            while isinstance(exc, ExceptionGroup):
                exc = exc.exceptions[0]
            await self._drop_remote(self._reason(exc), exc)

    async def apply(self, audio):
        index = self.sequence
        self.sequence += 1
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
        target = self.results.pop(index - JITTER_BLOCKS, None)
        if self.outstanding >= 3:
            await self._drop_remote("queue_saturation")
            return local
        future = asyncio.get_running_loop().create_future()
        self.results[index] = future
        try:
            self.requests.put_nowait((index, np.asarray(audio, dtype="<f4").tobytes(), future))
            self.outstanding += 1
        except asyncio.QueueFull:
            await self._drop_remote("queue_saturation")
            return local
        # Start transport even when the first buffered block returns immediately.
        await asyncio.sleep(0)
        if target is None:
            return local
        try:
            # A timed-out result is NOT canceled: the sole receiver drains and
            # discards it later, preserving ordered state and exact sample IDs.
            output = await asyncio.wait_for(asyncio.shield(target), self.config.deadline)
            if output is None or not self.remote:
                return local
            self.consecutive_late = 0
            self.remote_blocks += 1
            # Fill model lookahead/recurrent state before replacing the aligned,
            # continuously warm local branch. A model switch triggers revalidation.
            if self.remote_blocks < 5:
                return local
            if not self.selected_remote:
                self._select(True)
                self.warning = None
                mix = np.linspace(0, 1, BLOCK)
                return (local * (1 - mix) + output * mix).astype(np.float32)
            return output
        except TimeoutError:
            self.late_blocks += 1
            self.consecutive_late += 1
            self.remote_blocks = 0
            self._select(False)
            if self.consecutive_late >= MAX_LATE_BLOCKS:
                await self._drop_remote("sustained_lateness")
            else:
                if self.consecutive_late == 1:
                    self._record_failure("playout_deadline")
                self.warning = (f"LAN audio block late; local fallback active, connection retained "
                                f"({self.consecutive_late}/{MAX_LATE_BLOCKS})")
            return local

    async def close(self):
        self.closed = True
        task = self.connect_task
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.exchange_task:
            self.exchange_task.cancel()
            await asyncio.gather(self.exchange_task, return_exceptions=True)
            self.exchange_task = None
        if self.remote:
            await self._close_socket(self.remote)
            self.remote = None
        for future in self.results.values():
            if not future.done():
                future.cancel()
        self.results.clear()
        self.requests = None
        if self.cleanup_tasks:
            await asyncio.gather(*self.cleanup_tasks, return_exceptions=True)
        await self.local.close()
