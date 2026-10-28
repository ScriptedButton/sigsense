"use client";

import { useState } from "react";
import {
  analyzeImage,
  analyzeIq,
  decodeBle,
  exportBleCapture,
  enrollSignal,
  type AnalyzeResult,
  type BleDecodeOptions,
  type BleDecodeResult,
} from "@/lib/api";
import { UploadDropzone } from "@/components/UploadDropzone";
import { ResultPanel } from "@/components/ResultPanel";
import { BleDecryptPanel } from "@/components/BleDecryptPanel";
import { BleProtocolPanel } from "@/components/BleProtocolPanel";
import { BlePacketList } from "@/components/BlePacketList";

export function AnalyzePanel() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [ble, setBle] = useState<BleDecodeResult | null>(null);
  const [bleError, setBleError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [lastIqFile, setLastIqFile] = useState<File | null>(null);
  const [centerMhz, setCenterMhz] = useState(2402);
  const [sampleRateMhz, setSampleRateMhz] = useState(4);
  const [phy, setPhy] = useState<BleDecodeOptions["phy"]>("auto");
  const [accessAddress, setAccessAddress] = useState("");
  const [crcInit, setCrcInit] = useState("");
  const bleOptions = (file: File): BleDecodeOptions => {
    if (!!accessAddress !== !!crcInit)
      throw new Error(
        "A known connection requires both access address and CRCInit. Clear both to detect broadcasts.",
      );
    return {
      centerFreq: centerMhz * 1e6,
      sampleRate:
        /\.wav(e)?$/i.test(file.name) || file.type.includes("wav")
          ? undefined
          : sampleRateMhz * 1e6,
      phy,
      connections:
        accessAddress && crcInit
          ? [
              {
                access_address: `0x${accessAddress.replace(/^0x/i, "")}`,
                crc_init: `0x${crcInit.replace(/^0x/i, "")}`,
              },
            ]
          : [],
    };
  };

  const onFile = async (file: File, kind: "image" | "iq") => {
    setBusy(true);
    setError(null);
    setFileName(file.name);
    setBle(null);
    setBleError(null);
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setPreviewUrl(kind === "image" ? URL.createObjectURL(file) : null);
    setLastIqFile(kind === "iq" ? file : null);
    try {
      const res =
        kind === "image"
          ? await analyzeImage(file)
          : await analyzeIq(file, {
              centerFreq: centerMhz * 1e6,
              sampleRate: bleOptions(file).sampleRate,
            });
      setResult(res);
      if (kind === "iq") {
        try {
          const bleRes = await decodeBle(file, bleOptions(file));
          setBle(bleRes);
        } catch (e) {
          setBleError(e instanceof Error ? e.message : String(e));
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
    setBleError(null);
    setBle(null);
    try {
      const bleRes = await decodeBle(lastIqFile, bleOptions(lastIqFile));
      setBle(bleRes);
    } catch (e) {
      setBleError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <div className="space-y-4">
        <div>
          <h3 className="text-lg">Analyze a saved signal</h3>
          <p className="mt-1 text-xs text-[var(--muted)]">
            Upload IQ samples for classification and Bluetooth messages. A
            waterfall image supports classification only. For a sniffer capture,
            use “Open a Bluetooth capture” below.
          </p>
        </div>
        <details className="rounded-lg border border-[var(--line)] p-4">
          <summary className="cursor-pointer text-sm">
            IQ capture settings · {centerMhz} MHz / {sampleRateMhz} MS/s
          </summary>
          <p className="my-3 text-xs text-[var(--muted)]">
            Use the settings from the original recording. Apply these before
            uploading, or decode again after changing them.
          </p>
          <div className="mb-3 flex flex-wrap gap-2">
            {[2402, 2426, 2480].map((frequency, i) => (
              <button
                type="button"
                key={frequency}
                className="rounded border border-[var(--line)] px-3 py-2 text-xs"
                onClick={() => {
                  setCenterMhz(frequency);
                  setPhy("auto");
                }}
              >
                Advertising channel {37 + i}
              </button>
            ))}
          </div>
          <label className="flex max-w-xs flex-col gap-1 text-xs text-[var(--muted)]">
            Capture center (MHz) for BLE framing
            <input
              type="number"
              className="field"
              value={centerMhz}
              onChange={(e) => setCenterMhz(Number(e.target.value))}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-[var(--muted)]">
              Raw IQ sample rate (MS/s; WAV uses its header)
              <input
                type="number"
                min={2}
                step="any"
                className="field mt-1 w-full"
                value={sampleRateMhz}
                onChange={(e) => setSampleRateMhz(Number(e.target.value))}
              />
            </label>
            <label className="text-xs text-[var(--muted)]">
              BLE PHY
              <select
                className="field mt-1 w-full"
                value={phy}
                onChange={(e) =>
                  setPhy(e.target.value as BleDecodeOptions["phy"])
                }
              >
                <option value="auto">Auto 1M / 2M</option>
                <option value="LE1M">LE 1M</option>
                <option value="LE2M">LE 2M</option>
              </select>
            </label>
          </div>
          <details className="text-xs text-[var(--muted)]">
            <summary className="cursor-pointer">
              Known connection (optional)
            </summary>
            <p className="mt-2">
              For data-channel IQ captured after connection setup. Supply both
              values from CONNECT_IND or a sniffer.
            </p>
            <label className="mt-2 block">
              Access address (8 hex digits)
              <input
                className="field mt-1 w-full"
                value={accessAddress}
                onChange={(e) => setAccessAddress(e.target.value.trim())}
              />
            </label>
            <label className="mt-2 block">
              CRCInit (6 hex digits)
              <input
                className="field mt-1 w-full"
                value={crcInit}
                onChange={(e) => setCrcInit(e.target.value.trim())}
              />
            </label>
          </details>
        </details>
        <UploadDropzone onFile={onFile} busy={busy} />
        {lastIqFile ? (
          <button
            type="button"
            onClick={rerunBle}
            disabled={busy}
            className="border border-[var(--line)] px-3 py-2 text-sm text-[var(--foreground)] hover:border-[var(--signal)]"
          >
            Decode Bluetooth again
          </button>
        ) : null}
        {lastIqFile ? (
          <button
            type="button"
            disabled={busy}
            className="border border-[var(--line)] px-3 py-2 text-sm"
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                const capture = await exportBleCapture(
                  lastIqFile,
                  bleOptions(lastIqFile),
                );
                const url = URL.createObjectURL(capture);
                const link = document.createElement("a");
                link.href = url;
                link.download = "sigsense-ble.pcap";
                link.click();
                window.setTimeout(() => URL.revokeObjectURL(url), 1000);
              } catch (error) {
                setError(
                  error instanceof Error ? error.message : String(error),
                );
              } finally {
                setBusy(false);
              }
            }}
          >
            Download Bluetooth packets (.pcap)
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
        {bleError && (
          <p
            role="alert"
            className="rounded border border-amber-300/30 p-3 text-xs text-amber-200"
          >
            Bluetooth decoding: {bleError}
          </p>
        )}
        <BlePacketList result={ble} />
        <BleProtocolPanel />
        <BleDecryptPanel />
      </div>
      <ResultPanel result={result} error={error} onEnroll={onEnroll} />
    </div>
  );
}
