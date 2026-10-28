/**
 * Offline smoke: amateur + GMRS normalize/lookup.
 * Run: pnpm exec tsx scripts/callsign-smoke.ts
 */
import { lookupCallsign } from "../src/lib/sdr/callsign/lookup";
import {
  isGmrsCallsign,
  normalizeCallsign,
} from "../src/lib/sdr/callsign/normalize";

async function main() {
  const cases: Array<[string, string]> = [
    ["w1aw", "W1AW"],
    ["W1AW/4", "W1AW"],
    ["W4/G0ABC", "G0ABC"],
    ["  ve3ej ", "VE3EJ"],
    ["wrnt570", "WRNT570"],
    ["WQBW389", "WQBW389"],
  ];

  for (const [raw, want] of cases) {
    const got = normalizeCallsign(raw);
    if (got !== want) {
      console.error(
        `FAIL normalize ${JSON.stringify(raw)} → ${got}, want ${want}`,
      );
      process.exit(1);
    }
  }

  if (!isGmrsCallsign("WRNT570") || isGmrsCallsign("W1AW")) {
    console.error("FAIL: GMRS pattern detection");
    process.exit(1);
  }

  const record = await lookupCallsign("W1AW");
  console.log(
    "W1AW:",
    record.status,
    record.service,
    record.name,
    record.sources.join("+"),
  );

  if (record.status !== "valid" || record.service !== "amateur") {
    console.error("FAIL: W1AW should be valid amateur");
    process.exit(1);
  }
  if (!record.name?.includes("ARRL")) {
    console.error("FAIL: expected ARRL in name", record.name);
    process.exit(1);
  }

  const gmrs = await lookupCallsign("WRNT570", undefined, "gmrs");
  console.log(
    "WRNT570:",
    gmrs.status,
    gmrs.service,
    gmrs.name,
    gmrs.sources.join("+"),
  );
  if (gmrs.status !== "valid" || gmrs.service !== "gmrs") {
    console.error("FAIL: WRNT570 should be valid GMRS", gmrs);
    process.exit(1);
  }
  if (!gmrs.name) {
    console.error("FAIL: expected GMRS name");
    process.exit(1);
  }

  const missing = await lookupCallsign("ZZ0ZZZ");
  if (missing.status !== "not_found" && missing.status !== "invalid") {
    console.error("FAIL: ZZ0ZZZ should not be found", missing.status);
    process.exit(1);
  }

  const missingGmrs = await lookupCallsign("WRQQ000", undefined, "gmrs");
  if (missingGmrs.status !== "not_found" && missingGmrs.status !== "invalid") {
    console.error("FAIL: WRQQ000 should not be found", missingGmrs.status);
    process.exit(1);
  }

  console.log("OK");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
