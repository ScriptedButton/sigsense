"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { RadioController } from "@/lib/sdr/radio-controller";
import type { FskState } from "@/lib/sdr/fsk/types";
import { DEFAULT_FSK_CONFIG } from "@/lib/sdr/fsk/types";
import type { Ft8State } from "@/lib/sdr/ft8/types";
import { DEFAULT_FT8_CONFIG } from "@/lib/sdr/ft8/types";
import type { MorseState } from "@/lib/sdr/morse/types";
import { DEFAULT_MORSE_CONFIG } from "@/lib/sdr/morse/types";
import type { P25State } from "@/lib/sdr/p25/types";
import {
  DEFAULT_P25_CONFIG,
  DEFAULT_P25_SYSTEM,
} from "@/lib/sdr/p25/types";
import type { ScannerState } from "@/lib/sdr/scanner/types";
import { DEFAULT_SCANNER_CONFIG } from "@/lib/sdr/scanner/types";
import { createDefaultBanks } from "@/lib/sdr/scanner/banks";
import type { SignalIdState } from "@/lib/sdr/signal-id/types";
import type { TranscriptionState } from "@/lib/sdr/transcription-engine";
import type { WefaxState } from "@/lib/sdr/wefax/types";
import type { KiwiSession } from "@/lib/sdr/kiwi/protocol";
import type { OpenWebRxSession } from "@/lib/sdr/openwebrx/protocol";
import {
  DEFAULT_WEFAX_CONFIG,
  wefaxLineWidth,
} from "@/lib/sdr/wefax/types";
import { DEFAULT_SAMPLE_RATE, type SdrState } from "@/lib/sdr/types";

const ControllerContext = createContext<RadioController | null>(null);

/** Stable SSR snapshot — never reads browser APIs. */
const SERVER_STATE: SdrState = {
  connection: "disconnected",
  status: "idle",
  frequencyHz: 93_900_000,
  offsetHz: 0,
  sampleRate: DEFAULT_SAMPLE_RATE,
  scheme: "WBFM",
  volume: 0.8,
  autoGain: true,
  gainDb: 20,
  bandwidthHz: 150_000,
  squelch: 0,
  autoSquelch: false,
  squelchOpen: true,
  afc: false,
  afcLocked: false,
  stereo: true,
  stereoActive: false,
  ppm: 0,
  biasTee: false,
  error: null,
  webUsbSupported: false,
};

export function SdrProvider({ children }: { children: ReactNode }) {
  const controller = useMemo(() => new RadioController(), []);

  useEffect(() => {
    controller.detectCapabilities();
    controller.restoreTranscriptionSettings();
    return () => {
      void controller.stop();
    };
  }, [controller]);

  return (
    <ControllerContext.Provider value={controller}>
      {children}
    </ControllerContext.Provider>
  );
}

export function useRadioController(): RadioController {
  const controller = useContext(ControllerContext);
  if (!controller) {
    throw new Error("useRadioController must be used within SdrProvider");
  }
  return controller;
}

export function useSdrState(): SdrState {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribe(() => {
        onStoreChange();
      }),
    () => controller.getState(),
    () => SERVER_STATE,
  );
}

export function useSpectrumStream(
  onFrame: (spectrum: Float32Array, centerHz: number | undefined) => void,
): void {
  const controller = useRadioController();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    return controller.onSpectrum(onFrame);
  }, [controller, onFrame, ready]);
}

const SERVER_TRANSCRIPTION: TranscriptionState = {
  enabled: false,
  status: "idle",
  progress: 0,
  segments: [],
  error: null,
};

export function useTranscriptionState() {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeTranscription(() => {
        onStoreChange();
      }),
    () => controller.getTranscriptionState(),
    () => SERVER_TRANSCRIPTION,
  );
}

const SERVER_FSK: FskState = {
  enabled: false,
  status: "idle",
  config: DEFAULT_FSK_CONFIG,
  text: "",
  lastChar: null,
  lock: 0,
  error: null,
};

export function useFskState(): FskState {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeFsk(() => {
        onStoreChange();
      }),
    () => controller.getFskState(),
    () => SERVER_FSK,
  );
}

const SERVER_FT8: Ft8State = {
  enabled: false,
  status: "idle",
  config: DEFAULT_FT8_CONFIG,
  messages: [],
  cycleSec: 0,
  error: null,
};

export function useFt8State(): Ft8State {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeFt8(() => {
        onStoreChange();
      }),
    () => controller.getFt8State(),
    () => SERVER_FT8,
  );
}

const SERVER_MORSE: MorseState = {
  enabled: false,
  status: "idle",
  config: DEFAULT_MORSE_CONFIG,
  text: "",
  keywords: [],
  lastChar: null,
  wpmEstimate: DEFAULT_MORSE_CONFIG.wpm,
  lock: 0,
  error: null,
};

export function useMorseState(): MorseState {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeMorse(() => {
        onStoreChange();
      }),
    () => controller.getMorseState(),
    () => SERVER_MORSE,
  );
}

const SERVER_WEFAX: WefaxState = {
  enabled: false,
  status: "idle",
  config: DEFAULT_WEFAX_CONFIG,
  width: wefaxLineWidth(DEFAULT_WEFAX_CONFIG.ioc),
  lines: 0,
  frameId: 0,
  toneHz: 1900,
  lock: 0,
  error: null,
};

export function useWefaxState(): WefaxState {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeWefax(() => {
        onStoreChange();
      }),
    () => controller.getWefaxState(),
    () => SERVER_WEFAX,
  );
}

const SERVER_P25: P25State = {
  enabled: false,
  status: "idle",
  config: DEFAULT_P25_CONFIG,
  system: DEFAULT_P25_SYSTEM,
  lock: 0,
  decodeRate: 0,
  controlChannelHz: null,
  voiceFrequencyHz: null,
  activeCalls: [],
  recentCalls: [],
  messages: [],
  error: null,
  imbeReady: false,
};

export function useP25State(): P25State {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeP25(() => {
        onStoreChange();
      }),
    () => controller.getP25State(),
    () => SERVER_P25,
  );
}

const SERVER_SCANNER: ScannerState = {
  status: "idle",
  config: DEFAULT_SCANNER_CONFIG,
  banks: createDefaultBanks(),
  scanIndex: 0,
  currentChannelId: null,
  holdReason: null,
  hopCount: 0,
  error: null,
};

export function useScannerState(): ScannerState {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeScanner(() => {
        onStoreChange();
      }),
    () => controller.getScannerState(),
    () => SERVER_SCANNER,
  );
}

const SERVER_SIGNAL_ID: SignalIdState = {
  enabled: false,
  status: "idle",
  signals: [],
  noiseFloorDb: -100,
  error: null,
};

export function useSignalIdState(): SignalIdState {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) =>
      controller.subscribeSignalId(() => {
        onStoreChange();
      }),
    () => controller.getSignalIdState(),
    () => SERVER_SIGNAL_ID,
  );
}

const SERVER_OWRX: OpenWebRxSession | null = null;

export function useOpenWebRxSession(): OpenWebRxSession | null {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) => controller.subscribeOpenWebRx(onStoreChange),
    () => controller.getOpenWebRxSession(),
    () => SERVER_OWRX,
  );
}

const SERVER_KIWI: KiwiSession | null = null;

export function useKiwiSession(): KiwiSession | null {
  const controller = useRadioController();
  return useSyncExternalStore(
    (onStoreChange) => controller.subscribeKiwi(onStoreChange),
    () => controller.getKiwiSession(),
    () => SERVER_KIWI,
  );
}
