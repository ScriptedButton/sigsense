import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { dirname, resolve } from "node:path";
import type { ServiceSnapshot, ServiceStatus, ServiceView } from "../../src/lib/sdr/autofinder/service-types";
import { SERVICE_STALE_MS } from "../../src/lib/sdr/autofinder/service-types";

export function statusPath() {
  return process.env.SIGSENSE_AUTOFINDER_STATUS ?? resolve(process.cwd(), "../../.runtime/autofinder-service.json");
}

export function emptySnapshot(): ServiceSnapshot {
  return { version: 1, at: "", status: "stopped", message: "Background finder is offline.", sessionId: null,
    receiver: null, finder: null, enabled: true, findings: [], checks: [] };
}

export async function readSnapshot(path = statusPath()): Promise<ServiceSnapshot> {
  try {
    const saved = JSON.parse(await readFile(/* turbopackIgnore: true */ path, "utf8"));
    if (saved.version !== 1 || !Array.isArray(saved.findings) || !Array.isArray(saved.checks)) return emptySnapshot();
    return { ...saved, findings: saved.findings.slice(0, 200), checks: saved.checks.slice(0, 500) };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT" || error instanceof SyntaxError) return emptySnapshot();
    throw error;
  }
}

export function serviceView(snapshot: ServiceSnapshot, now = Date.now()): ServiceView {
  const timestamp = Date.parse(snapshot.at);
  const stale = !Number.isFinite(timestamp) || now - timestamp > SERVICE_STALE_MS;
  return { ...snapshot, stale, online: !stale && snapshot.status !== "stopped" };
}

export class FinderStore {
  private pending: Promise<void> = Promise.resolve();
  constructor(public snapshot = emptySnapshot(), private path = statusPath()) {}

  update(status: ServiceStatus, enabled: boolean): Promise<void> {
    const result = this.pending.then(() => this.persist(status, enabled));
    this.pending = result.catch(() => {});
    return result;
  }

  private async persist(status: ServiceStatus, enabled: boolean) {
    const checks = [...this.snapshot.checks];
    const findings = this.snapshot.findings.map((finding) => ({ ...finding }));
    // Process in chronological order. Repeated polling must never count as another detection.
    for (const hit of [...(status.finder?.recent ?? [])].reverse()) {
      if (hit.frequencyHz < 2_300_000 || hit.frequencyHz > 30_000_000 || !Number.isFinite(hit.speechRatio) || !Number.isFinite(Date.parse(hit.at))) continue;
      const id = `${status.sessionId}:${hit.at}:${hit.frequencyHz}`;
      if (checks.some((check) => check.id === id)) continue;
      checks.unshift({ ...hit, id });
      if (!hit.accepted) continue;
      const existing = findings.find((finding) => Math.abs(finding.frequencyHz - hit.frequencyHz) <= 750);
      if (existing) {
        existing.lastSeen = hit.at;
        existing.speechRatio = hit.speechRatio;
        existing.confirmations++;
      } else {
        findings.push({ frequencyHz: hit.frequencyHz, bandwidthHz: hit.bandwidthHz ?? 6000, band: hit.band ?? "HF",
          firstSeen: hit.at, lastSeen: hit.at, speechRatio: hit.speechRatio, confirmations: 1 });
      }
    }
    const cleaning = status.finder?.cleaning;
    if (cleaning?.status === "optimizing" && cleaning.metrics?.eligible) {
      const finding = findings.find((value) => Math.abs(value.frequencyHz - cleaning.frequencyHz) <= 750);
      if (finding) finding.cleaning = cleaning;
    }
    const next: ServiceSnapshot = { ...status, version: 1, enabled,
      findings: findings.sort((a, b) => Date.parse(b.lastSeen) - Date.parse(a.lastSeen)).slice(0, 200), checks: checks.slice(0, 500) };
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    await writeFile(temporary, `${JSON.stringify(next)}\n`, { mode: 0o600 });
    await rename(temporary, this.path);
    this.snapshot = next;
  }
}
