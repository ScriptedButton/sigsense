/** KiwiSDR receiver websocket constants and session types. */

export const KIWI_WF_BINS = 1024;
/** Default Kiwi span before reading `bandwidth` from the server (kHz). */
export const KIWI_MAX_FREQ_KHZ = 30_000;
export const KIWI_MAX_ZOOM = 14;
/** Default zoom ≈ 117 kHz span at 30 MHz max. */
export const KIWI_DEFAULT_ZOOM = 8;
/** Server drops idle sockets after ~60s; stay well under that even if the UI janks. */
export const KIWI_KEEPALIVE_MS = 3_000;
export const KIWI_CONNECT_TIMEOUT_MS = 12_000;
/** ADPCM waterfall rows append a short predictor tail. */
export const KIWI_WF_ADPCM_TAIL = 10;

export const DEFAULT_KIWI_URL = "http://localhost:8073";
export const KIWI_URL_STORAGE_KEY = "sdrsense.kiwi.url";
export const KIWI_PASSWORD_STORAGE_KEY = "sdrsense.kiwi.password";

export type KiwiSession = {
  /** Address the user typed, not the websocket URL. */
  url: string;
  receiverName: string | null;
  zoom: number;
  maxFreqKhz: number;
};

export type KiwiConfig = {
  audioRate: number | null;
  sampleRate: number | null;
  maxFreqKhz: number;
  versionMajor: number | null;
  versionMinor: number | null;
  receiverName: string | null;
};

export const EMPTY_KIWI_CONFIG: KiwiConfig = {
  audioRate: null,
  sampleRate: null,
  maxFreqKhz: KIWI_MAX_FREQ_KHZ,
  versionMajor: null,
  versionMinor: null,
  receiverName: null,
};

/** Waterfall span in Hz for a zoom level. */
export function kiwiZoomSpanHz(zoom: number, maxFreqKhz: number): number {
  const z = Math.max(0, Math.min(KIWI_MAX_ZOOM, Math.round(zoom)));
  return (maxFreqKhz * 1_000) / 2 ** z;
}

/** Nearest zoom whose span is at most `spanHz` (prefer wider if tie). */
export function kiwiZoomForSpanHz(spanHz: number, maxFreqKhz: number): number {
  const target = Math.max(1, spanHz);
  let best = KIWI_MAX_ZOOM;
  for (let z = 0; z <= KIWI_MAX_ZOOM; z++) {
    const span = kiwiZoomSpanHz(z, maxFreqKhz);
    if (span <= target * 1.05) {
      best = z;
      break;
    }
    best = z;
  }
  return best;
}
