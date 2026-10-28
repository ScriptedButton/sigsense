"use client";

import { useEffect, useMemo, useState } from "react";

import { CallDetailSheet, CallLog } from "@/components/sdr/dvoice/call-log";
import { LiveCalls, StatusCard, SystemInfoCard } from "@/components/sdr/dvoice/live";
import { CodecSetupSheet, DEFAULT_DISCLAIMER, IntroSheet, SettingsSheet } from "@/components/sdr/dvoice/sheets";
import { TalkgroupManager } from "@/components/sdr/dvoice/talkgroups";
import { useSdrState } from "@/components/sdr/sdr-provider";
import {
  PROTOCOL_CHOICES,
  PROTOCOL_LABELS,
  describeError,
  transcriptText,
  voiceAvailable,
  type VoiceSources,
} from "@/lib/sdr/dvoice/model";
import type { DvChannelSnapshot } from "@/lib/sdr/dvoice/client";
import { dvStore, useDvoice } from "@/lib/sdr/dvoice/store";
import type { DvProtocol } from "@/lib/sdr/dvoice/types";
import type { ConnectionMode } from "@/lib/sdr/types";

type SheetName = "intro" | "codec" | "settings" | null;

/** What the engine can see on each receiver source (see docs/digital-voice-architecture.md §5). */
function sourceNote(connection: ConnectionMode, frequencyHz: number, offsetHz: number): { text: string; tone: "ok" | "info" | "warn" } {
  switch (connection) {
    case "hackrf":
      return {
        tone: "ok",
        text: `Decoding the HackRF on the server at ${((frequencyHz + offsetHz) / 1e6).toFixed(4)} MHz. Click the spectrum to move the channel.`,
      };
    case "openwebrx":
      return { tone: "warn", text: "Digital voice isn't available with OpenWebRX: it only supplies demodulated audio, not the raw signal." };
    case "kiwi":
      return { tone: "warn", text: "Digital voice isn't available with KiwiSDR: it only supplies demodulated audio, not the raw signal." };
    case "disconnected":
      return { tone: "info", text: "No receiver is connected. Connect a HackRF to decode a channel." };
    default:
      return {
        tone: "info",
        text: "Browser sources (RTL-SDR, RTL server, Demo) would send the signal to the engine for decoding. That upload isn't wired up yet; use a HackRF for now.",
      };
  }
}

export function DigitalVoicePanel() {
  const ui = useDvoice();
  const radio = useSdrState();
  const store = dvStore();
  const [sheet, setSheet] = useState<SheetName>(null);
  const [openCallId, setOpenCallId] = useState<string | null>(null);
  const [dismissedErrors, setDismissedErrors] = useState(0);
  const { dv, capabilities, settings, prefs, audio } = ui;

  useEffect(() => {
    store.load();
    // Join an engine that is already running on the server rather than showing "off".
    void store.attach();
  }, [store]);

  const offsetHz = radio.offsetHz;
  const tunedHz = radio.frequencyHz + radio.offsetHz;
  useEffect(() => {
    store.setTuning(offsetHz, tunedHz);
  }, [store, offsetHz, tunedHz]);
  useEffect(() => {
    store.player.setVolume(radio.volume);
  }, [store, radio.volume]);

  const link = dv.links.ch0 ?? Object.values(dv.links)[0];
  const sources: VoiceSources = useMemo(
    () => ({
      engineVocoders: dv.engine?.vocoders ?? capabilities?.vocoders,
      localProtocols: capabilities?.licence_notices?.local_codec?.protocols_with_audio,
      backends: capabilities?.licence_notices?.backends,
    }),
    [dv.engine?.vocoders, capabilities],
  );
  const voiceFor = (protocol: DvProtocol) => voiceAvailable(protocol, sources);

  const protocolSupport = capabilities?.protocols;
  const channelSnapshot = (ui.engineStatus?.engine?.channels?.ch0 ?? (dv.engine?.channels?.ch0 as DvChannelSnapshot | undefined));
  const feeder = ui.engineStatus?.feeder;
  const feederProblem =
    ui.running && feeder && ["unavailable", "waiting", "error"].includes(feeder.state) && feeder.reason
      ? feeder.reason
      : null;
  const noVoice = capabilities
    ? (["p25p1", "dmr", "nxdn96", "ysf", "dstar", "m17"] as DvProtocol[]).filter((p) => !voiceFor(p))
    : [];
  // A live call without audio already carries its own "turn on voice audio" prompt.
  const liveNeedsVoice = dv.calls.some((c) => c.ended_at === null && c.encryption.state !== "encrypted" && !voiceFor(c.protocol));
  const note = sourceNote(radio.connection, radio.frequencyHz, radio.offsetHz);
  const disclaimer = capabilities?.disclaimer ?? ui.engineStatus?.disclaimer ?? DEFAULT_DISCLAIMER;
  const openCall = openCallId ? dv.calls.find((c) => c.call_id === openCallId) : undefined;
  const latestError = dv.errors.length > dismissedErrors ? dv.errors[0] : undefined;
  const shownError = latestError && latestError.code !== "vocoder_unavailable" ? describeError(latestError) : null;
  const reconnecting = ui.running && dv.connection === "closed";
  const unavailable = ui.running && (dv.connection === "unavailable" || ui.capabilitiesError);
  const searching = ui.running && dv.connection === "open" && (!link || link.state === "searching");

  const start = () => {
    const source = radio.connection === "hackrf" ? "hackrf" : "upload";
    store.start(source);
    // The click is the user gesture iOS needs before it will play audio.
    void store.unlockAudio();
    if (!ui.introSeen) setSheet("intro");
  };

  return (
    <fieldset className="control-group dv-panel">
      <legend className="control-label">Digital voice</legend>

      <div className="dv-row dv-wrap">
        {ui.running ? (
          <button type="button" className="btn-primary" onClick={() => store.stop()}>
            Stop digital voice
          </button>
        ) : (
          <button type="button" className="btn-primary" onClick={start}>
            Start digital voice
          </button>
        )}
        {ui.running ? (
          audio.unlocked ? (
            <button
              type="button"
              className="btn-secondary"
              aria-pressed={audio.muted}
              onClick={() => store.setMuted(!audio.muted)}
            >
              {audio.muted ? "Unmute" : "Mute"}
            </button>
          ) : (
            <button type="button" className="btn-secondary" onClick={() => void store.unlockAudio()}>
              Enable audio
            </button>
          )
        ) : null}
        <button type="button" className="btn-secondary" onClick={() => setSheet("settings")}>
          Settings
        </button>
      </div>

      <p className={`dv-note dv-note-${note.tone}`}>{note.text}</p>

      {unavailable ? (
        <section className="dv-card dv-card-error" role="alert">
          <strong>The digital voice engine isn&apos;t available</strong>
          <p>{ui.capabilitiesError ?? "The API didn't answer."} Call details and audio need the engine running on the API computer.</p>
          <button type="button" className="btn-secondary" onClick={() => void store.refreshCapabilities()}>
            Try again
          </button>
        </section>
      ) : null}
      {reconnecting ? (
        <p className="dv-banner dv-banner-info" role="status">
          Connection lost. Reconnecting…
        </p>
      ) : null}
      {ui.configError ? (
        <p className="dv-banner dv-banner-warn" role="status">
          Couldn&apos;t apply settings: {ui.configError}
        </p>
      ) : null}
      {shownError ? (
        <p className="dv-banner dv-banner-warn" role="status">
          <strong>{shownError.title}.</strong> {shownError.fix}{" "}
          <button type="button" className="dv-link" onClick={() => setDismissedErrors(dv.errors.length)}>
            Dismiss
          </button>
        </p>
      ) : null}

      {ui.running ? (
        <>
          <StatusCard link={link} engine={dv.engine} forced={settings.protocol} channel={channelSnapshot} />
          {feederProblem ? (
            <p className="dv-banner dv-banner-warn" role="status">
              <strong>No samples reaching the decoder.</strong> {feederProblem[0].toUpperCase() + feederProblem.slice(1)}.
            </p>
          ) : null}
          {searching ? (
            <p className="dv-banner dv-banner-info" role="status">
              No sync yet. Tune to a channel that carries digital voice ({PROTOCOL_CHOICES.filter((p) => p.id !== "auto").map((p) => p.label).join(", ")}),
              then check the antenna and gain.
            </p>
          ) : null}

          <div className="dv-chips" role="radiogroup" aria-label="Protocol">
            {PROTOCOL_CHOICES.map((choice) => {
              const support = choice.id === "auto" ? undefined : protocolSupport?.[choice.id];
              const missing = choice.id === "auto" ? false : support ? !support.implemented : Boolean(choice.note);
              const active = settings.protocol === choice.id;
              return (
                <button
                  key={choice.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={missing && !active}
                  title={missing ? (support?.reason || "Not supported by this engine yet") : choice.id === "auto" ? "Detect the protocol from the signal" : `Force ${PROTOCOL_LABELS[choice.id as DvProtocol]}`}
                  className={`mode-chip ${active ? "mode-chip-active" : ""}`}
                  onClick={() => store.setSettings({ protocol: choice.id })}
                >
                  {choice.label}
                  {missing ? <small> not yet</small> : null}
                </button>
              );
            })}
          </div>

          <LiveCalls
            state={dv}
            aliases={prefs.aliases}
            voiceFor={voiceFor}
            audio={audio}
            onListen={(callNum) => {
              void store.unlockAudio().then(() => store.followCall(callNum));
            }}
            onSetupAudio={() => setSheet("codec")}
          />

          {noVoice.length > 0 && capabilities && !liveNeedsVoice ? (
            <section className="dv-card dv-card-info">
              <strong>Voice audio is off for some protocols</strong>
              <p className="dv-muted">
                {noVoice.map((p) => PROTOCOL_LABELS[p]).join(", ")}: no codec installed. You still get who talked, where and for how long.
              </p>
              <button type="button" className="btn-secondary" onClick={() => setSheet("codec")}>
                Turn on voice audio
              </button>
            </section>
          ) : null}

          <SystemInfoCard systems={dv.systems} />
          <CallLog calls={dv.calls} aliases={prefs.aliases} onOpen={setOpenCallId} />
          <TalkgroupManager state={dv} prefs={prefs} />
        </>
      ) : (
        <p className="dv-muted">
          Start digital voice to decode P25, DMR, NXDN, D-STAR, YSF and M17 calls from the tuned channel. Encrypted calls are reported but not decoded.
        </p>
      )}

      <p className="dv-disclaimer">
        {disclaimer}{" "}
        <button type="button" className="dv-link" onClick={() => setSheet("intro")}>
          What does clear vs encrypted mean?
        </button>
      </p>

      {sheet === "intro" ? (
        <IntroSheet
          disclaimer={disclaimer}
          onClose={() => {
            store.markIntroSeen();
            setSheet(null);
          }}
        />
      ) : null}
      {sheet === "codec" ? <CodecSetupSheet capabilities={capabilities} onClose={() => setSheet(null)} /> : null}
      {sheet === "settings" ? (
        <SettingsSheet
          settings={settings}
          speechAvailable={Boolean(capabilities?.speech?.available)}
          onChange={(partial) => store.setSettings(partial)}
          onClose={() => setSheet(null)}
        />
      ) : null}
      {openCall ? (
        <CallDetailSheet call={openCall} aliases={prefs.aliases} transcript={transcriptText(dv, openCall)} onClose={() => setOpenCallId(null)} />
      ) : null}
    </fieldset>
  );
}
