/** In-browser assistant model (WebGPU via WebLLM). No server, API key or network after the first model download. */
import type { MLCEngine } from "@mlc-ai/web-llm";
import { ASSISTANT_SYSTEM_PROMPT, ASSISTANT_TOOLS } from "./tools";
import type { AssistantStep, Block, Message } from "./types";

export const LOCAL_MODELS = [
  { id: "Qwen2.5-1.5B-Instruct-q4f16_1-MLC", label: "Fast (1.5B, ~1 GB)" },
  { id: "Qwen2.5-3B-Instruct-q4f16_1-MLC", label: "Smarter (3B, ~2 GB)" },
] as const;

export function localLlmSupported(): boolean {
  return typeof navigator !== "undefined" && "gpu" in navigator;
}

const MAX_RESULT_CHARS = 1_500;

function describeProps(properties: Record<string, unknown>): string {
  return Object.entries(properties).map(([key, raw]) => {
    const p = raw as { type?: string; enum?: string[] };
    return `${key}${p.enum ? `:${p.enum.join("|")}` : p.type ? `:${p.type}` : ""}`;
  }).join(", ");
}

const TOOL_LIST = ASSISTANT_TOOLS.map((t) => `- ${t.name}(${describeProps(t.input_schema.properties)}): ${t.description}`).join("\n");

const LOCAL_SYSTEM_PROMPT = `${ASSISTANT_SYSTEM_PROMPT}

Tools:
${TOOL_LIST}

Always answer with one JSON object: {"calls":[{"tool":"<name>","args":{...}}],"reply":"<text>"}.
- To act, put tool calls in "calls" (at most 3) and leave "reply" empty. You will then receive the tool results.
- When the task is done or no tool is needed, return "calls":[] and a short "reply" for the user.
- Frequencies are in Hz: 7.2 MHz is 7200000.`;

export const RESPONSE_SCHEMA = JSON.stringify({
  type: "object",
  properties: {
    calls: {
      type: "array", maxItems: 3,
      items: {
        type: "object",
        properties: { tool: { type: "string", enum: ASSISTANT_TOOLS.map((t) => t.name) }, args: { type: "object" } },
        required: ["tool", "args"],
      },
    },
    reply: { type: "string" },
  },
  required: ["calls", "reply"],
});

type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

export function toChat(messages: Message[]): ChatMessage[] {
  const out: ChatMessage[] = [{ role: "system", content: LOCAL_SYSTEM_PROMPT }];
  const names = new Map<string, string>();
  for (const m of messages) {
    if (typeof m.content === "string") { out.push({ role: m.role, content: m.content }); continue; }
    if (m.role === "assistant") {
      const calls = m.content.filter((b): b is Extract<Block, { type: "tool_use" }> => b.type === "tool_use");
      calls.forEach((c) => names.set(c.id, c.name));
      const reply = m.content.filter((b): b is Extract<Block, { type: "text" }> => b.type === "text").map((b) => b.text).join("\n");
      out.push({ role: "assistant", content: JSON.stringify({ calls: calls.map((c) => ({ tool: c.name, args: c.input })), reply }) });
    } else {
      const results = m.content.filter((b): b is Extract<Block, { type: "tool_result" }> => b.type === "tool_result");
      out.push({
        role: "user",
        content: `Tool results:\n${results.map((r) => `${names.get(r.tool_use_id) ?? "tool"}${r.is_error ? " ERROR" : ""}: ${r.content.slice(0, MAX_RESULT_CHARS)}`).join("\n")}`,
      });
    }
  }
  return out;
}

let engine: MLCEngine | null = null;
let engineModel = "";
let loading: Promise<MLCEngine> | null = null;

export async function loadLocalModel(modelId: string, onProgress: (text: string) => void): Promise<void> {
  if (engine && engineModel === modelId) return;
  loading ??= (async () => {
    const { CreateMLCEngine } = await import("@mlc-ai/web-llm");
    if (engine) { await engine.unload(); engine = null; }
    const next = await CreateMLCEngine(modelId, { initProgressCallback: (p) => onProgress(p.text) });
    engine = next;
    engineModel = modelId;
    return next;
  })().finally(() => { loading = null; });
  await loading;
}

export function parseOutput(raw: string): Block[] {
  let parsed: { calls?: { tool?: unknown; args?: unknown }[]; reply?: unknown };
  try { parsed = JSON.parse(raw); } catch { return [{ type: "text", text: raw.trim() }]; }
  const blocks: Block[] = [];
  const calls = Array.isArray(parsed.calls) ? parsed.calls : [];
  const reply = typeof parsed.reply === "string" ? parsed.reply.trim() : "";
  if (reply) blocks.push({ type: "text", text: reply });
  for (const call of calls) {
    if (typeof call.tool !== "string") continue;
    const args = call.args && typeof call.args === "object" && !Array.isArray(call.args) ? call.args as Record<string, unknown> : {};
    blocks.push({ type: "tool_use", id: `local_${Math.random().toString(36).slice(2, 10)}`, name: call.tool, input: args });
  }
  return blocks;
}

/** Model must be loaded with loadLocalModel first. */
export const localStep: AssistantStep = async (messages) => {
  if (!engine) throw new Error("Local model is not loaded");
  const completion = await engine.chat.completions.create({
    messages: toChat(messages),
    temperature: 0.1,
    max_tokens: 400,
    response_format: { type: "json_object", schema: RESPONSE_SCHEMA },
  });
  return parseOutput(completion.choices[0]?.message?.content ?? "");
};

/** Same prompt and JSON protocol, served by a llama.cpp server through /api/assistant/llama. */
export const llamaStep: AssistantStep = async (messages) => {
  const response = await fetch("/api/assistant/llama", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ messages: toChat(messages), schema: RESPONSE_SCHEMA }),
  });
  const data = await response.json().catch(() => null) as { content?: string; error?: string } | null;
  if (!response.ok || data?.content === undefined) throw new Error(data?.error ?? `llama.cpp error ${response.status}`);
  return parseOutput(data.content);
};
