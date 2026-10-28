"use client";

import { useEffect, useState } from "react";

import {
  PROTOCOL_LABELS,
  PROTOCOL_SHORT,
  encryptionBadge,
  linkGrade,
  type SignalGrade,
} from "@/lib/sdr/dvoice/model";
import type { DvEncryptionInfo, DvLinkStats, DvProtocol } from "@/lib/sdr/dvoice/types";

/** Re-render about once a second so running call durations tick. */
export function useTick(intervalMs = 1000, active = true): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setTick((value) => value + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, active]);
  return tick;
}

export function ProtocolBadge({ protocol, slot }: { protocol: DvProtocol; slot?: number | null }) {
  return (
    <span className="dv-badge dv-badge-proto" title={PROTOCOL_LABELS[protocol]}>
      {PROTOCOL_SHORT[protocol]}
      {slot !== null && slot !== undefined ? ` · S${slot + 1}` : ""}
    </span>
  );
}

export function EncryptionBadge({ encryption, compact = false }: { encryption: DvEncryptionInfo; compact?: boolean }) {
  const badge = encryptionBadge(encryption);
  if (badge.tone === "clear" && compact) return null;
  return (
    <span className={`dv-badge dv-badge-${badge.tone}`} title="What the air interface says about encryption">
      {badge.tone === "encrypted" ? (
        <span aria-hidden="true" className="dv-lock">
          ⚿
        </span>
      ) : null}
      {compact && badge.tone === "encrypted" ? "Encrypted" : badge.label}
    </span>
  );
}

export function EmergencyBadge() {
  return <span className="dv-badge dv-badge-emergency">Emergency</span>;
}

const GRADE_TEXT: Record<SignalGrade, string> = {
  none: "No signal",
  weak: "Weak",
  fair: "Fair",
  good: "Good",
};

/** Four-segment signal quality meter driven by sync ratio and BER. */
export function QualityMeter({ link }: { link: DvLinkStats | undefined }) {
  const grade = linkGrade(link);
  const lit = { none: 0, weak: 1, fair: 3, good: 4 }[grade];
  return (
    <span className="dv-meter" role="img" aria-label={`Signal quality: ${GRADE_TEXT[grade]}`}>
      {[0, 1, 2, 3].map((i) => (
        <span key={i} className={`dv-meter-bar ${i < lit ? `dv-meter-on dv-meter-${grade}` : ""}`} style={{ height: 6 + i * 4 }} />
      ))}
      <span className="dv-meter-text">{GRADE_TEXT[grade]}</span>
    </span>
  );
}

export function CopyButton({ text, label = "Copy", className = "btn-secondary" }: { text: string; label?: string; className?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const id = setTimeout(() => setState("idle"), 1800);
    return () => clearTimeout(id);
  }, [state]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      // Clipboard access can be blocked (insecure origin, iOS permission); fall back to selection.
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        setState(document.execCommand("copy") ? "copied" : "failed");
      } catch {
        setState("failed");
      }
      area.remove();
    }
  };

  return (
    <button type="button" className={className} onClick={() => void copy()} aria-live="polite">
      {state === "copied" ? "Copied" : state === "failed" ? "Select and copy" : label}
    </button>
  );
}

/** Save text as a file from a button (the browser's download, no server round trip). */
export function downloadText(filename: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
