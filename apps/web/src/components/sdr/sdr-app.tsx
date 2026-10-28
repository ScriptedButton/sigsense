"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { AppHeader } from "@/components/sdr/app-header";
import { KeyboardShortcuts } from "@/components/sdr/keyboard-shortcuts";
import { ControlRail } from "@/components/sdr/control-rail";
import { DisplayModeToggle } from "@/components/sdr/display-mode-toggle";
import { FirstRun } from "@/components/sdr/first-run";
import { Sheet } from "@/components/sdr/sheet";
import { MobileDock } from "@/components/sdr/mobile-dock";
import { NoiseFilterControl } from "@/components/sdr/noise-filter-control";
import { KiwiConnect } from "@/components/sdr/kiwi/kiwi-connect";
import { OpenWebRxConnect } from "@/components/sdr/openwebrx/openwebrx-connect";
import { RtlTcpConnect } from "@/components/sdr/rtl-tcp/rtl-tcp-connect";
import { PrimaryToolbar } from "@/components/sdr/primary-toolbar";
import {
  useRadioController,
  useSdrState,
  useSignalIdState,
} from "@/components/sdr/sdr-provider";
import { Spectrometer3D } from "@/components/sdr/spectrometer-3d";
import { SpectrumNavChrome } from "@/components/sdr/spectrum-nav-chrome";
import { SpectrumScope } from "@/components/sdr/spectrum-scope";
import { StatusBar } from "@/components/sdr/status-bar";
import { WaterfallControls } from "@/components/sdr/waterfall-controls";
import { WaterfallDisplay } from "@/components/sdr/waterfall-display";
import { useHackrf } from "@/components/HackrfPanel";
import { relaxedSimdSupported } from "@/lib/sdr/dsp";
import type { SectionId } from "@/lib/sections";
import type { DisplayMode } from "@/lib/sdr/display-mode";
import {
  DEFAULT_DISPLAY_SETTINGS,
  loadDisplaySettings,
  patchDisplaySettings,
  type DisplaySettings,
  type NoiseFilterId,
} from "@/lib/sdr/display-settings";
import { classInfo } from "@/lib/sdr/signal-id/labels";
import { useCorrectionHost } from "@/components/sdr/signal-id/correction-host";
import { buildMarkers } from "@/lib/sdr/signal-id/markers";
import {
  MARKER_MODES,
  selectMarkers,
  type SignalMarker,
} from "@/lib/sdr/signal-id/scope-overlay";
import {
  DEFAULT_VIEWPORT,
  ViewportStore,
} from "@/lib/sdr/spectrum-viewport";

type SdrAppProps = {
  onSectionChange: (section: SectionId) => void;
};

export function SdrApp({ onSectionChange }: SdrAppProps) {
  const controller = useRadioController();
  const state = useSdrState();
  const scan = useSignalIdState();
  const live = state.status === "playing";
  const [displayMode, setDisplayMode] = useState<DisplayMode>("scope");
  const [settings, setSettings] = useState<DisplaySettings>(
    DEFAULT_DISPLAY_SETTINGS,
  );

  useEffect(() => {
    setSettings(loadDisplaySettings());
  }, []);

  const updateSettings = useCallback((patch: Partial<DisplaySettings>) => {
    setSettings((prev) => patchDisplaySettings(prev, patch));
  }, []);

  const { error: hackrfError, notice: hackrfNotice, dismiss } = useHackrf();

  // Without relaxed-SIMD WASM (iOS Safari) only HackRF, OpenWebRX and Kiwi apply,
  // and the first-run card covers both; phones then drop the connect row.
  const dspOk = useSyncExternalStore(
    noopSubscribe,
    relaxedSimdSupported,
    () => true,
  );

  // Informational notices fade on their own; errors stay until dismissed.
  useEffect(() => {
    if (!hackrfNotice || hackrfError) return;
    const timer = setTimeout(dismiss, 7000);
    return () => clearTimeout(timer);
  }, [hackrfNotice, hackrfError, dismiss]);
  const [wideRail, setWideRail] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);

  // Backend-identified signals (HackRF and browser sources alike) arrive
  // through the signal-id state, so one list drives the markers.
  const markers = useMemo<SignalMarker[]>(
    () => selectMarkers(buildMarkers(scan.signals), settings.markerMode),
    [scan.signals, settings.markerMode],
  );

  const { correct } = useCorrectionHost();
  const onMarkerCorrect = useCallback(
    (markerId: string) => {
      const signal = scan.signals.find((s) => s.id === markerId);
      if (signal) correct(signal);
    },
    [scan.signals, correct],
  );

  const onTuneOffset = useCallback(
    (offsetHz: number) => {
      controller.setOffset(offsetHz);
    },
    [controller],
  );

  const onCenterOnOffset = useCallback(
    (offsetHz: number) => {
      controller.centerOnHz(state.frequencyHz + offsetHz);
    },
    [controller, state.frequencyHz],
  );

  const onPanLo = useCallback(
    (deltaHz: number) => {
      void controller.panFrequency(deltaHz);
    },
    [controller],
  );

  const headerHz =
    settings.freqTarget === "tune"
      ? state.frequencyHz + state.offsetHz
      : state.frequencyHz;

  const onHeaderFreqChange = useCallback(
    (hz: number) => {
      if (settings.freqTarget === "tune") {
        controller.tuneToHz(hz);
      } else {
        void controller.setFrequency(hz);
      }
    },
    [controller, settings.freqTarget],
  );

  return (
    <div
      className="sdr-shell sdr-shell-fit"
      data-sheet={sheetOpen ? "open" : "closed"}
      data-live={live ? "true" : "false"}
      data-dsp={dspOk ? "true" : "false"}
    >
      <AppHeader
        frequencyHz={headerHz}
        freqFormat={settings.freqFormat}
        freqTarget={settings.freqTarget}
        onFrequencyChange={onHeaderFreqChange}
        onSectionChange={onSectionChange}
      />

      {state.error && (
        <div className="sdr-error" role="alert">
          {state.error}
        </div>
      )}
      {hackrfError && (
        <div className="sdr-error flex items-center justify-between gap-3" role="alert">
          <span>{hackrfError}</span>
          <button type="button" className="sdr-notice-dismiss" aria-label="Dismiss" onClick={dismiss}>
            ×
          </button>
        </div>
      )}
      {hackrfNotice && (
        <div className="sdr-notice" role="status">
          <span>{hackrfNotice}</span>
          <button type="button" className="sdr-notice-dismiss" aria-label="Dismiss" onClick={dismiss}>
            ×
          </button>
        </div>
      )}

      <PrimaryToolbar />

      <div className={`sdr-main ${wideRail ? "sdr-main-wide" : ""}`}>
        <section className="sdr-display">
          <div className="sdr-display-chrome">
            <div className="flex items-center gap-3">
              <h2 className="text-sm font-medium text-ink">
                {displayMode === "spectrometer3d" ? "3D Spectrometer" : "Spectrum"}
              </h2>
              <DisplayModeToggle
                value={displayMode}
                onChange={setDisplayMode}
              />
            </div>
            <div className="flex items-center gap-3">
              {displayMode === "scope" && (
                <div
                  className="marker-group-chrome flex items-center gap-1"
                  role="group"
                  aria-label="Signal markers"
                  title="Which identified signals are labelled on the scope. Strongest folds overlapping detections into one."
                >
                  <span className="hidden text-xs text-muted sm:inline">Markers</span>
                  {MARKER_MODES.map((mode) => (
                    <button
                      key={mode.id}
                      type="button"
                      className={`mode-chip ${settings.markerMode === mode.id ? "mode-chip-active" : ""}`}
                      aria-pressed={settings.markerMode === mode.id}
                      onClick={() => updateSettings({ markerMode: mode.id })}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
              )}
              {live &&
                displayMode === "scope" &&
                settings.markerMode !== "off" &&
                !scan.enabled && (
                  <span
                    className="scan-hint"
                    title="Open Identify and press Scan band to label signals on the scope."
                  >
                    Scan off · no labels
                  </span>
                )}
              <p className="hidden text-xs text-muted lg:block">
                Click to tune · drag to pan · scroll to zoom
              </p>
              <span className="hide-touch">
                <KeyboardShortcuts />
              </span>
            </div>
          </div>

          {displayMode === "scope" ? (
            <ScopeLayout
              live={live}
              sampleRate={state.sampleRate}
              frequencyHz={state.frequencyHz}
              offsetHz={state.offsetHz}
              bandwidthHz={state.bandwidthHz}
              onTuneOffset={onTuneOffset}
              onCenterOnOffset={onCenterOnOffset}
              onPanLo={onPanLo}
              markers={markers}
              onMarkerCorrect={onMarkerCorrect}
              settings={settings}
              onSettingsChange={updateSettings}
            />
          ) : (
            <SpectrometerLayout
              live={live}
              sampleRate={state.sampleRate}
              offsetHz={state.offsetHz}
              bandwidthHz={state.bandwidthHz}
              noiseFilter={settings.noiseFilter}
              onNoiseFilterChange={(noiseFilter) =>
                updateSettings({ noiseFilter })
              }
              onTuneOffset={onTuneOffset}
            />
          )}
        </section>

        <MobileDock />

        <ControlRail
          onWideChange={setWideRail}
          onSheetChange={setSheetOpen}
          freqTarget={settings.freqTarget}
          freqFormat={settings.freqFormat}
          onFreqTargetChange={(freqTarget) => updateSettings({ freqTarget })}
          onFreqFormatChange={(freqFormat) => updateSettings({ freqFormat })}
        />
      </div>

      <StatusBar />
    </div>
  );
}

type ScopeLayoutProps = {
  live: boolean;
  sampleRate: number;
  frequencyHz: number;
  offsetHz: number;
  bandwidthHz: number;
  onTuneOffset: (offsetHz: number) => void;
  onCenterOnOffset: (offsetHz: number) => void;
  onPanLo: (deltaHz: number) => void;
  markers: SignalMarker[];
  onMarkerCorrect: (markerId: string) => void;
  settings: DisplaySettings;
  onSettingsChange: (patch: Partial<DisplaySettings>) => void;
};

function ScopeLayout({
  live,
  sampleRate,
  frequencyHz,
  offsetHz,
  bandwidthHz,
  onTuneOffset,
  onCenterOnOffset,
  onPanLo,
  markers,
  onMarkerCorrect,
  settings,
  onSettingsChange,
}: ScopeLayoutProps) {
  // Pan/zoom lives outside React so dragging never re-renders this tree.
  const [viewportStore] = useState(() => new ViewportStore(sampleRate));
  useEffect(() => {
    viewportStore.setSampleRate(sampleRate);
  }, [viewportStore, sampleRate]);
  useEffect(() => () => viewportStore.dispose(), [viewportStore]);

  const resetZoom = useCallback(() => {
    viewportStore.jump(DEFAULT_VIEWPORT);
  }, [viewportStore]);

  const [displayOpen, setDisplayOpen] = useState(false);
  const waterfallControls = (
    <WaterfallControls
      palette={settings.waterfallPalette}
      speed={settings.waterfallSpeed}
      direction={settings.waterfallDirection}
      dbRange={settings.dbRange}
      noiseFilter={settings.noiseFilter}
      onPaletteChange={(waterfallPalette) => onSettingsChange({ waterfallPalette })}
      onSpeedChange={(waterfallSpeed) => onSettingsChange({ waterfallSpeed })}
      onDirectionChange={(waterfallDirection) => onSettingsChange({ waterfallDirection })}
      onDbRangeChange={(dbRange) => onSettingsChange({ dbRange })}
      onNoiseFilterChange={(noiseFilter) => onSettingsChange({ noiseFilter })}
    />
  );

  return (
    <>
      {live && (
        <SpectrumNavChrome
          sampleRate={sampleRate}
          store={viewportStore}
          onResetZoom={resetZoom}
        />
      )}

      <div className="scope-pane relative min-h-48 flex-1 bg-canvas">
        {live ? (
          <SpectrumScope
            sampleRate={sampleRate}
            frequencyHz={frequencyHz}
            offsetHz={offsetHz}
            bandwidthHz={bandwidthHz}
            store={viewportStore}
            onTuneOffset={onTuneOffset}
            onCenterOnOffset={onCenterOnOffset}
            onPanLo={onPanLo}
            dbRange={settings.dbRange}
            noiseFilter={settings.noiseFilter}
            markers={markers}
            onMarkerCorrect={onMarkerCorrect}
          />
        ) : (
          <EmptyDisplay
            title="Waiting for RF"
            body="Pick a source below, or start Demo to try it with no hardware. Then click the spectrum to tune and open Identify → Scan band to label signals. Press ? for keyboard shortcuts."
            showConnect
          />
        )}
      </div>

      <div className="sdr-waterfall-chrome">
        <h2 className="text-sm font-medium text-ink">Waterfall</h2>
        {live && (
          <button
            type="button"
            className="display-settings-btn mode-chip"
            aria-haspopup="dialog"
            onClick={() => setDisplayOpen(true)}
          >
            Display settings
          </button>
        )}
        {live && displayOpen && (
          <Sheet title="Display settings" onClose={() => setDisplayOpen(false)}>
            <div className="display-settings-sheet">
              <div role="group" aria-label="Signal labels on the scope" className="display-settings-markers">
                <span className="text-sm text-muted">Signal labels</span>
                <div className="flex gap-2">
                  {MARKER_MODES.map((mode) => (
                    <button
                      key={mode.id}
                      type="button"
                      className={`mode-chip ${settings.markerMode === mode.id ? "mode-chip-active" : ""}`}
                      aria-pressed={settings.markerMode === mode.id}
                      onClick={() => onSettingsChange({ markerMode: mode.id })}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
              </div>
              {waterfallControls}
            </div>
          </Sheet>
        )}
        {live && (
          <div className="waterfall-controls-inline">{waterfallControls}</div>
        )}
        {!live && <p className="text-xs text-muted">Newest at top</p>}
      </div>

      <div className="waterfall-pane relative min-h-52 flex-[1.15] bg-canvas">
        {live ? (
          <WaterfallDisplay
            sampleRate={sampleRate}
            frequencyHz={frequencyHz}
            offsetHz={offsetHz}
            bandwidthHz={bandwidthHz}
            store={viewportStore}
            onTuneOffset={onTuneOffset}
            onCenterOnOffset={onCenterOnOffset}
            onPanLo={onPanLo}
            paletteId={settings.waterfallPalette}
            speed={settings.waterfallSpeed}
            direction={settings.waterfallDirection}
            dbRange={settings.dbRange}
            noiseFilter={settings.noiseFilter}
          />
        ) : (
          <EmptyDisplay
            title="No waterfall yet"
            body="Signal history appears here once reception starts."
          />
        )}
      </div>
    </>
  );
}

type SpectrometerLayoutProps = {
  live: boolean;
  sampleRate: number;
  offsetHz: number;
  bandwidthHz: number;
  noiseFilter: NoiseFilterId;
  onNoiseFilterChange: (id: NoiseFilterId) => void;
  onTuneOffset: (offsetHz: number) => void;
};

function SpectrometerLayout({
  live,
  sampleRate,
  offsetHz,
  bandwidthHz,
  noiseFilter,
  onNoiseFilterChange,
  onTuneOffset,
}: SpectrometerLayoutProps) {
  return (
    <div className="spectrometer-pane relative flex min-h-[32rem] flex-1 flex-col bg-canvas">
      {live ? (
        <>
          <div className="relative min-h-0 flex-1">
            <Spectrometer3D
              sampleRate={sampleRate}
              offsetHz={offsetHz}
              bandwidthHz={bandwidthHz}
              noiseFilter={noiseFilter}
              onTuneOffset={onTuneOffset}
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-1.5">
            <p className="text-xs text-muted">
              Drag to orbit · scroll to zoom · click to tune · X frequency · Z
              time · Y power
            </p>
            <NoiseFilterControl
              value={noiseFilter}
              onChange={onNoiseFilterChange}
            />
          </div>
        </>
      ) : (
        <EmptyDisplay
          title="Waiting for RF"
          body="Connect an RTL-SDR stick, an RTL server, OpenWebRX+, KiwiSDR, or start Demo to explore the 3D spectrometer."
          showConnect
        />
      )}
    </div>
  );
}

const noopSubscribe = () => () => {};

function EmptyDisplay({
  title,
  body,
  showConnect = false,
}: {
  title: string;
  body: string;
  showConnect?: boolean;
}) {
  const controller = useRadioController();
  const state = useSdrState();
  const hackrf = useHackrf();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const usbReady = mounted && state.webUsbSupported;
  // The browser DSP behind RTL-SDR, RTL server and Demo needs relaxed SIMD.
  const dspReady = mounted && relaxedSimdSupported();
  const busy = state.status === "starting" || state.status === "stopping";

  return (
    <div className="empty-display">
      {showConnect && <FirstRun />}
      {mounted && !dspReady && (
        <p className="empty-display-body">
          This browser can&apos;t run the built-in radio DSP (iOS Safari lacks
          WebAssembly relaxed SIMD), so RTL-SDR and Demo are unavailable here.
          HackRF, OpenWebRX, and KiwiSDR still work.
        </p>
      )}
      <p className="empty-display-mobile-hint">
        {mounted && !dspReady
          ? "Connect HackRF above. RTL-SDR and Demo need desktop Chrome."
          : "Pick a source above to start."}
      </p>
      <p className="empty-display-title">{title}</p>
      <p className="empty-display-body">{body}</p>
      {showConnect && (
        <div className="empty-display-actions">
          {dspReady && (
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !usbReady}
              onClick={() => void controller.connectDevice()}
            >
              Connect RTL-SDR
            </button>
          )}
          {dspReady && <RtlTcpConnect align="center" />}
          <OpenWebRxConnect align="center" />
          <KiwiConnect align="center" />
          <button
            type="button"
            className="btn-secondary"
            disabled={busy || hackrf.connecting}
            onClick={hackrf.connect}
          >
            {hackrf.demo ? "HackRF demo" : "Connect HackRF"}
          </button>
          {dspReady && (
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => void controller.connectDemo()}
            >
              Start Demo
            </button>
          )}
        </div>
      )}
    </div>
  );
}
