"use client";

import { useState } from "react";

import { CopyButton } from "@/components/sdr/dvoice/parts";
import { Sheet } from "@/components/sdr/sheet";
import { dvStore } from "@/lib/sdr/dvoice/store";
import type { DvCapabilities, LocalCodecStatus } from "@/lib/sdr/dvoice/client";
import { DEFAULT_SETTINGS, type DvSettings } from "@/lib/sdr/dvoice/model";

export const DEFAULT_DISCLAIMER =
  "Monitoring is legal for unencrypted public-safety and amateur traffic in many places but not all. " +
  "You are responsible for complying with the laws that apply to you. Encrypted transmissions are not decoded.";

const FALLBACK_BUILD_COMMAND = "python3 apps/api/scripts/build_mbe_adapter.py --i-understand";

/** One-time explainer: what clear vs encrypted means, plus the lawful-use note. */
export function IntroSheet({ disclaimer, onClose }: { disclaimer: string; onClose: () => void }) {
  return (
    <Sheet title="Digital voice, in short" onClose={onClose}>
      <div className="dv-sheet-body">
        <p>
          Many radio systems send speech as digital data. This panel decodes the call information (who is talking, to which
          talkgroup, for how long) and, when a voice codec is installed, plays the audio.
        </p>
        <dl className="dv-defs">
          <dt>Clear</dt>
          <dd>The call is not scrambled. Details are shown and, with a codec installed, you can hear it.</dd>
          <dt>Encrypted</dt>
          <dd>
            The call is scrambled on purpose. SigSense shows that it happened and which algorithm it reports, but it does not
            decode it and there is no audio. It cannot be unlocked from here.
          </dd>
        </dl>
        <p className="dv-disclaimer">{disclaimer}</p>
        <button type="button" className="btn-primary" onClick={onClose}>
          Got it
        </button>
      </div>
    </Sheet>
  );
}

/** How to turn on voice audio: the local build, the warning, a status refresh. */
export function CodecSetupSheet({
  capabilities,
  onClose,
}: {
  capabilities: DvCapabilities | null;
  onClose: () => void;
}) {
  const status: LocalCodecStatus | undefined = capabilities?.licence_notices?.local_codec;
  const [busy, setBusy] = useState(false);
  const command = status?.build_command ?? FALLBACK_BUILD_COMMAND;
  const refresh = async () => {
    setBusy(true);
    await dvStore().refreshCapabilities();
    setBusy(false);
  };

  return (
    <Sheet title="Turn on voice audio" onClose={onClose}>
      <div className="dv-sheet-body">
        <p className={status?.installed ? "dv-ok" : "dv-muted"} role="status">
          {status?.installed
            ? `Installed${status.library ? ` · ${status.library}` : ""}. Audio is available for ${
                status.protocols_with_audio.join(", ") || "no protocols yet"
              }.`
            : "No voice codec is installed, so calls show details only. M17 voice works without it."}
        </p>
        {status?.last_error ? <p className="dv-error-text">Last problem: {status.last_error}</p> : null}

        <ol className="dv-steps">
          <li>
            Open a terminal in the SigSense folder on the computer that runs the API (you need a C compiler such as{" "}
            <code>gcc</code> and an internet connection for one download).
          </li>
          <li>
            Run this command. It prints a warning and asks you to confirm before it downloads or builds anything:
            <div className="dv-code-row">
              <code className="dv-code" tabIndex={0}>
                {command}
              </code>
              <CopyButton text={command} />
            </div>
          </li>
          <li>Restart the API, then press Refresh below.</li>
        </ol>

        <details className="dv-warning" open>
          <summary>Patents and licences: read this first</summary>
          <p>
            {status?.warning ??
              "The voice codecs used by P25, DMR, NXDN and YSF may be patent protected, and the codec source has its own licence. Check the laws that apply to you before you build or use it. SigSense does not ship the codec; the build is yours, on your machine, for your own use."}
          </p>
        </details>

        {capabilities?.licence_notices?.backends?.length ? (
          <details>
            <summary>Backends and licences</summary>
            <ul className="dv-backends">
              {capabilities.licence_notices.backends.map((backend) => (
                <li key={backend.name}>
                  <strong>{backend.codec}</strong> · {backend.active ? "active" : "not active"}
                  {backend.licence ? ` · ${backend.licence}` : ""}
                  {backend.supplied_by ? ` · from ${backend.supplied_by}` : ""}
                  {!backend.active && backend.reason ? <div className="dv-muted">{backend.reason}</div> : null}
                </li>
              ))}
            </ul>
          </details>
        ) : null}

        {status?.uninstall_command ? (
          <details>
            <summary>Remove it again</summary>
            <div className="dv-code-row">
              <code className="dv-code" tabIndex={0}>
                {status.uninstall_command}
              </code>
              <CopyButton text={status.uninstall_command} />
            </div>
          </details>
        ) : null}

        <div className="dv-row">
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void refresh()}>
            {busy ? "Checking…" : "Refresh status"}
          </button>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </Sheet>
  );
}

const STORAGE_CHOICES = [
  { gb: 1, label: "1 GB" },
  { gb: 5, label: "5 GB" },
  { gb: 20, label: "20 GB" },
  { gb: 50, label: "50 GB" },
];

export function SettingsSheet({
  settings,
  speechAvailable,
  onChange,
  onClose,
}: {
  settings: DvSettings;
  /** Whether the API's speech service is installed (for the transcription option). */
  speechAvailable: boolean;
  onChange: (partial: Partial<DvSettings>) => void;
  onClose: () => void;
}) {
  return (
    <Sheet title="Digital voice settings" onClose={onClose}>
      <div className="dv-sheet-body">
        <label className="dv-field">
          <span>
            Hang time · {settings.hangTimeS.toFixed(1)} s
            <small className="dv-muted"> How long to stay on a talkgroup after it goes quiet before looking for the next call.</small>
          </span>
          <input
            type="range"
            min={0.5}
            max={10}
            step={0.5}
            value={settings.hangTimeS}
            onChange={(e) => onChange({ hangTimeS: Number(e.target.value) })}
          />
        </label>
        <p className="dv-muted">Encrypted calls are always skipped: they have no audio to follow and are never decoded or recorded.</p>
        <label className="dv-check">
          <input type="checkbox" checked={settings.autoListen} onChange={(e) => onChange({ autoListen: e.target.checked })} />
          Start playing the next call automatically
        </label>
        <label className="dv-check">
          <input type="checkbox" checked={settings.recording} onChange={(e) => onChange({ recording: e.target.checked })} />
          Record clear calls on the API computer
        </label>
        {settings.recording ? (
          <label className="dv-field">
            <span>Keep up to</span>
            <select
              className="field"
              value={settings.maxRecordingGb}
              onChange={(e) => onChange({ maxRecordingGb: Number(e.target.value) })}
            >
              {STORAGE_CHOICES.map((choice) => (
                <option key={choice.gb} value={choice.gb}>
                  {choice.label} of audio
                </option>
              ))}
            </select>
            <small className="dv-muted">Recordings are saved on the computer running the API; the oldest go first when the limit is reached. Encrypted calls are never recorded.</small>
          </label>
        ) : null}
        <label className="dv-check">
          <input
            type="checkbox"
            checked={settings.transcribe}
            disabled={!speechAvailable}
            onChange={(e) => onChange({ transcribe: e.target.checked })}
          />
          <span>
            Transcribe clear calls as they happen
            {!speechAvailable ? <small className="dv-muted"> (the speech service isn&apos;t available on this API)</small> : null}
          </span>
        </label>
        <div className="dv-row">
          <button type="button" className="btn-secondary" onClick={() => onChange({ ...DEFAULT_SETTINGS, protocol: settings.protocol })}>
            Reset to defaults
          </button>
          <button type="button" className="btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Sheet>
  );
}
