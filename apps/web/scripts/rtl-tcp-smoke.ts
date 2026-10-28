import { encodeRtlTcpCommand, RTL_TCP_SET_FREQ } from "../src/lib/sdr/rtl-tcp/protocol";
import { rtlServerWebSocketUrl } from "../src/lib/sdr/rtl-tcp/url";

const cases: Array<[string, string]> = [
  ["localhost:8081", "ws://localhost:8081/"],
  ["http://192.168.1.20:8081", "ws://192.168.1.20:8081/"],
  ["https://sdr.example/bridge", "wss://sdr.example/bridge"],
  ["ws://pi.local:8081/", "ws://pi.local:8081/"],
];

for (const [input, expected] of cases) {
  const got = rtlServerWebSocketUrl(input);
  if (got !== expected) {
    throw new Error(`rtlServerWebSocketUrl(${input}) = ${got}, want ${expected}`);
  }
}

const cmd = encodeRtlTcpCommand(RTL_TCP_SET_FREQ, 146_520_000);
if (cmd.length !== 5 || cmd[0] !== RTL_TCP_SET_FREQ) {
  throw new Error("encodeRtlTcpCommand failed");
}
const view = new DataView(cmd.buffer);
if (view.getUint32(1, false) !== 146_520_000) {
  throw new Error("encodeRtlTcpCommand param endianness wrong");
}

console.log("rtl-tcp smoke ok");
