export type MarkerMode = "off" | "strongest" | "all";

export const MARKER_MODES: { id: MarkerMode; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "strongest", label: "Strongest" },
  { id: "all", label: "All" },
];

export function isMarkerMode(value: unknown): value is MarkerMode {
  return MARKER_MODES.some((mode) => mode.id === value);
}

export type SignalMarker = {
  id: string;
  offsetHz: number;
  bandwidthHz: number;
  label: string;
  color: string;
  /** Higher draws first and keeps its label on collision. */
  priority?: number;
  /** Full description shown on hover. */
  detail?: string;
  /** Markers folded into this one (set by selectMarkers). */
  count?: number;
};

/** Where one marker landed this frame, for hover hit-testing. */
export type PlacedMarker = {
  marker: SignalMarker;
  left: number;
  right: number;
  labelShown: boolean;
};

type DrawOptions = {
  markers: SignalMarker[];
  width: number;
  height: number;
  /** Maps an LO-relative offset (Hz) to a CSS pixel x. */
  offsetToX: (offsetHz: number) => number;
  /** Maps a bandwidth (Hz) to a CSS pixel width. */
  hzToPx: (hz: number) => number;
};

const ROW_HEIGHT = 14;
const ROW_COUNT = 3;
const TOP_MARGIN = 4;
const MIN_MARKER_PX = 4;
const MAX_LABEL_PX = 150;
const LABEL_GAP = 6;
/** Strongest mode keeps at most this many markers. */
const STRONGEST_LIMIT = 6;
/** Hard cap in All mode so a noisy scan cannot drown the trace. */
const ALL_LIMIT = 40;
/** Overlap, as a share of the narrower band, above which markers are one signal. */
const COLLAPSE_OVERLAP = 0.5;
/** Vertical band (px from the top) where hover finds a marker. */
export const MARKER_HOVER_ZONE = TOP_MARGIN + ROW_COUNT * ROW_HEIGHT + 8;

/**
 * Chooses which markers to draw. Done in Hz, so it only reruns when the
 * detections change, not on every pan or frame.
 *
 * Strongest: markers whose bands overlap by more than half fold into the
 * highest-priority one (shown as "+N"), then the strongest few are kept.
 * All: nothing is folded, only capped.
 */
export function selectMarkers(
  markers: SignalMarker[],
  mode: MarkerMode,
): SignalMarker[] {
  if (mode === "off" || markers.length === 0) return [];
  const ranked = [...markers].sort(
    (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || a.offsetHz - b.offsetHz,
  );
  if (mode === "all") {
    return ranked.slice(0, ALL_LIMIT).map((m) => ({ ...m, count: 1 }));
  }

  const kept: SignalMarker[] = [];
  for (const marker of ranked) {
    const lo = marker.offsetHz - marker.bandwidthHz / 2;
    const hi = marker.offsetHz + marker.bandwidthHz / 2;
    const host = kept.find((k) => {
      const klo = k.offsetHz - k.bandwidthHz / 2;
      const khi = k.offsetHz + k.bandwidthHz / 2;
      const overlap = Math.min(hi, khi) - Math.max(lo, klo);
      const narrower = Math.max(1, Math.min(marker.bandwidthHz, k.bandwidthHz));
      return overlap / narrower > COLLAPSE_OVERLAP;
    });
    if (host) {
      host.count = (host.count ?? 1) + 1;
      continue;
    }
    kept.push({ ...marker, count: 1 });
  }
  return kept.slice(0, STRONGEST_LIMIT);
}

function fitLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
): { text: string; width: number } {
  const full = ctx.measureText(text).width;
  if (full <= maxWidth) return { text, width: full };
  let lo = 1;
  let hi = text.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ctx.measureText(`${text.slice(0, mid)}…`).width <= maxWidth) lo = mid;
    else hi = mid - 1;
  }
  const cut = `${text.slice(0, lo)}…`;
  return { text: cut, width: ctx.measureText(cut).width };
}

/**
 * Annotates identified signals over the spectrum trace.
 *
 * A thin bracket over the occupied bandwidth plus a label. Labels are placed
 * strongest-first onto a few rows and a label that cannot find free space is
 * dropped (its bracket stays), so a crowded band never becomes a wall of text.
 * Returns where each marker landed so the caller can show details on hover.
 */
export function drawSignalMarkers(
  ctx: CanvasRenderingContext2D,
  { markers, width, height, offsetToX, hzToPx }: DrawOptions,
): PlacedMarker[] {
  const placed: PlacedMarker[] = [];
  if (markers.length === 0) return placed;

  ctx.save();
  ctx.font = "10px var(--font-mono), ui-monospace, monospace";
  ctx.textBaseline = "top";
  ctx.lineJoin = "round";

  const visible = markers
    .map((marker) => {
      const centerX = offsetToX(marker.offsetHz);
      const halfSpan = Math.max(hzToPx(marker.bandwidthHz), MIN_MARKER_PX) / 2;
      return { marker, centerX, left: centerX - halfSpan, right: centerX + halfSpan };
    })
    .filter(({ left, right }) => right >= 0 && left <= width)
    .sort((a, b) => (b.marker.priority ?? 0) - (a.marker.priority ?? 0));

  // Occupied [start, end] label spans on each row, in placement order.
  const rows: [number, number][][] = Array.from({ length: ROW_COUNT }, () => []);
  const bandTop = TOP_MARGIN + ROW_COUNT * ROW_HEIGHT;

  for (const { marker, centerX, left, right } of visible) {
    ctx.fillStyle = `color-mix(in oklch, ${marker.color} 12%, transparent)`;
    ctx.fillRect(left, bandTop, right - left, height - bandTop);

    const count = marker.count ?? 1;
    const text = count > 1 ? `${marker.label} +${count - 1}` : marker.label;
    const { text: shown, width: textWidth } = fitLabel(
      ctx,
      text,
      Math.min(MAX_LABEL_PX, Math.max(right - left, 64)),
    );
    const labelLeft = Math.min(
      Math.max(centerX - textWidth / 2, 2),
      Math.max(2, width - textWidth - 2),
    );
    const labelRight = labelLeft + textWidth;

    let row = -1;
    for (let r = 0; r < ROW_COUNT && row < 0; r++) {
      const clash = rows[r].some(
        ([s, e]) => labelLeft < e + LABEL_GAP && labelRight > s - LABEL_GAP,
      );
      if (!clash) row = r;
    }

    const bracketY = row >= 0 ? TOP_MARGIN + (row + 1) * ROW_HEIGHT - 3 : bandTop - 3;

    ctx.strokeStyle = marker.color;
    ctx.lineWidth = 1.5;
    ctx.globalAlpha = row >= 0 ? 0.95 : 0.5;
    ctx.beginPath();
    ctx.moveTo(left, bracketY);
    ctx.lineTo(right, bracketY);
    ctx.moveTo(left, bracketY);
    ctx.lineTo(left, bracketY + 4);
    ctx.moveTo(right, bracketY);
    ctx.lineTo(right, bracketY + 4);
    ctx.stroke();
    ctx.globalAlpha = 1;

    placed.push({ marker, left, right, labelShown: row >= 0 });
    if (row < 0) continue;

    rows[row].push([labelLeft, labelRight]);
    const y = TOP_MARGIN + row * ROW_HEIGHT;
    // Dark halo keeps the label readable over the trace and neighbouring fills.
    ctx.lineWidth = 3;
    ctx.strokeStyle = "oklch(0.08 0 0 / 0.85)";
    ctx.strokeText(shown, labelLeft, y);
    ctx.fillStyle = marker.color;
    ctx.fillText(shown, labelLeft, y);
  }

  ctx.restore();
  return placed;
}
