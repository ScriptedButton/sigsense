"""Upconverters placed between the antenna and the SDR.

An upconverter mixes the antenna signal with a fixed local oscillator so
frequencies the SDR handles poorly (HF and below) land higher up. Every
frequency the app shows or stores stays the real RF frequency; only the
hardware tuning adds ``lo_hz``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Upconverter:
    id: str
    label: str
    lo_hz: float
    rf_min_hz: float
    rf_max_hz: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "lo_hz": self.lo_hz,
            "rf_min_hz": self.rf_min_hz,
            "rf_max_hz": self.rf_max_hz,
        }


# Nooelec Ham It Up (v1.3 / Plus): 125 MHz LO, low-side injection, so the
# IF is RF + 125 MHz and the spectrum is not inverted.
HAM_IT_UP = Upconverter(
    id="ham-it-up",
    label="Nooelec Ham It Up",
    lo_hz=125e6,
    rf_min_hz=10e3,
    rf_max_hz=65e6,
)

UPCONVERTERS: dict[str, Upconverter] = {HAM_IT_UP.id: HAM_IT_UP}


def get_upconverter(upconverter_id: str | None) -> Upconverter | None:
    """The upconverter for an id; ``None``/``"none"`` means direct to the SDR."""
    if not upconverter_id or upconverter_id == "none":
        return None
    try:
        return UPCONVERTERS[upconverter_id]
    except KeyError:
        raise ValueError(
            f"Unknown upconverter {upconverter_id!r}; expected one of "
            f"none, {', '.join(UPCONVERTERS)}"
        ) from None
