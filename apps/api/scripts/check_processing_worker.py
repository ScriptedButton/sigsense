"""Read-only authenticated health + optional real-time streaming check. No radio access."""
import argparse
import asyncio
import json
import time

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from sigsense_api.processing_client import WorkerConfig, compatible, public_info, worker_health
from sigsense_api.processing_protocol import BLOCK, HELLO, decode_audio


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream-test", action="store_true", help="Send 3 seconds of synthetic audio and measure latency")
    args = parser.parse_args()
    health = await worker_health()
    print(json.dumps(health, indent=2))
    if not health.get("ready"):
        return 1
    if not args.stream_test:
        return 0
    config = WorkerConfig.environment()
    latencies = []
    async with connect(config.socket_url, additional_headers={"Authorization": "Bearer " + config.token},
                       open_timeout=2, close_timeout=.2, max_size=16000, max_queue=1, compression=None, proxy=None) as ws:
        await ws.send(json.dumps(HELLO))
        info = json.loads(await asyncio.wait_for(ws.recv(), 2))
        if not compatible(info):
            raise ValueError("Incompatible processing worker")
        rng = np.random.default_rng(2026)
        for _ in range(30):
            audio = rng.normal(0, .015, BLOCK).astype("<f4")
            started = time.monotonic()
            try:
                await ws.send(audio.tobytes())
                decode_audio(await asyncio.wait_for(ws.recv(), .5))
            except (TimeoutError, ConnectionClosed):
                print(json.dumps({**public_info(info), "receivedPackets": len(latencies),
                    "withinRealtimeBudget": False,
                    "message": "Worker disconnected or exceeded the 500 ms test deadline. Check GPU availability/load; CPU DNS64 may be too slow."}, indent=2))
                return 1
            elapsed = time.monotonic() - started
            latencies.append(elapsed * 1000)
            await asyncio.sleep(max(0, .1 - elapsed))
    # Exclude initial kernel warmup but report worst case as well.
    p95 = float(np.percentile(latencies[3:], 95))
    ready = p95 <= config.deadline * 1000
    print(json.dumps({**public_info(info), "packets": len(latencies), "p95RoundTripMs": round(p95, 2),
                      "maxRoundTripMs": round(max(latencies), 2), "deadlineMs": config.deadline * 1000,
                      "withinRealtimeBudget": ready}, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:
        # Never dump network exceptions that might contain endpoint/credential details.
        print(f"Worker check failed ({type(exc).__name__}): verify authentication, protocol and real-time performance.")
        raise SystemExit(1)
