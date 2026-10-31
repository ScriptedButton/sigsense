"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { MobileDock } from "@/components/sdr/mobile-dock";
import {
  HackrfBluetoothSection,
  HackrfRadioSection,
} from "@/components/HackrfPanel";
import { AudioGainControl } from "@/components/sdr/audio-gain-control";
import {
  CallsignLookupNavProvider,
} from "@/components/sdr/callsign/callsign-lookup-nav";
import {
  CallsignLookupPanel,
  type CallsignLookupSeed,
} from "@/components/sdr/callsign/callsign-lookup-panel";
import { FrequencyControl } from "@/components/sdr/frequency-control";
import { FreqLookupPanel } from "@/components/sdr/freq-lookup/freq-lookup-panel";
import { DigitalVoicePanel } from "@/components/sdr/dvoice/dvoice-panel";
import { FskPanel } from "@/components/sdr/fsk/fsk-panel";
import { Ft8Panel } from "@/components/sdr/ft8/ft8-panel";
import { GmrsChannelPresets } from "@/components/sdr/gmrs-channel-presets";
import { isTyping } from "@/components/sdr/keyboard-shortcuts";
import { ModeControl } from "@/components/sdr/mode-control";
import { SCHEME_LABELS } from "@/components/sdr/mode-scheme-chips";
import { MorsePanel } from "@/components/sdr/morse/morse-panel";
import { P25Panel } from "@/components/sdr/p25/p25-panel";
import { RailSection } from "@/components/sdr/rail-section";
import { RailTabs } from "@/components/sdr/rail-tabs";
import { SampleRateControl } from "@/components/sdr/sample-rate-control";
import { ScannerPanel } from "@/components/sdr/scanner/scanner-panel";
import { UpconverterControl } from "@/components/sdr/upconverter-control";
import {
  useAutofinderState,
  useFskState,
  useFt8State,
  useMorseState,
  useP25State,
  useScannerState,
  useSdrState,
  useSignalIdState,
  useTranscriptionState,
  useWefaxState,
} from "@/components/sdr/sdr-provider";
import { ShortwaveExplorer } from "@/components/sdr/shortwave/shortwave-explorer";
import { AutofinderPanel } from "@/components/sdr/shortwave/autofinder-panel";
import { ServiceFinderPanel } from "@/components/sdr/shortwave/service-finder-panel";
import { SignalIdPanel } from "@/components/sdr/signal-id/signal-id-panel";
import { TranscriptPanel } from "@/components/sdr/transcript-panel";
import { WefaxPanel } from "@/components/sdr/wefax/wefax-panel";
import { normalizeCallsign } from "@/lib/sdr/callsign/normalize";
import { useDvoice } from "@/lib/sdr/dvoice/store";
import type { FreqFormatId, FreqTargetId } from "@/lib/sdr/display-settings";
import { readRailPref, writeRailPref } from "@/lib/sdr/rail-prefs";

type ControlRailProps = {
  onWideChange: (wide: boolean) => void;
  /** Phone bottom sheet opened/closed; the shell folds its chrome away. */
  onSheetChange?: (open: boolean) => void;
  freqTarget: FreqTargetId;
  freqFormat: FreqFormatId;
  onFreqTargetChange: (id: FreqTargetId) => void;
  onFreqFormatChange: (id: FreqFormatId) => void;
};

const MAIN_TABS = ["radio", "find", "decode", "identify", "bluetooth"] as const;
const DECODE_TABS = ["morse", "fsk", "ft8", "wefax", "trunk", "digital", "speech"] as const;
const IDENTIFY_TABS = ["signals", "frequency", "callsign"] as const;
type MainTab = (typeof MAIN_TABS)[number];
type DecodeTab = (typeof DECODE_TABS)[number];
type IdentifyTab = (typeof IDENTIFY_TABS)[number];

/** Picks up where you left off: the tab and sub-tab survive a reload. */
function useRailTab<T extends string>(key: string, tabs: readonly T[]) {
  const [tab, setTab] = useState<T>(() => readRailPref(key, tabs[0], tabs));
  const set = useCallback(
    (next: T) => {
      setTab(next);
      writeRailPref(key, next);
    },
    [key],
  );
  return [tab, set] as const;
}

/**
 * Tabbed side rail. Radio holds only the everyday tune/mode/audio controls;
 * Find gathers every "find me something to hear" tool. Each tool is a
 * collapsible section, so a tab reads as a short list of headers.
 */
export function ControlRail({
  onWideChange,
  onSheetChange,
  freqTarget,
  freqFormat,
  onFreqTargetChange,
  onFreqFormatChange,
}: ControlRailProps) {
  const state = useSdrState();
  const morse = useMorseState();
  const fsk = useFskState();
  const ft8 = useFt8State();
  const wefax = useWefaxState();
  const p25 = useP25State();
  const tx = useTranscriptionState();
  const dvoice = useDvoice();
  const scan = useSignalIdState();
  const scanner = useScannerState();
  const finder = useAutofinderState();

  const [mainTab, setMainTabState] = useRailTab<MainTab>("tab", MAIN_TABS);
  // The rail keeps one width across tabs; widening is opt-in from the
  // Bluetooth panel so switching tabs never moves the tab bar or display.
  const [btExpanded, setBtExpanded] = useState(false);
  // On phones the rail is a bottom sheet: the tab bar is always visible and
  // the panel opens above it. Desktop ignores this (see receiver.css).
  const [sheetOpen, setSheetOpenState] = useState(false);
  const setSheetOpen = (next: boolean | ((open: boolean) => boolean)) => {
    const value = typeof next === "function" ? next(sheetOpen) : next;
    setSheetOpenState(value);
    onSheetChange?.(value);
  };
  const setMainTab = (tab: MainTab) => {
    setMainTabState(tab);
    if (tab !== "bluetooth") {
      setBtExpanded(false);
      onWideChange(false);
    }
  };
  const toggleBtExpanded = () => {
    const next = !btExpanded;
    setBtExpanded(next);
    onWideChange(next);
  };
  const [decodeTab, setDecodeTab] = useRailTab<DecodeTab>("decode", DECODE_TABS);
  const [identifyTab, setIdentifyTab] = useRailTab<IdentifyTab>(
    "identify",
    IDENTIFY_TABS,
  );
  const [lookupSeed, setLookupSeed] = useState<CallsignLookupSeed | null>(
    null,
  );

  const decodeActive =
    morse.enabled ||
    fsk.enabled ||
    ft8.enabled ||
    wefax.enabled ||
    p25.enabled ||
    tx.enabled;

  const scannerActive =
    scanner.status === "scanning" ||
    scanner.status === "held" ||
    scanner.status === "paused";
  const finderActive = !["idle", "error"].includes(finder.status);

  // 1–5 jump straight to a tab, from anywhere that isn't a text field.
  const setMainTabRef = useRef(setMainTab);
  useEffect(() => {
    setMainTabRef.current = setMainTab;
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.metaKey || event.ctrlKey) return;
      if (isTyping(event.target)) return;
      const index = Number(event.key) - 1;
      const tab = MAIN_TABS[index];
      if (!Number.isInteger(index) || !tab) return;
      event.preventDefault();
      setMainTabRef.current(tab);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const callsignNav = useMemo(
    () => ({
      openCallsignLookup: (callsign: string) => {
        const call = normalizeCallsign(callsign) ?? callsign.trim().toUpperCase();
        setMainTabRef.current("identify");
        setIdentifyTab("callsign");
        setLookupSeed({ callsign: call, nonce: Date.now() });
      },
    }),
    [setIdentifyTab],
  );

  const spanMhz = (state.sampleRate / 1e6).toFixed(state.sampleRate < 1e6 ? 3 : 2);

  return (
    <CallsignLookupNavProvider value={callsignNav}>
      <button
        type="button"
        className="rail-drawer-toggle"
        aria-expanded={sheetOpen}
        aria-controls="control-rail"
        onClick={() => setSheetOpen((open) => !open)}
      >
        {sheetOpen ? "Close controls" : "Controls"}
      </button>
      <aside
        id="control-rail"
        className="control-rail"
        data-sheet={sheetOpen ? "open" : "closed"}
        data-tab={mainTab}
      >
        <button
          type="button"
          className="rail-sheet-handle"
          aria-label={sheetOpen ? "Hide controls" : "Show controls"}
          aria-expanded={sheetOpen}
          onClick={() => setSheetOpen((open) => !open)}
        />
        <RailTabs
          label="Control panels"
          value={mainTab}
          onChange={(tab) => {
            // Tapping the open tab again folds the sheet away.
            if (tab === mainTab && sheetOpen) {
              setSheetOpen(false);
              return;
            }
            setMainTab(tab);
            setSheetOpen(true);
          }}
          tabs={[
            { id: "radio", label: "Radio", title: "Tune, mode, audio, RF (1)" },
            {
              id: "find",
              label: "Find",
              title: "Auto finder, station schedules, scanner (2)",
              badge: scannerActive || finderActive,
            },
            {
              id: "decode",
              label: "Decode",
              title: "Morse, FSK, FT8, fax, trunking, digital voice, speech (3)",
              badge: decodeActive,
            },
            {
              id: "identify",
              label: "Identify",
              title: "Signal ID, frequency and callsign lookup (4)",
              badge: scan.signals.length > 0 ? scan.signals.length : false,
            },
            {
              id: "bluetooth",
              label: "BLE",
              title: "HackRF Bluetooth LE capture (5)",
              badge: state.connection === "hackrf",
            },
          ]}
        />

        <div className="control-rail-body">
          {mainTab === "radio" && (
            <div
              role="tabpanel"
              id="rail-panel-radio"
              aria-labelledby="rail-tab-radio"
              className="control-rail-stack rail-section-stack"
            >
              <MobileDock />
              <RailSection id="tune" title="Tune" defaultOpen>
                <FrequencyControl
                  freqTarget={freqTarget}
                  freqFormat={freqFormat}
                  onFreqTargetChange={onFreqTargetChange}
                  onFreqFormatChange={onFreqFormatChange}
                  showDigits={false}
                />
              </RailSection>
              <RailSection
                id="mode"
                title="Mode & squelch"
                hint={SCHEME_LABELS[state.scheme]}
                defaultOpen
              >
                <ModeControl showSchemes={false} />
              </RailSection>
              <RailSection id="audio" title="Audio" defaultOpen>
                <AudioGainControl />
              </RailSection>
              <RailSection id="rf" title="RF & span" hint={`${spanMhz} MHz`}>
                <div className="control-rail-stack">
                  <SampleRateControl />
                  <UpconverterControl />
                  <HackrfRadioSection />
                </div>
              </RailSection>
              <RailSection id="gmrs" title="GMRS channels">
                <GmrsChannelPresets />
              </RailSection>
              <RailSection id="setup" title="Setup help">
                <HelpCard supported={state.webUsbSupported} />
              </RailSection>
            </div>
          )}

          {mainTab === "find" && (
            <div
              role="tabpanel"
              id="rail-panel-find"
              aria-labelledby="rail-tab-find"
              className="control-rail-stack rail-section-stack"
            >
              <RailSection
                id="autofinder"
                title="Find speech automatically"
                hint={finderActive ? finder.status : "HF 2.3–30 MHz"}
                active={finderActive}
                defaultOpen
              >
                <AutofinderPanel />
              </RailSection>
              <RailSection id="schedules" title="Shortwave stations" hint="EiBi schedule" defaultOpen>
                <ShortwaveExplorer />
              </RailSection>
              <RailSection
                id="scanner"
                title="Memory scanner"
                hint={scannerActive ? scanner.status : undefined}
                active={scannerActive}
              >
                <ScannerPanel />
              </RailSection>
              <RailSection id="background-finder" title="Background finder" hint="runs on the receiver host">
                <ServiceFinderPanel />
              </RailSection>
            </div>
          )}

          {mainTab === "bluetooth" && (
            <div
              role="tabpanel"
              id="rail-panel-bluetooth"
              aria-labelledby="rail-tab-bluetooth"
              className="control-rail-stack"
            >
              <button
                type="button"
                className="btn-secondary self-end bt-expand-btn"
                aria-pressed={btExpanded}
                title="Widen this panel over the display"
                onClick={toggleBtExpanded}
              >
                {btExpanded ? "Collapse ⇥" : "Expand ⇤"}
              </button>
              <HackrfBluetoothSection />
            </div>
          )}

          {mainTab === "decode" && (
            <div
              role="tabpanel"
              id="rail-panel-decode"
              aria-labelledby="rail-tab-decode"
              className="control-rail-stack"
            >
              <RailTabs
                label="Decoder"
                size="sm"
                value={decodeTab}
                onChange={setDecodeTab}
                tabs={[
                  { id: "morse", label: "Morse", badge: morse.enabled },
                  { id: "fsk", label: "FSK", badge: fsk.enabled },
                  { id: "ft8", label: "FT8", badge: ft8.enabled },
                  { id: "wefax", label: "Fax", badge: wefax.enabled },
                  {
                    id: "trunk",
                    label: "Trunk",
                    badge: p25.enabled || p25.status === "trunking",
                  },
                  { id: "digital", label: "Digital", badge: dvoice.running },
                  { id: "speech", label: "Speech", badge: tx.enabled },
                ]}
              />
              {decodeTab === "morse" && <MorsePanel />}
              {decodeTab === "fsk" && <FskPanel />}
              {decodeTab === "ft8" && <Ft8Panel />}
              {decodeTab === "wefax" && <WefaxPanel />}
              {decodeTab === "trunk" && <P25Panel />}
              {decodeTab === "digital" && <DigitalVoicePanel />}
              {decodeTab === "speech" && <TranscriptPanel />}
            </div>
          )}

          {mainTab === "identify" && (
            <div
              role="tabpanel"
              id="rail-panel-identify"
              aria-labelledby="rail-tab-identify"
              className="control-rail-stack"
            >
              <RailTabs
                label="Identify tools"
                size="sm"
                value={identifyTab}
                onChange={setIdentifyTab}
                tabs={[
                  {
                    id: "signals",
                    label: "Signals",
                    badge:
                      scan.signals.length > 0 ? scan.signals.length : false,
                  },
                  { id: "frequency", label: "Frequency" },
                  { id: "callsign", label: "Callsign" },
                ]}
              />
              {identifyTab === "signals" && <SignalIdPanel />}
              {identifyTab === "frequency" && <FreqLookupPanel />}
              {identifyTab === "callsign" && (
                <CallsignLookupPanel seed={lookupSeed} />
              )}
            </div>
          )}
        </div>
      </aside>
    </CallsignLookupNavProvider>
  );
}

function HelpCard({ supported }: { supported: boolean }) {
  return (
    <div className="help-card">
      <p className="help-card-title">Setup</p>
      <ul className="help-card-list">
        <li>Chrome, Edge, or Opera on desktop/Android</li>
        <li>Serve over HTTPS or localhost</li>
        <li>RTL-SDR v4 (R828D) or other RTL2832U sticks</li>
        <li>Or RTL server (Pi + rtl_tcp bridge), usually localhost:8081</li>
        <li>Or a local OpenWebRX+ server, usually localhost:8073</li>
        <li>Or a public/local KiwiSDR, usually host:8073</li>
        <li>Or a HackRF through the SigSense API (Connect → HackRF)</li>
        {!supported && (
          <li className="text-warn">
            This browser has no WebUSB — Demo still works
          </li>
        )}
      </ul>
    </div>
  );
}
