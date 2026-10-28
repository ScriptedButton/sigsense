"use client";

import { createContext, useContext, type ReactNode } from "react";

export type CallsignLookupNav = {
  /** Open the Callsign tool and look up this call. */
  openCallsignLookup: (callsign: string) => void;
};

const CallsignLookupNavContext = createContext<CallsignLookupNav | null>(null);

export function CallsignLookupNavProvider({
  value,
  children,
}: {
  value: CallsignLookupNav;
  children: ReactNode;
}) {
  return (
    <CallsignLookupNavContext.Provider value={value}>
      {children}
    </CallsignLookupNavContext.Provider>
  );
}

export function useCallsignLookupNav(): CallsignLookupNav {
  const ctx = useContext(CallsignLookupNavContext);
  if (!ctx) {
    return {
      openCallsignLookup: () => {
        /* no-op outside rail */
      },
    };
  }
  return ctx;
}
