import { readFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import { AutofinderService } from "./service";
import { createServiceServer } from "./http";
import { FinderStore, readSnapshot, serviceView } from "./store";
import { DEFAULT_TOKEN_FILE } from "../receiver-mcp/tools";

async function main() {
  const store = new FinderStore(await readSnapshot());
  const service: AutofinderService = new AutofinderService(undefined, undefined, (status): Promise<void> => store.update(status, service.enabled));
  service.enabled = store.snapshot.enabled;
  service.gain = store.snapshot.gain;
  const server = createServiceServer(async () => {
    const config = JSON.parse(await readFile(process.env.SIGSENSE_RECEIVER_TOKEN_FILE ?? DEFAULT_TOKEN_FILE, "utf8"));
    const token = process.env.SIGSENSE_RECEIVER_TOKEN ?? config.token;
    if (typeof token !== "string" || !token) throw new Error("Receiver token unavailable");
    return token;
  }, () => ({ ...serviceView(store.snapshot), bridgeUrl: process.env.SIGSENSE_AUTOFINDER_RTL_URL ?? "http://127.0.0.1:8081" }), (action) => service.control(action));
  await new Promise<void>((done, reject) => {
    server.once("error", reject);
    server.listen(Number(process.env.SIGSENSE_AUTOFINDER_PORT ?? 8790), "127.0.0.1", done);
  });
  const abort = new AbortController();
  const stop = () => abort.abort();
  process.once("SIGINT", stop); process.once("SIGTERM", stop);
  let interval = 2_000;
  try {
    while (!abort.signal.aborted) {
      try {
        await service.tick();
        interval = 2_000;
      } catch (error) {
        await store.update({ at: new Date().toISOString(), status: "error", message: error instanceof Error ? error.message : String(error), sessionId: null, receiver: null, finder: null, gain: service.gain }, service.enabled);
        interval = Math.min(30_000, interval * 2);
      }
      await delay(interval, undefined, { signal: abort.signal }).catch(() => {});
    }
  } finally {
    try { await service.stop(); }
    finally { await new Promise<void>((done) => server.close(() => done())); }
  }
}

void main().catch((error) => { process.stderr.write(`autofinder-service: ${String(error)}\n`); process.exitCode = 1; });
