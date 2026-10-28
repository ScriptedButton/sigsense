/**
 * Turn a user-entered OpenWebRX+ address into a receiver websocket URL.
 * Accepts `localhost:8073`, `http://…`, or an existing `ws://…/ws/` URL.
 */
export function openWebRxWebSocketUrl(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    throw new Error("Enter the OpenWebRX+ address.");
  }

  let url: URL;
  if (/^wss?:\/\//i.test(trimmed)) {
    url = new URL(trimmed);
  } else if (/^https?:\/\//i.test(trimmed)) {
    url = new URL(trimmed);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  } else {
    url = new URL(`http://${trimmed}`);
    url.protocol = "ws:";
  }

  if (!url.hostname) {
    throw new Error("Enter a host, for example localhost:8073.");
  }

  const path = url.pathname.replace(/\/+$/, "");
  if (path === "" || path === "/") {
    url.pathname = "/ws/";
  } else if (path.endsWith("/ws")) {
    url.pathname = `${path}/`;
  } else {
    url.pathname = `${path}/ws/`;
  }

  url.hash = "";
  return url.toString();
}

/** Browsers block ws:// from an https page. */
export function openWebRxMixedContentMessage(wsUrl: string): string | null {
  if (typeof window === "undefined") return null;
  if (window.location.protocol !== "https:") return null;
  if (!wsUrl.startsWith("ws:")) return null;
  return "This page is HTTPS, so the browser blocks ws://. Use a wss:// address, or open SDRSense on http://localhost.";
}
