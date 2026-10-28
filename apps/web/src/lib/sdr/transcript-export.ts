import type { TranscriptSegment } from "./transcription-engine";

function tag(segment: TranscriptSegment): string {
  const parts = [
    segment.frequencyHz ? `${(segment.frequencyHz / 1e6).toFixed(4)} MHz` : null,
    segment.mode || null,
  ].filter(Boolean);
  return parts.length ? `[${parts.join(" ")}] ` : "";
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour12: false });
}

/** One line per transmission: "14:03:21 [462.5625 MHz NBFM] roger that". */
export function transcriptToText(segments: TranscriptSegment[]): string {
  return segments.map((s) => `${clock(s.at)} ${tag(s)}${s.text}`).join("\n");
}

function srtTime(seconds: number): string {
  const ms = Math.max(0, Math.round(seconds * 1000));
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms % 1000, 3)}`;
}

/** SubRip, using the server's stream clock when present, else arrival times. */
export function transcriptToSrt(segments: TranscriptSegment[]): string {
  const origin = segments[0]?.at ?? 0;
  const hasClock = segments.every((s) => s.startS !== undefined && s.endS !== undefined);
  const base = hasClock ? segments[0]?.startS ?? 0 : 0;
  return segments
    .map((s, i) => {
      const start = hasClock ? (s.startS as number) - base : (s.at - origin) / 1000;
      const end = hasClock ? (s.endS as number) - base : start + Math.max(1.5, s.text.split(/\s+/).length * 0.4);
      return `${i + 1}\n${srtTime(start)} --> ${srtTime(Math.max(end, start + 0.2))}\n${tag(s)}${s.text}\n`;
    })
    .join("\n");
}

export function transcriptToJson(segments: TranscriptSegment[]): string {
  return JSON.stringify(
    segments.map((s) => ({
      time: new Date(s.at).toISOString(),
      text: s.text,
      confidence: s.confidence ?? null,
      frequency_hz: s.frequencyHz ?? null,
      mode: s.mode ?? null,
      language: s.language ?? null,
      start_s: s.startS ?? null,
      end_s: s.endS ?? null,
      words: s.words ?? null,
    })),
    null,
    2,
  );
}
