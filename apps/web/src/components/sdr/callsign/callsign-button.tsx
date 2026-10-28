"use client";

import { useCallsignLookupNav } from "@/components/sdr/callsign/callsign-lookup-nav";
import type { ReactNode } from "react";

type CallsignButtonProps = {
  callsign: string;
  className?: string;
  title?: string;
  children?: ReactNode;
};

/** Clickable callsign — opens the Callsign lookup tool. */
export function CallsignButton({
  callsign,
  className,
  title,
  children,
}: CallsignButtonProps) {
  const { openCallsignLookup } = useCallsignLookupNav();

  return (
    <button
      type="button"
      className={className}
      title={title ?? `Look up ${callsign}`}
      onClick={() => openCallsignLookup(callsign)}
    >
      {children ?? callsign}
    </button>
  );
}
