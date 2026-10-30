/** Next may normalize request.url to localhost even when the browser uses a LAN IP. */
export function requestOrigin(request: Request): URL | null {
  const internal = new URL(request.url);
  const host = request.headers.get("host") ?? internal.host;
  const scheme = request.headers.get("x-forwarded-proto") ?? internal.protocol.slice(0, -1);
  if (!["http", "https"].includes(scheme)) return null;
  try {
    const origin = new URL(`${scheme}://${host}`);
    if (origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) return null;
    return origin;
  } catch { return null; }
}

export function sameOrigin(request: Request): boolean {
  const site = request.headers.get("sec-fetch-site");
  if (site && !["same-origin", "none"].includes(site)) return false;
  const target = requestOrigin(request);
  const origin = request.headers.get("origin");
  return target !== null && (!origin || origin === target.origin);
}
