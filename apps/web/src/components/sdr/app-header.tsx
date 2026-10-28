"use client";

import { useState } from "react";

import { ConnectBar } from "@/components/sdr/connect-bar";
import { FrequencyDigitDisplay } from "@/components/sdr/frequency-digit-display";
import { FrequencyKeypad } from "@/components/sdr/frequency-keypad";
import { SectionSwitch } from "@/components/sdr/section-switch";
import { ShortwaveStationInfo } from "@/components/sdr/shortwave/shortwave-station-info";
import { formatFrequencyWith } from "@/lib/sdr/format";
import type { FreqFormatId, FreqTargetId } from "@/lib/sdr/display-settings";
import type { SectionId } from "@/lib/sections";

type AppHeaderProps = {
  frequencyHz: number;
  freqFormat: FreqFormatId;
  freqTarget: FreqTargetId;
  onFrequencyChange: (hz: number) => void;
  onSectionChange: (section: SectionId) => void;
};

/**
 * Brand + hero VFO + connect. Frequency is the product; connect is the gate.
 */
export function AppHeader({
  frequencyHz,
  freqFormat,
  freqTarget,
  onFrequencyChange,
  onSectionChange,
}: AppHeaderProps) {
  const [keypadOpen, setKeypadOpen] = useState(false);

  return (
    <header className="app-header">
      <div className="app-header-brand">
        <h1 className="app-header-title">SigSense</h1>
        <p className="app-header-tagline">RTL-SDR · HackRF · BLE</p>
        <SectionSwitch current="receiver" onChange={onSectionChange} />
      </div>

      {/* Landscape phones: the digit display is too wide; one tap opens the keypad. */}
      <button
        type="button"
        className="header-freq-btn"
        aria-label={`Frequency ${(frequencyHz / 1e6).toFixed(4)} megahertz. Tap to enter a frequency.`}
        onClick={() => setKeypadOpen(true)}
      >
        <span className="font-mono tabular-nums">{(frequencyHz / 1e6).toFixed(3)}</span>
        <span className="header-freq-unit">MHz</span>
      </button>

      <div className="app-header-vfo">
        <FrequencyDigitDisplay
          frequencyHz={frequencyHz}
          onChange={onFrequencyChange}
          size="lg"
        />
        <p className="app-header-vfo-meta">
          <span className="font-mono tabular-nums text-accent">
            {formatFrequencyWith(frequencyHz, freqFormat)}
          </span>
          <span className="text-muted">
            · {freqTarget === "tune" ? "listen" : "LO center"}
          </span>
          <button
            type="button"
            className="vfo-keypad-btn"
            aria-label="Enter a frequency"
            onClick={() => setKeypadOpen(true)}
          >
            Enter
          </button>
        </p>
        <ShortwaveStationInfo />
      </div>

      <div className="app-header-actions">
        <ConnectBar />
      </div>

      {keypadOpen && (
        <FrequencyKeypad
          frequencyHz={frequencyHz}
          onChange={onFrequencyChange}
          onClose={() => setKeypadOpen(false)}
        />
      )}
    </header>
  );
}
