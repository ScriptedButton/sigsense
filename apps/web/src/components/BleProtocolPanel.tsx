"use client";

import { useState } from "react";
import { analyzeBleCapture, type BleCaptureAnalysis } from "@/lib/api";

export function BleProtocolPanel() {
  const [result, setResult] = useState<BleCaptureAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  return (
    <section className="space-y-3 border border-[var(--line)] bg-[var(--panel)] p-4">
      <h3 className="text-lg text-[var(--foreground)]">Open a Bluetooth capture</h3>
      <p className="text-xs text-[var(--muted)]">
        Already have a file from a Bluetooth sniffer or Android Bluetooth logging? Open it here to inspect conversations, service data and pairing messages. This is for packet captures; raw SDR IQ files belong in the upload area above.
      </p>
      <label className="block text-sm text-[var(--foreground)]">
        Choose a .pcap, .pcapng or .btsnoop file (up to 32 MiB)
        <input
          type="file"
          accept=".pcap,.pcapng,.cap,.btsnoop,.log"
          disabled={busy}
          className="mt-2 block w-full text-xs"
          onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) return;
            setBusy(true); setError(null); setResult(null); setPage(0);
            try { setResult(await analyzeBleCapture(file)); }
            catch (error) { setError(error instanceof Error ? error.message : String(error)); }
            finally { setBusy(false); }
          }}
        />
      </label>
      {busy ? <p className="text-xs text-[var(--muted)]">Analyzing capture…</p> : null}
      {error ? <p role="alert" className="break-words text-sm text-red-400">{error}</p> : null}
      {result ? <>
        <p className="text-xs text-[var(--muted)]">
          {result.packet_count} decoded frames · {result.protocols.join(" → ")}
        </p>
        <p className="text-xs text-[var(--muted)]">First {result.packet_limit} input frames inspected. Capture gaps and encrypted payloads remain visible as incomplete or undecoded traffic.</p>
        <div className="max-h-[32rem] space-y-2 overflow-auto">
          {result.packets.slice(page * 50, (page + 1) * 50).map((packet, index) => (
            <details key={`${packet.number}-${index}`} className="border border-[var(--line)] p-2">
              <summary className="cursor-pointer font-mono text-xs text-[var(--foreground)]">
                #{packet.number} · {protocolName(packet.summary)} · {packet.length} bytes
              </summary>
              <div className="mt-3 space-y-2"><ProtocolTree value={packet.layers}/><details className="text-xs text-[var(--muted)]"><summary className="cursor-pointer">Raw decoder output</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(packet.layers,null,2)}</pre></details></div>
            </details>
          ))}
        </div>
        {result.packet_count > 50 ? <div className="flex items-center gap-4 text-xs">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span>Page {page + 1} / {Math.ceil(result.packet_count / 50)}</span>
          <button type="button" disabled={(page + 1) * 50 >= result.packet_count} onClick={() => setPage(page + 1)}>Next</button>
        </div> : null}
      </> : null}
    </section>
  );
}

const protocolNames:Record<string,string>={btle:"Bluetooth radio message",btle_rf:"Radio metadata",btl2cap:"Connection transport (L2CAP)",btatt:"Service / characteristic data (ATT/GATT)",btsmp:"Pairing & security",bthci_acl:"Host connection data",bthci_evt:"Host event",bluetooth:"Bluetooth",frame:"Capture metadata",data:"Payload"};
function protocolName(name:string){return protocolNames[name]??name.replaceAll("_"," ");}
function fieldName(name:string){return protocolNames[name]??name.replace(/^[^.]+\./,"").replaceAll("_"," ");}
function ProtocolTree({value,depth=0}:{value:unknown;depth?:number}){
  if(depth>10)return <p className="text-xs text-[var(--muted)]">Expand raw output for deeper fields.</p>;
  if(value===null||typeof value!=="object")return <span className="break-all font-mono text-[11px] text-[var(--foreground)]">{String(value??"—")}</span>;
  return <div className="space-y-1">{Object.entries(value).map(([key,item])=>item!==null&&typeof item==="object"?<details key={key} className="rounded border border-[var(--line)] px-3 py-2"><summary className="cursor-pointer text-xs text-[var(--signal)]">{fieldName(key)}</summary><div className="mt-2"><ProtocolTree value={item} depth={depth+1}/></div></details>:<div key={key} className="grid grid-cols-[minmax(100px,1fr)_minmax(0,2fr)] gap-2 px-1 py-1"><span title={key} className="break-words text-[11px] text-[var(--muted)]">{fieldName(key)}</span><ProtocolTree value={item} depth={depth+1}/></div>)}</div>;
}
