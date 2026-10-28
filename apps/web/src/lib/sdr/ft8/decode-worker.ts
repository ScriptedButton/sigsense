/// <reference lib="webworker" />

import { decodeFT8, HashCallBook, type DecodedMessage } from "@e04/ft8ts";

import type { Ft8Depth } from "./types";

export type Ft8WorkerRequest = {
  type: "decode";
  id: number;
  samples: Float32Array;
  sampleRate: number;
  depth: Ft8Depth;
  freqLow: number;
  freqHigh: number;
  periodAt: number;
};

export type Ft8WorkerResponse =
  | {
      type: "decoded";
      id: number;
      periodAt: number;
      durationMs: number;
      messages: DecodedMessage[];
    }
  | {
      type: "error";
      id: number;
      error: string;
    };

const hashCallBook = new HashCallBook();

self.onmessage = (event: MessageEvent<Ft8WorkerRequest>) => {
  const data = event.data;
  if (!data || data.type !== "decode") return;

  try {
    const t0 = performance.now();
    const messages = decodeFT8(data.samples, {
      sampleRate: data.sampleRate,
      depth: data.depth,
      freqLow: data.freqLow,
      freqHigh: data.freqHigh,
      hashCallBook,
    });
    const response: Ft8WorkerResponse = {
      type: "decoded",
      id: data.id,
      periodAt: data.periodAt,
      durationMs: Math.round(performance.now() - t0),
      messages,
    };
    self.postMessage(response);
  } catch (err) {
    const response: Ft8WorkerResponse = {
      type: "error",
      id: data.id,
      error: err instanceof Error ? err.message : String(err),
    };
    self.postMessage(response);
  }
};
