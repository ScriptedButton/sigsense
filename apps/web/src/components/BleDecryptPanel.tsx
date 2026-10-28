"use client";

import { useState } from "react";
import { decryptBlePayload } from "@/lib/api";

export function BleDecryptPanel() {
  const [key, setKey] = useState("");
  const [iv, setIv] = useState("");
  const [header, setHeader] = useState("");
  const [payload, setPayload] = useState("");
  const [counter, setCounter] = useState(0);
  const [direction, setDirection] = useState("central_to_peripheral");
  const [plain, setPlain] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return <details className="border border-[var(--line)] bg-[var(--panel)] p-4">
    <summary className="cursor-pointer text-sm text-[var(--foreground)]">Decrypt a captured BLE payload with a session key</summary>
    <form className="mt-3 space-y-3" onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setPlain(null); setError(null);
      try {
        const result = await decryptBlePayload({ session_key_hex:key, iv_hex:iv, header_hex:header,
          ciphertext_mic_hex:payload, packet_counter:counter, direction });
        setPlain(result.plaintext_hex);
      } catch (error) { setError(error instanceof Error ? error.message : String(error)); }
      finally { setBusy(false); }
    }}>
      <p className="text-xs text-[var(--muted)]">Requires the captured connection’s session key, IV, direction and packet counter. Plaintext is returned only after the MIC authenticates. This does not recover unknown keys or pairing secrets.</p>
      <label className="block text-xs">Session key (16 bytes, normal AES hex order)
        <input className="field mt-1 w-full" type="password" autoComplete="off" required value={key} onChange={e => setKey(e.target.value)} /></label>
      <label className="block text-xs">IV (8 bytes, on-air order: central then peripheral)
        <input className="field mt-1 w-full" required value={iv} onChange={e => setIv(e.target.value)} /></label>
      <label className="block text-xs">LL header (2 bytes, hex)
        <input className="field mt-1 w-full" required value={header} onChange={e => setHeader(e.target.value)} /></label>
      <label className="block text-xs">Ciphertext followed by the 4-byte MIC (hex)
        <textarea className="field mt-1 w-full font-mono" required value={payload} onChange={e => setPayload(e.target.value)} /></label>
      <label className="block text-xs">Packet counter
        <input className="field mt-1 w-full" type="number" min={0} max={549755813887} step={1} required value={counter} onChange={e => setCounter(Number(e.target.value))} /></label>
      <label className="block text-xs">Direction
        <select className="field mt-1 w-full" value={direction} onChange={e => setDirection(e.target.value)}>
          <option value="central_to_peripheral">Central → Peripheral</option>
          <option value="peripheral_to_central">Peripheral → Central</option>
        </select></label>
      <button className="border border-[var(--line)] px-3 py-2 text-sm" type="submit" disabled={busy}>{busy ? "Authenticating…" : "Verify MIC and decrypt"}</button>
      {plain !== null ? <pre className="overflow-auto whitespace-pre-wrap break-all text-xs text-emerald-400">MIC verified · plaintext: {plain || "(empty)"}</pre> : null}
      {error ? <p role="alert" className="break-all text-xs text-red-400">{error}</p> : null}
    </form>
  </details>;
}
