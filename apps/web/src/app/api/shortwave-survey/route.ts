import { readFile } from "node:fs/promises";
import path from "node:path";
import { rankListeningCandidates, type SurveyCarrier } from "@/lib/sdr/shortwave/survey";

type ListeningTest = { frequencyHz: number; file: string; speechFile?: string; setting: { bandwidthHz: number; audioNoiseReduction: string }; scheduled: Array<{ name: string; language: string; scheduledNow: boolean }> };

/** Only publish the compact report and clips explicitly recorded in that report. */
export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store, private", "Cross-Origin-Resource-Policy": "same-origin" };
  const directory = path.resolve(process.cwd(), "../../.runtime/shortwave-survey");
  try {
    const report = JSON.parse(await readFile(/* turbopackIgnore: true */ path.join(directory, "latest.json"), "utf8")) as {
      startedAt: string; completedAt: string | null; status: string; source: string;
      bands: Array<{ id: string }>; candidates: Array<SurveyCarrier & { band: string }>; listeningTests: ListeningTest[];
    };
    const clip = new URL(request.url).searchParams.get("clip");
    if (clip !== null) {
      if (!/^[\w-]+\.wav$/.test(clip) || !report.listeningTests.some((test) => test.file === clip || test.speechFile === clip)) {
        return Response.json({ error: "Unknown recording" }, { status: 404, headers });
      }
      const data = await readFile(/* turbopackIgnore: true */ path.join(directory, clip));
      return new Response(Uint8Array.from(data), { headers: { ...headers, "Content-Type": "audio/wav", "Content-Length": String(data.length) } });
    }
    return Response.json({ startedAt: report.startedAt, completedAt: report.completedAt, status: report.status,
      source: report.source, bands: report.bands.map(({ id }) => id), candidates: rankListeningCandidates(report.candidates),
      listeningTests: report.listeningTests.map(({ frequencyHz, file, speechFile, setting, scheduled }) => ({ frequencyHz, file, speechFile, setting, scheduled })) }, { headers });
  } catch {
    return Response.json({ status: "unavailable", candidates: [], listeningTests: [] }, { headers });
  }
}
