/** Receive-only, real-device shortwave survey through the official MCP client. */
import { Client } from "@modelcontextprotocol/client";
import { StdioClientTransport } from "@modelcontextprotocol/client/stdio";
import { mkdir, writeFile, rename } from "node:fs/promises";
import { resolve } from "node:path";
import { ReceiverBrokerClient } from "../server/receiver-mcp/tools";
import { surveyCarriers, confirmSurveyCarriers, rankListeningCandidates, type SurveyCarrier } from "../src/lib/sdr/shortwave/survey";
import { SHORTWAVE_BANDS } from "../src/lib/sdr/shortwave/bands";
import { lookupAtFrequency } from "../src/lib/sdr/shortwave/schedule";
import schedule from "../src/lib/sdr/shortwave/schedule.json";
import type { ReceiverSpectrum } from "../src/lib/sdr/receiver-control/protocol";
import type { SdrState } from "../src/lib/sdr/types";
import { filterSurveyRecordings } from "./shortwave-filter-recordings";

const pause = (ms: number) => new Promise((done) => setTimeout(done, ms));
const ROOT = resolve("../..");
const DIRECTORY = resolve(ROOT, ".runtime/shortwave-survey");

async function main() {
  await mkdir(DIRECTORY, { recursive: true, mode: 0o700 });
  const broker = new ReceiverBrokerClient();
  let target: { id: string; state: SdrState; url: string } | undefined;
  for (let attempt = 0; attempt < 30; attempt++) {
    const sessions = await broker.sessions() as Array<{ id: string; state: SdrState; url: string }>;
    target = sessions.find((session) => session.url.includes("receiverAgent=1"));
    if (target) break;
    await pause(500);
  }
  if (!target) throw new Error("The autonomous receiver browser has not connected to MCP");
  const receivers = await broker.sessions() as Array<{ id: string; state: SdrState }>;
  if (receivers.some((session) => session.id !== target!.id && ["playing", "connecting"].includes(session.state.status) && session.state.connection === "rtltcp")) {
    throw new Error("RTL-SDR is in use by another receiver; survey will not interrupt listening");
  }
  const sessionId = target.id, original = target.state;
  const client = new Client({ name: "sigsense-shortwave-survey", version: "1.0.0" });
  const transport = new StdioClientTransport({ command: process.execPath, args: ["--import", "tsx", resolve("server/receiver-mcp/index.ts")], cwd: resolve("."), stderr: "pipe" });
  await client.connect(transport);
  const call = async (name: string, args: Record<string, unknown> = {}) => {
    const response = await client.callTool({ name, arguments: { sessionId, ...args } });
    if (response.isError) throw new Error(`${name}: ${JSON.stringify(response.content)}`);
    return response;
  };
  const report = { startedAt: new Date().toISOString(), completedAt: null as string | null, status: "scanning",
    source: "RTL-SDR Blog V4 / rtl_tcp", sessionId, originalState: original,
    bands: [] as Array<Record<string, unknown>>, candidates: [] as Array<SurveyCarrier & { band: string }>,
    listeningTests: [] as Array<Record<string, unknown>>, errors: [] as string[] };
  const save = async () => {
    await writeFile(resolve(DIRECTORY, "latest.tmp.json"), JSON.stringify(report, null, 2), { mode: 0o600 });
    await rename(resolve(DIRECTORY, "latest.tmp.json"), resolve(DIRECTORY, "latest.json"));
  };
  try {
    await call("configure", { frequencyHz: 6_087_500, scheme: "AM", bandwidthHz: 6000, gainDb: 0, volume: 0, sampleRate: 1_024_000,
      squelch: 0, autoSquelch: false, afc: false, audioNoiseReduction: "off", voiceIsolation: false, audioSpeechFilter: false });
    if (original.status !== "playing") {
      const connected = (await call("connect", { type: "rtltcp", url: "http://127.0.0.1:8081" })).structuredContent as SdrState;
      if (connected.status !== "playing" || connected.connection !== "rtltcp") throw new Error(`RTL connection failed: ${JSON.stringify(connected)}`);
    }
    await save();
    const scanPass = async (centerHz: number) => {
      await call("tune", { frequencyHz: centerHz, center: true });
      const tunedAt = Date.now();
      await pause(850);
      for (let attempt = 0; attempt < 10; attempt++) {
        const snapshot = (await call("get_spectrum")).structuredContent as ReceiverSpectrum;
        if (snapshot?.values?.length && snapshot.centerHz === centerHz && Date.parse(snapshot.capturedAt) > tunedAt + 200) return snapshot;
        await pause(200);
      }
      throw new Error(`No fresh spectrum at ${centerHz} Hz`);
    };
    for (const id of ["49m", "31m", "41m", "25m", "60m", "19m", "75m", "16m"]) {
      const band = SHORTWAVE_BANDS.find((value) => value.id === id)!;
      const center = Math.round((band.loKHz + band.hiKHz) * 500) + 37_500;
      const first = await scanPass(center), second = await scanPass(center + 125_000);
      const peaks1 = surveyCarriers(first, band.loKHz * 1000, band.hiKHz * 1000);
      const peaks2 = surveyCarriers(second, band.loKHz * 1000, band.hiKHz * 1000);
      const confirmed = confirmSurveyCarriers(peaks1, peaks2, Math.max(first.binHz, second.binHz) * 2.1);
      const diagnostics = (await call("get_diagnostics")).structuredContent;
      report.bands.push({ id, centerHz: center, first, second, peaks: peaks1, confirmed, diagnostics });
      report.candidates.push(...confirmed.map((carrier) => ({ ...carrier, band: id })));
      await save();
      process.stdout.write(`${id}: ${peaks1.length} peaks, ${confirmed.length} frequency-stable candidates\n`);
    }
    report.candidates = rankListeningCandidates(report.candidates);
    report.status = "comparing-audio"; await save();
    const selected = report.candidates.filter((candidate) => candidate.sidebandExcessDb > 1.5).slice(0, 4);
    for (const candidate of selected) {
      // Listen away from the tuner DC offset while keeping the exact carrier offset.
      await call("tune", { frequencyHz: candidate.frequencyHz + 50_000, center: true, scheme: "AM" });
      await call("tune", { frequencyHz: candidate.frequencyHz, scheme: "AM" });
      const scheduled = lookupAtFrequency(schedule.entries, candidate.frequencyHz).slice(0, 4).map((match) => ({ name: match.entry.n, language: match.entry.l, scheduledNow: match.onAir }));
      const options = [{ bandwidthHz: 9000, audioNoiseReduction: "off" }, { bandwidthHz: 6000, audioNoiseReduction: "off" }, { bandwidthHz: 4000, audioNoiseReduction: "low" }];
      for (const setting of options) {
        await call("configure", { ...setting, volume: .7 }); await pause(600);
        const audio = await call("capture_audio", { durationMs: 3000, maxSamples: 144000, stage: "playback" });
        const block = audio.content.find((part) => part.type === "audio") as { data: string } | undefined;
        if (!block) throw new Error("MCP returned no WAV audio");
        const file = `${candidate.frequencyHz}-${setting.bandwidthHz}-${setting.audioNoiseReduction}.wav`;
        await writeFile(resolve(DIRECTORY, file), Uint8Array.from(Buffer.from(block.data, "base64")), { mode: 0o600 });
        report.listeningTests.push({ frequencyHz: candidate.frequencyHz, band: candidate.band, scheduled,
          setting, file, metadata: audio.structuredContent, diagnostics: (await call("get_diagnostics")).structuredContent });
        await save();
      }
      process.stdout.write(`${candidate.frequencyHz} Hz: recorded 9k/off, 6k/off, 4k/low\n`);
    }
    report.status = "complete"; report.completedAt = new Date().toISOString(); await save();
    await filterSurveyRecordings();
    process.stdout.write(`Saved real-device survey: ${resolve(DIRECTORY, "latest.json")}\n`);
  } catch (error) {
    report.status = "error"; report.errors.push(error instanceof Error ? error.message : String(error)); await save(); throw error;
  } finally {
    // Release the single-client RTL bridge for normal listening after the survey.
    await call("stop").catch(() => {}); await client.close();
  }
}
void main().catch((error) => { process.stderr.write(`${error instanceof Error ? error.stack : error}\n`); process.exitCode = 1; });
