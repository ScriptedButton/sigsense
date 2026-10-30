/** Private binary DSP worker: mono float32/16 kHz in and out. No network or microphone. */
import { AudioEnhancer, loadVoiceIsolation } from "../../src/lib/sdr/audio-enhancement";

async function main() {
  await loadVoiceIsolation();
  const enhancer = new AudioEnhancer(() => ({ noiseReduction: "off", voiceIsolation: true }));
  let pending = new Uint8Array(0);
  process.stdout.write("READY\n");
  for await (const chunk of process.stdin) {
    const bytes = new Uint8Array(pending.length + chunk.length);
    bytes.set(pending); bytes.set(chunk, pending.length);
    const length = bytes.length - bytes.length % 4;
    const input = new Float32Array(bytes.buffer.slice(0, length));
    const output = enhancer.process(input, 16000);
    pending = bytes.slice(length);
    if (!process.stdout.write(new Uint8Array(output.buffer, output.byteOffset, output.byteLength))) {
      await new Promise<void>((done) => process.stdout.once("drain", done));
    }
  }
  enhancer.reset();
}
void main().catch((error) => { process.stderr.write(String(error)); process.exitCode = 1; });
