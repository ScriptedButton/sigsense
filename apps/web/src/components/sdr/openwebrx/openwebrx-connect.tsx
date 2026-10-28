"use client";

import { useEffect, useId, useState } from "react";

import {
  useOpenWebRxSession,
  useRadioController,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import {
  DEFAULT_OWRX_URL,
  OWRX_URL_STORAGE_KEY,
} from "@/lib/sdr/openwebrx/protocol";

type OpenWebRxConnectProps = {
  align: "end" | "center";
};

export function OpenWebRxConnect({ align }: OpenWebRxConnectProps) {
  const controller = useRadioController();
  const state = useSdrState();
  const session = useOpenWebRxSession();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(DEFAULT_OWRX_URL);
  const fieldId = useId();
  const busy = state.status === "starting" || state.status === "stopping";
  const playing = state.status === "playing" || state.status === "starting";

  useEffect(() => {
    const saved = window.localStorage.getItem(OWRX_URL_STORAGE_KEY);
    if (saved) setUrl(saved);
  }, []);

  if (playing) {
    if (state.connection !== "openwebrx" || !session || session.profiles.length < 2) {
      return null;
    }
    return (
      <label className="owrx-profile-label">
        <span className="sr-only">OpenWebRX+ profile</span>
        <select
          className="owrx-profile"
          value={session.profileId ?? session.profiles[0]?.id ?? ""}
          disabled={busy}
          onChange={(event) => controller.selectOpenWebRxProfile(event.target.value)}
        >
          {session.profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name}
            </option>
          ))}
        </select>
      </label>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn-secondary"
        disabled={busy}
        onClick={() => setOpen(true)}
      >
        OpenWebRX+
      </button>
    );
  }

  const submit = () => {
    const next = url.trim();
    if (!next) return;
    window.localStorage.setItem(OWRX_URL_STORAGE_KEY, next);
    void controller.connectOpenWebRx(next);
  };

  return (
    <form
      className={`owrx-form ${align === "center" ? "owrx-form-center" : "owrx-form-end"}`}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label className="owrx-url-label" htmlFor={fieldId}>
        <span className="sr-only">OpenWebRX+ address</span>
        <input
          id={fieldId}
          className="owrx-url"
          value={url}
          inputMode="url"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder="localhost:8073"
          aria-label="OpenWebRX+ address"
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>
      <button type="submit" className="btn-primary" disabled={busy || !url.trim()}>
        Connect server
      </button>
      <button
        type="button"
        className="btn-secondary"
        disabled={busy}
        onClick={() => setOpen(false)}
      >
        Cancel
      </button>
    </form>
  );
}
