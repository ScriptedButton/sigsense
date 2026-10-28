"use client";

import { useEffect, useState } from "react";

import { ArtemisSignalView } from "@/components/library/artemis-signal-view";
import { LibraryList } from "@/components/library/library-list";
import { fetchLibrary, type LibraryItem } from "@/lib/api";

export function LibraryPanel() {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

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

  const selected = items.find((item) => item.pageId === selectedId) ?? null;
  const artemisCount = items.filter((item) => item.pageId).length;

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
        <p className="mt-1 max-w-[62ch] text-sm text-[var(--muted)]">
          Built-in classes plus enrolled prototypes for open-set matching.
          {artemisCount > 0
            ? ` ${artemisCount} Artemis references open a waterfall and audio sample.`
            : " No Artemis references are enrolled."}
        </p>
      </div>
      <div className="grid lg:grid-cols-[minmax(18rem,24rem)_minmax(0,1fr)]">
        <div className={selected ? "order-2 border-[var(--line)] lg:order-1 lg:border-r" : "border-[var(--line)] lg:border-r"}>
          <LibraryList
            items={items}
            selectedId={selectedId}
            onSelect={(item) => {
              if (item.pageId) setSelectedId(item.pageId);
            }}
          />
        </div>
        <div className={selected ? "order-1 border-b border-[var(--line)] lg:order-2 lg:border-b-0" : "border-t border-[var(--line)] lg:border-t-0"}>
          <ArtemisSignalView item={selected} />
        </div>
      </div>
    </div>
  );
}
