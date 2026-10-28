"""Carrier-offset tracking (CONTRACT v1). Owner: WP-B."""

from __future__ import annotations


class FrequencyTracker:
    """Slow loop that turns a measured DC offset on the discriminator output (or the
    FLL error) into a correction for `Channelizer.set_offset`. Bounded +/- `limit_hz`.

    Closed-loop use (the engine does this once per block):

        err = block.meta["freq_error_hz"]          # residual offset the front end still sees
        correction = tracker.update(err)           # Hz, accumulated and clamped
        channelizer.set_offset(base_offset_hz + correction)

    The correction is an integrator: each update adds `bandwidth * error`, so the residual
    shrinks geometrically by (1 - bandwidth) per update and the loop is stable for
    0 < bandwidth <= 1. `update` ignores non-finite measurements and a small dead band
    (`dead_band_hz`) so a noise-level estimate does not make the channel filter creep.
    """

    def __init__(self, limit_hz: float = 1500.0, bandwidth: float = 0.01, dead_band_hz: float = 0.0) -> None:
        if limit_hz <= 0 or not 0 < bandwidth <= 1:
            raise ValueError("limit_hz must be positive and bandwidth in (0, 1]")
        self.limit_hz = float(limit_hz)
        self.bandwidth = float(bandwidth)
        self.dead_band_hz = float(dead_band_hz)
        self.correction_hz = 0.0

    def update(self, error_hz: float) -> float:
        """Feed a measurement, get the current correction in Hz."""
        if error_hz == error_hz and abs(error_hz) != float("inf") and abs(error_hz) > self.dead_band_hz:
            self.correction_hz += self.bandwidth * error_hz
            self.correction_hz = max(-self.limit_hz, min(self.limit_hz, self.correction_hz))
        return self.correction_hz

    def reset(self) -> None:
        self.correction_hz = 0.0
