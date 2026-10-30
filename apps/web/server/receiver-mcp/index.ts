import { McpServer } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { readFile } from "node:fs/promises";
import { z } from "zod";

import type { ReceiverAction } from "../../src/lib/sdr/receiver-control/protocol";
import { ReceiverBrokerClient, ReceiverMcpError, audioContent, compact, persistIqCapture, summarizeCapture } from "./tools";

const session = z.object({ sessionId: z.string().min(1).max(128).optional() }).strict();
const timeout = z.number().int().min(100).max(120_000).optional();
const mode = z.enum(["WBFM", "NBFM", "AM", "USB", "LSB", "CW"]);
type Args = Record<string, unknown> & { sessionId?: string; timeoutMs?: number };

function result(value: unknown) {
  const structuredContent = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : { result: value ?? null };
  return { content: [{ type: "text" as const, text: compact(value) }], structuredContent };
}
function failure(error: unknown) {
  const detail = error instanceof ReceiverMcpError ? { code: error.code, message: error.message, status: error.status } : { code: "internal_error", message: error instanceof Error ? error.message : String(error) };
  return { isError: true as const, content: [{ type: "text" as const, text: `${detail.code}: ${detail.message}` }], structuredContent: { error: detail } };
}

export function createReceiverMcpServer(client = new ReceiverBrokerClient()) {
  const server = new McpServer({ name: "sigsense-receiver", version: "1.0.0" });
  const command = async (action: ReceiverAction, input: Args, transform: (value: unknown) => unknown = (v) => v) => {
    try {
      const { sessionId, timeoutMs, ...args } = input;
      return result(transform(await client.command(action, args, sessionId, timeoutMs)));
    } catch (error) { return failure(error); }
  };
  const register = (name: string, description: string, action: ReceiverAction, inputSchema: z.ZodType, annotations?: { readOnlyHint?: boolean; destructiveHint?: boolean }) => {
    server.registerTool(name, { description, inputSchema, annotations: { destructiveHint: false, ...annotations } }, (input) => command(action, input as Args));
  };

  server.registerTool("list_receivers", { description: "List live browser receiver sessions and their current state.", inputSchema: z.object({}).strict(), annotations: { readOnlyHint: true } }, async () => {
    try { const sessions = await client.sessions(); return result({ sessions }); } catch (error) { return failure(error); }
  });
  register("get_state", "Get the selected receiver's complete current state.", "get_state", session, { readOnlyHint: true });
  register("autofinder_state", "Read continuous autonomous HF search progress and recent neural speech checks.", "autofinder_state", session, { readOnlyHint: true });
  register("autofinder_start", "Start continuous autonomous shortwave speech search. Automatically selects bands and frequencies; uses local neural speech assessment before listening.", "autofinder_start", session);
  register("autofinder_stop", "Stop autonomous search and restore the original receiver dial/settings.", "autofinder_stop", session);
  register("autofinder_skip", "Skip the current autonomous-search candidate and avoid it for 15 minutes.", "autofinder_skip", session);
  register("tune", "Tune to an absolute frequency in Hz, optionally selecting scheme/bandwidth. center=true retunes the hardware center; otherwise an in-passband offset is preferred.", "tune", session.extend({ frequencyHz: z.number().int().min(0).max(6_000_000_000), scheme: mode.optional(), bandwidthHz: z.number().int().min(50).max(300_000).optional(), center: z.boolean().optional() }));
  register("configure", "Configure receiver state using the browser receiver's exact parameters.", "configure", session.extend({
    frequencyHz: z.number().int().min(0).max(6_000_000_000).optional(), scheme: mode.optional(),
    bandwidthHz: z.number().int().min(50).max(300_000).optional(), sampleRate: z.number().int().min(250_000).max(20_000_000).optional(),
    gainDb: z.number().min(0).max(50).optional(), autoGain: z.boolean().optional(),
    squelch: z.number().min(0).max(40).optional(), autoSquelch: z.boolean().optional(),
    volume: z.number().min(0).max(1).optional(), offsetHz: z.number().int().min(-20_000_000).max(20_000_000).optional(),
    ppm: z.number().int().min(-200).max(200).optional(), biasTee: z.boolean().optional(), afc: z.boolean().optional(),
    audioNoiseReduction: z.enum(["off", "low", "medium", "high"]).optional(), voiceIsolation: z.boolean().optional(), audioSpeechFilter: z.boolean().optional(),
  }));
  register("connect", "Connect the browser receiver to demo, rtl_tcp, KiwiSDR, or OpenWebRX+.", "connect", session.extend({ type: z.enum(["demo", "rtltcp", "kiwi", "openwebrx"]), url: z.string().url().max(2048).optional(), password: z.string().max(256).optional() }));
  register("stop", "Stop the selected receiver stream.", "stop", session, { destructiveHint: false });
  register("get_spectrum", "Return the latest bounded RF spectrum snapshot.", "get_spectrum", session, { readOnlyHint: true });
  server.registerTool("capture_audio", { description: "Capture demodulated audio at the raw or playback stage. Returns native MCP WAV audio.", inputSchema: session.extend({ durationMs: z.number().int().min(50).max(10_000).default(3_000), maxSamples: z.number().int().min(256).max(480_000).default(144_000), stage: z.enum(["raw", "playback"]).default("playback"), timeoutMs: timeout }) }, async (input) => {
    try { const { sessionId, timeoutMs, ...args } = input; const captured = await client.command("capture_audio", args, sessionId, timeoutMs); const audio = audioContent(captured); return { content: audio.content as never, structuredContent: audio.summary }; } catch (error) { return failure(error); }
  });
  server.registerTool("capture_iq", { description: "Capture bounded raw IQ and save it in SigSense's private capture directory. Returns a file resource link, never inline base64.", inputSchema: session.extend({ durationMs: z.number().int().min(10).max(2_000).default(250), maxSamples: z.number().int().min(256).max(262_144).default(65_536), timeoutMs: timeout }) }, async (input) => {
    try {
      const { sessionId, timeoutMs, ...args } = input;
      const saved = await persistIqCapture(await client.command("capture_iq", args, sessionId, timeoutMs));
      server.registerResource(`iq-${saved.path.split("/").at(-1)}`, saved.uri,
        { title: "Native-rate receiver IQ capture", mimeType: "application/octet-stream" },
        async (uri) => ({ contents: [{ uri: uri.href, mimeType: "application/octet-stream", blob: (await readFile(saved.path)).toString("base64") }] }));
      return { content: [{ type: "text" as const, text: compact(saved.metadata) }, { type: "resource_link" as const, uri: saved.uri, name: saved.path.split("/").at(-1) ?? "IQ capture", mimeType: "application/octet-stream", size: saved.bytes }], structuredContent: saved };
    } catch (error) { return failure(error); }
  });
  register("get_signals", "List currently detected RF peaks.", "get_signals", session, { readOnlyHint: true });
  register("find_signal", "Return the strongest signal, or the detected signal nearest frequencyHz.", "find_signal", session.extend({ frequencyHz: z.number().min(0).max(6_000_000_000).optional() }), { readOnlyHint: true });
  register("get_schedule", "Query the shortwave schedule using the browser's schedule filters.", "get_schedule", session.extend({ when: z.enum(["now", "soon", "all"]).default("now"), bandId: z.string().max(80).optional(), language: z.string().max(80).optional(), query: z.string().max(200).optional() }), { readOnlyHint: true });
  register("set_display", "Set spectrum/waterfall display options using exact browser display keys.", "set_display", session.extend({
    waterfallPalette: z.enum(["thermal", "viridis", "ocean", "mono", "cubehelix"]).optional(),
    waterfallRangeDb: z.number().min(16).max(60).optional(), waterfallFloorOffsetDb: z.number().min(-12).max(12).optional(),
    waterfallSpeed: z.enum(["slow", "normal", "fast"]).optional(), waterfallDirection: z.enum(["top", "bottom"]).optional(),
    dbRange: z.enum(["auto", "wide", "normal", "tight"]).optional(), noiseFilter: z.enum(["off", "low", "medium", "high"]).optional(),
    freqTarget: z.enum(["tune", "center"]).optional(), freqFormat: z.enum(["auto", "mhz", "khz", "hz"]).optional(),
    markerMode: z.enum(["off", "strongest", "all"]).optional(),
  }));
  register("get_display", "Get spectrum/waterfall display settings.", "get_display", session, { readOnlyHint: true });
  register("get_diagnostics", "Get receiver, stream, clipping, and audio diagnostics.", "get_diagnostics", session, { readOnlyHint: true });

  server.registerResource("receiver-state", "receiver://current/state", { title: "Current receiver state", mimeType: "application/json" }, async (uri) => ({ contents: [{ uri: uri.href, mimeType: "application/json", text: JSON.stringify(await client.command("get_state", {}), null, 2) }] }));
  server.registerResource("receiver-diagnostics", "receiver://current/diagnostics", { title: "Current receiver diagnostics", mimeType: "application/json" }, async (uri) => ({ contents: [{ uri: uri.href, mimeType: "application/json", text: JSON.stringify(await client.command("get_diagnostics", {}), null, 2) }] }));
  server.registerPrompt("troubleshoot_reception", { description: "Inspect state and diagnostics to troubleshoot weak, noisy, distorted, or silent reception." }, () => ({ messages: [{ role: "user" as const, content: { type: "text" as const, text: "Read receiver://current/state and receiver://current/diagnostics. Diagnose tuning mode, bandwidth, RF gain/clipping, squelch, and local interference. Recommend the safest next receiver-control tool call; do not enable neural voice isolation automatically." } }] }));
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  serveStdio(() => createReceiverMcpServer(), { onerror: (error) => process.stderr.write(`receiver-mcp: ${error.message}\n`) });
}
