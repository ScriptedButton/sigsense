import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync, chmodSync } from "node:fs";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { WebSocket, WebSocketServer } from "ws";

import {
  RECEIVER_ACTIONS,
  RECEIVER_CONTROL_PORT,
  RECEIVER_CONTROL_VERSION,
  type ReceiverAction,
  type ReceiverCommand,
  type ReceiverResponse,
  type ReceiverSpectrum,
} from "../../src/lib/sdr/receiver-control/protocol";

type JsonObject = Record<string, unknown>;

type Session = {
  id: string;
  socket: WebSocket;
  state: unknown;
  spectrum?: ReceiverSpectrum;
  diagnostics?: unknown;
  url: string;
  title: string;
  lastSeen: string;
};

type PendingCommand = {
  sessionId: string;
  socket: WebSocket;
  timer: NodeJS.Timeout;
  resolve: (value: unknown) => void;
  reject: (error: BrokerError) => void;
};

export type ReceiverBrokerOptions = {
  host?: string;
  port?: number;
  token?: string;
  configPath?: string;
  allowedOriginPorts?: number[];
  defaultTimeoutMs?: number;
};

export class BrokerError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const DEFAULT_CONFIG = resolve(ROOT, ".runtime/receiver-control.json");
const MAX_HTTP_BODY = 1 << 20;
const MAX_TIMEOUT_MS = 120_000;

function persistBootstrap(path: string, token: string, port: number): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  writeFileSync(path, `${JSON.stringify({ token, port, version: RECEIVER_CONTROL_VERSION }, null, 2)}\n`, { mode: 0o600 });
  chmodSync(path, 0o600);
}

function loadToken(path: string, requested: string | undefined, port: number): string {
  let saved: { token?: unknown } = {};
  try {
    saved = JSON.parse(readFileSync(path, "utf8")) as { token?: unknown };
  } catch {
    // First run or invalid old file: replace it atomically enough for local use.
  }
  const token = requested ?? (typeof saved.token === "string" ? saved.token : randomBytes(32).toString("base64url"));
  persistBootstrap(path, token, port);
  return token;
}

function safeTokenEqual(actual: string, expected: string): boolean {
  const a = Uint8Array.from(Buffer.from(actual));
  const b = Uint8Array.from(Buffer.from(expected));
  return a.length === b.length && timingSafeEqual(a, b);
}

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  response.end(JSON.stringify(body));
}

function errorJson(response: ServerResponse, error: BrokerError): void {
  sendJson(response, error.status, { error: { code: error.code, message: error.message } });
}

async function readJson(request: IncomingMessage): Promise<JsonObject> {
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Uint8Array.from(Buffer.from(chunk));
    size += buffer.length;
    if (size > MAX_HTTP_BODY) throw new BrokerError("body_too_large", "Request body exceeds 1 MiB", 413);
    chunks.push(buffer);
  }
  try {
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    const value = JSON.parse(new TextDecoder().decode(bytes));
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value as JsonObject;
  } catch {
    throw new BrokerError("invalid_json", "Expected a JSON object", 400);
  }
}

function publicSession(session: Session) {
  return {
    id: session.id,
    state: session.state,
    lastSeen: session.lastSeen,
    spectrum: session.spectrum,
    diagnostics: session.diagnostics,
    url: session.url,
    title: session.title,
  };
}

export function createReceiverBroker(options: ReceiverBrokerOptions = {}) {
  const host = options.host ?? "0.0.0.0";
  const requestedPort = options.port ?? Number(process.env.SIGSENSE_CONTROL_PORT || RECEIVER_CONTROL_PORT);
  const configPath = options.configPath ?? DEFAULT_CONFIG;
  const token = loadToken(configPath, options.token, requestedPort);
  const allowedPorts = new Set(options.allowedOriginPorts ?? [3000, 3001]);
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 15_000;
  const sessions = new Map<string, Session>();
  const pending = new Map<string, PendingCommand>();
  const wss = new WebSocketServer({ noServer: true, maxPayload: 8 * 1024 * 1024 });

  const rejectForSocket = (socket: WebSocket, code: string, message: string) => {
    for (const [id, item] of pending) {
      if (item.socket !== socket) continue;
      clearTimeout(item.timer);
      pending.delete(id);
      item.reject(new BrokerError(code, message, 502));
    }
  };

  const command = (session: Session, action: ReceiverAction, args: JsonObject, timeoutMs: number) =>
    new Promise<unknown>((resolvePromise, rejectPromise) => {
      if (session.socket.readyState !== WebSocket.OPEN) {
        rejectPromise(new BrokerError("session_closed", "Receiver session is closed", 409));
        return;
      }
      const id = randomUUID();
      const timer = setTimeout(() => {
        pending.delete(id);
        rejectPromise(new BrokerError("command_timeout", `Receiver command timed out after ${timeoutMs} ms`, 504));
      }, timeoutMs);
      pending.set(id, { sessionId: session.id, socket: session.socket, timer, resolve: resolvePromise, reject: rejectPromise });
      const message: ReceiverCommand = { type: "command", id, action, args };
      session.socket.send(JSON.stringify(message), (error) => {
        if (!error) return;
        const item = pending.get(id);
        if (!item) return;
        clearTimeout(item.timer);
        pending.delete(id);
        rejectPromise(new BrokerError("send_failed", error.message, 502));
      });
    });

  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://localhost");
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        sendJson(response, 200, { ok: true, version: RECEIVER_CONTROL_VERSION });
        return;
      }
      const authorization = request.headers.authorization ?? "";
      const supplied = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
      if (!safeTokenEqual(supplied, token)) throw new BrokerError("unauthorized", "Valid Bearer token required", 401);

      if (request.method === "GET" && url.pathname === "/sessions") {
        sendJson(response, 200, { sessions: [...sessions.values()].map(publicSession) });
        return;
      }
      if (request.method === "POST" && url.pathname === "/command") {
        const body = await readJson(request);
        const action = body.action;
        if (typeof action !== "string" || !(RECEIVER_ACTIONS as readonly string[]).includes(action)) {
          throw new BrokerError("invalid_action", "Unknown or missing receiver action", 400);
        }
        const args = body.args === undefined ? {} : body.args;
        if (!args || typeof args !== "object" || Array.isArray(args)) {
          throw new BrokerError("invalid_args", "args must be an object", 400);
        }
        let session: Session | undefined;
        if (body.sessionId !== undefined) {
          if (typeof body.sessionId !== "string") throw new BrokerError("invalid_session", "sessionId must be a string", 400);
          session = sessions.get(body.sessionId);
          if (!session) throw new BrokerError("session_not_found", "Receiver session not found", 404);
        } else if (sessions.size === 1) {
          session = sessions.values().next().value as Session;
        } else if (sessions.size === 0) {
          throw new BrokerError("no_sessions", "No live receiver session", 409);
        } else {
          throw new BrokerError("session_required", "Multiple receivers are live; specify sessionId", 409);
        }
        const rawTimeout = body.timeoutMs ?? defaultTimeoutMs;
        if (typeof rawTimeout !== "number" || !Number.isFinite(rawTimeout) || rawTimeout <= 0) {
          throw new BrokerError("invalid_timeout", "timeoutMs must be a positive number", 400);
        }
        const result = await command(session, action as ReceiverAction, args as JsonObject, Math.min(rawTimeout, MAX_TIMEOUT_MS));
        sendJson(response, 200, { result });
        return;
      }
      throw new BrokerError("not_found", "Endpoint not found", 404);
    } catch (error) {
      errorJson(response, error instanceof BrokerError ? error : new BrokerError("internal_error", "Internal broker error", 500));
    }
  });

  server.on("upgrade", (request, socket, head) => {
    try {
      const hostHeader = request.headers.host;
      const originHeader = request.headers.origin;
      if (!hostHeader || !originHeader) throw new BrokerError("invalid_origin", "Browser Origin and Host are required", 403);
      const requestUrl = new URL(request.url ?? "/", `http://${hostHeader}`);
      if (requestUrl.pathname !== "/receiver" || !safeTokenEqual(requestUrl.searchParams.get("token") ?? "", token)) {
        throw new BrokerError("unauthorized", "Invalid receiver token", 401);
      }
      const hostUrl = new URL(`http://${hostHeader}`);
      const origin = new URL(originHeader);
      const originPort = Number(origin.port || (origin.protocol === "https:" ? 443 : 80));
      if (!["http:", "https:"].includes(origin.protocol) || origin.hostname !== hostUrl.hostname || !allowedPorts.has(originPort)) {
        throw new BrokerError("invalid_origin", "Receiver origin is not allowed", 403);
      }
      wss.handleUpgrade(request, socket, head, (websocket) => wss.emit("connection", websocket, request));
    } catch (error) {
      const status = error instanceof BrokerError ? error.status : 400;
      socket.write(`HTTP/1.1 ${status} Rejected\r\nConnection: close\r\n\r\n`);
      socket.destroy();
    }
  });

  wss.on("connection", (socket) => {
    let sessionId: string | null = null;
    socket.on("message", (raw, isBinary) => {
      if (isBinary) return socket.close(1003, "JSON messages required");
      let message: JsonObject;
      try {
        message = JSON.parse(raw.toString()) as JsonObject;
        if (!message || typeof message !== "object" || Array.isArray(message)) throw new Error("Expected object");
      } catch {
        socket.close(1007, "Invalid JSON");
        return;
      }
      if (message.type === "hello") {
        if (sessionId || typeof message.sessionId !== "string" || !message.sessionId || typeof message.url !== "string" || typeof message.title !== "string") {
          socket.close(1008, "Invalid hello");
          return;
        }
        sessionId = message.sessionId;
        const previous = sessions.get(sessionId);
        if (previous && previous.socket !== socket) {
          rejectForSocket(previous.socket, "session_replaced", "Receiver session was replaced");
          previous.socket.close(1008, "Session replaced");
        }
        sessions.set(sessionId, {
          id: sessionId,
          socket,
          state: message.state,
          url: message.url,
          title: message.title,
          lastSeen: new Date().toISOString(),
        });
        socket.send(JSON.stringify({ type: "welcome", sessionId }));
        return;
      }
      if (!sessionId) {
        socket.close(1008, "hello required");
        return;
      }
      const session = sessions.get(sessionId);
      if (!session || session.socket !== socket) return;
      session.lastSeen = new Date().toISOString();
      if (message.type === "telemetry") {
        session.state = message.state;
        if (message.spectrum !== undefined) session.spectrum = message.spectrum as ReceiverSpectrum;
        if (message.diagnostics !== undefined) session.diagnostics = message.diagnostics;
      } else if (message.type === "result" && typeof message.id === "string") {
        const item = pending.get(message.id);
        if (!item || item.socket !== socket || item.sessionId !== sessionId) return;
        clearTimeout(item.timer);
        pending.delete(message.id);
        const result = message as ReceiverResponse;
        if (typeof result.error === "string") item.reject(new BrokerError("receiver_error", result.error, 422));
        else item.resolve(result.result);
      }
    });
    socket.on("close", () => {
      if (sessionId && sessions.get(sessionId)?.socket === socket) sessions.delete(sessionId);
      rejectForSocket(socket, "session_disconnected", "Receiver disconnected while command was pending");
    });
  });

  return {
    token,
    sessions,
    async listen(): Promise<{ host: string; port: number }> {
      await new Promise<void>((resolvePromise, rejectPromise) => {
        server.once("error", rejectPromise);
        server.listen(requestedPort, host, () => {
          server.off("error", rejectPromise);
          resolvePromise();
        });
      });
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : requestedPort;
      persistBootstrap(configPath, token, port);
      return { host, port };
    },
    async close(): Promise<void> {
      for (const socket of wss.clients) socket.close(1001, "Broker shutting down");
      for (const item of pending.values()) {
        clearTimeout(item.timer);
        item.reject(new BrokerError("broker_closed", "Broker shut down", 503));
      }
      pending.clear();
      await new Promise<void>((resolvePromise) => server.close(() => resolvePromise()));
      wss.close();
    },
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const broker = createReceiverBroker();
  broker.listen().then(({ host, port }) => {
    console.log(`Receiver control broker listening on http://${host}:${port}`);
  });
}
