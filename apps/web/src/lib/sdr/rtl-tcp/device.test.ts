import assert from "node:assert/strict";
import test from "node:test";
import { RtlTcpDevice } from "./device";

function receiver() {
  const device = new RtlTcpDevice("ws://localhost:8081");
  const input = device as unknown as {
    pushIq(chunk: Uint8Array): void;
    onSocketClosed(reason: string): void;
  };
  return { device, input };
}

test("parallel sample reads receive fragmented IQ in FIFO order", async () => {
  const { device, input } = receiver();
  const first = device.readSamples(2);
  const second = device.readSamples(2);
  input.pushIq(Uint8Array.of(1, 2, 3));
  input.pushIq(Uint8Array.of(4, 5, 6, 7, 8));
  assert.deepEqual([...new Uint8Array((await first).data)], [1, 2, 3, 4]);
  assert.deepEqual([...new Uint8Array((await second).data)], [5, 6, 7, 8]);
});

test("closing rejects every pending sample read", async () => {
  const { device } = receiver();
  const first = assert.rejects(device.readSamples(2), /RTL server closed/);
  const second = assert.rejects(device.readSamples(2), /RTL server closed/);
  await device.close();
  await Promise.all([first, second]);
});

test("disconnect rejects every pending sample read", async () => {
  const { device, input } = receiver();
  const first = assert.rejects(device.readSamples(2), /disconnected: test/);
  const second = assert.rejects(device.readSamples(2), /disconnected: test/);
  input.onSocketClosed("test");
  await Promise.all([first, second]);
});

test("automatic HF gain starts at zero rather than enabling the overloaded tuner AGC", async () => {
  const { device } = receiver();
  const commands: [number, number][] = [];
  const internal = device as unknown as { sendCommand(command: number, value: number): void };
  internal.sendCommand = (command, value) => { commands.push([command, value]); };
  await device.setCenterFrequency(4_412_000);
  assert.deepEqual(commands, [[1, 4_412_000], [3, 1], [4, 0]]);
  assert.equal(device.getGain(), 0);
  commands.length = 0;
  await device.setGain(10);
  await device.setCenterFrequency(6_100_000);
  assert.deepEqual(commands, [[3, 1], [4, 100], [1, 6_100_000]]);
  assert.equal(device.getGain(), 10, "explicit manual gain remains under user control");
});
