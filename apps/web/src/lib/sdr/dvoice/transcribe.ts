/**
 * "Transcribe this call": fetch the recorded WAV, resample it to 16 kHz and run
 * it through the speech service's /transcribe socket. The WAV and resampling
 * helpers are pure so they can be tested without a browser.
 */
import { wsUrl } from "@/lib/api";

export type Wav = { sampleRate: number; samples: Int16Array };

/** Parse a mono/stereo 16-bit PCM WAV (what the recorder writes). Throws on anything else. */
export function parseWav(data: ArrayBuffer): Wav {
  const view = new DataView(data);
  const tag = (offset: number) =>
    String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3));
  if (data.byteLength < 44 || tag(0) !== "RIFF" || tag(8) !== "WAVE") throw new Error("Not a WAV file");
  let offset = 12;
  let channels = 1;
  let sampleRate = 8000;
  let bits = 16;
  let format = 1;
  while (offset + 8 <= data.byteLength) {
    const id = tag(offset);
    const size = view.getUint32(offset + 4, true);
    const body = offset + 8;
    if (id === "fmt ") {
      format = view.getUint16(body, true);
      channels = view.getUint16(body + 2, true);
      sampleRate = view.getUint32(body + 4, true);
      bits = view.getUint16(body + 14, true);
    } else if (id === "data") {
      if (format !== 1 || bits !== 16) throw new Error("Only 16-bit PCM WAV is supported");
      const length = Math.min(size, data.byteLength - body) & ~1;
      const raw = new Int16Array(data.slice(body, body + length));
      if (channels === 1) return { sampleRate, samples: raw };
      const mono = new Int16Array(Math.floor(raw.length / channels));
      for (let i = 0; i < mono.length; i++) {
        let sum = 0;
        for (let c = 0; c < channels; c++) sum += raw[i * channels + c];
        mono[i] = Math.round(sum / channels);
      }
      return { sampleRate, samples: mono };
    }
    offset = body + size + (size & 1);
  }
  throw new Error("WAV has no audio data");
}

/** Linear-interpolation resampler; fine for 8 kHz voice going to 16 kHz speech recognition. */
export function resampleLinear(input: Int16Array, from: number, to: number): Int16Array {
  if (from === to || input.length === 0) return input;
  const length = Math.max(1, Math.floor((input.length * to) / from));
  const out = new Int16Array(length);
  const ratio = from / to;
  for (let i = 0; i < length; i++) {
    const position = i * ratio;
    const index = Math.floor(position);
    const frac = position - index;
    const a = input[Math.min(index, input.length - 1)];
    const b = input[Math.min(index + 1, input.length - 1)];
    out[i] = Math.round(a + (b - a) * frac);
  }
  return out;
}

export type TranscribeResult = { text: string; segments: { start: number; end: number; text: string }[] };

type ServerMessage = {
  type?: string;
  state?: string;
  message?: string;
  text?: string;
  dropped?: boolean;
  start_s?: number;
  end_s?: number;
};

/** After the last audio the server needs a moment to finish; stop waiting after this much silence. */
const SETTLE_MS = 2500;
const CHUNK_SAMPLES = 3200;

export async function transcribeWavUrl(
  url: string,
  meta: { frequencyHz: number | null; mode: string },
  onPartial?: (text: string) => void,
): Promise<TranscribeResult> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 404 ? "No recording for this call" : `Download failed (${response.status})`);
  const wav = parseWav(await response.arrayBuffer());
  const pcm = resampleLinear(wav.samples, wav.sampleRate, 16_000);

  return await new Promise<TranscribeResult>((resolve, reject) => {
    const socket = new WebSocket(wsUrl("/transcribe"));
    socket.binaryType = "arraybuffer";
    const finals = new Map<string, { start: number; end: number; text: string }>();
    let settle: ReturnType<typeof setTimeout> | null = null;
    let finished = false;

    const done = (error?: Error) => {
      if (finished) return;
      finished = true;
      if (settle !== null) clearTimeout(settle);
      socket.close();
      if (error) reject(error);
      else {
        const segments = [...finals.values()].sort((a, b) => a.start - b.start);
        resolve({ text: segments.map((s) => s.text).join(" ").trim(), segments });
      }
    };
    const armSettle = () => {
      if (settle !== null) clearTimeout(settle);
      settle = setTimeout(() => done(), SETTLE_MS);
    };

    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: "config",
          language: "auto",
          sample_rate: 16_000,
          source_label: "digital voice call",
          frequency_hz: meta.frequencyHz,
          mode: meta.mode,
        }),
      );
      for (let i = 0; i < pcm.length; i += CHUNK_SAMPLES) {
        const chunk = pcm.subarray(i, i + CHUNK_SAMPLES);
        socket.send(chunk.slice().buffer);
      }
      socket.send(JSON.stringify({ type: "flush" }));
      armSettle();
    };
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let msg: ServerMessage;
      try {
        msg = JSON.parse(event.data) as ServerMessage;
      } catch {
        return;
      }
      if (msg.type === "status" && msg.state === "error") {
        done(new Error(msg.message || "The speech service is not available"));
        return;
      }
      if ((msg.type === "partial" || msg.type === "final") && typeof msg.text === "string") {
        if (msg.type === "final") {
          const key = String((msg as { id?: string }).id ?? finals.size);
          if (msg.dropped) finals.delete(key);
          else finals.set(key, { start: msg.start_s ?? 0, end: msg.end_s ?? 0, text: msg.text.trim() });
        }
        onPartial?.(msg.text);
        armSettle();
      }
    };
    socket.onerror = () => done(new Error("Could not reach the speech service"));
    socket.onclose = () => done();
  });
}
