"use client";

import { useState } from "react";
import {
  analyzeImage,
  analyzeIq,
  decodeBle,
  enrollSignal,
  type AnalyzeResult,
  type BleDecodeResult,
} from "@/lib/api";
import { UploadDropzone } from "@/components/UploadDropzone";
import { ResultPanel } from "@/components/ResultPanel";
import { BlePacketList } from "@/components/BlePacketList";

export function AnalyzePanel() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [ble, setBle] = useState<BleDecodeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [lastIqFile, setLastIqFile] = useState<File | null>(null);
  const [centerMhz, setCenterMhz] = useState(2402);

  const onFile = async (file: File, kind: "image" | "iq") => {
    setBusy(true);
    setError(null);
    setFileName(file.name);
    setBle(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(kind === "image" ? URL.createObjectURL(file) : null);
    setLastIqFile(kind === "iq" ? file : null);
    try {
      const res =
        kind === "image" ? await analyzeImage(file) : await analyzeIq(file, {
          centerFreq: centerMhz * 1e6,
          sampleRate: kind === "iq" ? undefined : undefined,
        });
      setResult(res);
      if (kind === "iq") {
        try {
          const bleRes = await decodeBle(file, {
            centerFreq: centerMhz * 1e6,
            channel: centerMhz === 2402 ? 37 : centerMhz === 2426 ? 38 : centerMhz === 2480 ? 39 : undefined,
          });
          setBle(bleRes);
        } catch {
          /* classification can succeed even if BLE framing finds nothing */
        }
      }
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onEnroll = async (label: string) => {
    if (!result?.embedding) return;
    try {
      await enrollSignal(label, result.embedding);
      setError(null);
      alert(`Enrolled “${label}” into the library.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const rerunBle = async () => {
    if (!lastIqFile) return;
    setBusy(true);
    try {
      const bleRes = await decodeBle(lastIqFile, {
        centerFreq: centerMhz * 1e6,
        channel:
          centerMhz === 2402
            ? 37
            : centerMhz === 2426
              ? 38
              : centerMhz === 2480
                ? 39
                : undefined,
      });
      setBle(bleRes);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <div className="space-y-4">
        <UploadDropzone onFile={onFile} busy={busy} />
        <label className="flex max-w-xs flex-col gap-1 text-xs text-[var(--muted)]">
          Capture center (MHz) for BLE framing
          <input
            type="number"
            className="field"
            value={centerMhz}
            onChange={(e) => setCenterMhz(Number(e.target.value))}
          />
        </label>
        {lastIqFile ? (
          <button
            type="button"
            onClick={rerunBle}
            disabled={busy}
            className="border border-[var(--line)] px-3 py-2 text-sm text-[var(--foreground)] hover:border-[var(--signal)]"
          >
            Re-decode BLE PDUs
          </button>
        ) : null}
        {fileName ? (
          <p className="font-mono text-xs text-[var(--muted)]">{fileName}</p>
        ) : null}
        {previewUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={previewUrl}
            alt="Uploaded waterfall preview"
            className="max-h-72 w-full border border-[var(--line)] object-contain bg-black"
          />
        ) : null}
        <BlePacketList result={ble} />
      </div>
      <ResultPanel result={result} error={error} onEnroll={onEnroll} />
    </div>
  );
}
