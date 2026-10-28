"use client";

import { useHackrf } from "@/components/HackrfPanel";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import {
  formatGmrsMhz,
  GMRS_REPEATER_INPUTS,
  GMRS_SIMPLEX_CHANNELS,
  type GmrsChannel,
} from "@/lib/sdr/gmrs/channels";

/**
 * GMRS channels cluster in two 175 kHz groups 5 MHz apart, so each group fits
 * one narrow HackRF capture with fine bins (about 1 kHz at 2 MS/s).
 */
const GMRS_BANDS = [
  { id: "low", label: "462.6 MHz", hint: "Ch 1–7, 15–22", centerHz: 462_637_500 },
  { id: "high", label: "467.6 MHz", hint: "Ch 8–14, 15R–22R", centerHz: 467_637_500 },
] as const;
const GMRS_SPAN_HZ = 2_000_000;
/** Mid-range stages for UHF narrowband FM; adjustable afterwards. */
const GMRS_GAIN = { lna: 24, vga: 24, amp: false };

function ChannelChip({ channel }: { channel: GmrsChannel }) {
  const controller = useRadioController();
  const state = useSdrState();
  const hackrf = useHackrf();
  const active =
    Math.abs(state.frequencyHz - channel.frequencyHz) < 500 &&
    state.scheme === "NBFM";

  return (
    <button
      type="button"
      className={`preset-chip ${active ? "preset-chip-active" : ""}`}
      title={`GMRS ${channel.channel} · ${formatGmrsMhz(channel.frequencyHz)} MHz`}
      onClick={() => {
        controller.setScheme("NBFM");
        if (state.connection === "hackrf") {
          const inSpan =
            Math.abs(channel.frequencyHz - state.frequencyHz) <
            state.sampleRate * 0.45;
          if (inSpan) {
            // Already visible: move the marker instead of restarting the stream.
            controller.tuneToHz(channel.frequencyHz);
          } else {
            const band = GMRS_BANDS.reduce((a, b) =>
              Math.abs(a.centerHz - channel.frequencyHz) <=
              Math.abs(b.centerHz - channel.frequencyHz)
                ? a
                : b,
            );
            hackrf.applyPreset({
              frequencyHz: band.centerHz,
              sampleRate: GMRS_SPAN_HZ,
              gain: GMRS_GAIN,
            });
          }
          return;
        }
        void controller.setFrequency(channel.frequencyHz);
        controller.setOffset(0);
      }}
    >
      {channel.label}
    </button>
  );
}

/** GMRS / FRS channel grid for the Radio rail. */
export function GmrsChannelPresets() {
  const state = useSdrState();
  const hackrf = useHackrf();
  const controller = useRadioController();
  const onHackrf = state.connection === "hackrf";
  return (
    <fieldset className="control-group">
      <legend className="control-label">GMRS channels</legend>
      <p className="mb-2 text-[0.65rem] leading-relaxed text-muted">
        Channels 1–22 shared with FRS · 15R–22R repeater inputs (+5 MHz). NBFM.
      </p>
      <p className="mb-1 text-[0.65rem] font-medium text-muted">
        Narrow capture · 2 MHz span
      </p>
      <div className="flex flex-wrap gap-1">
        {GMRS_BANDS.map((band) => {
          const active =
            onHackrf &&
            Math.abs(state.frequencyHz - band.centerHz) < 1_000 &&
            Math.abs(state.sampleRate - GMRS_SPAN_HZ) < 1_000;
          return (
            <button
              key={band.id}
              type="button"
              className={`preset-chip ${active ? "preset-chip-active" : ""}`}
              title={`${band.hint} · fine ~1 kHz bins${onHackrf ? "" : " (connect a HackRF)"}`}
              onClick={() => {
                controller.setScheme("NBFM");
                if (onHackrf) {
                  hackrf.applyPreset({
                    frequencyHz: band.centerHz,
                    sampleRate: GMRS_SPAN_HZ,
                    gain: GMRS_GAIN,
                  });
                } else {
                  void controller.setFrequency(band.centerHz);
                  controller.setOffset(0);
                }
              }}
            >
              {band.label}
            </button>
          );
        })}
      </div>
      <p className="mb-1 mt-2 text-[0.65rem] font-medium text-muted">1–22</p>
      <div className="flex flex-wrap gap-1">
        {GMRS_SIMPLEX_CHANNELS.map((ch) => (
          <ChannelChip key={ch.id} channel={ch} />
        ))}
      </div>
      <p className="mb-1 mt-2 text-[0.65rem] font-medium text-muted">
        Repeater in
      </p>
      <div className="flex flex-wrap gap-1">
        {GMRS_REPEATER_INPUTS.map((ch) => (
          <ChannelChip key={ch.id} channel={ch} />
        ))}
      </div>
    </fieldset>
  );
}
