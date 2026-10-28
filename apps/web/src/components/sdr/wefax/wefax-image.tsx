"use client";

import { useEffect, useRef } from "react";

import { useRadioController, useWefaxState } from "@/components/sdr/sdr-provider";

/**
 * Progressive weatherfax raster — paints grayscale scan lines as they arrive.
 */
export function WefaxImage() {
  const controller = useRadioController();
  const wefax = useWefaxState();
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const { pixels, width, lines } = controller.getWefaxImage();
    const h = Math.max(1, lines);
    if (canvas.width !== width || canvas.height !== h) {
      canvas.width = width;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    if (lines === 0) {
      ctx.fillStyle = "oklch(0.12 0 0)";
      ctx.fillRect(0, 0, width, 1);
      return;
    }

    const image = ctx.createImageData(width, lines);
    const data = image.data;
    for (let i = 0; i < width * lines; i++) {
      const g = pixels[i]!;
      const o = i * 4;
      data[o] = g;
      data[o + 1] = g;
      data[o + 2] = g;
      data[o + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
  }, [controller, wefax.frameId, wefax.width, wefax.lines]);

  const empty = wefax.lines === 0;

  return (
    <div className="wefax-image-wrap mt-3">
      {empty ? (
        <p className="p-3 font-sans text-sm text-muted">
          {wefax.enabled
            ? wefax.config.sync === "auto"
              ? "Waiting for APT start (300 / 675 Hz B/W)… or Force start mid-chart."
              : "Imaging from demod audio — chart lines appear here."
            : "Enable Decode to paint HF weatherfax from the current USB/LSB demod."}
        </p>
      ) : null}
      <canvas
        ref={canvasRef}
        className={`wefax-canvas ${empty ? "sr-only" : ""}`}
        aria-label="Decoded weatherfax image"
      />
    </div>
  );
}
