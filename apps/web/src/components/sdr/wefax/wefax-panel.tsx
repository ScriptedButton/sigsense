"use client";

import {
  useRadioController,
  useSdrState,
  useWefaxState,
} from "@/components/sdr/sdr-provider";
import { WefaxGuide } from "@/components/sdr/wefax/wefax-guide";
import { WefaxImage } from "@/components/sdr/wefax/wefax-image";
import { WefaxSettings } from "@/components/sdr/wefax/wefax-settings";
import { DEMO_WEFAX_FREQUENCY_HZ } from "@/lib/sdr/wefax/types";

export function WefaxPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const wefax = useWefaxState();
  const live = radio.status === "playing";

  const statusLabel = (() => {
    const tone = `${Math.round(wefax.toneHz)} Hz`;
    switch (wefax.status) {
      case "imaging":
        return `Imaging · ${wefax.lines} lines · ${tone}`;
      case "phasing":
        return `Phasing · ${tone}`;
      case "starting":
        return `APT start · ${tone}`;
      case "stopped":
        return `Stop tone · ${wefax.lines} lines`;
      case "listening":
        return `Listening · ${tone}`;
      default:
        return "Off";
    }
  })();

  const savePng = () => {
    const { pixels, width, lines } = controller.getWefaxImage();
    if (lines === 0 || width === 0) return;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = lines;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const image = ctx.createImageData(width, lines);
    for (let i = 0; i < width * lines; i++) {
      const g = pixels[i]!;
      const o = i * 4;
      image.data[o] = g;
      image.data[o + 1] = g;
      image.data[o + 2] = g;
      image.data[o + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
    const a = document.createElement("a");
    a.href = canvas.toDataURL("image/png");
    a.download = `wefax-${Date.now()}.png`;
    a.click();
  };

  return (
    <fieldset className="control-group">
      <legend className="control-label">Weatherfax</legend>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={wefax.enabled ? "btn-primary" : "btn-secondary"}
          onClick={() => controller.setWefaxEnabled(!wefax.enabled)}
          title="Decode HF weatherfax from demodulated audio"
        >
          {wefax.enabled ? "Stop" : "Decode"}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!wefax.enabled}
          onClick={() => controller.forceWefaxStart()}
          title="Skip APT and start imaging now"
        >
          Force start
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={wefax.lines === 0 && !wefax.enabled}
          onClick={() => controller.clearWefaxImage()}
        >
          Clear
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={wefax.lines === 0}
          onClick={savePng}
        >
          Save PNG
        </button>
      </div>

      <p className="mt-2 text-xs text-muted">
        {statusLabel}
        {wefax.enabled && wefax.lock > 0
          ? ` · lock ${Math.round(wefax.lock * 100)}%`
          : null}
        {!live && wefax.enabled ? " · waiting for RF" : null}
      </p>

      <WefaxGuide />
      <WefaxSettings />
      <WefaxImage />

      <p className="mt-2 text-[0.65rem] leading-relaxed text-muted">
        Tune starts Demo (or uses your stick), sets USB ~2.9 kHz, dials assigned −
        1.9 kHz, and enables decode. Black/white tones 1500 / 2300 Hz. Band preset
        Fax {(DEMO_WEFAX_FREQUENCY_HZ / 1e6).toFixed(3)} MHz also has a demo chart.
      </p>
    </fieldset>
  );
}
