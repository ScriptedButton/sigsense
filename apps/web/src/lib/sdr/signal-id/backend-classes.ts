/**
 * The backend CNN's built-in class names, in its own order. Used to tell a
 * label the user invented from a model class, and to offer the list before the
 * backend has answered. The backend's /classify/classes is authoritative.
 */
export const BACKEND_CLASSES: readonly string[] = [
  "AM", "NFM", "WFM", "USB", "LSB", "CW",
  "ASK", "FSK", "GFSK", "BPSK", "QPSK", "8PSK", "QAM16", "QAM64", "OFDM",
  "ADS-B", "AIS", "APRS", "LoRa", "WiFi", "Bluetooth-LE", "Zigbee", "LTE", "GSM",
  "DMR", "P25", "ACARS",
  "Pulse", "Chirp-LFM", "FMCW",
  "Noise", "Impulsive",
  "ASK4", "ASK8", "ASK16", "FSK4", "FSK8", "FSK16", "GFSK4", "GFSK8",
  "MSK", "GMSK", "16PSK", "32PSK", "QAM32", "QAM256", "APSK16", "APSK32",
  "5G-NR", "ITS-G5", "C-V2X", "DVB-T",
];

const KNOWN = new Set(BACKEND_CLASSES);

export function isBuiltinClass(label: string): boolean {
  return KNOWN.has(label) || label === "unknown";
}

/** A stable colour for a user-invented label, so the same name always matches. */
export function customLabelColor(label: string): string {
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  return `oklch(0.78 0.13 ${hash % 360})`;
}
