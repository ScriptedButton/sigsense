"use client";

import { useMemo, useState } from "react";

import { LibraryRow } from "@/components/library/library-row";
import type { LibraryItem } from "@/lib/api";

type SourceFilter = "all" | "artemis" | "builtin";

type LibraryListProps = {
  items: LibraryItem[];
  selectedId: string | null;
  onSelect: (item: LibraryItem) => void;
};

const FILTERS: { id: SourceFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "artemis", label: "Artemis" },
  { id: "builtin", label: "Built-in" },
];

export function LibraryList({ items, selectedId, onSelect }: LibraryListProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<SourceFilter>("all");

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((item) => {
      if (filter === "artemis" && !item.pageId) return false;
      if (filter === "builtin" && item.source !== "builtin") return false;
      if (!q) return true;
      return (
        item.label.toLowerCase().includes(q) ||
        item.source.toLowerCase().includes(q) ||
        (item.notes ?? "").toLowerCase().includes(q)
      );
    });
  }, [filter, items, query]);

  return (
    <div className="flex min-h-0 flex-col">
      <div className="border-b border-[var(--line)] px-4 py-3">
        <label className="block">
          <span className="sr-only">Search the signal library</span>
          <input
            className="field w-full"
            value={query}
            placeholder="Search signals"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label="Filter by source">
          {FILTERS.map((option) => {
            const active = filter === option.id;
            return (
              <button
                key={option.id}
                type="button"
                className={`min-h-11 px-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--signal)] ${
                  active
                    ? "bg-[var(--signal)] text-[var(--ink)]"
                    : "border border-[var(--line)] text-[var(--muted)]"
                }`}
                aria-pressed={active}
                onClick={() => setFilter(option.id)}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-[var(--muted)]">
          {shown.length} {shown.length === 1 ? "signal" : "signals"}
        </p>
      </div>
      <ul className="max-h-[32rem] divide-y divide-[var(--line)] overflow-auto lg:max-h-[calc(100dvh-16rem)]">
        {shown.length === 0 ? (
          <li className="px-4 py-6 text-sm text-[var(--muted)]">
            No signals match this search.
          </li>
        ) : (
          shown.map((item) => (
            <LibraryRow
              key={`${item.source}-${item.label}`}
              item={item}
              selected={item.pageId != null && item.pageId === selectedId}
              onSelect={onSelect}
            />
          ))
        )}
      </ul>
    </div>
  );
}
