/** rtl_tcp command opcodes (osmocom librtlsdr). */

export const RTL_TCP_SET_FREQ = 0x01;
export const RTL_TCP_SET_SAMPLE_RATE = 0x02;
export const RTL_TCP_SET_GAIN_MODE = 0x03;
export const RTL_TCP_SET_GAIN = 0x04;
export const RTL_TCP_SET_FREQ_CORRECTION = 0x05;
export const RTL_TCP_SET_IF_GAIN = 0x06;
export const RTL_TCP_SET_TEST_MODE = 0x07;
export const RTL_TCP_SET_AGC_MODE = 0x08;
export const RTL_TCP_SET_DIRECT_SAMPLING = 0x09;
export const RTL_TCP_SET_OFFSET_TUNING = 0x0a;
export const RTL_TCP_SET_RTL_XTAL = 0x0b;
export const RTL_TCP_SET_TUNER_XTAL = 0x0c;
export const RTL_TCP_SET_TUNER_GAIN_BY_INDEX = 0x0d;
export const RTL_TCP_SET_BIAS_TEE = 0x0e;

/** Dongle info magic: "RTL0" */
export const RTL_TCP_MAGIC = "RTL0";
export const RTL_TCP_HEADER_BYTES = 12;

export const DEFAULT_RTL_SERVER_URL = "http://localhost:8081";
export const RTL_SERVER_URL_STORAGE_KEY = "sdrsense.rtl-server.url";

export function encodeRtlTcpCommand(cmd: number, param: number): Uint8Array {
  const out = new Uint8Array(5);
  out[0] = cmd & 0xff;
  const view = new DataView(out.buffer);
  view.setUint32(1, param >>> 0, false);
  return out;
}
