import { type NextRequest } from "next/server";

import { normalizeGmrsCallsign } from "@/lib/sdr/callsign/normalize";
import type { SkywaveGmrsResponse } from "@/lib/sdr/callsign/types";

const SKYWAVE = "https://gmrslookup.skywave.org";

/**
 * Proxy GMRS callsign lookups to Skywave (no browser CORS).
 * Credit: Skywave Communications Inc. — https://gmrslookup.skywave.org
 */
export async function GET(request: NextRequest) {
  const raw = request.nextUrl.searchParams.get("call") ?? "";
  const call = normalizeGmrsCallsign(raw);
  if (!call) {
    return Response.json(
      { message: "Invalid GMRS callsign." },
      { status: 400 },
    );
  }

  try {
    const upstream = await fetch(`${SKYWAVE}/${encodeURIComponent(call)}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    const text = await upstream.text();
    let data: SkywaveGmrsResponse;
    try {
      data = JSON.parse(text) as SkywaveGmrsResponse;
    } catch {
      return Response.json(
        { message: "Upstream returned non-JSON." },
        { status: 502 },
      );
    }

    if (!upstream.ok && upstream.status !== 404) {
      return Response.json(data, { status: upstream.status });
    }

    // Skywave uses 404 + JSON for unknown calls; normalize to 200 for the client.
    return Response.json(data);
  } catch {
    return Response.json(
      { message: "GMRS lookup unavailable." },
      { status: 502 },
    );
  }
}
