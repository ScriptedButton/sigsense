"use client";

import { useState } from "react";
import {
  DEFAULT_FILTERS,
  DEVICE_TYPES,
  SORT_OPTIONS,
  activeFilterCount,
  type BleDeviceType,
  type BleFilters,
  type BleSort,
  type BleSortKey,
} from "@/lib/bleSort";

const chip =
  "inline-flex min-h-8 shrink-0 items-center gap-1 rounded-full border px-3 text-xs transition-colors";
const chipOff = "border-[var(--line)] text-[var(--muted)] hover:border-[var(--signal)]";
const chipOn = "border-[var(--signal)] bg-[var(--signal)]/15 text-[var(--foreground)]";

type Props = {
  sort: BleSort;
  onSort: (sort: BleSort) => void;
  filters: BleFilters;
  onFilters: (filters: BleFilters) => void;
  /** Devices per type before filtering, for chip badges. */
  counts: Record<BleDeviceType, number>;
  shown: number;
  total: number;
  /** Live view: rows re-sort every couple of seconds so they don't jump. */
  live?: boolean;
  orderPaused?: boolean;
  onToggleOrderPaused?: () => void;
};

/** Sort and filter controls for the Bluetooth device list. */
export function BleSortFilterBar({
  sort,
  onSort,
  filters,
  onFilters,
  counts,
  shown,
  total,
  live,
  orderPaused,
  onToggleOrderPaused,
}: Props) {
  const [open, setOpen] = useState(false);
  const active = activeFilterCount(filters);
  const set = (patch: Partial<BleFilters>) => onFilters({ ...filters, ...patch });
  const toggleType = (type: BleDeviceType) =>
    set({
      types: filters.types.includes(type)
        ? filters.types.filter((t) => t !== type)
        : [...filters.types, type],
    });
  const toggleChannel = (channel: number) =>
    set({
      channels: filters.channels.includes(channel)
        ? filters.channels.filter((c) => c !== channel)
        : [...filters.channels, channel],
    });
  const changeKey = (key: BleSortKey) => {
    const option = SORT_OPTIONS.find((o) => o.id === key);
    onSort({ key, dir: option?.defaultDir ?? "desc" });
  };

  return (
    <div className="mt-3 space-y-2" role="group" aria-label="Sort and filter Bluetooth devices">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-xs text-[var(--muted)]">
          Sort
          <select
            aria-label="Sort devices by"
            className="field text-xs"
            value={sort.key}
            onChange={(e) => changeKey(e.target.value as BleSortKey)}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className={`${chip} ${chipOff}`}
          aria-label={`Sort direction: ${sort.dir === "desc" ? "high to low" : "low to high"}. Press to reverse.`}
          onClick={() => onSort({ ...sort, dir: sort.dir === "desc" ? "asc" : "desc" })}
        >
          {sort.dir === "desc" ? "↓ High → low" : "↑ Low → high"}
        </button>
        <button
          type="button"
          className={`${chip} ${active ? chipOn : chipOff}`}
          aria-expanded={open}
          aria-controls="ble-filter-panel"
          onClick={() => setOpen((v) => !v)}
        >
          Filters{active ? ` · ${active}` : ""}
        </button>
        {live && onToggleOrderPaused && (
          <button
            type="button"
            className={`${chip} ${orderPaused ? chipOn : chipOff}`}
            aria-pressed={orderPaused}
            title="Rows re-sort every couple of seconds. Pause to keep the current order while you read."
            onClick={onToggleOrderPaused}
          >
            {orderPaused ? "Order paused" : "Pause order"}
          </button>
        )}
        <span className="ml-auto text-[11px] text-[var(--muted)]" aria-live="polite">
          {shown === total ? `${total} devices` : `${shown} of ${total} devices`}
        </span>
      </div>

      {open && (
        <div
          id="ble-filter-panel"
          className="space-y-3 rounded-lg border border-[var(--line)] p-3"
        >
          <fieldset>
            <legend className="mb-1.5 text-[11px] uppercase tracking-wider text-[var(--muted)]">
              Type · matches any selected
            </legend>
            <div className="flex flex-wrap gap-1.5">
              {DEVICE_TYPES.map((type) => {
                const on = filters.types.includes(type.id);
                return (
                  <button
                    key={type.id}
                    type="button"
                    aria-pressed={on}
                    title={type.hint}
                    className={`${chip} ${on ? chipOn : chipOff}`}
                    onClick={() => toggleType(type.id)}
                  >
                    {type.label}
                    <span className="tabular-nums opacity-70">{counts[type.id]}</span>
                  </button>
                );
              })}
            </div>
          </fieldset>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-xs text-[var(--muted)]">
              Minimum strength ·{" "}
              {filters.minStrengthDbfs === null ? "any" : `${filters.minStrengthDbfs} dBFS`}
              <input
                type="range"
                className="mt-1 w-full"
                min={-100}
                max={-20}
                step={5}
                value={filters.minStrengthDbfs ?? -100}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  set({ minStrengthDbfs: v <= -100 ? null : v });
                }}
              />
              <span className="text-[10px]">
                Devices with no reading are hidden once a minimum is set.
              </span>
            </label>
            <label className="block text-xs text-[var(--muted)]">
              Minimum messages · {filters.minMessages || "any"}
              <input
                type="range"
                className="mt-1 w-full"
                min={0}
                max={50}
                step={1}
                value={filters.minMessages}
                onChange={(e) => set({ minMessages: Number(e.target.value) })}
              />
            </label>
          </div>

          <fieldset>
            <legend className="mb-1.5 text-[11px] uppercase tracking-wider text-[var(--muted)]">
              Advertising channel · seen on any selected
            </legend>
            <div className="flex flex-wrap gap-1.5">
              {[37, 38, 39].map((channel) => {
                const on = filters.channels.includes(channel);
                return (
                  <button
                    key={channel}
                    type="button"
                    aria-pressed={on}
                    className={`${chip} ${on ? chipOn : chipOff}`}
                    onClick={() => toggleChannel(channel)}
                  >
                    CH {channel}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-xs text-[var(--muted)]">
              PHY
              <select
                className="field mt-1 w-full text-xs"
                value={filters.phy}
                onChange={(e) => set({ phy: e.target.value as BleFilters["phy"] })}
              >
                <option value="all">Any</option>
                <option value="LE1M">LE 1M</option>
                <option value="LE2M">LE 2M</option>
              </select>
            </label>
            <label className="block text-xs text-[var(--muted)]">
              Address
              <select
                className="field mt-1 w-full text-xs"
                value={filters.addressType}
                onChange={(e) => set({ addressType: e.target.value as BleFilters["addressType"] })}
              >
                <option value="all">Any</option>
                <option value="public">Public</option>
                <option value="random">Random / private</option>
              </select>
            </label>
            <label className="block text-xs text-[var(--muted)]">
              Identity
              <select
                className="field mt-1 w-full text-xs"
                value={filters.identity}
                onChange={(e) => set({ identity: e.target.value as BleFilters["identity"] })}
              >
                <option value="all">Any</option>
                <option value="identified">Identified</option>
                <option value="saved">Saved</option>
              </select>
            </label>
          </div>

          <button
            type="button"
            className={`${chip} ${chipOff}`}
            disabled={!active}
            onClick={() => onFilters(DEFAULT_FILTERS)}
          >
            Reset filters
          </button>
        </div>
      )}
    </div>
  );
}
