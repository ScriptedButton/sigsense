/**
 * Turn a user-entered RTL server address into a WebSocket URL.
 * Accepts `localhost:8081`, `http://…`, or an existing `ws://…` URL.
 */
export function rtlServerWebSocketUrl(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) {
    throw new Error("Enter the RTL server address.");
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
    throw new Error("Enter a host, for example localhost:8081.");
  }

  // Bridge serves at the root — strip a trailing slash for a clean WS URL.
  if (url.pathname === "/" || url.pathname === "") {
    url.pathname = "/";
  }

  url.hash = "";
  url.search = "";
  return url.toString();
}

/** Browsers block ws:// from an https page. */
export function rtlServerMixedContentMessage(wsUrl: string): string | null {
  if (typeof window === "undefined") return null;
  if (window.location.protocol !== "https:") return null;
  if (!wsUrl.startsWith("ws:")) return null;
  return "This page is HTTPS, so the browser blocks ws://. Use a wss:// address, or open SDRSense on http://localhost.";
}
