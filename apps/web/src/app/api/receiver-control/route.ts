import { readFile } from "node:fs/promises";
import path from "node:path";
import { sameOrigin } from "../../../../server/request-origin";

/** Same-origin pairing bootstrap; the broker token is never bundled in client JavaScript. */
export async function GET(request: Request) {
  if (!sameOrigin(request)) {
    return Response.json({ error: "Same-origin receiver access required" }, { status: 403 });
  }
  try {
    const configPath = process.env.SIGSENSE_CONTROL_CONFIG ?? path.resolve(process.cwd(), "../../.runtime/receiver-control.json");
    const config = JSON.parse(await readFile(/* turbopackIgnore: true */ configPath, "utf8"));
    if (typeof config.token !== "string" || !Number.isInteger(config.port)) throw new Error("Invalid broker configuration");
    return Response.json({ token: config.token, port: config.port, version: 1 }, {
      headers: { "Cache-Control": "no-store, private", "Cross-Origin-Resource-Policy": "same-origin" },
    });
  } catch {
    return Response.json({ error: "Receiver control broker is not running" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
