"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { CorrectionsManager } from "@/components/sdr/signal-id/corrections-manager";
import { CorrectSignalSheet } from "@/components/sdr/signal-id/correct-signal-sheet";
import { useCorrections } from "@/components/sdr/signal-id/use-corrections";
import type { IdentifiedSignal } from "@/lib/sdr/signal-id/types";

type CorrectionApi = {
  /** Open the correction sheet for a signal (list row, marker, long-press). */
  correct: (signal: IdentifiedSignal) => void;
  /** Open the "Your corrections" manager. */
  manage: () => void;
  /** Corrections stored so far, or null before the backend has answered. */
  total: number | null;
};

const CorrectionContext = createContext<CorrectionApi | null>(null);

export function useCorrectionHost(): CorrectionApi {
  const api = useContext(CorrectionContext);
  if (!api) throw new Error("useCorrectionHost must be used within CorrectionHost");
  return api;
}

/** Owns the correction sheets so any part of the UI can open them. */
export function CorrectionHost({ children }: { children: ReactNode }) {
  const [signal, setSignal] = useState<IdentifiedSignal | null>(null);
  const [managing, setManaging] = useState(false);
  const corrections = useCorrections(true);
  const { refresh } = corrections;

  const correct = useCallback((next: IdentifiedSignal) => setSignal(next), []);
  const manage = useCallback(() => setManaging(true), []);
  const api = useMemo(
    () => ({ correct, manage, total: corrections.data?.total ?? null }),
    [correct, manage, corrections.data?.total],
  );

  return (
    <CorrectionContext.Provider value={api}>
      {children}
      {signal && (
        <CorrectSignalSheet signal={signal} onClose={() => setSignal(null)} onChanged={refresh} />
      )}
      {managing && <CorrectionsManager onClose={() => setManaging(false)} onChanged={refresh} />}
    </CorrectionContext.Provider>
  );
}
