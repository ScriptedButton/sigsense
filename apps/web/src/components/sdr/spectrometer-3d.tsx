"use client";

import { useCallback, useEffect, useRef } from "react";

import { useSpectrumStream } from "@/components/sdr/sdr-provider";
import type { NoiseFilterId } from "@/lib/sdr/display-settings";
import { Spectrometer3DScene } from "@/lib/sdr/spectrometer-3d-scene";
import { SpectrumNoiseFilter } from "@/lib/sdr/spectrum-noise-filter";

type Spectrometer3DProps = {
  sampleRate: number;
  offsetHz: number;
  bandwidthHz: number;
  noiseFilter?: NoiseFilterId;
  onTuneOffset?: (offsetHz: number) => void;
};

export function Spectrometer3D({
  sampleRate,
  offsetHz,
  bandwidthHz,
  noiseFilter = "off",
  onTuneOffset,
}: Spectrometer3DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<Spectrometer3DScene | null>(null);
  const sampleRateRef = useRef(sampleRate);
  const onTuneRef = useRef(onTuneOffset);
  const noiseFilterRef = useRef(new SpectrumNoiseFilter());
  const noiseStrengthRef = useRef(noiseFilter);
  sampleRateRef.current = sampleRate;
  onTuneRef.current = onTuneOffset;
  noiseStrengthRef.current = noiseFilter;

  useEffect(() => {
    noiseFilterRef.current.reset();
  }, [noiseFilter]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    const scene = new Spectrometer3DScene({
      canvas,
      reducedMotion,
      onTuneFraction: (fraction) => {
        onTuneRef.current?.(Math.round(fraction * sampleRateRef.current));
      },
    });
    sceneRef.current = scene;

    return () => {
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    sceneRef.current?.setTuneMarkers(offsetHz, bandwidthHz, sampleRate);
  }, [bandwidthHz, offsetHz, sampleRate]);

  useSpectrumStream(
    useCallback((spectrum) => {
      const filtered = noiseFilterRef.current.apply(
        spectrum,
        noiseStrengthRef.current,
      );
      sceneRef.current?.pushSpectrum(filtered);
    }, []),
  );

  return (
    <canvas
      ref={canvasRef}
      className="h-full w-full touch-none"
      role="img"
      aria-label="3D RF spectrometer. Drag to orbit, scroll to zoom, click to tune."
    />
  );
}
