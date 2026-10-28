/**
 * WebSocket ↔ rtl_tcp bridge for the SigSense browser receiver.
 *
 * Usage:
 *   pnpm rtl-server -- --listen 8081 --tcp 127.0.0.1:1234
 *
 * On the Pi (or host with the stick):
 *   rtl_tcp -a 127.0.0.1 -p 1234
 *   pnpm rtl-server
 *
 * Then open Receiver, choose RTL server, and connect to ws://<host>:8081
 */

import { createServer } from "node:http";
import net from "node:net";
import { WebSocketServer, type RawData, type WebSocket } from "ws";

const DEFAULT_LISTEN = 8081;
const DEFAULT_TCP = "127.0.0.1:1234";

type Args = {
  listen: number;
  tcpHost: string;
  tcpPort: number;
};

function rawDataToUint8Array(data: RawData): Uint8Array {
  if (typeof data === "string") {
    return new TextEncoder().encode(data);
  }
  if (data instanceof ArrayBuffer) {
    return new Uint8Array(data);
  }
  if (ArrayBuffer.isView(data)) {
    return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  }
  const parts = data as Array<ArrayBufferView>;
  let total = 0;
  for (const part of parts) total += part.byteLength;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(
      new Uint8Array(part.buffer, part.byteOffset, part.byteLength),
      offset,
    );
    offset += part.byteLength;
  }
  return out;
}

function parseArgs(argv: string[]): Args {
  let listen = DEFAULT_LISTEN;
  let tcp = DEFAULT_TCP;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]!;
    if (arg === "--listen" || arg.startsWith("--listen=")) {
      const value = arg.includes("=") ? arg.slice("--listen=".length) : argv[++i];
      const n = Number(value);
      if (!Number.isFinite(n) || n <= 0) {
        throw new Error(`Invalid --listen value: ${value}`);
      }
      listen = n;
    } else if (arg === "--tcp" || arg.startsWith("--tcp=")) {
      const value = arg.includes("=") ? arg.slice("--tcp=".length) : argv[++i];
      if (!value) throw new Error("Missing --tcp host:port");
      tcp = value;
    } else if (arg === "--help" || arg === "-h") {
      console.log(`Usage: pnpm rtl-server -- [--listen PORT] [--tcp HOST:PORT]

  --listen   WebSocket listen port (default ${DEFAULT_LISTEN})
  --tcp      rtl_tcp address (default ${DEFAULT_TCP})
`);
      process.exit(0);
    }
  }

  const colon = tcp.lastIndexOf(":");
  if (colon <= 0) throw new Error(`Invalid --tcp address: ${tcp}`);
  const tcpHost = tcp.slice(0, colon);
  const tcpPort = Number(tcp.slice(colon + 1));
  if (!tcpHost || !Number.isFinite(tcpPort) || tcpPort <= 0) {
    throw new Error(`Invalid --tcp address: ${tcp}`);
  }

  return { listen, tcpHost, tcpPort };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  let active: WebSocket | null = null;

  const httpServer = createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(
      "SDRSense RTL bridge\nConnect with WebSocket from the SDRSense UI (RTL server).\n",
    );
  });

  const wss = new WebSocketServer({ server: httpServer });

  wss.on("connection", (ws) => {
    if (active && active.readyState === active.OPEN) {
      ws.close(4000, "Bridge already has a client");
      return;
    }

    active = ws;
    let tcp: net.Socket | null = null;
    let closed = false;

    const cleanup = () => {
      if (closed) return;
      closed = true;
      if (active === ws) active = null;
      // Graceful FIN — a TCP RST (socket.destroy) leaves many rtl_tcp builds
      // listening but unable to accept the next client until restarted.
      const sock = tcp;
      tcp = null;
      if (sock && !sock.destroyed) {
        try {
          sock.removeAllListeners("data");
          sock.end();
          const force = setTimeout(() => {
            try {
              sock.destroy();
            } catch {
              /* ignore */
            }
          }, 1_000);
          sock.once("close", () => clearTimeout(force));
        } catch {
          try {
            sock.destroy();
          } catch {
            /* ignore */
          }
        }
      }
      if (ws.readyState === ws.OPEN || ws.readyState === ws.CONNECTING) {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      }
    };

    tcp = net.connect(args.tcpPort, args.tcpHost, () => {
      console.log(
        `Client connected → rtl_tcp ${args.tcpHost}:${args.tcpPort}`,
      );
    });

    tcp.on("data", (chunk) => {
      if (ws.readyState === ws.OPEN) {
        ws.send(chunk, { binary: true });
      }
    });

    tcp.on("error", (err) => {
      console.error(`rtl_tcp error: ${err.message}`);
      if (ws.readyState === ws.OPEN) {
        ws.close(4001, `rtl_tcp: ${err.message}`);
      }
      cleanup();
    });

    tcp.on("close", () => {
      console.log("rtl_tcp closed");
      cleanup();
    });

    ws.on("message", (data) => {
      if (!tcp || tcp.destroyed) return;
      // rtl_tcp only understands 5-byte command packets.
      tcp.write(rawDataToUint8Array(data));
    });

    ws.on("close", () => {
      console.log("WebSocket client disconnected");
      cleanup();
    });

    ws.on("error", (err) => {
      console.error(`WebSocket error: ${err.message}`);
      cleanup();
    });
  });

  httpServer.listen(args.listen, () => {
    console.log(
      `SDRSense RTL bridge listening on ws://0.0.0.0:${args.listen} → ${args.tcpHost}:${args.tcpPort}`,
    );
  });
}

try {
  main();
} catch (err) {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
}
