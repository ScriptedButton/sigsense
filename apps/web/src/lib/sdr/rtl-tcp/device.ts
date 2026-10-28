import {
  DirectSampling,
  type RtlDevice,
  type RtlDeviceProvider,
  type SampleBlock,
} from "@jtarrio/webrtlsdr/rtlsdr.js";

import {
  encodeRtlTcpCommand,
  RTL_TCP_HEADER_BYTES,
  RTL_TCP_MAGIC,
  RTL_TCP_SET_BIAS_TEE,
  RTL_TCP_SET_DIRECT_SAMPLING,
  RTL_TCP_SET_FREQ,
  RTL_TCP_SET_FREQ_CORRECTION,
  RTL_TCP_SET_GAIN,
  RTL_TCP_SET_GAIN_MODE,
  RTL_TCP_SET_SAMPLE_RATE,
} from "./protocol";

type PendingRead = {
  bytesNeeded: number;
  resolve: (data: ArrayBuffer) => void;
  reject: (err: Error) => void;
};

/**
 * RtlDevice backed by the SDRSense WebSocket ↔ rtl_tcp bridge.
 * IQ format matches USB: interleaved unsigned 8-bit I/Q pairs.
 */
export class RtlTcpDevice implements RtlDevice {
  private ws: WebSocket | null = null;
  private closed = false;
  private headerDone = false;
  private headerError: Error | null = null;
  private headerBuf: Uint8Array = new Uint8Array(0);
  private chunks: Uint8Array[] = [];
  private bufferedBytes = 0;
  private pending: PendingRead | null = null;

  private sampleRate = 1_024_000;
  private frequency = 88_500_000;
  private gain: number | null = null;
  private ppm = 0;
  private directSamplingMethod = DirectSampling.Off;
  private biasTee = false;

  constructor(private readonly wsUrl: string) {}

  async open(): Promise<void> {
    if (this.ws) return;

    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(this.wsUrl);
      ws.binaryType = "arraybuffer";
      let settled = false;

      const fail = (err: Error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        try {
          ws.close();
        } catch {
          /* ignore */
        }
        reject(err);
      };

      const ok = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        resolve();
      };

      const timer = window.setTimeout(() => {
        fail(
          new Error(
            "Timed out connecting to the RTL server. Is the bridge and rtl_tcp running?",
          ),
        );
      }, 10_000);

      ws.onopen = () => {
        this.ws = ws;
      };

      ws.onerror = () => {
        fail(
          new Error(
            "Could not connect to the RTL server. Check the address and that pnpm rtl-server is running.",
          ),
        );
      };

      ws.onclose = (event) => {
        if (!settled) {
          fail(
            new Error(
              event.reason
                ? `RTL server closed: ${event.reason}`
                : "RTL server closed before rtl_tcp finished connecting.",
            ),
          );
          return;
        }
        this.onSocketClosed(event.reason || "connection closed");
      };

      ws.onmessage = (event) => {
        if (typeof event.data === "string") return;
        this.onBinary(new Uint8Array(event.data as ArrayBuffer));
        if (this.headerError) {
          fail(this.headerError);
          return;
        }
        // Resolve only after the RTL0 dongle header — proves rtl_tcp is up.
        if (!settled && this.headerDone) ok();
      };
    });
  }

  private onSocketClosed(reason: string): void {
    this.ws = null;
    const err = new Error(`RTL server disconnected: ${reason}`);
    if (this.pending) {
      this.pending.reject(err);
      this.pending = null;
    }
    if (!this.closed) {
      this.closed = true;
    }
  }

  private onBinary(chunk: Uint8Array): void {
    if (this.closed) return;

    if (!this.headerDone) {
      this.headerBuf = concatBytes(this.headerBuf, chunk);
      if (this.headerBuf.length < RTL_TCP_HEADER_BYTES) return;

      const magic = String.fromCharCode(
        this.headerBuf[0]!,
        this.headerBuf[1]!,
        this.headerBuf[2]!,
        this.headerBuf[3]!,
      );
      if (magic !== RTL_TCP_MAGIC) {
        const err = new Error(
          `Unexpected rtl_tcp header magic "${magic}" (want RTL0).`,
        );
        this.headerError = err;
        void this.close();
        if (this.pending) {
          this.pending.reject(err);
          this.pending = null;
        }
        return;
      }

      this.headerDone = true;
      const rest = this.headerBuf.subarray(RTL_TCP_HEADER_BYTES);
      this.headerBuf = new Uint8Array(0);
      if (rest.length > 0) this.pushIq(rest);
      return;
    }

    this.pushIq(chunk);
  }

  private pushIq(chunk: Uint8Array): void {
    if (chunk.length === 0) return;
    this.chunks.push(chunk);
    this.bufferedBytes += chunk.length;
    this.fulfillPending();
  }

  private fulfillPending(): void {
    const pending = this.pending;
    if (!pending) return;
    if (this.bufferedBytes < pending.bytesNeeded) return;

    this.pending = null;
    pending.resolve(this.takeBytes(pending.bytesNeeded));
  }

  private takeBytes(n: number): ArrayBuffer {
    const out = new Uint8Array(n);
    let offset = 0;
    while (offset < n && this.chunks.length > 0) {
      const head = this.chunks[0]!;
      const need = n - offset;
      if (head.length <= need) {
        out.set(head, offset);
        offset += head.length;
        this.chunks.shift();
      } else {
        out.set(head.subarray(0, need), offset);
        this.chunks[0] = head.subarray(need);
        offset += need;
      }
    }
    this.bufferedBytes -= n;
    return out.buffer;
  }

  private sendCommand(cmd: number, param: number): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("RTL server is not connected.");
    }
    this.ws.send(encodeRtlTcpCommand(cmd, param));
  }

  async setSampleRate(rate: number): Promise<number> {
    const next = Math.floor(rate);
    this.sendCommand(RTL_TCP_SET_SAMPLE_RATE, next);
    this.sampleRate = next;
    return next;
  }

  async setFrequencyCorrection(ppm: number): Promise<void> {
    const next = Math.floor(ppm);
    this.sendCommand(RTL_TCP_SET_FREQ_CORRECTION, next);
    this.ppm = next;
  }

  getFrequencyCorrection(): number {
    return this.ppm;
  }

  async setGain(gain: number | null): Promise<void> {
    if (gain == null) {
      this.sendCommand(RTL_TCP_SET_GAIN_MODE, 0);
      this.gain = null;
      return;
    }
    this.sendCommand(RTL_TCP_SET_GAIN_MODE, 1);
    // rtl_tcp expects tenths of a dB.
    this.sendCommand(RTL_TCP_SET_GAIN, Math.round(gain * 10));
    this.gain = gain;
  }

  getGain(): number | null {
    return this.gain;
  }

  async setCenterFrequency(freq: number): Promise<number> {
    const next = Math.floor(freq);
    this.sendCommand(RTL_TCP_SET_FREQ, next);
    this.frequency = next;
    return next;
  }

  async setDirectSamplingMethod(method: DirectSampling): Promise<void> {
    this.sendCommand(RTL_TCP_SET_DIRECT_SAMPLING, method);
    this.directSamplingMethod = method;
  }

  getDirectSamplingMethod(): DirectSampling {
    return this.directSamplingMethod;
  }

  async enableBiasTee(enable: boolean): Promise<void> {
    this.sendCommand(RTL_TCP_SET_BIAS_TEE, enable ? 1 : 0);
    this.biasTee = enable;
  }

  isBiasTeeEnabled(): boolean {
    return this.biasTee;
  }

  async resetBuffer(): Promise<void> {
    // rtl_tcp has no reset opcode; drop locally buffered IQ so the next read is fresh.
    this.chunks = [];
    this.bufferedBytes = 0;
  }

  async readSamples(length: number): Promise<SampleBlock> {
    if (this.closed) {
      throw new Error("RTL server is closed.");
    }
    const bytesNeeded = length * 2;
    const data = await new Promise<ArrayBuffer>((resolve, reject) => {
      if (this.pending) {
        reject(new Error("Overlapping readSamples calls."));
        return;
      }
      this.pending = { bytesNeeded, resolve, reject };
      this.fulfillPending();
    });

    return {
      frequency: this.frequency,
      data,
      directSampling: this.directSamplingMethod !== DirectSampling.Off,
    };
  }

  async close(): Promise<void> {
    this.closed = true;
    if (this.pending) {
      this.pending.reject(new Error("RTL server closed."));
      this.pending = null;
    }
    this.chunks = [];
    this.bufferedBytes = 0;
    const ws = this.ws;
    this.ws = null;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.close();
    }
  }
}

export class RtlTcpDeviceProvider implements RtlDeviceProvider {
  constructor(private readonly wsUrl: string) {}

  async get(): Promise<RtlDevice> {
    const device = new RtlTcpDevice(this.wsUrl);
    await device.open();
    return device;
  }
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  if (a.length === 0) return new Uint8Array(b);
  if (b.length === 0) return new Uint8Array(a);
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}
