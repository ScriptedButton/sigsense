/** Carrier candidates from unmodified FFT-order power bins. These are signals, not station IDs. */
export type ReceivedCarrier = { frequencyHz: number; aboveFloorDb: number };

export function findReceivedCarriers(
  spectrum: Float32Array,
  centerHz: number,
  sampleRate: number,
): ReceivedCarrier[] {
  const n = spectrum.length;
  const binHz = sampleRate / n;
  if (n < 64 || !Number.isFinite(binHz) || binHz <= 0 || binHz > 2_500) return [];
  const shifted = (index: number) => spectrum[(index + Math.floor(n / 2)) % n];
  const candidates: ReceivedCarrier[] = [];
  // A local floor handles a sloping receiver response better than a global threshold.
  const radius = Math.max(12, Math.min(80, Math.ceil(15_000 / binHz)));
  for (let i = radius; i < n - radius; i++) {
    const frequencyHz = centerHz + (i - Math.floor(n / 2)) * binHz;
    if (frequencyHz < 2_300_000 || frequencyHz > 30_000_000) continue;
    if (Math.abs(frequencyHz - centerHz) < 2 * binHz) continue; // receiver DC spur
    const peak = shifted(i);
    if (!Number.isFinite(peak) || peak <= shifted(i - 1) || peak < shifted(i + 1)) continue;
    const neighbors: number[] = [];
    for (let j = i - radius; j <= i + radius; j++) {
      if (Math.abs(j - i) <= 2) continue;
      const db = shifted(j);
      if (Number.isFinite(db)) neighbors.push(db);
    }
    if (neighbors.length < 8) continue;
    neighbors.sort((a, b) => a - b);
    const aboveFloorDb = peak - neighbors[Math.floor(neighbors.length / 2)];
    if (aboveFloorDb >= 8) candidates.push({ frequencyHz: Math.round(frequencyHz), aboveFloorDb });
  }
  candidates.sort((a, b) => b.aboveFloorDb - a.aboveFloorDb);
  const result: ReceivedCarrier[] = [];
  for (const candidate of candidates) {
    if (result.every((other) => Math.abs(other.frequencyHz - candidate.frequencyHz) >= Math.max(4_000, 3 * binHz))) {
      result.push(candidate);
      if (result.length === 8) break;
    }
  }
  return result;
}
