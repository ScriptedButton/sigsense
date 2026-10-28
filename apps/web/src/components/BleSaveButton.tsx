"use client";

import { useState } from "react";
import { signalRequest } from "@/lib/api";
import type { BlePacket, SavedBleMessages } from "@/lib/bleTypes";

export function BleSaveButton({ packets, getPackets, name: initialName, kind, demo = false, onSaved, compact = false }: {
  packets: BlePacket[];
  getPackets?: () => BlePacket[];
  name: string;
  kind: "broadcast" | "device";
  demo?: boolean;
  onSaved?: () => Promise<void>;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(initialName.slice(0, 80));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  return (
    <div className={compact ? "text-xs" : "px-4 py-2 text-xs"}>
      {!open ? (
        <button type="button" className="text-[var(--signal)]" onClick={() => { setOpen(true); setNotice(""); }}>
          {kind === "device" ? "Save device & associated messages" : "Save / classify broadcast"}
        </button>
      ) : (
        <form className="flex flex-wrap items-center gap-2" onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setNotice("");
          try {
            // Resolve the complete device snapshot at save time, independently of filters.
            const result = await signalRequest<SavedBleMessages>("/ble/saved", {
              name: name.trim(), kind, demo, packets: getPackets ? getPackets() : packets,
            });
            setNotice(`${result.duplicate ? "Updated saved name" : "Saved"} · ${result.message_count} messages · ${result.broadcast_count} unique broadcasts`);
            setOpen(false);
            await onSaved?.();
          } catch (error) {
            setNotice(error instanceof Error ? error.message : String(error));
          } finally { setBusy(false); }
        }}>
          <input aria-label={kind === "device" ? "Saved device name" : "Broadcast type label"}
            className="field min-w-0 flex-1 text-xs" value={name} maxLength={80}
            placeholder={kind === "device" ? "Device name" : "e.g. Lamp · On button"}
            onChange={(event) => setName(event.target.value)} />
          <button className="rounded border border-[var(--signal)] px-3 py-2 text-[var(--signal)] disabled:opacity-40" disabled={busy || !name.trim()}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
          {kind === "device" && <p className="w-full text-[var(--muted)]">Saves every available message for this address, including repeats and messages hidden by filters. Live captures include the current listening session.</p>}
        </form>
      )}
      {notice && <p role="status" className="mt-2 break-words text-[var(--muted)]">{notice}</p>}
    </div>
  );
}
