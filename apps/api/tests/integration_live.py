"""Exercise real HTTP/WS capture -> save -> naming -> local playback -> TX dry run.

Starts an isolated demo-only server with a temporary capture store. Never emits RF.
Run: PYTHONPATH=apps/api .venv/bin/python apps/api/tests/integration_live.py
"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import websockets

ROOT = Path(__file__).resolve().parents[3]
PORT = 18801
BASE = f"http://127.0.0.1:{PORT}"


def request(path, body=None, method="POST"):
    data = None if body is None else json.dumps(body).encode()
    req = Request(
        BASE + path,
        data=data,
        method="GET" if body is None else method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=20) as response:
        return json.load(response)


async def exercise():
    request(
        "/sdr/tuner",
        {
            "demo": True,
            "ble_decode": True,
            "ble_hop": True,
            "sample_rate_hz": 4e6,
            "center_freq_hz": 2402e6,
        },
    )
    async with websockets.connect(
        f"ws://127.0.0.1:{PORT}/live", max_size=8_000_000
    ) as ws:
        frame = json.loads(await asyncio.wait_for(ws.recv(), 15))
        assert frame["type"] == "frame" and len(frame["spectrum"]["power_dbfs"]) == 1024
        assert frame["observations"], "Demo should produce tracked observations"
        device = frame["observations"][0]["id"]
        request("/devices/name", {"device_id": device, "name": "Integration demo"})
        saved = request(
            "/signals/save",
            {
                "frame_id": frame["frame_id"],
                "name": "Integration capture",
                "device_id": device,
            },
        )
        duplicate = request(
            "/signals/save",
            {"frame_id": frame["frame_id"], "name": "Duplicate", "device_id": device},
        )
        assert duplicate["duplicate"] and duplicate["id"] == saved["id"]
        assert saved["device_name"] == "Integration demo"
        request(
            "/sdr/scanner",
            {
                "enabled": True,
                "start_hz": 2402e6,
                "stop_hz": 2406e6,
                "step_hz": 2e6,
                "dwell_ms": 50,
            },
        )
        for _ in range(5):
            scanned = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if scanned["scanner"]["enabled"]:
                break
        assert scanned["sweep"]["swept"] and scanned["scanner"]["steps"] == 3
        request("/sdr/scanner", {"enabled": False})
    identity = saved["id"]
    request("/signals/" + identity, {"name": "Renamed integration"}, method="PATCH")
    with urlopen(BASE + "/signals/" + identity + "/iq", timeout=10) as response:
        assert response.read(6) == b"\x93NUMPY"
    dry = request("/signals/" + identity + "/transmit", {"dry_run": True, "repeats": 2})
    assert dry["dry_run"] and not dry["transmitted"]
    async with websockets.connect(
        f"ws://127.0.0.1:{PORT}/signals/{identity}/playback?repeat=false",
        max_size=8_000_000,
    ) as ws:
        played = json.loads(await asyncio.wait_for(ws.recv(), 15))
        assert played["playback"] and played["tuner"]["backend"] == "recording"
        assert played["recording"]["name"] == "Renamed integration"
        assert len(played["spectrum"]["power_dbfs"]) == 1024
    assert len(request("/signals")["items"]) == 1
    print(
        "PASS: live FFT, tracking, naming, exact deduplication, saving, scanner, IQ download, rename, local playback, TX dry run"
    )


if __name__ == "__main__":
    with TemporaryDirectory(prefix="sigsense-integration-") as root:
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "apps/api"),
            "SIGSENSE_CAPTURE_DIR": root,
            "OMP_NUM_THREADS": "2",
        }
        with open(Path(root) / "server.log", "w+") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "sigsense_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(PORT),
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    try:
                        request("/health")
                        break
                    except URLError:
                        if process.poll() is not None:
                            raise RuntimeError("Server exited during startup")
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Server startup timed out")
                asyncio.run(exercise())
            except Exception:
                log.seek(0)
                print(log.read()[-8000:], file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
