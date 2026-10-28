"use client";

import { useEffect, useId, useState } from "react";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import {
  DEFAULT_RTL_SERVER_URL,
  RTL_SERVER_URL_STORAGE_KEY,
} from "@/lib/sdr/rtl-tcp/protocol";

type RtlTcpConnectProps = {
  align: "end" | "center";
};

export function RtlTcpConnect({ align }: RtlTcpConnectProps) {
  const controller = useRadioController();
  const state = useSdrState();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(DEFAULT_RTL_SERVER_URL);
  const fieldId = useId();
  const busy = state.status === "starting" || state.status === "stopping";
  const playing = state.status === "playing" || state.status === "starting";

  useEffect(() => {
    const saved = window.localStorage.getItem(RTL_SERVER_URL_STORAGE_KEY);
    if (saved) setUrl(saved);
  }, []);

  if (playing) return null;

  if (!open) {
    return (
      <button
        type="button"
        className="btn-secondary"
        disabled={busy}
        onClick={() => setOpen(true)}
      >
        RTL server
      </button>
    );
  }

  const submit = () => {
    const next = url.trim();
    if (!next) return;
    window.localStorage.setItem(RTL_SERVER_URL_STORAGE_KEY, next);
    void controller.connectRtlServer(next);
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
        <span className="sr-only">RTL server address</span>
        <input
          id={fieldId}
          className="owrx-url"
          value={url}
          inputMode="url"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder="localhost:8081"
          aria-label="RTL server address"
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>
      <button type="submit" className="btn-primary" disabled={busy || !url.trim()}>
        Connect IQ
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
