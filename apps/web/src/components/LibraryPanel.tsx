"use client";

import { useEffect, useState } from "react";
import { fetchLibrary, type LibraryItem } from "@/lib/api";

export function LibraryPanel() {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchLibrary();
        if (!cancelled) setItems(data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <p className="text-sm text-[var(--muted)]">Loading library…</p>;
  }
  if (error) {
    return (
      <p className="text-sm text-red-300">
        Could not load library. Is the API running? {error}
      </p>
    );
  }

  return (
    <div className="border border-[var(--line)] bg-[var(--panel)]">
      <div className="border-b border-[var(--line)] px-5 py-4">
        <h2 className="font-[family-name:var(--font-display)] text-xl text-[var(--foreground)]">
          Signal library
        </h2>
        <p className="mt-1 text-sm text-[var(--muted)]">
          Built-in classes plus user-enrolled prototypes for open-set matching
        </p>
      </div>
      <ul className="max-h-[28rem] divide-y divide-[var(--line)] overflow-auto">
        {items.map((item) => (
          <li
            key={`${item.label}-${item.source}`}
            className="flex items-center justify-between px-5 py-3 text-sm"
          >
            <div>
              <p className="font-mono text-[var(--foreground)]">{item.label}</p>
              <p className="text-xs text-[var(--muted)]">{item.source}</p>
            </div>
            <span className="font-mono text-[var(--muted)]">
              {item.count > 0 ? `${item.count} proto` : "model only"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
