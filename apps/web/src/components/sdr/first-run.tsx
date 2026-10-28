"use client";

import { useCallback, useEffect, useState } from "react";

import { useHackrf } from "@/components/HackrfPanel";
import { KiwiConnect } from "@/components/sdr/kiwi/kiwi-connect";
import { OpenWebRxConnect } from "@/components/sdr/openwebrx/openwebrx-connect";
import { useRadioController } from "@/components/sdr/sdr-provider";
import {
  API_BASE,
  DEFAULT_API_BASE_URL,
  apiBaseOverride,
  setApiBaseOverride,
} from "@/lib/api";

type Probe =
  | { state: "checking" }
  | { state: "ok"; backend: string; demo: boolean }
  | { state: "down"; reason: string };

/** Absolute URL for display and for mixed-content checks. */
function resolvedBase(): string {
  try {
    return new URL(API_BASE, window.location.origin)
      .toString()
      .replace(/\/+$/, "");
  } catch {
    return API_BASE;
  }
}

/** Asks the API for its tuner state; a short timeout keeps a bad address quick. */
async function probeApi(signal: AbortSignal): Promise<Probe> {
  try {
    const res = await fetch(`${API_BASE}/sdr/tuner`, { signal });
    if (!res.ok)
      return { state: "down", reason: `The API answered ${res.status}.` };
    const tuner = (await res.json()) as { backend?: string; demo?: boolean };
    return {
      state: "ok",
      backend: tuner.backend ?? "unknown",
      demo: Boolean(tuner.demo),
    };
  } catch (err) {
    if (
      signal.aborted &&
      !(err instanceof DOMException && err.name === "TimeoutError")
    ) {
      return { state: "checking" };
    }
    const base = resolvedBase();
    const mixed =
      window.location.protocol === "https:" && base.startsWith("http:")
        ? " This page is HTTPS but the API address is plain HTTP, which the browser blocks."
        : "";
    return {
      state: "down",
      reason: `Can't reach the SigSense API at ${base}. Are you on the same network as the computer running it?${mixed}`,
    };
  }
}

/**
 * Phone first-run: what this app does, whether it can reach the API, which
 * source to use, and one big button. The API address can be changed here for
 * LAN use (saved on this device).
 */
export function FirstRun() {
  const hackrf = useHackrf();
  const controller = useRadioController();
  const [probe, setProbe] = useState<Probe>({ state: "checking" });
  const [editing, setEditing] = useState(false);
  // This card only renders client-side (the receiver is loaded with ssr:false).
  const [address, setAddress] = useState(() =>
    typeof window === "undefined" ? "" : (apiBaseOverride() ?? ""),
  );

  /** Re-check on demand (a tap), showing the pending state meanwhile. */
  const check = useCallback(() => {
    setProbe({ state: "checking" });
    void probeApi(AbortSignal.timeout(4000)).then((result) => {
      if (result.state !== "checking") setProbe(result);
    });
  }, []);

  // First check on mount: state is already "checking", so only the result sets it.
  useEffect(() => {
    let live = true;
    void probeApi(AbortSignal.timeout(4000)).then((result) => {
      if (live && result.state !== "checking") setProbe(result);
    });
    return () => {
      live = false;
    };
  }, []);

  const saveAddress = () => {
    setApiBaseOverride(address);
    // API_BASE is read once at load, so a reload applies the new address.
    window.location.reload();
  };

  return (
    <section className="first-run" aria-label="Get started">
      <h2 className="first-run-title">Listen with your HackRF</h2>
      <p className="first-run-lead">
        The HackRF plugs into the computer running SigSense. This phone tunes it
        and plays what it hears over your network.
      </p>

      <p
        className={`first-run-status first-run-status-${probe.state}`}
        role="status"
        aria-live="polite"
      >
        {probe.state === "checking" && "Checking the SigSense API…"}
        {probe.state === "ok" &&
          `API connected · ${probe.demo ? "demo source" : probe.backend === "hackrf" ? "HackRF ready" : probe.backend}`}
        {probe.state === "down" && probe.reason}
      </p>

      <div className="first-run-source" role="group" aria-label="Signal source">
        <button
          type="button"
          className={`mode-chip ${!hackrf.demo ? "mode-chip-active" : ""}`}
          aria-pressed={!hackrf.demo}
          onClick={() => hackrf.setDemo(false)}
        >
          HackRF hardware
        </button>
        <button
          type="button"
          className={`mode-chip ${hackrf.demo ? "mode-chip-active" : ""}`}
          aria-pressed={hackrf.demo}
          onClick={() => hackrf.setDemo(true)}
        >
          Demo signals
        </button>
      </div>

      <button
        type="button"
        className="btn-primary first-run-go"
        disabled={hackrf.connecting || probe.state === "down"}
        onClick={() => {
          controller.prepareExternalAudio(); // must happen inside this tap
          hackrf.connect();
        }}
      >
        {hackrf.connecting
          ? "Connecting…"
          : hackrf.demo
            ? "Start demo signals"
            : "Connect HackRF"}
      </button>

      <div className="first-run-alt">
        <OpenWebRxConnect align="center" />
        <KiwiConnect align="center" />
      </div>

      <div className="first-run-links">
        <button type="button" className="first-run-link" onClick={check}>
          Check again
        </button>
        <button
          type="button"
          className="first-run-link"
          aria-expanded={editing}
          onClick={() => setEditing((open) => !open)}
        >
          API address
        </button>
      </div>

      {editing && (
        <form
          className="first-run-address"
          onSubmit={(e) => {
            e.preventDefault();
            saveAddress();
          }}
        >
          <label htmlFor="api-address">
            Address of the SigSense API, e.g. http://192.168.1.20:8000
          </label>
          <input
            id="api-address"
            type="url"
            inputMode="url"
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            placeholder={DEFAULT_API_BASE_URL}
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
          <div className="first-run-address-actions">
            <button type="submit" className="btn-primary">
              Save and reload
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setApiBaseOverride(null);
                window.location.reload();
              }}
            >
              Use default
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
