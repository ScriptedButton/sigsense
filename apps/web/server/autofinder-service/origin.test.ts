import assert from "node:assert/strict";
import test from "node:test";
import { requestOrigin, sameOrigin } from "../request-origin";

const request = (origin: string, host = "192.168.1.131:3000", site = "same-origin", extra = {}) =>
  new Request("http://localhost:3000/api/autofinder-service", { headers: { host, origin, "sec-fetch-site": site, ...extra } });

test("LAN origins match the incoming Host, not Next's normalized localhost URL", () => {
  assert.equal(sameOrigin(request("http://192.168.1.131:3000")), true);
  assert.equal(requestOrigin(request("http://192.168.1.131:3000"))?.hostname, "192.168.1.131");
  assert.equal(sameOrigin(request("http://127.0.0.1:3000", "127.0.0.1:3000")), true);
  assert.equal(sameOrigin(request("http://[::1]:3000", "[::1]:3000")), true);
  assert.equal(sameOrigin(request("https://radio.example", "radio.example", "same-origin", { "x-forwarded-proto": "https" })), true);
});

test("cross-origin, same-site, null and spoofed forwarded hosts cannot authorize controls", () => {
  for (const origin of ["http://attacker.test", "http://192.168.1.131:3001", "https://192.168.1.131:3000", "null"])
    assert.equal(sameOrigin(request(origin)), false);
  for (const site of ["cross-site", "same-site"]) assert.equal(sameOrigin(request("http://192.168.1.131:3000", undefined, site)), false);
  assert.equal(sameOrigin(request("http://attacker.test", undefined, "same-origin", { "x-forwarded-host": "attacker.test" })), false);
  assert.equal(sameOrigin(request("http://localhost:3000", "bad/host")), false);
});
