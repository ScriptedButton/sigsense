"use client";

import type { ReactNode } from "react";

import { classInfo } from "@/lib/sdr/signal-id/labels";
import {
  FILTERABLE_CLASSES,
  type SignalIdFilters,
} from "@/lib/sdr/signal-id/filters";

type SignalIdFiltersProps = {
  filters: SignalIdFilters;
  onChange: (next: SignalIdFilters) => void;
  /** How many rows remain after filtering, for the class select aria. */
  matchedCount: number;
  totalCount: number;
};

export function SignalIdFiltersBar({
  filters,
  onChange,
  matchedCount,
  totalCount,
}: SignalIdFiltersProps) {
  return (
    <div className="signal-filters">
      <label className="signal-filter-class">
        <span className="sr-only">Modulation class</span>
        <select
          className="fsk-select signal-filter-select"
          value={filters.classFilter}
          onChange={(e) =>
            onChange({
              ...filters,
              classFilter: e.target.value as SignalIdFilters["classFilter"],
            })
          }
          aria-label={`Modulation class filter, ${matchedCount} of ${totalCount} shown`}
        >
          <option value="all">All classes</option>
          {FILTERABLE_CLASSES.map((label) => (
            <option key={label} value={label}>
              {classInfo(label).short}
            </option>
          ))}
        </select>
      </label>

      <div
        className="flex flex-wrap items-center gap-1"
        role="group"
        aria-label="Signal list filters"
      >
        <FilterChip
          active={filters.hideNoise}
          onClick={() =>
            onChange({ ...filters, hideNoise: !filters.hideNoise })
          }
          title="Hide noise and spur detections"
        >
          Hide noise
        </FilterChip>
        <FilterChip
          active={filters.identifiedOnly}
          onClick={() =>
            onChange({
              ...filters,
              identifiedOnly: !filters.identifiedOnly,
            })
          }
          title="Only signals matched to the SigID catalog"
        >
          Named
        </FilterChip>
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`mode-chip${active ? " mode-chip-active" : ""}`}
      onClick={onClick}
      aria-pressed={active}
      title={title}
    >
      {children}
    </button>
  );
}
