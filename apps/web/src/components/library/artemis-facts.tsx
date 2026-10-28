import type { ArtemisFacts } from "@/lib/library/artemis-record";

type ArtemisFactsListProps = {
  facts: ArtemisFacts | null;
  notes: string;
  loading: boolean;
  error: string | null;
};

const ROWS: { key: keyof ArtemisFacts; label: string }[] = [
  { key: "frequencies", label: "Frequency" },
  { key: "bandwidth", label: "Bandwidth" },
  { key: "modulation", label: "Modulation" },
  { key: "mode", label: "Mode" },
  { key: "location", label: "Location" },
  { key: "categories", label: "Categories" },
];

export function ArtemisFactsList({
  facts,
  notes,
  loading,
  error,
}: ArtemisFactsListProps) {
  const visible = facts
    ? ROWS.filter((row) => facts[row.key])
    : [];

  return (
    <div className="space-y-3">
      {loading ? (
        <p className="text-sm text-[var(--muted)]">Loading signal record…</p>
      ) : null}
      {error ? <p className="text-sm text-red-300">{error}</p> : null}
      {facts?.summary ? (
        <p className="max-w-[65ch] text-sm leading-relaxed text-[var(--foreground)]">
          {facts.summary}
        </p>
      ) : notes ? (
        <p className="font-mono text-xs leading-relaxed text-[var(--muted)]">{notes}</p>
      ) : null}
      {visible.length > 0 && facts ? (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {visible.map((row) => (
            <div key={row.key}>
              <dt className="text-xs text-[var(--muted)]">{row.label}</dt>
              <dd className="font-mono text-[var(--foreground)]">{facts[row.key]}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}
