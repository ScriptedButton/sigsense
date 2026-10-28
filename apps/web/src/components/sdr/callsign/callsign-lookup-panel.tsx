"use client";

import { useEffect, useRef, useState } from "react";

import { CallsignLookupForm } from "@/components/sdr/callsign/callsign-lookup-form";
import { CallsignLookupResult } from "@/components/sdr/callsign/callsign-lookup-result";
import { lookupCallsign } from "@/lib/sdr/callsign/lookup";
import {
  detectCallsignService,
  normalizeCallsign,
  type CallsignServiceMode,
} from "@/lib/sdr/callsign/normalize";
import type { CallsignRecord } from "@/lib/sdr/callsign/types";

export type CallsignLookupSeed = {
  callsign: string;
  nonce: number;
};

type CallsignLookupPanelProps = {
  seed: CallsignLookupSeed | null;
};

function errorRecord(call: string, message: string): CallsignRecord {
  return {
    callsign: call || "?",
    status: "error",
    service: null,
    name: null,
    firstName: null,
    class: null,
    type: null,
    grid: null,
    latitude: null,
    longitude: null,
    addressLine1: null,
    addressLine2: null,
    country: null,
    expires: null,
    granted: null,
    trusteeCall: null,
    trusteeName: null,
    ulsUrl: null,
    frn: null,
    sources: [],
    error: message,
  };
}

export function CallsignLookupPanel({ seed }: CallsignLookupPanelProps) {
  const [input, setInput] = useState(seed?.callsign ?? "");
  const [mode, setMode] = useState<CallsignServiceMode>("auto");
  const [busy, setBusy] = useState(false);
  const [record, setRecord] = useState<CallsignRecord | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!seed) return;

    const raw = seed.callsign;
    const detected = detectCallsignService(raw);
    const nextMode: CallsignServiceMode = detected ?? "auto";
    setMode(nextMode);
    const call = normalizeCallsign(raw, nextMode) ?? raw.trim().toUpperCase();
    setInput(call);
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);

    void lookupCallsign(raw, ac.signal, nextMode)
      .then((result) => {
        if (!ac.signal.aborted) setRecord(result);
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setRecord(
          errorRecord(
            call,
            err instanceof Error ? err.message : "Lookup failed.",
          ),
        );
      })
      .finally(() => {
        if (!ac.signal.aborted) setBusy(false);
      });
  }, [seed]);

  const submit = () => {
    const raw = input;
    const call = normalizeCallsign(raw, mode) ?? raw.trim().toUpperCase();
    setInput(call);
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);
    void lookupCallsign(raw, ac.signal, mode)
      .then((result) => {
        if (!ac.signal.aborted) setRecord(result);
      })
      .catch((err: unknown) => {
        if (ac.signal.aborted) return;
        setRecord(
          errorRecord(
            call,
            err instanceof Error ? err.message : "Lookup failed.",
          ),
        );
      })
      .finally(() => {
        if (!ac.signal.aborted) setBusy(false);
      });
  };

  return (
    <fieldset className="control-group">
      <legend className="control-label">Callsign lookup</legend>
      <p className="mb-3 text-xs leading-relaxed text-muted">
        Amateur (HamDB / Callook) or GMRS (FCC via Skywave). Click a callsign in
        Morse decode to open it here.
      </p>
      <CallsignLookupForm
        value={input}
        mode={mode}
        busy={busy}
        onChange={setInput}
        onModeChange={setMode}
        onSubmit={submit}
      />
      <CallsignLookupResult record={record} busy={busy} />
    </fieldset>
  );
}
