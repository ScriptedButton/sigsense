"use client";

import { useCallback, useState } from "react";

type Props = {
  onFile: (file: File, kind: "image" | "iq") => void;
  busy?: boolean;
};

function detectKind(file: File): "image" | "iq" {
  const name = file.name.toLowerCase();
  if (
    file.type.includes("wav") ||
    /\.(wav|wave|iq|cfile|raw|bin|npy)$/.test(name)
  ) {
    return "iq";
  }
  if (
    file.type.startsWith("image/") ||
    /\.(png|jpe?g|webp|gif|bmp)$/.test(name)
  ) {
    return "image";
  }
  return "iq";
}

export function UploadDropzone({ onFile, busy }: Props) {
  const [dragging, setDragging] = useState(false);

  const handleFiles = useCallback(
    (files: FileList | null) => {
      const file = files?.[0];
      if (!file) return;
      onFile(file, detectKind(file));
    },
    [onFile],
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        handleFiles(e.dataTransfer.files);
      }}
      className={`relative overflow-hidden border border-dashed px-6 py-16 text-center transition-colors ${
        dragging
          ? "border-[var(--signal)] bg-[var(--signal-dim)]"
          : "border-[var(--line)] bg-[var(--panel)]"
      }`}
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "linear-gradient(90deg, transparent 49%, rgba(45,212,191,0.08) 50%, transparent 51%), linear-gradient(0deg, transparent 49%, rgba(45,212,191,0.05) 50%, transparent 51%)",
          backgroundSize: "24px 24px",
        }}
      />
      <p className="relative font-[family-name:var(--font-display)] text-xl text-[var(--foreground)]">
        Drop a waterfall or IQ file
      </p>
      <p className="relative mt-2 text-sm text-[var(--muted)]">
        PNG / JPG waterfalls · WAV IQ (stereo L/R) · raw complex64 / int16 / uint8
      </p>
      <label className="relative mt-6 inline-flex cursor-pointer items-center justify-center bg-[var(--signal)] px-5 py-2.5 text-sm font-medium text-[var(--ink)] transition hover:brightness-110">
        {busy ? "Analyzing…" : "Choose file"}
        <input
          type="file"
          className="sr-only"
          disabled={busy}
          accept="image/*,audio/wav,audio/x-wav,.wav,.wave,.iq,.cfile,.raw,.bin,.npy"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </label>
    </div>
  );
}
