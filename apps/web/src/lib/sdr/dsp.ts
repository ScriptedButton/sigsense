/**
 * Lazy access to the browser DSP (see dsp-impl.ts). Also detects up front
 * whether this browser can run it, so the UI can say so instead of failing.
 */
export type Dsp = typeof import("./dsp-impl");

let loading: Promise<Dsp> | null = null;

export function loadDsp(): Promise<Dsp> {
  loading ??= import("./dsp-impl").catch((err) => {
    loading = null; // allow a retry after a transient failure
    throw err;
  });
  return loading;
}

/**
 * The DSP's WASM uses relaxed-SIMD instructions. Probe with a one-function
 * module (`i8x16.relaxed_swizzle`); validate() is false where unsupported
 * (iOS Safari, older browsers) without throwing.
 */
export function relaxedSimdSupported(): boolean {
  if (typeof WebAssembly === "undefined") return false;
  const zeros16 = new Array<number>(16).fill(0);
  const bytes = new Uint8Array([
    0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00, // magic, version
    0x01, 0x05, 0x01, 0x60, 0x00, 0x01, 0x7b, // type: () -> v128
    0x03, 0x02, 0x01, 0x00, // one function
    0x0a, 0x2b, 0x01, 0x29, 0x00, // code section, body size 41, no locals
    0xfd, 0x0c, ...zeros16, // v128.const
    0xfd, 0x0c, ...zeros16, // v128.const
    0xfd, 0x80, 0x02, // i8x16.relaxed_swizzle
    0x0b, // end
  ]);
  try {
    return WebAssembly.validate(bytes);
  } catch {
    return false;
  }
}

export const DSP_UNSUPPORTED_MESSAGE =
  "This browser can't run the built-in radio DSP (it needs WebAssembly relaxed SIMD, which iOS Safari lacks). Use HackRF here, or open the app in desktop Chrome.";
