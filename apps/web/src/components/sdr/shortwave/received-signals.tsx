"use client";

import { useCallback, useRef, useState } from "react";
import { useSdrState, useSpectrumStream } from "@/components/sdr/sdr-provider";
import { useTuneShortwave } from "./use-tune-shortwave";
import { findReceivedCarriers, type ReceivedCarrier } from "@/lib/sdr/shortwave/received-signals";
import type { SpectrumFrameInfo } from "@/lib/sdr/streaming-spectrum";

export function ReceivedSignals() {
  const radio = useSdrState();
  const tune = useTuneShortwave();
  const [snapshot, setSnapshot] = useState<{ center: number; rate: number; carriers: ReceivedCarrier[] } | null>(null);
  const lastUpdate = useRef(0);
  const active = radio.status === "playing" && radio.connection !== "demo";
  useSpectrumStream(useCallback((spectrum: Float32Array, center?: number, info?: SpectrumFrameInfo) => {
    if (!active || performance.now() - lastUpdate.current < 1_000) return;
    lastUpdate.current = performance.now();
    const rate = info?.sampleRate ?? radio.sampleRate;
    const centerHz = center ?? radio.frequencyHz;
    setSnapshot({ center: centerHz, rate, carriers: findReceivedCarriers(spectrum, centerHz + (info?.centerOffsetHz ?? 0), rate) });
  }, [active, radio.frequencyHz, radio.sampleRate]), true);

  if (!active) return null;
  const carriers = snapshot?.center === radio.frequencyHz ? snapshot.carriers : [];
  return (
    <section className="my-3 rounded border border-line p-2" aria-label="Received shortwave signals">
      <p className="text-xs font-medium text-ink">Signals in this spectrum</p>
      <p className="my-1 text-[0.65rem] text-muted">Measured carrier candidates · strongest first · tap to try AM</p>
      <div className="flex flex-wrap gap-1">
        {carriers.map((carrier) => (
          <button key={carrier.frequencyHz} type="button" className="mode-chip" onClick={() => tune(carrier.frequencyHz)}>
            {(carrier.frequencyHz / 1_000).toFixed(1)} kHz · +{Math.round(carrier.aboveFloorDb)} dB
          </button>
        ))}
      </div>
      {carriers.length === 0 && <p className="text-[0.65rem] text-muted">No distinct HF carriers measured here yet. Try another band or zoom in.</p>}
      <p className="mt-1 text-[0.6rem] text-muted">A peak may be interference; its height does not identify a station or guarantee clear audio.</p>
    </section>
  );
}
