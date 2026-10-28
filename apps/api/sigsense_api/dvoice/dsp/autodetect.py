"""Modulation / protocol auto-detection (CONTRACT v1). Owner: WP-B (+ WP-G integration)."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

from sigsense_api.dvoice.dsp.cqpsk import CqpskSymbolRecovery
from sigsense_api.dvoice.dsp.discriminator import FmDiscriminator
from sigsense_api.dvoice.dsp.fsk import FskSymbolRecovery
from sigsense_api.dvoice.events import Modulation, Protocol
from sigsense_api.dvoice.sync.correlator import SyncCorrelator
from sigsense_api.dvoice.sync.patterns import SYNC_SPECS, SyncSpec

SAMPLE_RATE = 48_000.0

# What each protocol's decoder declares (see ProtocolContractTests.EXPECTED).
MODULATION_OF = {
    Protocol.P25P1: Modulation.C4FM,
    Protocol.P25P2: Modulation.CQPSK,
    Protocol.DMR: Modulation.FSK4,
    Protocol.NXDN48: Modulation.FSK4,
    Protocol.NXDN96: Modulation.FSK4,
    Protocol.DSTAR: Modulation.GMSK,
    Protocol.YSF: Modulation.C4FM,
    Protocol.M17: Modulation.FSK4,
}

DEFAULT_CANDIDATES = list(MODULATION_OF)


_WEIGHT_CACHE: dict[tuple[str, int], float] = {}


def hit_weight(spec: SyncSpec, errors: int) -> float:
    """Evidence (0..1) that a sync hit with `errors` mismatches is real, from how improbable it is
    on random data: P = P(a random symbol string of the spec's length matches with <= errors
    errors) and w = clip((-log10 P - 3) / 6, 0, 1). A 24-symbol 4-level word is near 1 even with 4
    errors; the 24-symbol 2-level D-STAR word with 3 errors (P = 1.4e-4) only gets 0.14, and an
    8-symbol M17 word 0.3 (exact) / 0.08 (one error): they must repeat to count."""
    key = (spec.name, errors)
    if key not in _WEIGHT_CACHE:
        levels = 2 if spec.protocol == Protocol.DSTAR else 4
        q = 1.0 / levels
        n = len(spec.dibits)
        p = sum(math.comb(n, k) * (1.0 - q) ** k * q ** (n - k) for k in range(errors + 1))
        _WEIGHT_CACHE[key] = float(min(1.0, max(0.0, (-math.log10(max(p, 1e-300)) - 3.0) / 6.0)))
    return _WEIGHT_CACHE[key]


@dataclass
class ProtocolHypothesis:
    protocol: Protocol
    modulation: Modulation
    symbol_rate: float
    score: float  # 0..1, from sync hits + level statistics


@dataclass
class _Chain:
    kind: str  # "fsk" | "cqpsk"
    symbol_rate: float
    levels: int
    modulation: Modulation
    protocols: list[Protocol]
    recovery: object
    correlator: SyncCorrelator
    spec_protocol: dict[str, Protocol]  # spec name -> protocol this chain reports for it


def _specs_for(protocol: Protocol, rate: float) -> list[SyncSpec]:
    if protocol == Protocol.NXDN48:  # same frame sync word as NXDN96, at half the symbol rate
        return [SYNC_SPECS["nxdn_fsw"]]
    return [s for s in SYNC_SPECS.values() if s.protocol == protocol and abs(s.symbol_rate - rate) < 1]


class AutoDetector:
    """Runs cheap candidate symbol-rate/modulation front ends in parallel on a short
    buffer and scores them by sync-word hits; used when the config leaves protocol='auto'.

    Chains (one per distinct symbol rate / slicer, so five at most):
      * 4800 baud 4-level FSK: P25 Phase 1 (C4FM), DMR, YSF, M17, NXDN96
      * 2400 baud 4-level FSK: NXDN48 (same frame sync word)
      * 4800 baud 2-level GMSK: D-STAR
      * 6000 baud CQPSK: P25 Phase 2
      * 4800 baud CQPSK: P25 Phase 1 (simulcast CQPSK/LSM)

    Each hit adds evidence `hit_weight(spec, errors)` (how improbable the match is on random data;
    see there) inside a sliding window; `score = 1 - exp(-0.5 * sum(w))`. Short or loose words
    (M17, NXDN, D-STAR) false-alarm in noise, so one hit scores low; a repeating sync (every
    frame) drives the score toward 1 within a fraction of a second.
    """

    def __init__(self, candidates: Optional[list[Protocol]] = None, window_s: float = 1.5, min_score: float = 0.15) -> None:
        self.candidates = list(candidates) if candidates else list(DEFAULT_CANDIDATES)
        self.window_s = float(window_s)
        self.min_score = float(min_score)
        self._disc = FmDiscriminator(SAMPLE_RATE)
        self._chains = self._build_chains()
        self.reset()

    def _build_chains(self) -> list[_Chain]:
        plan: list[tuple[str, float, int, Modulation, list[Protocol]]] = [
            ("fsk", 4800.0, 4, Modulation.C4FM, [Protocol.P25P1, Protocol.DMR, Protocol.YSF, Protocol.M17, Protocol.NXDN96]),
            ("fsk", 2400.0, 4, Modulation.FSK4, [Protocol.NXDN48]),
            ("fsk", 4800.0, 2, Modulation.GMSK, [Protocol.DSTAR]),
            ("cqpsk", 6000.0, 4, Modulation.CQPSK, [Protocol.P25P2]),
            ("cqpsk", 4800.0, 4, Modulation.CQPSK, [Protocol.P25P1]),
        ]
        chains: list[_Chain] = []
        for kind, rate, levels, modulation, protocols in plan:
            wanted = [p for p in protocols if p in self.candidates]
            if not wanted:
                continue
            specs: list[SyncSpec] = []
            owner: dict[str, Protocol] = {}
            for p in wanted:
                for spec in _specs_for(p, rate):
                    if spec.name not in owner:
                        specs.append(spec)
                        owner[spec.name] = p
            if not specs:
                continue
            recovery = (
                FskSymbolRecovery(rate, SAMPLE_RATE, levels, modulation) if kind == "fsk" else CqpskSymbolRecovery(rate, SAMPLE_RATE)
            )
            chains.append(_Chain(kind, rate, levels, modulation, wanted, recovery, SyncCorrelator(specs), owner))
        return chains

    def reset(self) -> None:
        self._disc.reset()
        for chain in self._chains:
            chain.recovery.reset()
            chain.correlator.reset()
        self._events: deque = deque()  # (time_s, protocol, modulation, rate, weight)
        self._samples = 0

    def feed(self, iq: np.ndarray) -> list[ProtocolHypothesis]:
        """Complex 48 kHz baseband in; best hypotheses first (may be empty)."""
        x = np.ascontiguousarray(iq, dtype=np.complex64)
        if x.size == 0:
            return self._hypotheses()
        disc = self._disc.process(x)
        self._samples += x.size
        now = self._samples / SAMPLE_RATE
        for chain in self._chains:
            block = chain.recovery.process(disc if chain.kind == "fsk" else x)
            if block.dibits.size == 0:
                continue
            for hit in chain.correlator.scan(block.dibits, block.first_index):
                spec = SYNC_SPECS[hit.spec]
                weight = hit_weight(spec, hit.errors)
                if weight > 0:
                    protocol = chain.spec_protocol[hit.spec]
                    mod = MODULATION_OF.get(protocol, chain.modulation)
                    # P25 Phase 1 seen on the CQPSK chain is the simulcast variant
                    if chain.kind == "cqpsk":
                        mod = Modulation.CQPSK
                    self._events.append((now, protocol, mod, chain.symbol_rate, weight))
        return self._hypotheses()

    def _hypotheses(self) -> list[ProtocolHypothesis]:
        now = self._samples / SAMPLE_RATE
        while self._events and now - self._events[0][0] > self.window_s:
            self._events.popleft()
        acc: dict[tuple, float] = {}
        for _, protocol, mod, rate, weight in self._events:
            acc[(protocol, mod, rate)] = acc.get((protocol, mod, rate), 0.0) + weight
        out = [
            ProtocolHypothesis(protocol, mod, rate, float(1.0 - np.exp(-0.5 * total)))
            for (protocol, mod, rate), total in acc.items()
        ]
        out = [h for h in out if h.score >= self.min_score]
        return sorted(out, key=lambda h: -h.score)
