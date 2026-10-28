/**
 * Turn a user-entered KiwiSDR address into websocket base parts.
 * Accepts `host:8073`, `http://…`, or an existing `ws://…` URL.
 */

export type KiwiWsUrls = {
  /** Origin with ws/wss protocol, no trailing slash. */
  origin: string;
  /** Path prefix before `/<ts>/SND` ("" or "/ws/kiwi"). */
  pathPrefix: string;
};

export function kiwiHttpOrigin(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    throw new Error("Enter the KiwiSDR address.");
  }

  let url: URL;
  if (/^wss?:\/\//i.test(trimmed)) {
    url = new URL(trimmed);
    url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  } else if (/^https?:\/\//i.test(trimmed)) {
    url = new URL(trimmed);
  } else {
    url = new URL(`http://${trimmed}`);
  }

  if (!url.hostname) {
    throw new Error("Enter a host, for example kiwi.example.com:8073.");
  }

  url.pathname = "";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

/**
 * Build SND and W/F websocket URLs for a session timestamp.
 * Modern Kiwis (v1.550+) use `/ws/kiwi/<ts>/…`; older nodes use `/<ts>/…`.
 */
export function kiwiWebSocketUrls(
  input: string,
  timestampMs: number,
  legacy = false,
): { snd: string; wf: string } {
  const http = kiwiHttpOrigin(input);
  const url = new URL(http);
  const wsProtocol = url.protocol === "https:" ? "wss:" : "ws:";
  const origin = `${wsProtocol}//${url.host}`;
  const prefix = legacy ? "" : "/ws/kiwi";
  const ts = Math.round(timestampMs);
  return {
    snd: `${origin}${prefix}/${ts}/SND`,
    wf: `${origin}${prefix}/${ts}/W/F`,
  };
}

/** Browsers block ws:// from an https page. */
export function kiwiMixedContentMessage(wsUrl: string): string | null {
  if (typeof window === "undefined") return null;
  if (window.location.protocol !== "https:") return null;
  if (!wsUrl.startsWith("ws:")) return null;
  return "This page is HTTPS, so the browser blocks ws://. Use a wss:// Kiwi address, or open SigSense on http://localhost.";
}
