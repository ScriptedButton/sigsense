"use client";

import { useMemo, useState } from "react";

import { CopyButton, ProtocolBadge, downloadText } from "@/components/sdr/dvoice/parts";
import {
  PROTOCOL_CHOICES,
  PROTOCOL_LABELS,
  formatClock,
  talkgroupKey,
  type DvState,
  type TalkgroupPrefs,
  type TalkgroupRule,
} from "@/lib/sdr/dvoice/model";
import { dvStore } from "@/lib/sdr/dvoice/store";
import type { DvProtocol } from "@/lib/sdr/dvoice/types";

const RULES: { id: TalkgroupRule; label: string; hint: string }[] = [
  { id: "normal", label: "Normal", hint: "Follow it like any other talkgroup" },
  { id: "hold", label: "Only", hint: "Follow only talkgroups marked Only" },
  { id: "skip", label: "Skip", hint: "Never follow this talkgroup" },
  { id: "priority", label: "Priority", hint: "Switch to it when it is active" },
];

type Row = {
  key: string;
  protocol: DvProtocol;
  id: number;
  calls: number;
  encrypted: number;
  lastHeard: number | null;
};

function buildRows(state: DvState, prefs: TalkgroupPrefs): Row[] {
  const map = new Map<string, Row>();
  const add = (protocol: DvProtocol, id: number, calls: number, encrypted: number, lastHeard: number | null) => {
    const key = talkgroupKey(protocol, id);
    const existing = map.get(key);
    if (!existing) {
      map.set(key, { key, protocol, id, calls, encrypted, lastHeard });
    } else {
      existing.calls = Math.max(existing.calls, calls);
      existing.encrypted = Math.max(existing.encrypted, encrypted);
      existing.lastHeard = Math.max(existing.lastHeard ?? 0, lastHeard ?? 0) || null;
    }
  };
  for (const tg of Object.values(state.talkgroups)) add(tg.protocol, tg.id, tg.call_count, tg.encrypted_calls, tg.last_heard);
  // Count from the log too, so talkgroups appear even when the engine sends no talkgroup events.
  const counted = new Map<string, { calls: number; enc: number; last: number }>();
  for (const call of state.calls) {
    if (call.target_id === null || call.kind === "private") continue;
    const key = talkgroupKey(call.protocol, call.target_id);
    const c = counted.get(key) ?? { calls: 0, enc: 0, last: 0 };
    c.calls += 1;
    if (call.encryption.state === "encrypted") c.enc += 1;
    c.last = Math.max(c.last, call.started_at);
    counted.set(key, c);
  }
  for (const [key, c] of counted) {
    const [protocol, id] = [key.split(":")[0] as DvProtocol, Number(key.split(":")[1])];
    add(protocol, id, c.calls, c.enc, c.last);
  }
  // Anything with a saved alias or rule stays listed even if it has not been heard lately.
  for (const key of [...Object.keys(prefs.aliases), ...Object.keys(prefs.rules)]) {
    const [protocol, id] = [key.split(":")[0] as DvProtocol, Number(key.split(":")[1])];
    if (Number.isFinite(id)) add(protocol, id, 0, 0, null);
  }
  return [...map.values()].sort((a, b) => (b.lastHeard ?? 0) - (a.lastHeard ?? 0) || a.id - b.id);
}

export function TalkgroupManager({ state, prefs }: { state: DvState; prefs: TalkgroupPrefs }) {
  const [query, setQuery] = useState("");
  const [newProto, setNewProto] = useState<DvProtocol>("p25p1");
  const [newId, setNewId] = useState("");
  const rows = useMemo(() => buildRows(state, prefs), [state, prefs]);
  const q = query.trim().toLowerCase();
  const shown = rows.filter(
    (r) => !q || String(r.id).includes(q) || (prefs.aliases[r.key] ?? "").toLowerCase().includes(q) || PROTOCOL_LABELS[r.protocol].toLowerCase().includes(q),
  );
  const holding = Object.values(prefs.rules).filter((r) => r === "hold").length;

  const addManual = () => {
    const id = Number(newId);
    if (!Number.isInteger(id) || id < 0) return;
    dvStore().setAlias(newProto, id, prefs.aliases[talkgroupKey(newProto, id)] ?? `TG ${id}`);
    setNewId("");
  };

  const exportJson = () => JSON.stringify({ aliases: prefs.aliases, rules: prefs.rules }, null, 2);

  return (
    <section className="dv-card" aria-label="Talkgroups">
      <div className="dv-card-head">
        <strong>Talkgroups</strong>
        <span className="dv-muted">{rows.length} known</span>
        <span className="dv-spacer" />
        <button type="button" className="btn-secondary dv-small" onClick={() => downloadText("talkgroups.json", exportJson(), "application/json")}>
          Export
        </button>
        <CopyButton text={exportJson()} label="Copy" className="btn-secondary dv-small" />
      </div>
      {holding > 0 ? (
        <p className="dv-banner dv-banner-info" role="status">
          {holding} talkgroup{holding === 1 ? "" : "s"} marked Only: all others are ignored.
        </p>
      ) : null}
      <input
        type="search"
        className="field dv-search"
        aria-label="Search talkgroups"
        placeholder="Search ID, name or protocol…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <ul className="dv-tgs">
        {shown.map((row) => {
          const rule = prefs.rules[row.key] ?? "normal";
          return (
            <li key={row.key} className="dv-tg">
              <div className="dv-tg-head">
                <ProtocolBadge protocol={row.protocol} />
                <strong>{row.id}</strong>
                <span className="dv-muted">
                  {row.calls} call{row.calls === 1 ? "" : "s"}
                  {row.encrypted ? ` · ${row.encrypted} encrypted` : ""}
                  {row.lastHeard ? ` · ${formatClock(row.lastHeard)}` : ""}
                </span>
              </div>
              <input
                className="field"
                aria-label={`Name for talkgroup ${row.id}`}
                placeholder="Name (e.g. Dispatch)"
                maxLength={40}
                value={prefs.aliases[row.key] ?? ""}
                onChange={(e) => dvStore().setAlias(row.protocol, row.id, e.target.value)}
              />
              <div className="dv-chips" role="radiogroup" aria-label={`Follow rule for talkgroup ${row.id}`}>
                {RULES.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    role="radio"
                    aria-checked={rule === r.id}
                    title={r.hint}
                    className={`mode-chip ${rule === r.id ? "mode-chip-active" : ""}`}
                    onClick={() => dvStore().setRule(row.protocol, row.id, r.id)}
                  >
                    {r.label}
                  </button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      {shown.length === 0 ? <p className="dv-empty">{rows.length ? "No talkgroups match." : "Talkgroups appear here as they are heard."}</p> : null}

      <form
        className="dv-add"
        onSubmit={(e) => {
          e.preventDefault();
          addManual();
        }}
      >
        <select className="field" aria-label="Protocol for the new talkgroup" value={newProto} onChange={(e) => setNewProto(e.target.value as DvProtocol)}>
          {PROTOCOL_CHOICES.filter((p) => p.id !== "auto").map((p) => (
            <option key={p.id} value={p.id}>
              {PROTOCOL_LABELS[p.id as DvProtocol]}
            </option>
          ))}
        </select>
        <input
          className="field"
          inputMode="numeric"
          pattern="[0-9]*"
          aria-label="New talkgroup ID"
          placeholder="ID"
          value={newId}
          onChange={(e) => setNewId(e.target.value.replace(/\D/g, ""))}
        />
        <button type="submit" className="btn-secondary" disabled={!newId}>
          Add
        </button>
      </form>
    </section>
  );
}
