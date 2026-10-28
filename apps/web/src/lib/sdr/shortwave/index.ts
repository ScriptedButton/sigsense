import scheduleJson from "@/lib/sdr/shortwave/schedule.json";
import type { ShortwaveSchedule } from "@/lib/sdr/shortwave/types";

/** Eager schedule (scripts / tests). UI lazy-loads the JSON instead. */
export const SHORTWAVE_SCHEDULE = scheduleJson as ShortwaveSchedule;

export {
  SHORTWAVE_BANDS,
  bandForKHz,
  bandById,
} from "@/lib/sdr/shortwave/bands";
export { LANGUAGE_LABELS, languageLabel } from "@/lib/sdr/shortwave/languages";
export {
  collectLanguages,
  entryFrequencyHz,
  filterSchedule,
  formatKHz,
  formatLiveEta,
  formatUtcHmm,
  formatWindow,
  loadShortwaveSchedule,
  lookupAtFrequency,
  utcMinuteNow,
} from "@/lib/sdr/shortwave/schedule";
export type {
  ShortwaveBand,
  ShortwaveBandId,
  ShortwaveEntry,
  ShortwaveFilters,
  ShortwaveLive,
  ShortwaveSchedule,
  ShortwaveWhen,
} from "@/lib/sdr/shortwave/types";
