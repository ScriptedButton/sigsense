type ConfidenceMeterProps = {
  value: number;
  color: string;
};

/**
 * Compact certainty readout. Confidence is genuinely informative here — narrow
 * signals near the FFT's resolution limit are ambiguous, and the bar should
 * show that rather than hide it.
 */
export function ConfidenceMeter({ value, color }: ConfidenceMeterProps) {
  const percent = Math.round(value * 100);
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <span className="signal-confidence-track">
        <span
          className="signal-confidence-fill"
          style={{ width: `${percent}%`, background: color }}
        />
      </span>
      <span className="w-7 text-right font-mono text-[0.65rem] tabular-nums text-muted">
        {percent}%
      </span>
    </span>
  );
}
