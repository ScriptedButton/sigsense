"use client";

import { useEffect, useRef } from "react";

type Props = {
  /** 128x128 grayscale rows (0-255), newest at bottom when scrolling history */
  frame: number[][] | null;
  className?: string;
};

export function WaterfallCanvas({ frame, className }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const historyRef = useRef<Uint8ClampedArray[]>([]);

  useEffect(() => {
    if (!frame || frame.length === 0) return;
    const row = new Uint8ClampedArray(frame[0]?.length ?? 0);
    // Collapse 2D preview to a single spectral row (mean over time of preview)
    const h = frame.length;
    const w = frame[0].length;
    for (let x = 0; x < w; x++) {
      let s = 0;
      for (let y = 0; y < h; y++) s += frame[y][x];
      row[x] = Math.min(255, Math.round(s / h));
    }
    const hist = historyRef.current;
    hist.push(row);
    if (hist.length > 256) hist.shift();

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const width = w;
    const height = hist.length;
    canvas.width = width;
    canvas.height = height;
    const img = ctx.createImageData(width, height);
    for (let y = 0; y < height; y++) {
      const r = hist[y];
      for (let x = 0; x < width; x++) {
        const v = r[x];
        const i = (y * width + x) * 4;
        // teal heat map
        img.data[i] = Math.min(255, v * 0.2);
        img.data[i + 1] = Math.min(255, 40 + v);
        img.data[i + 2] = Math.min(255, 80 + v * 0.9);
        img.data[i + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }, [frame]);

  return (
    <canvas
      ref={canvasRef}
      className={
        className ??
        "h-64 w-full border border-[var(--line)] bg-black object-cover"
      }
    />
  );
}
