import { sameOrigin } from "../../../../server/request-origin";
import { ASSISTANT_SYSTEM_PROMPT, ASSISTANT_TOOLS } from "@/lib/sdr/assistant/tools";

const MAX_BODY_BYTES = 400_000;
const MAX_MESSAGES = 60;

/** One model turn. The browser runs the tool loop, so tools execute against the live receiver and the API key never leaves the server. */
export async function POST(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Same-origin access required" }, { status: 403 });
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return Response.json({ error: "Assistant is not configured. Set ANTHROPIC_API_KEY on the SigSense server and restart it." }, { status: 503 });
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY_BYTES) return Response.json({ error: "Conversation too long" }, { status: 413 });
  let messages: unknown;
  try { messages = (JSON.parse(raw) as { messages?: unknown }).messages; } catch { /* fall through */ }
  if (!Array.isArray(messages) || messages.length === 0 || messages.length > MAX_MESSAGES) {
    return Response.json({ error: "messages must be a non-empty array" }, { status: 400 });
  }
  const upstream = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: { "content-type": "application/json", "x-api-key": apiKey, "anthropic-version": "2023-06-01" },
    body: JSON.stringify({
      model: process.env.SIGSENSE_ASSISTANT_MODEL ?? "claude-sonnet-5-5",
      max_tokens: 1024,
      system: ASSISTANT_SYSTEM_PROMPT,
      tools: ASSISTANT_TOOLS,
      messages,
    }),
  }).catch(() => null);
  if (!upstream) return Response.json({ error: "Could not reach the model API" }, { status: 502 });
  const body = await upstream.json().catch(() => null) as { error?: { message?: string } } | null;
  if (!upstream.ok) return Response.json({ error: body?.error?.message ?? `Model API error ${upstream.status}` }, { status: 502 });
  return Response.json(body, { headers: { "Cache-Control": "no-store" } });
}
