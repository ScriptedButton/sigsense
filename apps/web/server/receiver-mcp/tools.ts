import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import type { ReceiverAction } from "../../src/lib/sdr/receiver-control/protocol";

type JsonObject = Record<string, unknown>;
type Bootstrap = { token: string; port: number; version: number };

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
export const DEFAULT_TOKEN_FILE = resolve(ROOT, ".runtime/receiver-control.json");
export const CAPTURE_DIR = resolve(ROOT, ".runtime/receiver-captures");

export class ReceiverMcpError extends Error {
  constructor(readonly code: string, message: string, readonly status?: number) {
    super(message);
  }
}

function object(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

export type BrokerClientOptions = { brokerUrl?: string; tokenFile?: string };

export class ReceiverBrokerClient {
  constructor(private options: BrokerClientOptions = {}) {}

  private async bootstrap(): Promise<{ url: string; token: string }> {
    const tokenFile = this.options.tokenFile ?? process.env.SIGSENSE_RECEIVER_TOKEN_FILE ?? DEFAULT_TOKEN_FILE;
    let config: Bootstrap;
    try {
      config = JSON.parse(await readFile(tokenFile, "utf8")) as Bootstrap;
    } catch (error) {
      throw new ReceiverMcpError("config_unavailable", `Cannot read receiver broker config at ${tokenFile}: ${error instanceof Error ? error.message : error}`);
    }
    if (!config.token || !Number.isInteger(config.port)) throw new ReceiverMcpError("invalid_config", "Receiver broker config is invalid");
    return {
      url: (this.options.brokerUrl ?? process.env.SIGSENSE_RECEIVER_BROKER_URL ?? `http://127.0.0.1:${config.port}`).replace(/\/$/, ""),
      token: process.env.SIGSENSE_RECEIVER_TOKEN ?? config.token,
    };
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const { url, token } = await this.bootstrap();
    let response: Response;
    try {
      response = await fetch(`${url}${path}`, {
        ...init,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json", ...init.headers },
        signal: AbortSignal.timeout(125_000),
      });
    } catch (error) {
      throw new ReceiverMcpError("broker_unavailable", `Receiver broker is unavailable: ${error instanceof Error ? error.message : error}`);
    }
    const body = await response.json().catch(() => ({})) as JsonObject;
    if (!response.ok) {
      const detail = object(body.error);
      throw new ReceiverMcpError(typeof detail.code === "string" ? detail.code : "broker_error", typeof detail.message === "string" ? detail.message : `Broker returned HTTP ${response.status}`, response.status);
    }
    return body;
  }

  async sessions(): Promise<unknown[]> {
    const body = object(await this.request("/sessions"));
    return Array.isArray(body.sessions) ? body.sessions : [];
  }

  async command(action: ReceiverAction, args: JsonObject, sessionId?: string, timeoutMs?: number): Promise<unknown> {
    const body = object(await this.request("/command", {
      method: "POST",
      body: JSON.stringify({ action, args, ...(sessionId ? { sessionId } : {}), ...(timeoutMs ? { timeoutMs } : {}) }),
    }));
    return body.result;
  }
}

export function compact(value: unknown, max = 900): string {
  const text = JSON.stringify(value ?? null);
  return text.length <= max ? text : `${text.slice(0, max)}…`;
}

export function summarizeCapture(value: unknown): JsonObject {
  const result = object(value);
  const { data: _data, ...summary } = result;
  if (typeof result.data === "string") summary.bytes = Math.floor(result.data.length * 3 / 4);
  if (Array.isArray(result.samples)) summary.sampleCount = result.samples.length;
  return summary;
}

export async function persistIqCapture(value: unknown): Promise<{ uri: string; path: string; bytes: number; metadata: JsonObject }> {
  const result = object(value);
  if (typeof result.data !== "string") throw new ReceiverMcpError("invalid_capture", "Receiver returned no base64 IQ data");
  const buffer = Buffer.from(result.data, "base64");
  if (buffer.length === 0 || buffer.length > 64 * 1024 * 1024) throw new ReceiverMcpError("capture_size", "IQ capture must be between 1 byte and 64 MiB");
  await mkdir(CAPTURE_DIR, { recursive: true, mode: 0o700 });
  const extension = result.format === "complex-f32-le" ? "cf32"
    : result.format === "uint8" ? "cu8"
      : typeof result.format === "string" && /^[a-z0-9]{1,8}$/i.test(result.format) ? result.format : "bin";
  const path = resolve(CAPTURE_DIR, `iq-${new Date().toISOString().replace(/[:.]/g, "-")}-${crypto.randomUUID().slice(0, 8)}.${extension}`);
  await writeFile(path, Uint8Array.from(buffer), { mode: 0o600 });
  return { uri: pathToFileURL(path).href, path, bytes: buffer.length, metadata: summarizeCapture(result) };
}

export function audioContent(value: unknown): { content: Array<Record<string, unknown>>; summary: JsonObject } {
  const result = object(value);
  const data = typeof result.data === "string" ? result.data : undefined;
  const format = typeof result.format === "string" ? result.format : "wav";
  const summary = summarizeCapture(result);
  return {
    content: [
      { type: "text", text: compact(summary) },
      ...(data ? [{ type: "audio", data, mimeType: format === "wav" ? "audio/wav" : `audio/${format}` }] : []),
    ],
    summary,
  };
}
