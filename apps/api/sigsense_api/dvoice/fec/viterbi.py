"""Convolutional codes + Viterbi (CONTRACT v1). Owner: WP-A. Original code.

For NXDN, M17, D-STAR, YSF and P25 P2 style rate-1/2 codes. (P25/DMR data
"trellis" codes are not plain convolutional codes: see trellis.py.)
Soft decisions: pass `soft` in [-1, +1] (+1 = confident 0, -1 = confident 1) or None.

Polynomial convention (important, it differs from some libraries): a generator
polynomial is an int whose bit (K-1) multiplies the NEWEST input bit and bit 0
the oldest, i.e. G(D) = sum g_i D^i is stored as sum g_i << (K-1-i). Use
`poly_from_delays((0, 3, 4), 5)` to build it from the D-exponents printed in a spec
(G1(D) = 1 + D^3 + D^4 -> 0o23). Outputs are interleaved G1, G2, ... per input bit.
Puncturing: `puncture` is a repeating 0/1 keep-mask over the coded stream; `tail_bits`
zeros are appended by the encoder and decoding is forced to end in the zero state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from sigsense_api.dvoice.fec.types import FecResult


@dataclass(frozen=True)
class ConvCode:
    constraint_length: int
    polys: tuple[int, ...]  # generator polynomials; MSB = newest input (see module doc)
    puncture: Optional[tuple[int, ...]] = None  # repeating 0/1 keep-mask over output bits
    tail_bits: int = 0  # flush bits appended by the encoder


def poly_from_delays(delays: Sequence[int], constraint_length: int) -> int:
    """Generator polynomial from the D-exponents of G(D), e.g. (0, 3, 4) for 1 + D^3 + D^4."""
    value = 0
    for d in delays:
        if not 0 <= d < constraint_length:
            raise ValueError("delay outside the constraint length")
        value |= 1 << (constraint_length - 1 - d)
    return value


# --- Named codes ---------------------------------------------------------------------
# NXDN TS 1-A v1.3 section 4.5: K=5, R=1/2, G1(D) = 1 + D^3 + D^4, G2(D) = 1 + D + D^2 + D^4,
# 4 tail bits, outputs read alternately G1, G2. M17 (spec appendix C) uses the same code.
_K5_G1 = poly_from_delays((0, 3, 4), 5)  # 0o23
_K5_G2 = poly_from_delays((0, 1, 2, 4), 5)  # 0o35
NXDN_K5_R12 = ConvCode(5, (_K5_G1, _K5_G2), None, 4)
# NXDN CAC (outbound) puncturing matrix rows "1111111" / "1011101" (TS 1-A 4.5.1.1), as a
# linear keep-mask over the G1,G2-alternating output stream: 14 outputs per 7 inputs.
NXDN_CAC_PUNCTURE = (1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1)
NXDN_K5_R12_CAC = ConvCode(5, (_K5_G1, _K5_G2), NXDN_CAC_PUNCTURE, 4)
# M17 Appendix E: P1 (61 entries, used 8 times for the LSF), P2 (12 entries, stream frames),
# P3 (8 entries, packet mode).
M17_P1 = tuple([1] + [1, 0, 1, 1] * 15)
M17_P2 = (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0)
M17_P3 = (1, 1, 1, 1, 1, 1, 1, 0)
M17_K5_R12 = ConvCode(5, (_K5_G1, _K5_G2), None, 4)
M17_K5_R12_P1 = ConvCode(5, (_K5_G1, _K5_G2), M17_P1, 4)
M17_K5_R12_P2 = ConvCode(5, (_K5_G1, _K5_G2), M17_P2, 4)
M17_K5_R12_P3 = ConvCode(5, (_K5_G1, _K5_G2), M17_P3, 4)
# D-STAR (JARL spec AP2): K=3, rate 1/2, G1(D) = 1 + D + D^2 (odd output), G2(D) = 1 + D^2 (even
# output), two zero tail bits, header bits fed LSB first (the caller orders the bits).
DSTAR_K3_R12 = ConvCode(3, (poly_from_delays((0, 1, 2), 3), poly_from_delays((0, 2), 3)), None, 2)
# PLACEHOLDER, NOT A YSF CODE: no public Yaesu System Fusion specification was readable, so this
# merely aliases the NXDN code to keep the contract name importable. WP-E must not rely on it.
YSF_K5_R12 = ConvCode(5, (_K5_G1, _K5_G2), None, 4)


def _mask(code: ConvCode):
    return None if code.puncture is None else np.array(code.puncture, dtype=bool)


def _outputs(code: ConvCode) -> np.ndarray:
    """out[state, input, j] = j-th coded bit for (state, input); state holds the K-1 previous
    inputs with the newest in the MSB."""
    k = code.constraint_length
    nstates = 1 << (k - 1)
    table = np.zeros((nstates, 2, len(code.polys)), dtype=np.uint8)
    for s in range(nstates):
        for u in (0, 1):
            reg = (u << (k - 1)) | s
            for j, g in enumerate(code.polys):
                table[s, u, j] = bin(reg & g).count("1") & 1
    return table


def conv_encode(code: ConvCode, bits: np.ndarray) -> np.ndarray:
    """Encode `bits`, appending `tail_bits` zeros, then apply the puncturing mask."""
    k = code.constraint_length
    stream = np.concatenate([np.asarray(bits, dtype=np.uint8).ravel(), np.zeros(code.tail_bits, np.uint8)])
    table = _outputs(code)
    state = 0
    out = np.empty(len(stream) * len(code.polys), dtype=np.uint8)
    for i, u in enumerate(stream):
        out[i * len(code.polys) : (i + 1) * len(code.polys)] = table[state, u]
        state = ((int(u) << (k - 2)) | (state >> 1)) if k > 1 else 0
    mask = _mask(code)
    if mask is not None:
        keep = np.resize(mask, out.size)
        out = out[keep]
    return out


def viterbi_decode(
    code: ConvCode, received: np.ndarray, soft: Optional[np.ndarray] = None
) -> FecResult:
    """Decode `received` (the transmitted, punctured 0/1 hard bits) to information bits.

    `soft` (same length, +1 = confident 0, -1 = confident 1) replaces the hard decisions
    when given. Punctured positions contribute no cost. Decoding is forced to finish in
    the zero state when the code has tail bits. `corrected` is the rounded path metric
    (channel errors for hard input); ok is True when it is at most a quarter of the
    received length (a heuristic; callers should still check a CRC)."""
    k = code.constraint_length
    nout = len(code.polys)
    nstates = 1 << (k - 1)
    rx = np.asarray(received, dtype=np.uint8).ravel()
    values = (1.0 - 2.0 * rx) if soft is None else np.asarray(soft, dtype=np.float64).ravel()
    if values.size != rx.size:
        raise ValueError("soft and received must have the same length")
    # Re-insert the punctured positions as zero-cost erasures.
    mask = _mask(code)
    if mask is None:
        full = values
        total_coded = values.size
    else:
        period = mask.size
        kept_per_period = int(mask.sum())
        periods, rem = divmod(values.size, kept_per_period)
        # Total coded length: the smallest length whose kept-count equals rx.size.
        total_coded = periods * period
        extra = 0
        while rem and extra < period:
            rem -= int(mask[extra])
            extra += 1
        total_coded += extra
        keep = np.resize(mask, total_coded)
        if int(keep.sum()) != values.size:
            raise ValueError("received length is inconsistent with the puncturing pattern")
        full = np.zeros(total_coded)
        full[keep] = values
    if total_coded % nout:
        raise ValueError("received length is not a whole number of coded symbols")
    steps = total_coded // nout
    expected = 1.0 - 2.0 * _outputs(code).astype(np.float64)  # [state, input, j] in +-1
    pairs = full.reshape(steps, nout)
    known = (pairs != 0.0).astype(np.float64)  # punctured / explicitly erased positions cost nothing
    # branch cost = sum_j known_j * (1 - e_j * v_j)/2 : 0 for a match, 1 for a hard-decision mismatch
    inf = 1e18
    metric = np.full(nstates, inf)
    metric[0] = 0.0
    decisions = np.zeros((steps, nstates), dtype=np.int8)   # which of the two predecessors won
    prev_state = np.zeros((steps, nstates), dtype=np.int64)
    # Predecessors of next state n: states s with ((u << (k-2)) | (s >> 1)) == n, i.e. s in
    # {(n << 1) & mask, ((n << 1) & mask) | 1} with u = n >> (k-2).
    n_idx = np.arange(nstates)
    inputs = n_idx >> (k - 2) if k > 1 else np.zeros(nstates, dtype=np.int64)
    cand = np.stack([(n_idx << 1) & (nstates - 1), ((n_idx << 1) & (nstates - 1)) | 1])  # [2, nstates]
    for t in range(steps):
        v = pairs[t]
        cost = (known[t] * (1.0 - expected * v) / 2.0).sum(axis=2)  # [state, input]
        branch = np.stack([cost[cand[0], inputs], cost[cand[1], inputs]])  # [2, nstates]
        total = np.stack([metric[cand[0]], metric[cand[1]]]) + branch
        choice = (total[1] < total[0]).astype(np.int8)
        decisions[t] = choice
        prev_state[t] = np.where(choice == 1, cand[1], cand[0])
        metric = np.minimum(total[0], total[1])
    end = 0 if code.tail_bits else int(np.argmin(metric))
    errors = float(metric[end])
    state = end
    decoded = np.empty(steps, dtype=np.uint8)
    for t in range(steps - 1, -1, -1):
        decoded[t] = state >> (k - 2) if k > 1 else 0  # the input bit that led into `state`
        state = int(prev_state[t, state])
    info = decoded[: steps - code.tail_bits] if code.tail_bits else decoded
    corrected = int(round(errors))
    return FecResult(info.astype(np.uint8), corrected, corrected <= max(1, rx.size // 4))
