"""Live SDR: HackRF via hackrf_transfer, optional SoapySDR, else demo IQ."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from sigsense_api.hackrf import (
    HackRFCapture,
    hackrf_available,
    list_hackrf_devices,
    transmit_iq,
)
from sigsense_ml.ble import (
    BLE_CHANNELS_MHZ,
    decode_ble_iq,
    generate_ble_adv_iq,
    modulate_ble_packet_iq,
)
from sigsense_ml.classes import SIGNAL_CLASSES, CAPTURE_ONLY_CLASSES
from sigsense_ml.preprocess import iq_to_spectrogram
from sigsense_ml.synthetic import generate_iq

try:
    import SoapySDR  # type: ignore
    from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX  # type: ignore

    HAS_SOAPY = True
except Exception:
    HAS_SOAPY = False


ADV_HOP_CHANNELS = (37, 38, 39)

# Fixture Remote A ON payload (AdvA + AdvData) for demo LED verification
_DEMO_LED_PAYLOAD = bytes.fromhex("387800de68be0dff55aa018a24be06c0b4060802")


@dataclass
class TunerState:
    # Match JiaoXianjun/BTLE btle_rx defaults: 4 MS/s, ~2 MHz BB, modest gain
    center_freq_hz: float = 2402e6
    sample_rate_hz: float = 4e6
    gain_db: float = 32.0
    demo: bool = True
    demo_class: str = "Bluetooth-LE"
    running: bool = False
    device_args: str = "driver=hackrf"
    ble_decode: bool = True
    ble_hop: bool = True
    backend: str = "demo"  # demo | hackrf | soapy


@dataclass
class LiveSDRService:
    state: TunerState = field(default_factory=TunerState)
    _device: Any = None
    _stream: Any = None
    _hackrf: HackRFCapture | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _iq_ring: deque = field(default_factory=lambda: deque(maxlen=8))
    _hop_idx: int = 0
    _frame_i: int = 0

    def list_devices(self) -> list[dict]:
        results: list[dict] = []
        results.extend(list_hackrf_devices())
        if not any(d.get("driver") == "hackrf" for d in results):
            results.append(
                {
                    "driver": "hackrf",
                    "label": "HackRF (install hackrf tools / plug in device)",
                    "available": hackrf_available(),
                }
            )
        if HAS_SOAPY:
            try:
                for d in SoapySDR.Device.enumerate():
                    results.append({k: str(v) for k, v in dict(d).items()})
            except Exception as exc:
                results.append({"driver": "soapy", "label": str(exc)})
        results.append(
            {"driver": "demo", "label": "Demo (synthetic IQ)", "available": True}
        )
        return results

    def _parse_args(self) -> dict[str, str]:
        args: dict[str, str] = {}
        if self.state.device_args:
            for part in self.state.device_args.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    args[k.strip()] = v.strip()
        return args

    def _apply_center_freq(self, freq_hz: float) -> None:
        self.state.center_freq_hz = float(freq_hz)
        if self._hackrf is not None:
            self._hackrf.center_freq_hz = self.state.center_freq_hz
        if self._device is not None and HAS_SOAPY:
            try:
                self._device.setFrequency(SOAPY_SDR_RX, 0, self.state.center_freq_hz)
            except Exception:
                pass

    def _advance_ble_hop(self) -> None:
        if not (self.state.ble_hop and self.state.ble_decode):
            return
        self._hop_idx = (self._hop_idx + 1) % len(ADV_HOP_CHANNELS)
        ch = ADV_HOP_CHANNELS[self._hop_idx]
        self._apply_center_freq(float(BLE_CHANNELS_MHZ[ch]) * 1e6)

    async def configure(
        self,
        *,
        center_freq_hz: float | None = None,
        sample_rate_hz: float | None = None,
        gain_db: float | None = None,
        demo: bool | None = None,
        demo_class: str | None = None,
        device_args: str | None = None,
        ble_decode: bool | None = None,
        ble_hop: bool | None = None,
    ) -> TunerState:
        async with self._lock:
            if demo_class in CAPTURE_ONLY_CLASSES:
                raise ValueError(f"{demo_class} requires captured IQ; no demo generator is available")
            if center_freq_hz is not None:
                self.state.center_freq_hz = float(center_freq_hz)
            if sample_rate_hz is not None:
                self.state.sample_rate_hz = float(sample_rate_hz)
            if gain_db is not None:
                self.state.gain_db = float(gain_db)
            if demo is not None:
                self.state.demo = bool(demo)
            if demo_class is not None and demo_class in SIGNAL_CLASSES:
                self.state.demo_class = demo_class
            if device_args is not None:
                self.state.device_args = device_args
            if ble_decode is not None:
                self.state.ble_decode = bool(ble_decode)
            if ble_hop is not None:
                self.state.ble_hop = bool(ble_hop)

            self._close_all()

            if self.state.demo:
                self.state.backend = "demo"
                return self.state

            args = self._parse_args()
            driver = args.get("driver", "hackrf").lower()

            if driver == "hackrf" and hackrf_available():
                try:
                    await asyncio.to_thread(self._open_hackrf, args.get("serial"))
                    self.state.demo = False
                    self.state.backend = "hackrf"
                    return self.state
                except Exception as exc:
                    self.state.demo = True
                    self.state.backend = f"demo (hackrf failed: {exc})"
                    return self.state

            if HAS_SOAPY:
                try:
                    await asyncio.to_thread(self._open_soapy, args)
                    self.state.demo = False
                    self.state.backend = "soapy"
                    return self.state
                except Exception as exc:
                    self.state.demo = True
                    self.state.backend = f"demo (soapy failed: {exc})"
                    return self.state

            self.state.demo = True
            self.state.backend = "demo (no hardware backend)"
            return self.state

    def _open_hackrf(self, serial: str | None) -> None:
        self._hackrf = HackRFCapture(
            center_freq_hz=self.state.center_freq_hz,
            sample_rate_hz=self.state.sample_rate_hz,
            gain_db=self.state.gain_db,
            serial=serial or None,
        )
        # Prefer streaming for live BLE
        try:
            self._hackrf.start_stream()
        except Exception:
            # one-shot mode still works in read path
            pass

    def _open_soapy(self, args: dict[str, str]) -> None:
        if not args:
            args = {"driver": "hackrf"}
        self._device = SoapySDR.Device(args)
        self._device.setSampleRate(SOAPY_SDR_RX, 0, self.state.sample_rate_hz)
        self._device.setFrequency(SOAPY_SDR_RX, 0, self.state.center_freq_hz)
        try:
            self._device.setGain(SOAPY_SDR_RX, 0, self.state.gain_db)
        except Exception:
            pass
        try:
            self._device.setBandwidth(
                SOAPY_SDR_RX, 0, min(self.state.sample_rate_hz, 5e6)
            )
        except Exception:
            pass
        self._stream = self._device.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        self._device.activateStream(self._stream)

    def _close_all(self) -> None:
        if self._hackrf is not None:
            self._hackrf.stop_stream()
            self._hackrf = None
        if self._device is not None and self._stream is not None:
            try:
                self._device.deactivateStream(self._stream)
                self._device.closeStream(self._stream)
            except Exception:
                pass
        self._stream = None
        self._device = None

    def _serial_from_args(self) -> str | None:
        return self._parse_args().get("serial") or None

    async def replay_ble_packet(
        self,
        *,
        header_hex: str,
        payload_hex: str,
        channel: int,
        sample_rate_hz: float | None = None,
        tx_gain: int = 20,
        amp: bool = False,
        repeats: int = 3,
        dry_run: bool = False,
    ) -> dict:
        """Pause RX, modulate PDU, TX via HackRF, then restore RX if needed."""
        if channel not in BLE_CHANNELS_MHZ:
            raise ValueError(f"Unsupported BLE channel {channel}")
        rate = float(sample_rate_hz or self.state.sample_rate_hz or 4e6)
        freq_hz = float(BLE_CHANNELS_MHZ[channel]) * 1e6
        iq = modulate_ble_packet_iq(
            header_hex=header_hex,
            payload_hex=payload_hex,
            channel=channel,
            sample_rate=rate,
            repeats=repeats,
        )
        meta = {
            "channel": channel,
            "center_freq_hz": freq_hz,
            "sample_rate_hz": rate,
            "iq_samples": int(iq.size),
            "repeats": int(repeats),
            "header_hex": header_hex,
            "payload_hex": payload_hex,
        }
        if dry_run:
            return {**meta, "ok": True, "dry_run": True, "transmitted": False}

        if not hackrf_available():
            raise RuntimeError("hackrf_transfer not available")

        async with self._lock:
            was_live = self._hackrf is not None or self._device is not None
            serial = self._serial_from_args()
            # Release device for exclusive TX
            self._close_all()
            try:
                tx = await asyncio.to_thread(
                    transmit_iq,
                    iq,
                    center_freq_hz=freq_hz,
                    sample_rate_hz=rate,
                    tx_gain=tx_gain,
                    amp=amp,
                    serial=serial,
                )
            finally:
                if was_live and not self.state.demo:
                    args = self._parse_args()
                    driver = args.get("driver", "hackrf").lower()
                    try:
                        if driver == "hackrf" and hackrf_available():
                            await asyncio.to_thread(
                                self._open_hackrf, args.get("serial")
                            )
                            self.state.backend = "hackrf"
                        elif HAS_SOAPY:
                            await asyncio.to_thread(self._open_soapy, args)
                            self.state.backend = "soapy"
                    except Exception as exc:
                        self.state.demo = True
                        self.state.backend = f"demo (reopen failed: {exc})"
            return {**meta, **tx, "dry_run": False, "transmitted": True}

    def _read_iq(self, n: int = 8192) -> np.ndarray:
        if self.state.demo or self.state.backend.startswith("demo"):
            if self.state.demo_class == "Bluetooth-LE" or self.state.ble_decode:
                # Every ~4th frame inject LED Remote A so the LED filter is testable
                use_led = (self._frame_i % 4) == 0
                ch = ADV_HOP_CHANNELS[self._hop_idx % 3]
                if use_led:
                    packet = modulate_ble_packet_iq(
                        header_hex="42",
                        payload_hex=_DEMO_LED_PAYLOAD.hex(),
                        channel=ch,
                        sample_rate=self.state.sample_rate_hz,
                        repeats=2,
                        amplitude=0.85,
                    )
                else:
                    packet = generate_ble_adv_iq(
                        sample_rate=self.state.sample_rate_hz,
                        channel=ch,
                        snr_db=18.0 + 2.0 * np.sin(time.time()),
                        seed=None,
                        n_pad=max(200, n // 10),
                    )
                if packet.size >= n:
                    start = int(time.time() * 1000) % max(1, packet.size - n + 1)
                    return packet[start : start + n]
                pad = n - packet.size
                noise = (
                    np.random.normal(0, 0.04, pad)
                    + 1j * np.random.normal(0, 0.04, pad)
                ).astype(np.complex64)
                return np.concatenate([noise[: pad // 2], packet, noise[pad // 2 :]])
            return generate_iq(
                self.state.demo_class,
                n=n,
                snr_db=12.0 + 3.0 * np.sin(time.time()),
                seed=None,
            )

        if self._hackrf is not None:
            try:
                return self._hackrf.read_stream(n)
            except Exception:
                return self._hackrf.capture_samples(n)

        if self._device is not None and self._stream is not None:
            buff = np.empty(n, dtype=np.complex64)
            sr = self._device.readStream(self._stream, [buff], n, timeoutUs=1_000_000)
            if sr.ret > 0:
                return buff[: sr.ret]

        return np.zeros(n, dtype=np.complex64)

    async def next_frame(self) -> dict:
        # Serialize with replay so HackRF is not opened twice
        async with self._lock:
            return await self._next_frame_unlocked()

    async def _next_frame_unlocked(self) -> dict:
        self._frame_i += 1
        # Shorter dwell when hopping so button-press bursts are less likely missed
        if self.state.ble_decode and self.state.ble_hop:
            n = int(max(160_000, min(480_000, self.state.sample_rate_hz * 0.05)))
        elif self.state.ble_decode:
            n = int(max(320_000, min(1_000_000, self.state.sample_rate_hz * 0.1)))
        else:
            n = 16384
        iq = await asyncio.to_thread(self._read_iq, n)
        self._iq_ring.append(iq)
        spec = iq_to_spectrogram(iq)
        img = Image.fromarray((np.clip(spec, 0, 1) * 255).astype(np.uint8), mode="L")
        small = np.asarray(img.resize((128, 128)), dtype=np.uint8)

        ble: dict | None = None
        if self.state.ble_decode:
            cat = np.concatenate(list(self._iq_ring))
            if cat.size > 400_000:
                cat = cat[-400_000:]
            ble = await asyncio.to_thread(
                decode_ble_iq,
                cat,
                sample_rate=self.state.sample_rate_hz,
                center_freq_hz=self.state.center_freq_hz,
            )

        power = float(np.mean(np.abs(iq) ** 2) + 1e-18)
        peak = float(np.max(np.abs(iq)) + 1e-18)
        out = {
            "spectrogram": spec,
            "preview": small.tolist(),
            "iq": iq,
            "ble": ble,
            "tuner": {
                "center_freq_hz": self.state.center_freq_hz,
                "sample_rate_hz": self.state.sample_rate_hz,
                "gain_db": self.state.gain_db,
                "demo": self.state.demo,
                "demo_class": self.state.demo_class,
                "device_args": self.state.device_args,
                "ble_decode": self.state.ble_decode,
                "ble_hop": self.state.ble_hop,
                "backend": self.state.backend,
                "rf_power_dbfs": 10.0 * float(np.log10(power)),
                "rf_peak": peak,
                "iq_samples": int(iq.size),
            },
        }
        # Hop after this dwell so the next frame is on the next ADV channel
        self._advance_ble_hop()
        return out
