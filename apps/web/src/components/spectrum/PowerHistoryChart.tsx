"use client";

type Props = {
  values: number[];
  className?: string;
};

export function PowerHistoryChart({ values, className }: Props) {
  if (!values.length) {
    return (
      <p className="text-[11px] text-[var(--muted)]">No power history yet.</p>
    );
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);
  const w = 280;
  const h = 56;
  const points = values
    .map((v, i) => {
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 8) - 4;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      className={className ?? "h-14 w-full text-[var(--signal)]"}
      role="img"
      aria-label="Recent power history in dBFS"
    >
      <rect x="0" y="0" width={w} height={h} fill="transparent" />
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
        points={points}
      />
      <text x="0" y="10" fill="var(--muted)" fontSize="9" fontFamily="ui-monospace, monospace">
        {max.toFixed(0)} dBFS
      </text>
      <text
        x="0"
        y={h - 2}
        fill="var(--muted)"
        fontSize="9"
        fontFamily="ui-monospace, monospace"
      >
        {min.toFixed(0)} dBFS
      </text>
    </svg>
  );
}
