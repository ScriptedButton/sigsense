"""P25 and DMR data trellis codes (CONTRACT v1). Owner: WP-A. Original code.

The data blocks of P25 (unconfirmed 1/2, confirmed 3/4, header 1/2; TIA-102.BAAA-A
section 7) and DMR (rate 3/4; ETSI TS 102 361-1 B.2.4) use a finite-state-machine
trellis code: the next state IS the input symbol, and the output for (state, input)
is one of 16 constellation points, each a pair of 4-level symbols (dibits). 48 input
symbols plus one flushing zero symbol give 49 points = 98 dibits, which are then
block-interleaved. Tables are transcribed in _specdata (and checked in the tests
against the other spec's copy where both define the same table).

Dibit values are 0..3 with the standard 4FSK/C4FM mapping 01=+3, 00=+1, 10=-1, 11=-3
(TIA-102.BAAA-A Table 9-1, ETSI TS 102 361-1 Table 10.3). `encode` returns the 98
dibits in transmission order; `decode` takes them in that order and runs a hard
decision Viterbi search that must end in state 0 (the flush symbol).
"""

from __future__ import annotations

import numpy as np

from sigsense_api.dvoice.fec import _specdata as spec
from sigsense_api.dvoice.fec.types import FecResult

# symbol level -> dibit value (Bit1 Bit0): +3 = 01, +1 = 00, -1 = 10, -3 = 11
_LEVEL_TO_DIBIT = {3: 0b01, 1: 0b00, -1: 0b10, -3: 0b11}
_POINT_DIBITS = np.array(
    [[_LEVEL_TO_DIBIT[a], _LEVEL_TO_DIBIT[b]] for a, b in spec.TRELLIS_CONSTELLATION], dtype=np.uint8
)  # [16, 2]
_INTERLEAVE = np.array(spec.TRELLIS_INTERLEAVE, dtype=np.int64)  # tx[i] = enc[_INTERLEAVE[i]]
_POPCOUNT2 = np.array([0, 1, 1, 2], dtype=np.int64)

#: Path-metric limits (bit errors of 196) above which a block is reported ok=False. Measured on
#: random dibit input: the rate-1/2 metric is never below 23 (median 28) and the rate-3/4 metric
#: never below 8 (median 13), while damaged valid blocks stay at or under 10 (1/2, six dibit
#: errors) and 7 (3/4, four dibit errors). These are heuristics: a Viterbi decoder always
#: produces output, and the rate-3/4 code corrects few errors (a single dibit error decodes
#: wrongly about a third of the time), so callers MUST verify the block CRC (CRC-9 / CRC-CCITT).
MAX_BIT_ERRORS_HALF = 15
MAX_BIT_ERRORS_THREE_QUARTER = 7


class _TrellisCode:
    def __init__(self, transition, symbol_bits: int, max_bit_errors: int, steps: int = 48) -> None:
        self.max_bit_errors = max_bit_errors
        self.transition = np.array(transition, dtype=np.int64)  # [state, input] -> point
        self.states = self.transition.shape[0]
        self.symbol_bits = symbol_bits
        self.steps = steps  # information symbols (a flush symbol follows)
        if self.transition.shape[1] != (1 << symbol_bits):
            raise ValueError("transition table width must be 2^symbol_bits")
        # Expected dibit pair for every (state, input): [state, input, 2]
        self.expected = _POINT_DIBITS[self.transition]

    @property
    def info_bits(self) -> int:
        return self.steps * self.symbol_bits

    def encode(self, data_bits: np.ndarray) -> np.ndarray:
        bits = np.asarray(data_bits, dtype=np.uint8).ravel()
        if bits.size != self.info_bits:
            raise ValueError(f"need {self.info_bits} data bits")
        weights = 1 << np.arange(self.symbol_bits - 1, -1, -1)
        symbols = (bits.reshape(-1, self.symbol_bits) * weights).sum(axis=1).tolist() + [0]  # flush
        state = 0
        enc = np.empty(2 * len(symbols), dtype=np.uint8)
        for i, sym in enumerate(symbols):
            enc[2 * i : 2 * i + 2] = self.expected[state, sym]
            state = sym
        return enc[_INTERLEAVE]

    def decode(self, dibits: np.ndarray) -> FecResult:
        rx = np.asarray(dibits, dtype=np.int64).ravel()
        if rx.size != 2 * (self.steps + 1):
            raise ValueError(f"need {2 * (self.steps + 1)} dibits")
        if rx.min() < 0 or rx.max() > 3:
            raise ValueError("dibits must be 0..3")
        enc = np.empty_like(rx)
        enc[_INTERLEAVE] = rx  # undo the interleave: tx[i] = enc[_INTERLEAVE[i]]
        pairs = enc.reshape(-1, 2)
        nsym = self.transition.shape[1]
        inf = 10**9
        metric = np.full(self.states, inf, dtype=np.int64)
        metric[0] = 0
        history = np.zeros((self.steps + 1, self.states), dtype=np.int16)  # previous state
        symbol = np.zeros((self.steps + 1, self.states), dtype=np.int16)   # input that led here
        for t in range(self.steps + 1):
            # cost[state, input] for this step
            branch = (
                _POPCOUNT2[self.expected[:, :, 0].astype(np.int64) ^ pairs[t, 0]]
                + _POPCOUNT2[self.expected[:, :, 1].astype(np.int64) ^ pairs[t, 1]]
            )
            total = metric[:, None] + branch  # [state, input]
            inputs = range(nsym) if t < self.steps else (0,)  # the last symbol is the zero flush
            new_metric = np.full(self.states, inf, dtype=np.int64)
            for x in inputs:
                # next state == x (the FSM property), so only column x competes for state x
                col = total[:, x]
                best = int(np.argmin(col))
                new_metric[x] = col[best]
                history[t, x] = best
                symbol[t, x] = x
            metric = new_metric
        # The flush forces the final state to 0.
        state = 0
        errors = int(metric[0])
        decoded = []
        for t in range(self.steps, -1, -1):
            if t < self.steps:
                decoded.append(state)  # input at step t equals the state it led to
            state = int(history[t, state])
        decoded.reverse()
        weights = 1 << np.arange(self.symbol_bits - 1, -1, -1)
        bits = ((np.array(decoded)[:, None] & weights) > 0).astype(np.uint8).ravel()
        return FecResult(bits, errors, errors <= self.max_bit_errors)


_P25_HALF = _TrellisCode(spec.P25_TRELLIS_12_TRANSITION, 2, MAX_BIT_ERRORS_HALF)
_P25_THREE_QUARTER = _TrellisCode(spec.DMR_TRELLIS_34_TRANSITION, 3, MAX_BIT_ERRORS_THREE_QUARTER)  # same table as DMR; test-verified
_DMR_THREE_QUARTER = _TrellisCode(spec.DMR_TRELLIS_34_TRANSITION, 3, MAX_BIT_ERRORS_THREE_QUARTER)


def p25_trellis_half_decode(dibits: np.ndarray) -> FecResult:
    """P25 rate-1/2 trellis: 98 dibits (196 bits) -> 12 bytes (TSBK block incl. CRC)."""
    return _P25_HALF.decode(dibits)


def p25_trellis_half_encode(data_bits: np.ndarray) -> np.ndarray:
    return _P25_HALF.encode(data_bits)


def p25_trellis_three_quarter_decode(dibits: np.ndarray) -> FecResult:
    """P25 rate-3/4 trellis data block: 98 dibits -> 18 bytes."""
    return _P25_THREE_QUARTER.decode(dibits)


def p25_trellis_three_quarter_encode(data_bits: np.ndarray) -> np.ndarray:
    return _P25_THREE_QUARTER.encode(data_bits)


def dmr_trellis_three_quarter_decode(dibits: np.ndarray) -> FecResult:
    """DMR rate-3/4 trellis data/CSBK block: 98 dibits -> 144 bits."""
    return _DMR_THREE_QUARTER.decode(dibits)


def dmr_trellis_three_quarter_encode(data_bits: np.ndarray) -> np.ndarray:
    return _DMR_THREE_QUARTER.encode(data_bits)
