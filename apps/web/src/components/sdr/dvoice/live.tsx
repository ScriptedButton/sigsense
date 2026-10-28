"use client";

import {
  EmergencyBadge,
  EncryptionBadge,
  ProtocolBadge,
  QualityMeter,
  useTick,
} from "@/components/sdr/dvoice/parts";
import {
  PROTOCOL_LABELS,
  activeCalls,
  callDurationS,
  callTitle,
  formatDuration,
  formatId,
  formatMhz,
  formatNac,
  formatPercent,
  isEncrypted,
  linkStateLabel,
  type Aliases,
  type DvState,
} from "@/lib/sdr/dvoice/model";
import type { DvChannelSnapshot } from "@/lib/sdr/dvoice/client";
import type { DvCall, DvLinkStats, DvSystemInfo } from "@/lib/sdr/dvoice/types";

/** Link meters: sync lamp, BER, FEC counters, signal quality, CPU. */
export function StatusCard({
  link,
  engine,
  forced,
  channel,
}: {
  link: DvLinkStats | undefined;
  engine: DvState["engine"];
  forced: string;
  channel: DvChannelSnapshot | undefined;
}) {
  const state = link?.state ?? "searching";
  const lamp = state === "voice" ? "voice" : state === "synced" ? "synced" : state === "lost" ? "lost" : "searching";
  const detected = link?.protocol ? PROTOCOL_LABELS[link.protocol] : null;
  const protocolText = forced !== "auto" ? `${detected ?? forced} (forced)` : detected ? `${detected} (auto)` : "Searching (auto)";
  return (
    <section className="dv-card" aria-label="Link status">
      <div className="dv-card-head">
        <span className={`dv-lamp dv-lamp-${lamp}`} aria-hidden="true" />
        <strong>{linkStateLabel(link)}</strong>
        <span className="dv-muted">{protocolText}</span>
        <span className="dv-spacer" />
        <QualityMeter link={link} />
      </div>
      <dl className="dv-stats">
        <div>
          <dt>Sync</dt>
          <dd>{link ? formatPercent(link.sync_ratio, 0) : "—"}</dd>
        </div>
        <div>
          <dt>BER</dt>
          <dd>{link ? formatPercent(link.ber, 2) : "—"}</dd>
        </div>
        <div>
          <dt title="Errors the error correction repaired / could not repair">FEC fixed/failed</dt>
          <dd>{link ? `${link.fec_corrected}/${link.fec_failed}` : "—"}</dd>
        </div>
        <div>
          <dt>SNR</dt>
          <dd>{link?.snr_db != null ? `${link.snr_db.toFixed(1)} dB` : "—"}</dd>
        </div>
        <div>
          <dt title="Carrier offset the decoder is tracking">Offset</dt>
          <dd>{link?.freq_error_hz != null ? `${Math.round(link.freq_error_hz)} Hz` : "—"}</dd>
        </div>
        <div>
          <dt title="Share of one CPU core the engine is using">CPU</dt>
          <dd>{channel?.cpu_fraction != null ? `${Math.round(channel.cpu_fraction * 100)}%` : "—"}</dd>
        </div>
      </dl>
      {engine && !engine.running ? <p className="dv-warn-text">The engine is not running.</p> : null}
      {channel?.decoder === "unavailable" && channel.decoder_note ? <p className="dv-warn-text">{channel.decoder_note}</p> : null}
      {link?.polarity_inverted ? <p className="dv-muted">Signal polarity is inverted; the decoder is correcting for it.</p> : null}
    </section>
  );
}

function liveTranscript(state: DvState, call: DvCall): string {
  const t = state.transcripts[call.call_id];
  if (!t) return "";
  return [...t.final.slice(-2), t.partial].filter(Boolean).join(" ");
}

function systemFor(call: DvCall, systems: Record<string, DvSystemInfo>): DvSystemInfo | undefined {
  return Object.values(systems).find((s) => s.protocol === call.protocol);
}

function networkLine(call: DvCall, system: DvSystemInfo | undefined): string {
  if (!system) return "";
  if (call.protocol === "p25p1" || call.protocol === "p25p2") return system.nac !== null ? `NAC ${formatNac(system.nac)}` : "";
  if (call.protocol === "dmr") return system.color_code !== null ? `Colour code ${system.color_code}` : "";
  if (call.protocol === "nxdn48" || call.protocol === "nxdn96") return system.ran !== null ? `RAN ${system.ran}` : "";
  return "";
}

export function LiveCalls({
  state,
  aliases,
  voiceFor,
  audio,
  onListen,
  onSetupAudio,
}: {
  state: DvState;
  aliases: Aliases;
  voiceFor: (protocol: DvCall["protocol"]) => boolean;
  audio: { unlocked: boolean; callNum: number | null };
  onListen: (callNum: number | null) => void;
  onSetupAudio: () => void;
}) {
  const live = activeCalls(state).sort((a, b) => a.started_at - b.started_at);
  useTick(1000, live.length > 0);

  if (live.length === 0) {
    const last = state.calls[0];
    return (
      <section className="dv-card dv-idle" aria-label="Live call">
        <strong>No call right now</strong>
        <span className="dv-muted">
          {last ? `Last call: ${callTitle(last, aliases)} · ${PROTOCOL_LABELS[last.protocol]}` : "Calls appear here as they are heard."}
        </span>
      </section>
    );
  }

  return (
    <div className="dv-live-list" aria-label="Live calls">
      {live.map((call) => {
        const encrypted = isEncrypted(call);
        const callNum = typeof call.meta.call_num === "number" ? call.meta.call_num : null;
        const listening = !encrypted && callNum !== null && audio.callNum === callNum;
        const hasVoice = voiceFor(call.protocol);
        const net = networkLine(call, systemFor(call, state.systems));
        return (
          <section
            key={call.call_id}
            className={`dv-card dv-live ${encrypted ? "dv-live-encrypted" : ""} ${call.emergency ? "dv-live-emergency" : ""}`}
            aria-label={`Live call: ${callTitle(call, aliases)}`}
          >
            <div className="dv-card-head">
              <span className="dv-rec" aria-hidden="true" />
              <strong className="dv-title">{callTitle(call, aliases)}</strong>
              <span className="dv-spacer" />
              <span className="dv-time" aria-label="Duration">
                {formatDuration(callDurationS(call))}
              </span>
            </div>
            <div className="dv-badges">
              <ProtocolBadge protocol={call.protocol} slot={call.slot} />
              {call.emergency ? <EmergencyBadge /> : null}
              <EncryptionBadge encryption={call.encryption} />
              {net ? <span className="dv-badge">{net}</span> : null}
            </div>
            <dl className="dv-stats dv-stats-tight">
              <div>
                <dt>From</dt>
                <dd>{formatId(call.source_id)}</dd>
              </div>
              <div>
                <dt>Frequency</dt>
                <dd>{formatMhz(call.frequency_hz)}</dd>
              </div>
              <div>
                <dt>Voice frames</dt>
                <dd>
                  {call.voice_frames}
                  {call.voice_frames_bad ? ` (${call.voice_frames_bad} bad)` : ""}
                </dd>
              </div>
              <div>
                <dt>BER</dt>
                <dd>{formatPercent(call.ber, 2)}</dd>
              </div>
            </dl>

            {liveTranscript(state, call) ? (
              <p className="dv-live-transcript" aria-live="polite">
                {liveTranscript(state, call)}
              </p>
            ) : null}

            {encrypted ? (
              <p className="dv-banner dv-banner-encrypted" role="status">
                <strong>No audio: encrypted.</strong> This call is scrambled
                {call.encryption.algorithm_name ? ` (${call.encryption.algorithm_name}` : ""}
                {call.encryption.algorithm_name && call.encryption.key_id !== null ? `, key ${call.encryption.key_id}` : ""}
                {call.encryption.algorithm_name ? ")" : ""}. SigSense shows that it happened but does not decode it.
              </p>
            ) : !hasVoice ? (
              <p className="dv-banner dv-banner-info" role="status">
                Voice audio is off for {PROTOCOL_LABELS[call.protocol]}: no codec is installed. Call details still work.{" "}
                <button type="button" className="dv-link" onClick={onSetupAudio}>
                  Turn on voice audio
                </button>
              </p>
            ) : listening ? (
              <p className="dv-banner dv-banner-ok" role="status">
                Listening to this call
              </p>
            ) : (
              <button
                type="button"
                className="btn-secondary dv-wide"
                onClick={() => onListen(callNum)}
                disabled={callNum === null}
              >
                {audio.unlocked ? "Listen to this call" : "Enable audio and listen"}
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** Per-protocol system facts: P25 NAC / WACN / site, DMR colour code, control channel. */
export function SystemInfoCard({ systems }: { systems: Record<string, DvSystemInfo> }) {
  const list = Object.values(systems);
  if (list.length === 0) return null;
  return (
    <section className="dv-card" aria-label="System information">
      <div className="dv-card-head">
        <strong>System</strong>
      </div>
      {list.map((system) => (
        <div key={`${system.protocol}-${system.name ?? ""}`} className="dv-system">
          <div className="dv-badges">
            <ProtocolBadge protocol={system.protocol} />
            {system.name ? <span className="dv-system-name">{system.name}</span> : null}
          </div>
          <dl className="dv-stats dv-stats-tight">
            {system.nac !== null ? (
              <div>
                <dt>NAC</dt>
                <dd>{formatNac(system.nac)}</dd>
              </div>
            ) : null}
            {system.wacn !== null ? (
              <div>
                <dt>WACN</dt>
                <dd>{system.wacn.toString(16).toUpperCase()}</dd>
              </div>
            ) : null}
            {system.system_id !== null ? (
              <div>
                <dt>System ID</dt>
                <dd>{system.system_id.toString(16).toUpperCase()}</dd>
              </div>
            ) : null}
            {system.rfss_id !== null || system.site_id !== null ? (
              <div>
                <dt>RFSS / Site</dt>
                <dd>
                  {formatId(system.rfss_id)} / {formatId(system.site_id)}
                </dd>
              </div>
            ) : null}
            {system.color_code !== null ? (
              <div>
                <dt>Colour code</dt>
                <dd>{system.color_code}</dd>
              </div>
            ) : null}
            {system.ran !== null ? (
              <div>
                <dt>RAN</dt>
                <dd>{system.ran}</dd>
              </div>
            ) : null}
            {system.network_id !== null ? (
              <div>
                <dt>Network</dt>
                <dd>{system.network_id}</dd>
              </div>
            ) : null}
            {system.control_channel_hz !== null ? (
              <div>
                <dt>Control channel</dt>
                <dd>{formatMhz(system.control_channel_hz)}</dd>
              </div>
            ) : null}
          </dl>
          {Object.entries(system.extra ?? {}).length ? (
            <p className="dv-muted">
              {Object.entries(system.extra)
                .map(([key, value]) => `${key}: ${String(value)}`)
                .join(" · ")}
            </p>
          ) : null}
        </div>
      ))}
    </section>
  );
}
