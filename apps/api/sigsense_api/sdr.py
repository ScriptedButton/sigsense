"""Live SDR: HackRF via hackrf_transfer, optional SoapySDR, else demo IQ."""

from __future__ import annotations

import asyncio
import time
import uuid
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
from sigsense_api.upconverters import get_upconverter
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


def display_fft_size(sample_rate_hz: float) -> int:
    """FFT length giving bins of about 1 kHz or finer, within 1024..4096.

    Narrow signals such as 12.5 kHz GMRS FM need several bins across them;
    very wide spans are capped to keep each stream message small.
    """
    size = 1024
    while size < 4096 and sample_rate_hz / size > 1000:
        size *= 2
    return size


@dataclass
class TunerState:
    # Match JiaoXianjun/BTLE btle_rx defaults: 4 MS/s, ~2 MHz BB, modest gain
    center_freq_hz: float = 2402e6
    sample_rate_hz: float = 4e6
    gain_db: float = 32.0
    # HackRF front-end stages (dB). None derives them from gain_db.
    lna_gain_db: int | None = None
    vga_gain_db: int | None = None
    amp_enable: bool | None = None
    demo: bool = True
    demo_class: str = "Bluetooth-LE"
    running: bool = False
    device_args: str = "driver=hackrf"
    ble_decode: bool = True
    ble_hop: bool = True
    backend: str = "demo"  # demo | hackrf | soapy
    # center_freq_hz is always the RF frequency; an upconverter only shifts
    # what the hardware is tuned to.
    upconverter: str = "none"

    @property
    def lo_offset_hz(self) -> float:
        converter = get_upconverter(self.upconverter)
        return converter.lo_hz if converter else 0.0

    def rf_range_hz(self) -> tuple[float, float, str]:
        converter = get_upconverter(self.upconverter)
        if converter:
            return converter.rf_min_hz, converter.rf_max_hz, converter.label
        # HackRF Pro (and demo IQ): 100 kHz–6 GHz. Classic HackRF One is
        # specified from 1 MHz; Pro extends the low end for MW AM etc.
        return 1e5, 6e9, "HackRF Pro / demo"


def _validate_rf(freq_hz: float, state: TunerState) -> None:
    low, high, source = state.rf_range_hz()
    if not np.isfinite(freq_hz) or not low <= freq_hz <= high:
        raise ValueError(
            f"Invalid center frequency {freq_hz:g} Hz; {source} covers "
            f"{low:g}..{high:g} Hz"
        )


@dataclass
class LiveSDRService:
    state: TunerState = field(default_factory=TunerState)
    _device: Any = None
    _stream: Any = None
    _hackrf: HackRFCapture | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _ble_connections: list[dict] = field(default_factory=list)
    _hop_idx: int = 0
    _frame_i: int = 0
    _recent_frames: deque = field(default_factory=lambda: deque(maxlen=16))
    scan: dict = field(default_factory=lambda: {"enabled": False})
    _scan_points: list = field(default_factory=list)
    _scan_index: int = 0
    _scan_changed: float = 0.0
    _scan_power: Any = None
    _scan_frequencies: Any = None
    sensitivity_db: float = 10.0

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
            self._hackrf.lo_offset_hz = self.state.lo_offset_hz
        if self._device is not None and HAS_SOAPY:
            try:
                self._device.setFrequency(
                    SOAPY_SDR_RX,
                    0,
                    self.state.center_freq_hz + self.state.lo_offset_hz,
                )
            except Exception:
                pass

    def _advance_ble_hop(self) -> None:
        if (
            self.scan.get("enabled")
            or not (self.state.ble_hop and self.state.ble_decode)
            or get_upconverter(self.state.upconverter) is not None
        ):
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
        lna_gain_db: float | None = None,
        vga_gain_db: float | None = None,
        amp_enable: bool | None = None,
        demo: bool | None = None,
        demo_class: str | None = None,
        device_args: str | None = None,
        ble_decode: bool | None = None,
        ble_hop: bool | None = None,
        upconverter: str | None = None,
    ) -> TunerState:
        if upconverter is not None:
            get_upconverter(upconverter)
        upconverter_changed = (
            upconverter is not None and upconverter != self.state.upconverter
        )
        if center_freq_hz is not None or upconverter_changed:
            # Validate against the converter that will be in use afterwards.
            target = TunerState(
                upconverter=upconverter or self.state.upconverter
            )
            _validate_rf(
                self.state.center_freq_hz
                if center_freq_hz is None
                else center_freq_hz,
                target,
            )
        for label, value, low, high in [
            ("sample rate", sample_rate_hz, 2e6, 20e6),
            ("gain", gain_db, 0, 102),
            ("LNA gain", lna_gain_db, 0, 40),
            ("VGA gain", vga_gain_db, 0, 62),
        ]:
            if value is not None and (
                not np.isfinite(value) or not low <= value <= high
            ):
                raise ValueError(f"Invalid {label}; expected {low:g}..{high:g}")
        async with self._lock:
            prev_demo = self.state.demo
            prev_args = self.state.device_args
            if center_freq_hz is not None or upconverter_changed:
                self.scan["enabled"] = False
            if upconverter is not None:
                self.state.upconverter = upconverter
            if demo_class in CAPTURE_ONLY_CLASSES:
                raise ValueError(
                    f"{demo_class} requires captured IQ; no demo generator is available"
                )
            if center_freq_hz is not None:
                self.state.center_freq_hz = float(center_freq_hz)
            if sample_rate_hz is not None:
                self.state.sample_rate_hz = float(sample_rate_hz)
            if gain_db is not None:
                self.state.gain_db = float(gain_db)
                # A single total-gain request replaces any explicit stages.
                if lna_gain_db is None and vga_gain_db is None:
                    self.state.lna_gain_db = None
                    self.state.vga_gain_db = None
            # hackrf_transfer takes LNA in 8 dB and VGA in 2 dB steps.
            if lna_gain_db is not None:
                self.state.lna_gain_db = int(round(lna_gain_db / 8) * 8)
            if vga_gain_db is not None:
                self.state.vga_gain_db = int(round(vga_gain_db / 2) * 2)
            if amp_enable is not None:
                self.state.amp_enable = bool(amp_enable)
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

            # Retunes, gain and rate changes on an open HackRF don't need the
            # device closed and reopened: update it and restart only its stream.
            if (
                self._hackrf is not None
                and self.state.backend == "hackrf"
                and not self.state.demo
                and self.state.demo == prev_demo
                and self.state.device_args == prev_args
            ):
                self._hackrf.center_freq_hz = self.state.center_freq_hz
                self._hackrf.lo_offset_hz = self.state.lo_offset_hz
                self._hackrf.sample_rate_hz = self.state.sample_rate_hz
                self._hackrf.gain_db = self.state.gain_db
                self._hackrf.lna_db = self.state.lna_gain_db
                self._hackrf.vga_db = self.state.vga_gain_db
                self._hackrf.amp = self.state.amp_enable
                await asyncio.to_thread(self._hackrf.restart_stream)
                return self.state

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
            lna_db=self.state.lna_gain_db,
            vga_db=self.state.vga_gain_db,
            amp=self.state.amp_enable,
            serial=serial or None,
            lo_offset_hz=self.state.lo_offset_hz,
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
        self._device.setFrequency(
            SOAPY_SDR_RX, 0, self.state.center_freq_hz + self.state.lo_offset_hz
        )
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
                from sigsense_ml.ble import channel_from_freq_hz

                ch = channel_from_freq_hz(self.state.center_freq_hz)
                ch = ch if ch is not None else 37
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
                        payload=bytes.fromhex("010000000002020106")
                        + bytes([14, 9])
                        + b"SigSense demo",
                        snr_db=18.0 + 2.0 * np.sin(time.time()),
                        seed=None,
                        n_pad=max(200, n // 10),
                    )
                if packet.size >= n:
                    start = int(time.time() * 1000) % max(1, packet.size - n + 1)
                    return packet[start : start + n]
                pad = n - packet.size
                noise = (
                    np.random.normal(0, 0.04, pad) + 1j * np.random.normal(0, 0.04, pad)
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

    def audio_source(self) -> HackRFCapture | None:
        """The open HackRF capture, for the audio demodulator (no lock taken)."""
        return self._hackrf

    def release_stream(self) -> None:
        """Let go of the HackRF while nobody is listening; reads restart it."""
        if self._hackrf is not None:
            self._hackrf.stop_stream()

    def can_peek(self) -> bool:
        """True when the display can be fed from the live stream directly."""
        return (
            self._hackrf is not None
            and self._hackrf.streaming
            and not self.scan.get("enabled")
            and not (self.state.ble_decode and self.state.ble_hop)
        )

    def _measure(self, iq: np.ndarray) -> dict:
        """Spectrum at a resolution suited to the current span."""
        from sigsense_api.spectrum import measure_spectrum

        spectrum = measure_spectrum(
            iq,
            self.state.sample_rate_hz,
            self.state.center_freq_hz,
            fft_size=display_fft_size(self.state.sample_rate_hz),
            sensitivity_db=self.sensitivity_db,
        )
        # Tenth-of-a-dB precision is plenty and keeps each message small.
        spectrum["power_dbfs"] = [round(v, 1) for v in spectrum["power_dbfs"]]
        return spectrum

    def peek_spectrum(self, n: int | None = None) -> dict | None:
        """Spectrum of the newest streamed samples; does not consume them."""
        if not self.can_peek():
            return None
        fft = display_fft_size(self.state.sample_rate_hz)
        # Several averaged blocks per row keep narrow-bin traces steady.
        iq = self._hackrf.peek_latest(n or max(32768, fft * 8))
        if iq is None:
            return None

        return {
            "type": "spectrum",
            "spectrum": self._measure(iq),
            "sweep": None,
            "tuner": {
                "center_freq_hz": self.state.center_freq_hz,
                "sample_rate_hz": self.state.sample_rate_hz,
            },
        }

    async def next_frame(self, on_spectrum=None) -> dict:
        # Serialize with replay so HackRF is not opened twice
        async with self._lock:
            return await self._next_frame_unlocked(on_spectrum)

    async def _next_frame_unlocked(self, on_spectrum=None) -> dict:
        self._frame_i += 1
        if (
            self.scan.get("enabled")
            and time.monotonic() - self._scan_changed >= self.scan["dwell_ms"] / 1000
        ):
            self._scan_index = (self._scan_index + 1) % len(self._scan_points)
            if self._scan_index == 0:
                self.scan["sweeps"] += 1
            self._apply_center_freq(self._scan_points[self._scan_index])
            self._scan_changed = time.monotonic()
        # Shorter dwell when hopping so button-press bursts are less likely missed
        if self.state.ble_decode and self.state.ble_hop:
            n = int(max(160_000, min(320_000, self.state.sample_rate_hz * 0.04)))
        elif self.state.ble_decode:
            n = int(max(160_000, min(400_000, self.state.sample_rate_hz * 0.05)))
        else:
            n = 16384
        iq = await asyncio.to_thread(self._read_iq, n)
        spec = iq_to_spectrogram(iq)
        img = Image.fromarray((np.clip(spec, 0, 1) * 255).astype(np.uint8), mode="L")
        small = np.asarray(img.resize((128, 128)), dtype=np.uint8)

        power = float(np.mean(np.abs(iq) ** 2) + 1e-18)
        peak = float(np.max(np.abs(iq)) + 1e-18)
        spectrum = self._measure(iq)
        sweep = None
        if self.scan.get("enabled"):
            freq = (
                spectrum["start_hz"]
                + np.arange(len(spectrum["power_dbfs"])) * spectrum["bin_hz"]
            )
            valid = (self._scan_frequencies >= freq[0]) & (
                self._scan_frequencies <= freq[-1]
            )
            self._scan_power[valid] = np.interp(
                self._scan_frequencies[valid], freq, spectrum["power_dbfs"]
            )
            sweep = {
                "start_hz": float(self._scan_frequencies[0]),
                "bin_hz": float(self._scan_frequencies[1] - self._scan_frequencies[0]),
                "power_dbfs": [
                    None if not np.isfinite(v) else float(v) for v in self._scan_power
                ],
                "noise_floor_dbfs": spectrum["noise_floor_dbfs"],
                "timestamp": spectrum["timestamp"],
                "swept": True,
            }
        if on_spectrum is not None:
            # Push the display row now; BLE decode and ML classification
            # below must not hold back the waterfall.
            await on_spectrum(
                {
                    "type": "spectrum",
                    "spectrum": spectrum,
                    "sweep": sweep,
                    "tuner": {
                        "center_freq_hz": self.state.center_freq_hz,
                        "sample_rate_hz": self.state.sample_rate_hz,
                    },
                }
            )
        ble: dict | None = None
        from sigsense_ml.ble import channel_from_freq_hz

        if (
            self.state.ble_decode
            and channel_from_freq_hz(self.state.center_freq_hz) is not None
        ):
            # Each HackRF one-shot and channel dwell is a distinct capture.
            # Joining them invents continuity and can mix different RF channels.
            ble = await asyncio.to_thread(
                decode_ble_iq,
                iq,
                sample_rate=self.state.sample_rate_hz,
                center_freq_hz=self.state.center_freq_hz,
                connections=self._ble_connections,
            )
            self._ble_connections = ble.get("connections", [])
            ble["continuous"] = False
            ble["capture_note"] = (
                "Independent dwell captures; no RF connection following or cross-dwell reassembly"
            )

        out = {
            "frame_id": uuid.uuid4().hex,
            "spectrum": spectrum,
            "sweep": sweep,
            "scanner": {
                **self.scan,
                "position": self._scan_index,
                "steps": len(self._scan_points),
            },
            "spectrogram": spec,
            "preview": small.tolist(),
            "iq": iq,
            "ble": ble,
            "tuner": {
                "center_freq_hz": self.state.center_freq_hz,
                "sample_rate_hz": self.state.sample_rate_hz,
                "gain_db": self.state.gain_db,
                "lna_gain_db": self.state.lna_gain_db,
                "vga_gain_db": self.state.vga_gain_db,
                "amp_enable": self.state.amp_enable,
                "demo": self.state.demo,
                "demo_class": self.state.demo_class,
                "device_args": self.state.device_args,
                "ble_decode": self.state.ble_decode,
                "ble_hop": self.state.ble_hop,
                "backend": self.state.backend,
                "upconverter": self.state.upconverter,
                "lo_offset_hz": self.state.lo_offset_hz,
                "rf_power_dbfs": 10.0 * float(np.log10(power)),
                "rf_peak": peak,
                "iq_samples": int(iq.size),
            },
        }
        self._recent_frames.append(out)
        # Hop after this dwell so the next frame is on the next ADV channel
        self._advance_ble_hop()
        return out

    async def configure_scanner(
        self, *, enabled, start_hz=2402e6, stop_hz=2480e6, step_hz=2e6, dwell_ms=150
    ):
        low, high, source = self.state.rf_range_hz()
        if enabled and not low <= start_hz <= stop_hz <= high:
            raise ValueError(
                f"Scanner range must lie within {low:g}..{high:g} Hz ({source})"
            )
        if (
            not 1e3 <= start_hz <= stop_hz <= 6e9
            or not np.isfinite(step_hz)
            or step_hz <= 0
            or not 50 <= dwell_ms <= 5000
        ):
            raise ValueError("Invalid scanner range, step or dwell")
        count = int(np.floor((stop_hz - start_hz) / step_hz)) + 1
        if count > 256:
            raise ValueError("Scanner supports at most 256 tuning steps")
        async with self._lock:
            self.scan = {
                "enabled": bool(enabled),
                "start_hz": start_hz,
                "stop_hz": stop_hz,
                "step_hz": step_hz,
                "dwell_ms": dwell_ms,
                "sweeps": 0,
            }
            if enabled:
                self.state.ble_hop = False
                self._scan_points = [start_hz + i * step_hz for i in range(count)]
                self._scan_index = 0
                self._scan_changed = time.monotonic()
                self._scan_frequencies = np.linspace(
                    start_hz - self.state.sample_rate_hz / 2,
                    stop_hz + self.state.sample_rate_hz / 2,
                    1024,
                    endpoint=False,
                )
                self._scan_power = np.full(1024, np.nan)
                self._apply_center_freq(start_hz)
        return self.scan

    def captured_frame(self, frame_id):
        for frame in list(self._recent_frames):
            if frame["frame_id"] == frame_id:
                return frame
        raise KeyError("Capture expired; save a current signal frame")

    async def transmit_saved(
        self, iq, metadata, *, repeats=1, tx_gain=10, amp=False, dry_run=False
    ):
        rate = float(metadata["sample_rate_hz"])
        frequency = float(metadata["center_freq_hz"])
        if not 1 <= repeats <= 20 or len(iq) * repeats > min(10_000_000, rate * 5):
            raise ValueError(
                "Transmit is limited to 20 repeats, 5 seconds and 10 million samples"
            )
        details = {
            "center_freq_hz": frequency,
            "sample_rate_hz": rate,
            "samples": len(iq) * repeats,
            "repeats": repeats,
        }
        if dry_run:
            return {**details, "dry_run": True, "transmitted": False}
        async with self._lock:
            was_open = self._hackrf is not None or self._stream is not None
            args = self._parse_args()
            self._close_all()
            try:
                result = await asyncio.to_thread(
                    transmit_iq,
                    np.tile(iq, repeats),
                    center_freq_hz=frequency,
                    sample_rate_hz=rate,
                    tx_gain=tx_gain,
                    amp=amp,
                    serial=args.get("serial"),
                )
                return {**details, **result, "dry_run": False, "transmitted": True}
            finally:
                if was_open and not self.state.demo:
                    if args.get("driver", "hackrf") == "hackrf":
                        self._open_hackrf(args.get("serial"))
                    elif HAS_SOAPY:
                        self._open_soapy(args)
