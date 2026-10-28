"use client";

import { useMemo, useState } from "react";
import { API_BASE, signalRequest } from "@/lib/api";
import type { SavedBleMessages } from "@/lib/bleTypes";
import { groupSavedMessages, savedBroadcastLabels } from "@/lib/bleSavedGroups";
import { BlePacketList } from "./BlePacketList";

const button = "rounded-md border border-[var(--line)] px-3 py-2 text-xs hover:border-[var(--signal)] disabled:opacity-40";

export function BleSavedLibrary({ items, onRefresh }: {
  items: SavedBleMessages[];
  onRefresh: () => Promise<void>;
}) {
  const groups = useMemo(() => groupSavedMessages(items), [items]);
  const labels = useMemo(() => savedBroadcastLabels(items), [items]);
  const [selected, setSelected] = useState<SavedBleMessages | null>(null);
  const [unique, setUnique] = useState(true);
  const [repeats, setRepeats] = useState(1);
  const [gain, setGain] = useState(10);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [name, setName] = useState("");
  const action = async (work: () => Promise<void>) => {
    setBusy(true); setNotice("");
    try { await work(); } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally { setBusy(false); }
  };
  const replay = (dryRun: boolean) => action(async () => {
    if (!selected) return;
    const result = await signalRequest<{messages: number; skipped: number; transmitted: boolean; timing: string}>(
      `/ble/saved/${selected.id}/replay`, {unique, repeats, tx_gain: gain, dry_run: dryRun},
    );
    setNotice(`${result.transmitted ? "Transmitted" : "Dry-run ready"} · ${result.messages} messages · ${result.skipped} unsupported messages skipped. ${result.timing}`);
  });
  return (
    <details className="border-t border-[var(--line)] p-4" open={items.length > 0 || undefined}>
      <summary className="cursor-pointer text-sm font-medium">Saved broadcasts & devices · {items.length}</summary>
      <p className="mt-2 text-xs text-[var(--muted)]">Device captures and individually saved broadcasts are grouped automatically by Bluetooth address and address type. Each keeps its own name and replay controls.</p>
      <button type="button" className={`${button} mt-3`} disabled={busy} onClick={() => action(onRefresh)}>Refresh library</button>
      {!items.length && <p className="mt-3 text-xs text-[var(--muted)]">Inspect a device to save a broadcast or its complete session messages.</p>}
      <div className="mt-3 max-h-96 space-y-3 overflow-auto">
        {groups.map((group) => (
          <details key={group.key} open className="rounded-lg border border-[var(--line)]">
            <summary className="cursor-pointer px-3 py-3 text-sm">
              <span className="font-medium">{group.name}{group.demo ? " · DEMO" : ""}</span>
              <span className="mt-1 block font-mono text-[10px] text-[var(--muted)]">{group.address ?? "No single device identity"}</span>
              <span className="mt-1 block text-[11px] text-[var(--muted)]">
                {group.items.filter((item) => item.kind === "device").length} device captures · {group.items.filter((item) => item.kind === "broadcast").length} saved broadcasts
              </span>
            </summary>
            <div className="space-y-2 border-t border-[var(--line)] p-2">
              {group.items.map((item) => (
                <button key={item.id} type="button" disabled={busy} aria-pressed={selected?.id === item.id}
                  className={`w-full rounded-lg border p-3 text-left text-xs ${selected?.id === item.id ? "border-[var(--signal)]" : "border-[var(--line)]"}`}
                  onClick={() => action(async () => {
                    const record = await signalRequest<SavedBleMessages>(`/ble/saved/${item.id}`);
                    setSelected(record); setName(record.name);
                  })}>
                  <span className="font-medium">{item.name}</span>
                  <span className="mt-1 block text-[var(--muted)]">{item.kind === "device" ? "Device capture" : "Broadcast type"} · {item.message_count} messages · {item.broadcast_count} unique · {item.replayable_count} replayable</span>
                </button>
              ))}
            </div>
          </details>
        ))}
      </div>
      {selected && (
        <div className="mt-4 space-y-3 rounded-lg border border-[var(--line)] p-3">
          <div className="flex items-center justify-between gap-2 text-sm"><span>{selected.name}</span><button type="button" className="text-xs" onClick={() => setSelected(null)}>Close</button></div>
          <form className="flex gap-2" onSubmit={(event) => {
            event.preventDefault();
            action(async () => {
              const record = await signalRequest<SavedBleMessages>(`/ble/saved/${selected.id}`, {name}, "PATCH");
              setSelected(record); await onRefresh();
            });
          }}>
            <input aria-label="Saved broadcast or device label" className="field min-w-0 flex-1 text-xs" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
            <button className={button} disabled={busy || !name.trim()}>Rename</button>
          </form>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <label className="flex items-center gap-2"><input type="checkbox" checked={unique} onChange={(event) => setUnique(event.target.checked)} />Replay unique broadcasts only</label>
            <label>Repeats <input aria-label="Bluetooth sequence repeats" className="field ml-1 w-16" type="number" min={1} max={20} value={repeats} onChange={(event) => setRepeats(+event.target.value)} /></label>
            <label>TX gain <input aria-label="Bluetooth sequence TX gain" className="field ml-1 w-16" type="number" min={0} max={47} value={gain} onChange={(event) => setGain(+event.target.value)} /> dB</label>
          </div>
          <p className="text-xs text-[var(--muted)]">RF replay reconstructs legacy LE1M announcements and scan responses on their recorded channels. Other messages stay saved for inspection. Original timing and connection behavior are not reproduced.</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={button} disabled={busy || !selected.replayable_count} onClick={() => replay(true)}>{busy ? "Working…" : "Dry-run replay"}</button>
            <button type="button" className={`${button} text-amber-200`} disabled={busy || !selected.replayable_count} onClick={() => replay(false)}>Transmit saved messages</button>
            <a className={button} href={`${API_BASE}/ble/saved/${selected.id}`} download={`${selected.name}.json`}>Export JSON</a>
          </div>
          <BlePacketList result={null} history={selected.packets ?? []} rfStatus={{demo: selected.demo}} broadcastLabels={labels} savedView />
        </div>
      )}
      {notice && <p role="status" className="mt-3 text-xs text-[var(--muted)]">{notice}</p>}
    </details>
  );
}
