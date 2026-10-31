import { sameOrigin } from "../../../../../server/request-origin";

const MAX_BODY_BYTES = 200_000;
// Fixed server-side so the browser can't point this route at arbitrary hosts.
const LLAMA_URL = (process.env.SIGSENSE_LLAMA_URL ?? "http://192.168.1.167:8080").replace(/\/+$/, "");

/** Forwards one chat turn to a llama.cpp `llama-server` (OpenAI-compatible API) on the LAN. */
export async function POST(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Same-origin access required" }, { status: 403 });
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return Response.json({ error: "Conversation too long" }, { status: 413 });
  let body: { messages?: unknown; schema?: unknown } = {};
  try { body = JSON.parse(raw); } catch { /* handled below */ }
  const valid = Array.isArray(body.messages) && body.messages.length > 0 && body.messages.length <= 80 &&
    body.messages.every((m) => m && ["system", "user", "assistant"].includes(m.role) && typeof m.content === "string");
  if (!valid) return Response.json({ error: "messages must be chat messages" }, { status: 400 });
  const schema = typeof body.schema === "string" ? (() => { try { return JSON.parse(body.schema as string); } catch { return undefined; } })() : undefined;
  const upstream = await fetch(`${LLAMA_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      messages: body.messages,
      temperature: 0.1,
      max_tokens: 400,
      ...(schema ? { response_format: { type: "json_object", schema } } : {}),
    }),
    signal: AbortSignal.timeout(120_000),
  }).catch(() => null);
  if (!upstream) return Response.json({ error: `Could not reach llama.cpp server at ${LLAMA_URL}` }, { status: 502 });
  const data = await upstream.json().catch(() => null) as { choices?: { message?: { content?: string } }[]; error?: { message?: string } } | null;
  if (!upstream.ok) return Response.json({ error: data?.error?.message ?? `llama.cpp error ${upstream.status}` }, { status: 502 });
  return Response.json({ content: data?.choices?.[0]?.message?.content ?? "" }, { headers: { "Cache-Control": "no-store" } });
}
