import type { AudioEnhancementSettings } from "../audio-enhancement";

export type ListeningProfile = { label: string; bandwidthHz: number; enhancement: AudioEnhancementSettings };
export const AM_LISTENING_PROFILES: readonly ListeningProfile[] = [
  { label: "Natural", bandwidthHz: 9000, enhancement: { noiseReduction: "off", speechFilter: false, voiceIsolation: false } },
  { label: "Speech", bandwidthHz: 6000, enhancement: { noiseReduction: "low", speechFilter: true, voiceIsolation: false } },
  { label: "Crowded", bandwidthHz: 4000, enhancement: { noiseReduction: "medium", speechFilter: true, voiceIsolation: false } },
];

/** Radio and file playback share this exact definition of an unprocessed comparison. */
export function cleanupEnabled(settings: AudioEnhancementSettings): boolean {
  return settings.noiseReduction !== "off" || settings.voiceIsolation || Boolean(settings.speechFilter);
}
