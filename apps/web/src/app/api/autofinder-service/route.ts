import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { readSnapshot, serviceView } from "../../../../server/autofinder-service/store";
import { parseServiceControl } from "@/lib/sdr/autofinder/service-types";
import type { ServiceControl, ServiceView } from "@/lib/sdr/autofinder/service-types";
import { requestOrigin, sameOrigin } from "../../../../server/request-origin";

const headers = { "Cache-Control": "no-store, private", "Cross-Origin-Resource-Policy": "same-origin" };
function publicView(view: ServiceView, request: Request) {
  if (view.bridgeUrl) {
    const address = new URL(view.bridgeUrl);
    // The daemon connects over loopback; a LAN listener reaches that same host through the web origin.
    if (["127.0.0.1", "localhost", "[::1]"].includes(address.hostname)) address.hostname = requestOrigin(request)!.hostname;
    view = { ...view, bridgeUrl: address.href };
  }
  return view;
}

async function daemon(path: string, body?: ServiceControl) {
  const configPath = process.env.SIGSENSE_RECEIVER_TOKEN_FILE ?? process.env.SIGSENSE_CONTROL_CONFIG ?? resolve(process.cwd(), "../../.runtime/receiver-control.json");
  const config = JSON.parse(await readFile(/* turbopackIgnore: true */ configPath, "utf8"));
  const token = process.env.SIGSENSE_RECEIVER_TOKEN ?? config.token;
  if (typeof token !== "string" || !token) throw new Error("Receiver control is unavailable");
  return fetch(`http://127.0.0.1:${Number(process.env.SIGSENSE_AUTOFINDER_PORT ?? 8790)}${path}`, {
    method: body ? "POST" : "GET", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}), cache: "no-store", signal: AbortSignal.timeout(body ? 40_000 : 2_000),
  });
}

export async function GET(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Same-origin receiver access required" }, { status: 403, headers });
  try {
    const response = await daemon("/status");
    if (response.ok) return Response.json(publicView(await response.json(), request), { headers });
  } catch { /* Saved findings remain available while the service is offline. */ }
  try {
    const view = serviceView(await readSnapshot());
    return Response.json({ ...view, online: false }, { headers });
  } catch {
    return Response.json({ error: "Saved finder results could not be read" }, { status: 503, headers });
  }
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) return Response.json({ error: "Same-origin receiver access required" }, { status: 403, headers });
  if (request.headers.get("content-type")?.split(";")[0] !== "application/json") return Response.json({ error: "Expected JSON" }, { status: 415, headers });
  let body: unknown;
  try {
    const reader = request.body?.getReader();
    if (!reader) throw new Error("Empty request");
    let data = "", size = 0;
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 1024) { await reader.cancel(); return Response.json({ error: "Request too large" }, { status: 413, headers }); }
      data += decoder.decode(value, { stream: true });
    }
    data += decoder.decode();
    body = JSON.parse(data);
  } catch { return Response.json({ error: "Invalid JSON" }, { status: 400, headers }); }
  const command = parseServiceControl(body);
  if (!command) {
    return Response.json({ error: "Invalid finder command. Manual gain must be between 0 and 50 dB; Auto requires autoGain: true." }, { status: 400, headers });
  }
  try {
    const response = await daemon("/control", command);
    const result = await response.json();
    return Response.json(response.ok ? publicView(result, request) : result, { status: response.status, headers });
  } catch {
    return Response.json({ error: "Background finder service is offline. Start the SigSense background finder service to use these controls." }, { status: 503, headers });
  }
}
