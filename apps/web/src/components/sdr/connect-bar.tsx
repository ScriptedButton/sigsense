"use client";

import { useEffect, useState } from "react";

import { relaxedSimdSupported } from "@/lib/sdr/dsp";
import { useHackrf } from "@/components/HackrfPanel";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { KiwiConnect } from "@/components/sdr/kiwi/kiwi-connect";
import { OpenWebRxConnect } from "@/components/sdr/openwebrx/openwebrx-connect";
import { RtlTcpConnect } from "@/components/sdr/rtl-tcp/rtl-tcp-connect";
import type { ConnectionMode, RadioStatus } from "@/lib/sdr/types";

export function ConnectBar() {
  const controller = useRadioController();
  const state = useSdrState();
  const hackrf = useHackrf();
  const [mounted, setMounted] = useState(false);
  const playing = state.status === "playing" || state.status === "starting";
  const busy = state.status === "starting" || state.status === "stopping";

  useEffect(() => {
    setMounted(true);
  }, []);

  // Keep SSR + first client paint identical; enable WebUSB UI after mount.
  const usbReady = mounted && state.webUsbSupported;
  // RTL-SDR, RTL server and Demo run the browser DSP; HackRF/OpenWebRX do not.
  const dspReady = mounted && relaxedSimdSupported();
  const showUsbWarning = mounted && !state.webUsbSupported;

  return (
    <div className="connect-bar">
      {!playing ? (
        <>
          {dspReady && (
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !usbReady}
              onClick={() => void controller.connectDevice()}
            >
              Connect
            </button>
          )}
          {dspReady && <RtlTcpConnect align="end" />}
          <OpenWebRxConnect align="end" />
          <KiwiConnect align="end" />
          <button
            type="button"
            // The only browser-DSP-free source with hardware: make it the main action.
            className={mounted && !dspReady ? "btn-primary" : "btn-secondary"}
            disabled={busy || hackrf.connecting}
            title="Stream from a HackRF through the SigSense API"
            onClick={hackrf.connect}
          >
            {hackrf.demo ? "HackRF demo" : mounted && !dspReady ? "Connect HackRF" : "HackRF"}
          </button>
          {dspReady && (
            <button
              type="button"
              className="btn-secondary"
              disabled={busy}
              onClick={() => void controller.connectDemo()}
            >
              Demo
            </button>
          )}
        </>
      ) : (
        <>
          <button
            type="button"
            className="btn-secondary connect-stop"
            disabled={busy}
            onClick={() => void controller.stop()}
          >
            Stop
          </button>
          <OpenWebRxConnect align="end" />
        </>
      )}

      <StatusPill
        state={state.status}
        connection={state.connection}
        synthetic={hackrf.synthetic}
      />

      {mounted && !dspReady && (
        <span className="connect-bar-warn">
          RTL-SDR and Demo need desktop Chrome
        </span>
      )}
      {dspReady && showUsbWarning && (
        <span className="connect-bar-warn">
          No WebUSB — try Demo
        </span>
      )}
    </div>
  );
}

function StatusPill({
  state,
  connection,
  synthetic,
}: {
  state: RadioStatus;
  connection: ConnectionMode;
  synthetic: boolean;
}) {
  const label =
    state === "playing"
      ? connection === "demo"
        ? "Demo live"
        : connection === "openwebrx"
          ? "OpenWebRX"
          : connection === "kiwi"
            ? "KiwiSDR"
            : connection === "rtltcp"
              ? "RTL server"
              : connection === "hackrf"
                ? synthetic
                  ? "HackRF demo"
                  : "HackRF live"
                : "Receiving"
      : state === "starting"
        ? "Starting…"
        : state === "stopping"
          ? "Stopping…"
          : state === "error"
            ? "Error"
            : "Idle";

  const tone =
    state === "playing"
      ? "status-pill-live"
      : state === "error"
        ? "status-pill-error"
        : "status-pill-idle";

  return (
    <span className={`status-pill ${tone}`}>
      <span
        className={`status-pill-dot ${
          state === "playing"
            ? "status-pill-dot-live"
            : state === "error"
              ? "status-pill-dot-error"
              : ""
        }`}
        aria-hidden
      />
      {label}
    </span>
  );
}
