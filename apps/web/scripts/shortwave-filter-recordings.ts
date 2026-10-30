/** Same-input A/B validation: apply the production speech filter to real captured PCM. */
import { readFile, writeFile, rename } from "node:fs/promises";
import { resolve } from "node:path";
import { AudioEnhancer } from "../src/lib/sdr/audio-enhancement";

export async function filterSurveyRecordings() {
  const directory = resolve("../../.runtime/shortwave-survey");
  const report = JSON.parse(await readFile(resolve(directory, "latest.json"), "utf8"));
  for (const test of report.listeningTests) {
    if (!/^[\w-]+\.wav$/.test(test.file)) throw new Error("Invalid captured filename");
    const wav = await readFile(resolve(directory, test.file));
    // capture.ts produces canonical mono PCM16 WAV; reject other layouts.
    if (wav.toString("ascii", 0, 4) !== "RIFF" || wav.toString("ascii", 36, 40) !== "data" || wav.readUInt16LE(22) !== 1 || wav.readUInt16LE(34) !== 16) throw new Error("Unsupported WAV layout");
    const count = wav.readUInt32LE(40) / 2, rate = wav.readUInt32LE(24);
    const input = Float32Array.from({ length: count }, (_, i) => wav.readInt16LE(44 + i * 2) / 32768);
    const filter = new AudioEnhancer(() => ({ noiseReduction: "off", voiceIsolation: false, speechFilter: true }));
    const output = new Float32Array(count);
    for (let i = 0; i < count; i += 997) output.set(filter.process(input.subarray(i, i + 997), rate), i);
    filter.reset();
    const filtered = Uint8Array.from(wav), view = new DataView(filtered.buffer);
    for (let i = 0; i < count; i++) view.setInt16(44 + i * 2, Math.round(output[i] * 32767), true);
    test.speechFile = test.file.replace(/\.wav$/, "-speech.wav");
    await writeFile(resolve(directory, test.speechFile), filtered, { mode: 0o600 });
  }
  await writeFile(resolve(directory, "filtered.tmp.json"), JSON.stringify(report, null, 2), { mode: 0o600 });
  await rename(resolve(directory, "filtered.tmp.json"), resolve(directory, "latest.json"));
  process.stdout.write(`Saved ${report.listeningTests.length} same-input speech-filter comparisons\n`);
}
if (process.argv[1]?.endsWith("shortwave-filter-recordings.ts")) {
  void filterSurveyRecordings().catch((error) => { process.stderr.write(`${error}\n`); process.exitCode = 1; });
}
