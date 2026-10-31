"use client";

import { useState, type ReactNode } from "react";

import { readRailPref, writeRailPref } from "@/lib/sdr/rail-prefs";

type RailSectionProps = {
  /** Stable key; the open/closed choice is remembered per section. */
  id: string;
  title: string;
  /** Short live status shown in the header so a closed section still reads. */
  hint?: ReactNode;
  defaultOpen?: boolean;
  /** Draws attention to the header (e.g. the tool is running). */
  active?: boolean;
  children: ReactNode;
};

/**
 * Collapsible rail section. Every tool in a tab is one header row, so the
 * whole tab fits on screen and the panel you want is one click away.
 */
export function RailSection({
  id,
  title,
  hint,
  defaultOpen = false,
  active = false,
  children,
}: RailSectionProps) {
  const [open, setOpen] = useState(
    () => readRailPref(`section.${id}`, defaultOpen ? "open" : "closed") === "open",
  );

  return (
    <details
      className="rail-section"
      data-active={active ? "true" : "false"}
      open={open}
      onToggle={(event) => {
        const next = event.currentTarget.open;
        if (next === open) return;
        setOpen(next);
        writeRailPref(`section.${id}`, next ? "open" : "closed");
      }}
    >
      <summary className="rail-section-summary">
        <span className="rail-section-title">{title}</span>
        {hint && <span className="rail-section-hint">{hint}</span>}
      </summary>
      <div className="rail-section-body">{children}</div>
    </details>
  );
}
