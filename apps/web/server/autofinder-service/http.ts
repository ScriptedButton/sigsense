import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import { SERVICE_ACTIONS, type ServiceAction, type ServiceView } from "../../src/lib/sdr/autofinder/service-types";

export function createServiceServer(token: () => Promise<string>, view: () => ServiceView, control: (action: ServiceAction) => Promise<void>) {
  return createServer(async (request, response) => {
    const send = (code: number, body: unknown) => {
      response.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(JSON.stringify(body));
    };
    try {
      const actual = Uint8Array.from(Buffer.from(request.headers.authorization ?? ""));
      const expected = Uint8Array.from(Buffer.from(`Bearer ${await token()}`));
      if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) return send(401, { error: "Valid receiver token required" });
      if (request.method === "GET" && request.url === "/status") return send(200, view());
      if (request.method !== "POST" || request.url !== "/control") return send(404, { error: "Unknown endpoint" });
      let size = 0;
      const chunks: Uint8Array<ArrayBuffer>[] = [];
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 1024) return send(413, { error: "Request too large" });
        chunks.push(Uint8Array.from(Buffer.from(chunk)));
      }
      let body: { action?: unknown };
      try { body = JSON.parse(Buffer.concat(chunks).toString()); } catch { return send(400, { error: "Expected JSON" }); }
      if (!body || typeof body.action !== "string" || !(SERVICE_ACTIONS as readonly string[]).includes(body.action)) return send(400, { error: "Unknown finder action" });
      await control(body.action as ServiceAction);
      send(200, view());
    } catch (error) {
      send(502, { error: error instanceof Error ? error.message : "Background finder failed" });
    }
  });
}
