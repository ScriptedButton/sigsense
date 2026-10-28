"use client";

import { useEffect, useId, useState } from "react";

import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import {
  DEFAULT_KIWI_URL,
  KIWI_PASSWORD_STORAGE_KEY,
  KIWI_URL_STORAGE_KEY,
} from "@/lib/sdr/kiwi/protocol";

type KiwiConnectProps = {
  align: "end" | "center";
};

export function KiwiConnect({ align }: KiwiConnectProps) {
  const controller = useRadioController();
  const state = useSdrState();
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState(DEFAULT_KIWI_URL);
  const [password, setPassword] = useState("");
  const fieldId = useId();
  const passwordId = useId();
  const busy = state.status === "starting" || state.status === "stopping";
  const playing = state.status === "playing" || state.status === "starting";

  useEffect(() => {
    const saved = window.localStorage.getItem(KIWI_URL_STORAGE_KEY);
    if (saved) setUrl(saved);
    const savedPw = window.localStorage.getItem(KIWI_PASSWORD_STORAGE_KEY);
    if (savedPw) setPassword(savedPw);
  }, []);

  if (playing) {
    return null;
  }

  if (!open) {
    return (
      <button
        type="button"
        className="btn-secondary"
        disabled={busy}
        onClick={() => setOpen(true)}
      >
        KiwiSDR
      </button>
    );
  }

  const submit = () => {
    const next = url.trim();
    if (!next) return;
    window.localStorage.setItem(KIWI_URL_STORAGE_KEY, next);
    if (password) {
      window.localStorage.setItem(KIWI_PASSWORD_STORAGE_KEY, password);
    } else {
      window.localStorage.removeItem(KIWI_PASSWORD_STORAGE_KEY);
    }
    void controller.connectKiwi(next, password);
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
        <span className="sr-only">KiwiSDR address</span>
        <input
          id={fieldId}
          className="owrx-url"
          value={url}
          inputMode="url"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder="host:8073"
          aria-label="KiwiSDR address"
          onChange={(event) => setUrl(event.target.value)}
        />
      </label>
      <label className="owrx-url-label" htmlFor={passwordId}>
        <span className="sr-only">KiwiSDR password</span>
        <input
          id={passwordId}
          className="owrx-url"
          type="password"
          value={password}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder="password (optional)"
          aria-label="KiwiSDR password"
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      <button type="submit" className="btn-primary" disabled={busy || !url.trim()}>
        Connect Kiwi
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
