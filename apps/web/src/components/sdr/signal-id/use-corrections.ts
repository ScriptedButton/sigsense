"use client";

import { useCallback, useEffect, useState } from "react";

import { listCorrections, type CorrectionsList } from "@/lib/sdr/signal-id/corrections";

/**
 * The user's corrections as the backend holds them. Re-fetched on demand and
 * after each change, so counts and the custom-label list stay current.
 */
export function useCorrections(active: boolean) {
  const [data, setData] = useState<CorrectionsList | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await listCorrections());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    listCorrections()
      .then((next) => {
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [active]);

  return { data, error, refresh };
}
