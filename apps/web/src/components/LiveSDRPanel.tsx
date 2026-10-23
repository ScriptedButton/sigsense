"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  configureTuner,
  fetchBlePreset,
  fetchClasses,
  fetchDevices,
  wsUrl,
  type BleDecodeResult,
} from "@/lib/api";
import { WaterfallCanvas } from "@/components/WaterfallCanvas";
import { ResultPanel } from "@/components/ResultPanel";
import { BlePacketList, type BlePacket } from "@/components/BlePacketList";
import type { AnalyzeResult } from "@/lib/api";

type LivePrediction = AnalyzeResult;

export function LiveSDRPanel() {
  const [running, setRunning] = useState(false);
  const [frame, setFrame] = useState<number[][] | null>(null);
  const [prediction, setPrediction] = useState<LivePrediction | null>(null);
  const [ble, setBle] = useState<BleDecodeResult | null>(null);
  const [bleHistory, setBleHistory] = useState<BlePacket[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [classes, setClasses] = useState<string[]>([]);
  const [devices, setDevices] = useState<{ driver?: string; label?: string }[]>(
    [],
  );
  const [centerMhz, setCenterMhz] = useState(2402);
  const [sampleRateMhz, setSampleRateMhz] = useState(4);
  const [gain, setGain] = useState(32);
  const [demo, setDemo] = useState(true);
  const [demoClass, setDemoClass] = useState("Bluetooth-LE");
  const [deviceArgs, setDeviceArgs] = useState("driver=hackrf");
  const [bleDecode, setBleDecode] = useState(true);
  const [bleHop, setBleHop] = useState(true);
  const [backend, setBackend] = useState("demo");
  const [rfStatus, setRfStatus] = useState<{
    backend?: string;
    demo?: boolean;
    rf_power_dbfs?: number;
    iq_samples?: number;
    center_freq_hz?: number;
    ble_hop?: boolean;
  } | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    fetchClasses()
      .then(setClasses)
      .catch(() => setClasses([]));
    fetchDevices()
      .then((d) => setDevices(d.devices ?? []))
      .catch(() => setDevices([]));
  }, []);

  const stop = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setRunning(false);
  }, []);

  const applyBleHackrfPreset = async (channelFreqMhz = 2402) => {
    try {
      const preset = await fetchBlePreset();
      const ch =
        preset.channels.find(
          (c) => Math.round(c.center_freq_hz / 1e6) === channelFreqMhz,
        ) ?? preset.channels[0];
      setDeviceArgs(preset.device_args);
      setSampleRateMhz(preset.sample_rate_hz / 1e6);
      setGain(preset.gain_db);
      setBleDecode(true);
      setBleHop(true);
      setDemoClass(preset.demo_class);
      setCenterMhz(ch.center_freq_hz / 1e6);
      // Keep demo on until user unchecks — safer if HackRF not plugged in
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const start = useCallback(async () => {
    setError(null);
    try {
      const tuned = await configureTuner({
        center_freq_hz: centerMhz * 1e6,
        sample_rate_hz: sampleRateMhz * 1e6,
        gain_db: gain,
        demo,
        demo_class: demoClass,
        device_args: deviceArgs,
        ble_decode: bleDecode,
        ble_hop: bleHop,
      });
      setBackend(String(tuned.backend ?? (tuned.demo ? "demo" : "unknown")));
      if (!demo && tuned.demo) {
        setError(
          `HackRF did not stay open — fell back to ${tuned.backend}. Check USB / close other SDR apps.`,
        );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }

    stop();
    const ws = new WebSocket(wsUrl("/live"));
    wsRef.current = ws;
    ws.onopen = () => setRunning(true);
    ws.onerror = () => setError("WebSocket error — is the API running?");
    ws.onclose = () => setRunning(false);
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "frame") {
          setFrame(msg.preview);
          const p = msg.prediction;
          setPrediction({
            label: p.label,
            confidence: p.confidence,
            is_unknown: p.is_unknown,
            alternatives: p.alternatives ?? [],
            neighbors: p.neighbors ?? [],
            meta: p.meta ?? {},
          });
          if (msg.tuner) {
            if (msg.tuner.backend) setBackend(String(msg.tuner.backend));
            setRfStatus({
              backend: msg.tuner.backend,
              demo: msg.tuner.demo,
              rf_power_dbfs: msg.tuner.rf_power_dbfs,
              iq_samples: msg.tuner.iq_samples,
              center_freq_hz: msg.tuner.center_freq_hz,
              ble_hop: msg.tuner.ble_hop,
            });
            if (msg.tuner.ble_hop && msg.tuner.center_freq_hz != null) {
              setCenterMhz(msg.tuner.center_freq_hz / 1e6);
            }
          }
          if (msg.ble) {
            setBle(msg.ble);
            const incoming = (msg.ble.packets ?? []) as BlePacket[];
            if (incoming.length) {
              setBleHistory((prev) => {
                const map = new Map(
                  prev.map((p) => [`${p.header_hex}:${p.payload_hex}`, p]),
                );
                for (const p of incoming) {
                  if (!p.crc_ok) continue;
                  map.set(`${p.header_hex}:${p.payload_hex}`, p);
                }
                return Array.from(map.values()).slice(-80);
              });
            }
          }
        }
      } catch {
        /* ignore */
      }
    };
  }, [
    centerMhz,
    sampleRateMhz,
    gain,
    demo,
    demoClass,
    deviceArgs,
    bleDecode,
    bleHop,
    stop,
  ]);

  useEffect(() => () => stop(), [stop]);

  const pushTune = () => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "tune",
          // When hopping, leave center freq to the server hop loop
          ...(bleHop ? {} : { center_freq_hz: centerMhz * 1e6 }),
          sample_rate_hz: sampleRateMhz * 1e6,
          gain_db: gain,
          demo,
          demo_class: demoClass,
          device_args: deviceArgs,
          ble_decode: bleDecode,
          ble_hop: bleHop,
        }),
      );
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
      <div className="space-y-4">
        <WaterfallCanvas frame={frame} />
        <div className="grid gap-3 border border-[var(--line)] bg-[var(--panel)] p-4 sm:grid-cols-2">
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <button
              type="button"
              className="border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--foreground)] hover:border-[var(--signal)]"
              onClick={() => applyBleHackrfPreset(2402)}
            >
              HackRF BLE ADV37
            </button>
            <button
              type="button"
              className="border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--foreground)] hover:border-[var(--signal)]"
              onClick={() => applyBleHackrfPreset(2426)}
            >
              ADV38
            </button>
            <button
              type="button"
              className="border border-[var(--line)] px-3 py-1.5 text-xs text-[var(--foreground)] hover:border-[var(--signal)]"
              onClick={() => applyBleHackrfPreset(2480)}
            >
              ADV39
            </button>
          </div>
          <Field label="Center (MHz)">
            <input
              type="number"
              step="0.1"
              value={centerMhz}
              onChange={(e) => setCenterMhz(Number(e.target.value))}
              onBlur={pushTune}
              className="field"
            />
          </Field>
          <Field label="Sample rate (MHz)">
            <input
              type="number"
              step="0.001"
              value={sampleRateMhz}
              onChange={(e) => setSampleRateMhz(Number(e.target.value))}
              onBlur={pushTune}
              className="field"
            />
          </Field>
          <Field label="Gain (dB)">
            <input
              type="number"
              value={gain}
              onChange={(e) => setGain(Number(e.target.value))}
              onBlur={pushTune}
              className="field"
            />
          </Field>
          <Field label="Soapy device args">
            <input
              value={deviceArgs}
              onChange={(e) => setDeviceArgs(e.target.value)}
              onBlur={pushTune}
              className="field"
              placeholder="driver=hackrf"
            />
          </Field>
          <Field label="Demo class">
            <select
              value={demoClass}
              onChange={(e) => {
                setDemoClass(e.target.value);
                setDemo(true);
              }}
              onBlur={pushTune}
              className="field"
            >
              {(classes.length ? classes : ["Bluetooth-LE", "WFM"]).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex items-center gap-2 text-sm text-[var(--muted)]">
            <input
              type="checkbox"
              checked={bleDecode}
              onChange={(e) => setBleDecode(e.target.checked)}
              onBlur={pushTune}
            />
            Frame BLE 1M packets
          </label>
          <label className="flex items-center gap-2 text-sm text-[var(--muted)]">
            <input
              type="checkbox"
              checked={bleHop}
              onChange={(e) => setBleHop(e.target.checked)}
              onBlur={pushTune}
            />
            Hop ADV 37/38/39
          </label>
          <label className="flex items-center gap-2 text-sm text-[var(--muted)] sm:col-span-2">
            <input
              type="checkbox"
              checked={demo}
              onChange={(e) => setDemo(e.target.checked)}
              onBlur={pushTune}
            />
            Demo mode (synthetic IQ). Uncheck to use HackRF.
          </label>
          <p className="text-xs text-[var(--muted)] sm:col-span-2">
            Backend:{" "}
            <span
              className={
                backend === "hackrf"
                  ? "text-emerald-400"
                  : "text-amber-300"
              }
            >
              {backend}
            </span>
            {devices.length > 0
              ? ` · Devices: ${devices.map((d) => d.label || d.driver).join(", ")}`
              : ""}
          </p>
          <div className="flex gap-2 sm:col-span-2">
            {!running ? (
              <button
                type="button"
                onClick={start}
                className="bg-[var(--signal)] px-4 py-2 text-sm font-medium text-[var(--ink)]"
              >
                Start stream
              </button>
            ) : (
              <button
                type="button"
                onClick={stop}
                className="border border-[var(--line)] px-4 py-2 text-sm text-[var(--foreground)]"
              >
                Stop
              </button>
            )}
          </div>
        </div>
        <BlePacketList
          result={ble}
          history={bleHistory}
          rfStatus={rfStatus}
          onClearHistory={() => setBleHistory([])}
        />
      </div>
      <ResultPanel result={prediction} error={error} />
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-[var(--muted)]">
      {label}
      {children}
    </label>
  );
}
