"use client";

import { useEffect, useState } from "react";

import {
  artemisAssets,
  type ArtemisRecord,
} from "@/lib/library/artemis-record";

export function useArtemisRecord(pageId: string | null): {
  record: ArtemisRecord | null;
  loading: boolean;
  error: string | null;
} {
  const [record, setRecord] = useState<ArtemisRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pageId) {
      setRecord(null);
      setLoading(false);
      setError(null);
      return;
    }
    const assets = artemisAssets(pageId);
    if (!assets) {
      setRecord(null);
      setLoading(false);
      setError("This entry has no Artemis id.");
      return;
    }

    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setRecord(null);

    fetch(assets.recordUrl, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Artemis record unavailable (${res.status}).`);
        }
        const data = (await res.json()) as ArtemisRecord;
        if (String(data.pageid) !== pageId) {
          throw new Error("Artemis record id did not match.");
        }
        return data;
      })
      .then((data) => {
        setRecord(data);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setRecord(null);
        setLoading(false);
        setError(
          err instanceof Error ? err.message : "Could not load the Artemis record.",
        );
      });

    return () => controller.abort();
  }, [pageId]);

  return { record, loading, error };
}
