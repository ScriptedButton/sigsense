/** OpenWebRX+ receiver websocket constants. */

export const OWRX_HANDSHAKE_CLIENT = "SERVER DE CLIENT type=receiver";
export const OWRX_HANDSHAKE_SERVER = "CLIENT DE SERVER";

export const OWRX_OUTPUT_RATE = 12_000;
export const OWRX_HD_OUTPUT_RATE = 48_000;

/** Samples the server prepends so the FFT ADPCM predictor can settle. */
export const OWRX_FFT_ADPCM_PAD = 10;

export const OWRX_OP_SPECTRUM = 0x01;
export const OWRX_OP_AUDIO = 0x02;
export const OWRX_OP_SECONDARY_FFT = 0x03;
export const OWRX_OP_HD_AUDIO = 0x04;

export const DEFAULT_OWRX_URL = "http://localhost:8073";
export const OWRX_URL_STORAGE_KEY = "sdrsense.openwebrx.url";

export type OpenWebRxCompression = "adpcm" | "none";

export type OpenWebRxProfile = {
  id: string;
  name: string;
};

export type OpenWebRxConfig = {
  centerFreqHz: number | null;
  sampleRate: number | null;
  fftSize: number | null;
  startMod: string | null;
  startOffsetHz: number | null;
  audioCompression: OpenWebRxCompression;
  fftCompression: OpenWebRxCompression;
  allowsCenterChanges: boolean;
  profileId: string | null;
};

export type OpenWebRxSession = {
  /** Address the user typed, not the websocket URL. */
  url: string;
  receiverName: string | null;
  profiles: OpenWebRxProfile[];
  profileId: string | null;
  allowsCenterChanges: boolean;
};

export const EMPTY_OWRX_CONFIG: OpenWebRxConfig = {
  centerFreqHz: null,
  sampleRate: null,
  fftSize: null,
  startMod: null,
  startOffsetHz: null,
  audioCompression: "adpcm",
  fftCompression: "adpcm",
  allowsCenterChanges: false,
  profileId: null,
};
