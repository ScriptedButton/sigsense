/**
 * IMBE 7200×4400 vocoder wrapper around vendored mbelib WASM.
 *
 * PATENT NOTICE: mbelib is provided for educational purposes. Executable
 * objects compiled from it may be covered by patents — see vendor/mbe/PATENT-NOTICE.md.
 */

type MbeModule = {
  _mbe_alloc_parms: () => number;
  _mbe_free_parms: (p: number) => void;
  _mbe_init_parms_js: (cur: number, prev: number, enh: number) => void;
  _mbe_process_imbe_4400_js: (
    floatBuf: number,
    imbeD: number,
    cur: number,
    prev: number,
    enh: number,
    uvquality: number,
  ) => number;
  _malloc: (n: number) => number;
  _free: (p: number) => void;
  HEAPF32: Float32Array;
  HEAP8: Int8Array;
  HEAPU8: Uint8Array;
};

type CreateMbeModule = (opts?: Record<string, unknown>) => Promise<MbeModule>;

const SAMPLES_PER_FRAME = 160; // 20 ms @ 8 kHz

let loadPromise: Promise<MbeModule | null> | null = null;

async function loadModule(): Promise<MbeModule | null> {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    // Browser-only — Emscripten glue must not load during SSR.
    if (typeof window === "undefined") return null;
    try {
      const mod = (await import("./vendor/mbe/mbe.js")) as unknown as
        | CreateMbeModule
        | { default: CreateMbeModule };
      const factory =
        typeof mod === "function"
          ? mod
          : (mod as { default: CreateMbeModule }).default;
      if (typeof factory !== "function") {
        console.warn("IMBE WASM factory missing");
        return null;
      }
      return await factory();
    } catch (err) {
      console.warn("IMBE WASM load failed:", err);
      return null;
    }
  })();
  return loadPromise;
}

export class ImbeVocoder {
  private mod: MbeModule | null = null;
  private cur = 0;
  private prev = 0;
  private enh = 0;
  private floatPtr = 0;
  private imbePtr = 0;
  private ready = false;

  async init(): Promise<boolean> {
    this.mod = await loadModule();
    if (!this.mod) {
      this.ready = false;
      return false;
    }
    this.cur = this.mod._mbe_alloc_parms();
    this.prev = this.mod._mbe_alloc_parms();
    this.enh = this.mod._mbe_alloc_parms();
    this.mod._mbe_init_parms_js(this.cur, this.prev, this.enh);
    this.floatPtr = this.mod._malloc(SAMPLES_PER_FRAME * 4);
    this.imbePtr = this.mod._malloc(88);
    this.ready = true;
    return true;
  }

  isReady(): boolean {
    return this.ready;
  }

  reset(): void {
    if (!this.mod || !this.ready) return;
    this.mod._mbe_init_parms_js(this.cur, this.prev, this.enh);
  }

  /** Decode one 88-bit IMBE parameter frame → 160 float samples @ 8 kHz. */
  decodeFrame(bits88: ArrayLike<number>): Float32Array {
    const out = new Float32Array(SAMPLES_PER_FRAME);
    if (!this.mod || !this.ready) return out;

    for (let i = 0; i < 88; i++) {
      this.mod.HEAP8[this.imbePtr + i] = bits88[i]! ? 1 : 0;
    }
    this.mod._mbe_process_imbe_4400_js(
      this.floatPtr,
      this.imbePtr,
      this.cur,
      this.prev,
      this.enh,
      1,
    );
    const base = this.floatPtr >> 2;
    for (let i = 0; i < SAMPLES_PER_FRAME; i++) {
      out[i] = this.mod.HEAPF32[base + i] ?? 0;
    }
    return out;
  }

  dispose(): void {
    if (!this.mod) return;
    if (this.floatPtr) this.mod._free(this.floatPtr);
    if (this.imbePtr) this.mod._free(this.imbePtr);
    if (this.cur) this.mod._mbe_free_parms(this.cur);
    if (this.prev) this.mod._mbe_free_parms(this.prev);
    if (this.enh) this.mod._mbe_free_parms(this.enh);
    this.ready = false;
    this.mod = null;
  }
}

export const IMBE_SAMPLES_PER_FRAME = SAMPLES_PER_FRAME;
export const IMBE_SAMPLE_RATE = 8_000;
