/** Diagnostic captures only; no station identity or intelligibility inferred from RF peaks. */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { ReceiverBrokerClient } from "../server/receiver-mcp/tools";

async function main() {
  const broker = new ReceiverBrokerClient();
  const sessions = await broker.sessions() as Array<{ id: string; url: string; state: { status: string; connection: string } }>;
  const target = sessions.find((s) => s.url.includes("receiverAgent=1"));
  if (!target) throw new Error("No diagnostic receiver");
  if (sessions.some((s) => s.id !== target.id && s.state.status === "playing" && s.state.connection === "rtltcp")) throw new Error("User receiver is busy");
  const directory = resolve("../../.runtime/speech-verification");
  await mkdir(directory, { recursive: true, mode: 0o700 });
  const command = (action: string, args: Record<string, unknown> = {}) => broker.command(action as never, args, target.id, 20000);
  const records: unknown[] = [];
  try {
    await command("configure", { scheme: "AM", bandwidthHz: 6000, gainDb: 0, volume: .8, audioNoiseReduction: "off", audioSpeechFilter: false, voiceIsolation: false, squelch: 0, autoSquelch: false });
    if (target.state.status !== "playing") await command("connect", { type: "rtltcp", url: "http://127.0.0.1:8081" });
    const frequencies = process.argv.slice(2).map(Number);
    for (const hz of frequencies.length ? frequencies : [4840000, 5935000, 5950000, 5850000, 5050000, 7490000, 7730000, 9395000, 9490000]) {
      if (!Number.isFinite(hz) || hz < 2e6 || hz > 30e6) throw new Error("Invalid shortwave frequency");
      await command("configure", { frequencyHz: hz + 50000, offsetHz: -50000 });
      await new Promise((done) => setTimeout(done, 800));
      const capture = await command("capture_audio", { durationMs: 10000, maxSamples: 480000, stage: "raw" }) as { data: string; [key: string]: unknown };
      const file = `${hz}-raw.wav`;
      await writeFile(resolve(directory, file), Uint8Array.from(Buffer.from(capture.data, "base64")), { mode: 0o600 });
      const { data: _, ...metadata } = capture;
      records.push({ frequencyHz: hz, file, metadata });
      await writeFile(resolve(directory, "captures.json"), JSON.stringify(records, null, 2), { mode: 0o600 });
      process.stdout.write(`Captured ${hz} Hz\n`);
    }
  } finally { await command("stop").catch(() => {}); }
}
void main().catch((error) => { process.stderr.write(`${error}\n`); process.exitCode = 1; });
