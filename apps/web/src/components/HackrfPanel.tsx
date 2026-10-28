"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  API_BASE,
  configureTuner,
  fetchClasses,
  signalRequest,
  wsUrl,
  type LiveFrame,
  type SavedSignal,
  type SignalTrack,
} from "@/lib/api";
import type { ExternalDemod } from "@/lib/sdr/radio-controller";
import type { TranscribeTapConfig } from "@/lib/sdr/remote-transcription-engine";
import type { BackendTrack } from "@/lib/sdr/signal-id/backend-tracks";
import {
  getUpconverter,
  inUpconverterRange,
  readUpconverter,
  subscribeUpconverter,
  writeUpconverter,
  type UpconverterId,
} from "@/lib/sdr/upconverters";
import { useRadioController } from "@/components/sdr/sdr-provider";
import { BlePacketList, type BlePacket } from "./BlePacketList";
import { SignalInvestigationPanel } from "@/components/spectrum/SignalInvestigationPanel";
import { attachDeviceIdentity, BleSessionCapture, mergeRecentPackets } from "@/lib/bleGroups";
import { investigationFromSelection } from "@/lib/signalSelection";

const button =
  "rounded-md border border-[var(--line)] px-3 py-2 text-xs transition hover:border-[var(--signal)] disabled:opacity-40";
const primary =
  "rounded-md bg-[var(--signal)] px-4 py-2 text-sm font-medium text-[var(--ink)] disabled:opacity-40";
const panel = "rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4";
const mhz = (hz: number) => (hz / 1e6).toFixed(3);
/** Lowest sample rate the HackRF accepts. */
const demodMessage = (cfg: ExternalDemod) => ({
  type: "config",
  mode: cfg.scheme,
  bandwidth_hz: cfg.bandwidthHz,
  offset_hz: cfg.offsetHz,
});
const HACKRF_MIN_RATE = 2e6;
const SOURCE_KEY = "sigsense.receiver-source";
const sourceListeners = new Set<() => void>();
const subscribeSource = (listener: () => void) => {
  sourceListeners.add(listener);
  return () => sourceListeners.delete(listener);
};
const readDemo = () => {
  try {
    return localStorage.getItem(SOURCE_KEY) === "demo";
  } catch {
    return false;
  }
};
const writeSource = (nextDemo: boolean) => {
  try {
    localStorage.setItem(SOURCE_KEY, nextDemo ? "demo" : "hackrf");
  } catch {
    /* The choice still applies for this visit when storage is blocked. */
  }
  sourceListeners.forEach((listener) => listener());
};
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Rarely changing controls: safe for the whole receiver to subscribe to. */
type HackrfControls = {
  /** Tracks currently observed, for scope markers in the shared display. */
  tracks: SignalTrack[];
  /** Start streaming from the HackRF (or its demo source) at the shared tuning. */
  connect: () => void;
  connecting: boolean;
  /** True when the backend source is the synthetic demo rather than hardware. */
  demo: boolean;
  /** Choose the backend source for the next connect (remembered on this device). */
  setDemo: (demo: boolean) => void;
  /** True while the stream on screen is synthetic (chosen, or HackRF fell back). */
  synthetic: boolean;
  /** HackRF front-end stages: LNA (8 dB steps), VGA (2 dB steps), RF amp. */
  gain: HackrfGain;
  setGain: (patch: Partial<HackrfGain>) => void;
  /**
   * Retune frequency, span and gain in one step (one stream restart), pinning
   * the channel so Bluetooth hopping cannot pull it away.
   */
  applyPreset: (preset: HackrfPreset) => void;
  /** Upconverter in front of the HackRF (remembered on this device). */
  upconverter: UpconverterId;
  /** Switch upconverter, retuning into its range if the current frequency is outside it. */
  setUpconverter: (id: UpconverterId) => void;
  /** Problem worth interrupting for (API down, HackRF missing, stream failed). */
  error: string;
  /** Informational message (audio unavailable, capture saved, ...). */
  notice: string;
  dismiss: () => void;
};

/** Changes at stream rate; only the tab bodies subscribe. */
type HackrfNodes = {
  /** Receiver, scanner, capture library and transmit controls (Radio tab). */
  radio: ReactNode;
  /** Bluetooth listening, device/message list and investigation (Bluetooth tab). */
  bluetooth: ReactNode;
};

export type HackrfPreset = {
  frequencyHz: number;
  sampleRate: number;
  gain?: Partial<HackrfGain>;
};

export type HackrfGain = { lna: number; vga: number; amp: boolean };

/** Matches the btle_rx preset (-l 16 -g 16, amp off). */
const DEFAULT_GAIN: HackrfGain = { lna: 16, vga: 16, amp: false };

const HackrfContext = createContext<HackrfControls | null>(null);
const HackrfNodesContext = createContext<HackrfNodes | null>(null);

export function useHackrf(): HackrfControls {
  const view = useContext(HackrfContext);
  if (!view) throw new Error("useHackrf must be used within HackrfProvider");
  return view;
}

function useHackrfNodes(): HackrfNodes {
  const nodes = useContext(HackrfNodesContext);
  if (!nodes) throw new Error("HackRF sections must be inside HackrfProvider");
  return nodes;
}

export function HackrfRadioSection() {
  return <>{useHackrfNodes().radio}</>;
}

export function HackrfBluetoothSection() {
  return <>{useHackrfNodes().bluetooth}</>;
}

/**
 * Owns the HackRF API session (stream, BLE capture, library) and feeds the
 * shared receiver display. Its controls are exposed as sections so the Radio
 * and Bluetooth tabs can each place the part that belongs to them.
 */
export function HackrfProvider({ children }: { children: ReactNode }) {
  const controller = useRadioController();
  const [frame, setFrame] = useState<LiveFrame>();
  const [running, setRunning] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [mode, setMode] = useState<"live" | "playback">("live");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [classes, setClasses] = useState<string[]>(["Bluetooth-LE"]);
  const demo = useSyncExternalStore(subscribeSource, readDemo, () => false);
  const upconverter = useSyncExternalStore(
    subscribeUpconverter,
    readUpconverter,
    (): UpconverterId => "none",
  );
  const [demoClass, setDemoClass] = useState("Bluetooth-LE");
  // Off until Bluetooth listening is requested; hopping retunes away from the
  // universal frequency.
  const [decode, setDecode] = useState(false),
    [hop, setHop] = useState(false);
  const [scanStart, setScanStart] = useState(2402),
    [scanStop, setScanStop] = useState(2480),
    [scanStep, setScanStep] = useState(2),
    [dwell, setDwell] = useState(150),
    [sensitivity, setSensitivity] = useState(10);
  const [gain, setGainState] = useState<HackrfGain>(DEFAULT_GAIN);
  const gainTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [swept, setSwept] = useState(false);
  const [saved, setSaved] = useState<SavedSignal[]>([]);
  const [captureName, setCaptureName] = useState("");
  const [selectedTrackId, setSelectedTrackId] = useState<string>();
  const [history, setHistory] = useState<BlePacket[]>([]);
  const [deviceRevision, setDeviceRevision] = useState(0);
  const [tx, setTx] = useState<SavedSignal>();
  const [txGain, setTxGain] = useState(10),
    [repeats, setRepeats] = useState(1),
    [amp, setAmp] = useState(false);
  const [renaming, setRenaming] = useState(""),
    [newName, setNewName] = useState("");
  const socket = useRef<WebSocket | null>(null);
  const sweptRef = useRef(false);
  const earlySpectrum = useRef(false);
  const audioSocket = useRef<WebSocket | null>(null);
  /** Last speech request, replayed when the audio socket (re)opens. */
  const transcribeRequest = useRef<TranscribeTapConfig | null>(null);
  const closeAudio = useCallback(() => {
    const old = audioSocket.current;
    audioSocket.current = null;
    old?.close();
  }, []);
  const setSweptMode = (value: boolean) => {
    sweptRef.current = value;
    setSwept(value);
  };
  const pendingFrame = useRef<LiveFrame | null>(null);
  const pendingPackets = useRef<BlePacket[]>([]);
  const sessionPackets = useRef(new BleSessionCapture());
  const getDevicePackets = useCallback((key: string) => sessionPackets.current.forDevice(key), []);
  const updateTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [session, setSession] = useState(0);
  const flush = useCallback(() => {
    updateTimer.current = null;
    if (pendingFrame.current) setFrame(pendingFrame.current);
    if (pendingPackets.current.length) {
      const packets = pendingPackets.current;
      setHistory((previous) => mergeRecentPackets(previous, packets));
    }
    pendingFrame.current = null;
    pendingPackets.current = [];
  }, []);
  const clearHistory = () => {
    pendingPackets.current = [];
    setHistory([]);
  };
  const refresh = useCallback(async () => {
    const data = await signalRequest<{ items: SavedSignal[] }>("/signals");
    setSaved(data.items);
  }, []);
  useEffect(() => {
    fetchClasses()
      .then(setClasses)
      .catch(() => {});
    signalRequest<{ items: SavedSignal[] }>("/signals")
      .then((data) => setSaved(data.items))
      .catch((e) => setError(message(e)));
  }, []);
  const stop = useCallback(() => {
    const old = socket.current;
    socket.current = null;
    old?.close();
    closeAudio();
    if (updateTimer.current !== null) clearTimeout(updateTimer.current);
    updateTimer.current = null;
    flush();
    setConnecting(false);
    setRunning(false);
    controller.detachExternal();
  }, [flush, controller, closeAudio]);
  useEffect(() => () => stop(), [stop]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  /** Demodulated audio arrives on its own socket as 48 kHz mono PCM. */
  const openAudio = () => {
    closeAudio();
    const aws = new WebSocket(wsUrl("/live/audio"));
    aws.binaryType = "arraybuffer";
    audioSocket.current = aws;
    aws.onopen = () => {
      if (audioSocket.current !== aws) return;
      aws.send(JSON.stringify(demodMessage(controller.getExternalDemod())));
      const request = transcribeRequest.current;
      if (request?.enabled) aws.send(JSON.stringify({ type: "transcribe", ...request }));
    };
    aws.onmessage = (event) => {
      if (audioSocket.current !== aws) return;
      if (typeof event.data === "string") {
        try {
          const info = JSON.parse(event.data) as { type?: string; reason?: string };
          if (info.type === "unavailable")
            setNotice(info.reason || "No audio is available from this source");
          // Server-side speech: transcript events ride on this same socket.
          else if (info.type === "partial" || info.type === "final" || info.type === "transcript_status")
            controller.pushExternalTranscript(info as Parameters<typeof controller.pushExternalTranscript>[0]);
        } catch {
          /* ignore malformed control text */
        }
        return;
      }
      controller.pushExternalAudio(new Int16Array(event.data as ArrayBuffer), 48_000);
    };
  };
  const connect = (
    path: string,
    nextMode: "live" | "playback",
    init: { frequencyHz: number; sampleRate: number } = {
      frequencyHz: universalTuning().center_freq_hz,
      sampleRate: universalTuning().sample_rate_hz,
    },
  ) => {
    stop();
    setMode(nextMode);
    setFrame(undefined);
    setSelectedTrackId(undefined);
    setHistory([]);
    sessionPackets.current.clear();
    setSession((value) => value + 1);
    setConnecting(true);
    earlySpectrum.current = false;
    const ws = new WebSocket(wsUrl(path));
    socket.current = ws;
    ws.onopen = () => {
      if (socket.current === ws) {
        setConnecting(false);
        setRunning(true);
        void controller.attachExternal(
          {
            onTune: (hz) => tuneFromDisplay.current(hz),
            onSampleRate: (hz) => rateFromControls.current(hz),
            onTranscribe: (config) => {
              transcribeRequest.current = config.enabled ? config : null;
              const aws = audioSocket.current;
              if (aws?.readyState === WebSocket.OPEN)
                aws.send(JSON.stringify({ type: "transcribe", ...config }));
            },
            onDemod: (cfg) => {
              const aws = audioSocket.current;
              if (aws?.readyState === WebSocket.OPEN && transcribeRequest.current) {
                aws.send(JSON.stringify({
                  type: "transcribe_meta",
                  frequency_hz: controller.getState().frequencyHz + cfg.offsetHz,
                  mode: cfg.scheme,
                }));
              }
              if (aws?.readyState === WebSocket.OPEN)
                aws.send(JSON.stringify(demodMessage(cfg)));
            },
            onStop: () => stop(),
          },
          init,
        ).then(() => {
          if (nextMode === "live" && socket.current === ws) openAudio();
        });
      }
    };
    ws.onclose = () => {
      if (socket.current === ws) {
        if (updateTimer.current !== null) clearTimeout(updateTimer.current);
        flush();
        setConnecting(false);
        setRunning(false);
        socket.current = null;
        controller.detachExternal();
      }
    };
    ws.onerror = () => {
      if (socket.current === ws)
        setError("Stream connection failed. Check the API connection.");
    };
    ws.onmessage = (event) => {
      if (socket.current !== ws) return;
      try {
        const data = JSON.parse(event.data) as LiveFrame & { message?: string };
        if (data.type === "error") {
          setError(data.message || "The HackRF stream stopped");
          return;
        }
        if (data.type === "spectrum") {
          // Display row sent ahead of analysis so the waterfall stays fluid.
          earlySpectrum.current = true;
          const row = sweptRef.current && data.sweep ? data.sweep : data.spectrum;
          if (row)
            controller.pushExternalSpectrum(
              row.power_dbfs,
              row.start_hz,
              row.bin_hz,
              data.tuner?.sample_rate_hz,
            );
          return;
        }
        if (data.type !== "frame") return;
        // Recorded-IQ playback sends full frames only; live streams already
        // painted this frame's spectrum from the early message.
        if (!earlySpectrum.current) {
          const row = sweptRef.current && data.sweep ? data.sweep : data.spectrum;
          if (row)
            controller.pushExternalSpectrum(
              row.power_dbfs,
              row.start_hz,
              row.bin_hz,
              data.tuner?.sample_rate_hz,
            );
        }
        // Paint the newest spectrum at most 12.5 times/s, retaining packets
        // from every intermediate frame rather than queuing stale UI renders.
        pendingFrame.current = data;
        const received = attachDeviceIdentity(data.ble?.packets ?? [], data.ble?.connections ?? []);
        const accepted = sessionPackets.current.append(received);
        pendingPackets.current = mergeRecentPackets(pendingPackets.current, accepted);
        if (sessionPackets.current.full) {
          stop();
          setNotice("Session complete at 10,000 messages. Save device sets before starting a new listening session. All accepted messages are retained.");
          return;
        }
        if (updateTimer.current === null)
          updateTimer.current = setTimeout(flush, 80);
      } catch {
        setError("Received an invalid stream frame");
      }
    };
  };
  const tuneFromDisplay = useRef<(hz: number) => void>(() => {});
  // Retunes are latest-wins: a drag asks for a new centre every frame, and each
  // one restarts the stream, so only one request is in flight and only the
  // newest target is sent once it returns.
  const tuneQueue = useRef<{ inflight: boolean; next: number | null }>({
    inflight: false,
    next: null,
  });
  const sendTune = (hz: number) => {
    const queue = tuneQueue.current;
    queue.next = hz;
    if (queue.inflight) return;
    queue.inflight = true;
    void (async () => {
      while (queue.next !== null) {
        const target = queue.next;
        queue.next = null;
        try {
          await configureTuner({ center_freq_hz: target, ble_hop: false });
        } catch (e) {
          setError(message(e));
        }
      }
      queue.inflight = false;
    })();
  };
  const rateFromControls = useRef<(hz: number) => void>(() => {});
  useEffect(() => {
    // The universal controls drive the HackRF; recordings keep their own tuning.
    tuneFromDisplay.current = (hz: number) => {
      if (mode === "playback") return;
      setHop(false);
      setSweptMode(false);
      sendTune(hz);
    };
    rateFromControls.current = (hz: number) => {
      if (mode === "playback") return;
      run(() => configureTuner({ sample_rate_hz: hz }).then(() => {}));
    };
  });
  /** Frequency, span and gain come from the shared receiver controls. */
  const universalTuning = () => {
    const st = controller.getState();
    const converter = getUpconverter(upconverter);
    return {
      center_freq_hz:
        converter && !inUpconverterRange(converter, st.frequencyHz)
          ? converter.defaultHz
          : st.frequencyHz,
      sample_rate_hz: st.sampleRate >= HACKRF_MIN_RATE ? st.sampleRate : 4e6,
    };
  };
  const tuner = () => ({
    ...universalTuning(),
    lna_gain_db: gain.lna,
    vga_gain_db: gain.vga,
    amp_enable: gain.amp,
    demo,
    demo_class: demoClass,
    device_args: "driver=hackrf",
    ble_decode: decode,
    ble_hop: hop,
    upconverter,
  });
  const setUpconverter = (id: UpconverterId) => {
    const converter = getUpconverter(id);
    const hz = controller.getState().frequencyHz;
    writeUpconverter(id);
    // The landing frequency is valid with or without the converter, so the
    // retune and the converter change may reach the API in either order.
    if (converter && !inUpconverterRange(converter, hz)) {
      controller.setOffset(0);
      void controller.setFrequency(converter.defaultHz);
    }
    if (running && mode === "live") {
      setHop(false);
      setSweptMode(false);
      run(() => configureTuner({ upconverter: id }).then(() => {}));
    }
  };
  const connectHackrf = () => {
    // Audio output must be created inside this click's user gesture.
    controller.prepareExternalAudio();
    return run(async () => {
      if (running || connecting) return;
      await configureTuner(tuner());
      setSweptMode(false);
      connect("/live", "live");
    });
  };
  const setGain = (patch: Partial<HackrfGain>) => {
    const next = { ...gain, ...patch };
    setGainState(next);
    if (gainTimer.current !== null) clearTimeout(gainTimer.current);
    if (!(running && mode === "live")) return;
    // Each change restarts the stream, so wait for the slider to settle.
    gainTimer.current = setTimeout(() => {
      gainTimer.current = null;
      run(() =>
        configureTuner({
          lna_gain_db: next.lna,
          vga_gain_db: next.vga,
          amp_enable: next.amp,
        }).then(() => {}),
      );
    }, 250);
  };
  const applyPreset = (preset: HackrfPreset) => {
    if (mode === "playback") return;
    const next = { ...gain, ...preset.gain };
    setGainState(next);
    setHop(false);
    controller.setOffset(0);
    run(async () => {
      await configureTuner({
        center_freq_hz: preset.frequencyHz,
        sample_rate_hz: preset.sampleRate,
        lna_gain_db: next.lna,
        vga_gain_db: next.vga,
        amp_enable: next.amp,
        ble_hop: false,
      });
      setSweptMode(false);
    });
  };
  const setBleOptions = (next: { ble_decode?: boolean; ble_hop?: boolean }) => {
    if (next.ble_decode !== undefined) setDecode(next.ble_decode);
    if (next.ble_hop !== undefined) setHop(next.ble_hop);
    if (running && mode === "live") run(() => configureTuner(next).then(() => {}));
  };
  const listenBle = () => {
    controller.prepareExternalAudio();
    return run(async () => {
      setDecode(true);
      setHop(true);
      setSweptMode(false);
      setDemoClass("Bluetooth-LE");
      const ble = {
        center_freq_hz: 2402e6,
        sample_rate_hz: 4e6,
        gain_db: 32,
        demo,
        demo_class: "Bluetooth-LE",
        device_args: "driver=hackrf",
        ble_decode: true,
        ble_hop: true,
        upconverter: "none" as const,
      };
      const hadUpconverter = upconverter !== "none";
      if (hadUpconverter) writeUpconverter("none");
      await configureTuner(ble);
      if (hadUpconverter)
        setNotice("Upconverter turned off for Bluetooth. Set its switch to bypass, or connect the antenna to the HackRF directly.");
      if (!(running && mode === "live"))
        connect("/live", "live", {
          frequencyHz: ble.center_freq_hz,
          sampleRate: ble.sample_rate_hz,
        });
    });
  };
  const scan = (enabled: boolean) =>
    run(async () => {
      await signalRequest("/sdr/scanner", {
        enabled,
        start_hz: scanStart * 1e6,
        stop_hz: scanStop * 1e6,
        step_hz: scanStep * 1e6,
        dwell_ms: dwell,
        sensitivity_db: sensitivity,
      });
      if (enabled) setHop(false);
      setSweptMode(enabled);
    });
  const save = () =>
    run(async () => {
      if (!frame?.frame_id)
        throw new Error("Start a live stream before saving");
      // The selected track must be observed in this exact IQ window.
      const current = frame.observations?.find((t) => t.id === selectedTrackId);
      const result = await signalRequest<SavedSignal>("/signals/save", {
        frame_id: frame.frame_id,
        name:
          captureName.trim() ||
          `${current?.label ?? frame.prediction.label} ${mhz(frame.tuner.center_freq_hz)} MHz`,
        device_id: current?.id,
      });
      await refresh();
      setNotice(
        result.duplicate
          ? "This exact IQ capture is already saved"
          : "IQ capture saved with frequency and sample rate",
      );
      setCaptureName("");
    });
  const nameTrack = (track: SignalTrack, name: string) =>
    run(async () => {
      await signalRequest("/devices/name", {device_id: track.id, name});
      setSelectedTrackId(track.id);
      setNotice("Device name saved");
      setDeviceRevision((value) => value + 1);
      await refresh();
    });
  const observations = useMemo(
    () => frame?.observations ?? [],
    [frame?.observations],
  );
  const tracks = useMemo(() => frame?.tracks ?? [], [frame?.tracks]);
  // The backend CNN already classified each band from the HackRF's IQ; hand
  // its tracks to the shared signal-id state instead of re-classifying here.
  useEffect(() => {
    if (!frame || !running) return;
    controller.pushExternalTracks(
      frame.tracks as unknown as BackendTrack[],
      frame.spectrum?.noise_floor_dbfs ?? -100,
      frame.spectrum?.timestamp ?? Date.now() / 1000,
    );
  }, [frame, running, controller]);
  const selectedTrack = useMemo(() => {
    if (!selectedTrackId) return undefined;
    const observation = observations.find((t) => t.id === selectedTrackId);
    const persisted = tracks.find((t) => t.id === selectedTrackId);
    if (!observation) return persisted;
    if (observation.name || !persisted?.name) return observation;
    return { ...observation, name: persisted.name };
  }, [selectedTrackId, observations, tracks]);
  const active = observations.map((t) => t.id);
  const investigation = investigationFromSelection(
    selectedTrack,
    frame?.prediction,
    {
      center_freq_hz: frame?.tuner.center_freq_hz ?? universalTuning().center_freq_hz,
      sample_rate_hz: frame?.tuner.sample_rate_hz ?? universalTuning().sample_rate_hz,
    },
  );
  const radioNode = (
    <div className="space-y-4">
          {frame?.scanner?.enabled && (
            <div className="flex items-center justify-between text-xs text-[var(--muted)]">
              <span>
                Scanner: step {frame.scanner.position + 1} /{" "}
                {frame.scanner.steps} · {frame.scanner.sweeps} sweeps
              </span>
              <button className={button} onClick={() => {
                const next = !swept;
                setSweptMode(next);
                const spec = next && frame?.sweep ? frame.sweep : frame?.spectrum;
                if (spec) controller.pushExternalSpectrum(spec.power_dbfs, spec.start_hz, spec.bin_hz, frame?.tuner.sample_rate_hz);
              }}>
                {swept ? "Show current dwell" : "Show swept overview"}
              </button>
            </div>
          )}

          <details className={panel}>
            <summary className="cursor-pointer text-xs font-medium uppercase tracking-widest text-[var(--muted)]">
              Frequency scanner
            </summary>
            <p className="my-3 text-xs text-[var(--muted)]">Scanning pauses Bluetooth channel hopping. Use Bluetooth listening to find nearby devices.</p>
            <div className="grid grid-cols-2 gap-3">
              <Field label="From · MHz">
                <input
                  className="field"
                  type="number"
                  value={scanStart}
                  onChange={(e) => setScanStart(+e.target.value)}
                />
              </Field>
              <Field label="To · MHz">
                <input
                  className="field"
                  type="number"
                  value={scanStop}
                  onChange={(e) => setScanStop(+e.target.value)}
                />
              </Field>
              <Field label="Step · MHz">
                <input
                  className="field"
                  type="number"
                  min="0.1"
                  max="20"
                  step="0.1"
                  value={scanStep}
                  onChange={(e) => setScanStep(+e.target.value)}
                />
              </Field>
              <Field label="Dwell · ms">
                <input
                  className="field"
                  type="number"
                  min={50}
                  max={5000}
                  value={dwell}
                  onChange={(e) => setDwell(+e.target.value)}
                />
              </Field>
            </div>
            <div className="my-4">
              <Field
                label={`Detection threshold · ${sensitivity} dB above noise`}
              >
                <input
                  type="range"
                  min={3}
                  max={40}
                  value={sensitivity}
                  onChange={(e) => setSensitivity(+e.target.value)}
                />
              </Field>
            </div>
            <button
              className={`${button} w-full`}
              disabled={busy || !running || mode !== "live"}
              onClick={() => scan(!frame?.scanner?.enabled)}
            >
              {frame?.scanner?.enabled
                ? "Stop scanner"
                : "Start frequency scan"}
            </button>
            <p className="mt-3 text-[11px] leading-relaxed text-[var(--muted)]">
              Sweeps measure one tuning window at a time. Fast bursts can occur
              between visits. Use a step no wider than the sample rate for
              continuous frequency coverage.
            </p>
          </details>
          <div className={panel}>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-medium">Capture library</h3>
                <p className="mt-1 text-xs text-[var(--muted)]">
                  Exact IQ windows ·{" "}
                  {(saved.reduce((n, r) => n + r.bytes, 0) / 1e6).toFixed(1)} MB
                  stored · playback and RF transmit are separate
                </p>
              </div>
              <button className={button} onClick={() => run(refresh)}>
                Refresh
              </button>
            </div>
            <div className="my-4 flex gap-2">
              <input
                aria-label="Capture name"
                className="field min-w-0 flex-1"
                placeholder="Name this capture…"
                maxLength={80}
                value={captureName}
                onChange={(e) => setCaptureName(e.target.value)}
              />
              <button
                className={primary}
                disabled={
                  busy || !running || mode !== "live" || !frame?.frame_id
                }
                onClick={save}
              >
                Save IQ
              </button>
            </div>
            <p className="mb-3 text-[11px] text-[var(--muted)]">
              Saves the full current receiver window, including other signals in
              its bandwidth.
            </p>
            <div className="max-h-96 space-y-2 overflow-y-auto">
              {saved.map((record) => (
                <div
                  key={record.id}
                  className="rounded-lg border border-[var(--line)] p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm">
                        {record.name}{" "}
                        {record.demo && (
                          <span className="ml-2 text-[10px] text-amber-300">
                            DEMO
                          </span>
                        )}
                      </div>
                      <div className="mt-1 font-mono text-[10px] text-[var(--muted)]">
                        {mhz(record.center_freq_hz)} MHz ·{" "}
                        {(record.duration_s * 1000).toFixed(1)} ms ·{" "}
                        {(record.bytes / 1024).toFixed(0)} KB
                        {record.device_name ? ` · ${record.device_name}` : ""}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        className={button}
                        onClick={() => {
                          setError("");
                          setSweptMode(false);
                          connect(`/signals/${record.id}/playback`, "playback");
                        }}
                      >
                        ▶ Play
                      </button>
                      <a
                        className={button}
                        href={`${API_BASE}/signals/${record.id}/iq`}
                        download
                      >
                        IQ ↓
                      </a>
                      <button
                        className={button}
                        onClick={() => {
                          setRenaming(record.id);
                          setNewName(record.name);
                        }}
                      >
                        Rename
                      </button>
                      <button
                        className={`${button} text-amber-300`}
                        onClick={() => {
                          setTx(record);
                          setNotice("");
                        }}
                      >
                        Transmit…
                      </button>
                    </div>
                  </div>
                  {renaming === record.id && (
                    <form
                      className="mt-3 flex gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        run(async () => {
                          await signalRequest(
                            `/signals/${record.id}`,
                            { name: newName },
                            "PATCH",
                          );
                          setRenaming("");
                          await refresh();
                        });
                      }}
                    >
                      <input
                        className="field min-w-0 flex-1"
                        aria-label="Recording name"
                        value={newName}
                        maxLength={80}
                        onChange={(e) => setNewName(e.target.value)}
                      />
                      <button className={button} disabled={busy}>
                        Save
                      </button>
                      <button
                        type="button"
                        className={button}
                        onClick={() => setRenaming("")}
                      >
                        Cancel
                      </button>
                    </form>
                  )}
                </div>
              ))}
              {!saved.length && (
                <p className="py-6 text-center text-sm text-[var(--muted)]">
                  Your saved signals will appear here.
                </p>
              )}
            </div>
          </div>

      {tx && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Transmit saved IQ"
        >
          <div className={`${panel} w-full max-w-md shadow-2xl`}>
            <div className="text-[10px] uppercase tracking-widest text-amber-300">
              HackRF RF transmit
            </div>
            <h3 className="mt-2 text-lg">{tx.name}</h3>
            <p className="mt-2 text-xs text-[var(--muted)]">
              {mhz(tx.center_freq_hz)} MHz · {tx.sample_rate_hz / 1e6} MS/s ·
              full saved IQ window. The receiver pauses during transmission.
            </p>
            <div className="my-5 grid grid-cols-2 gap-3">
              <Field label="TX gain · dB">
                <input
                  className="field"
                  type="number"
                  min={0}
                  max={47}
                  value={txGain}
                  onChange={(e) => setTxGain(+e.target.value)}
                />
              </Field>
              <Field label="Repeats">
                <input
                  className="field"
                  type="number"
                  min={1}
                  max={20}
                  value={repeats}
                  onChange={(e) => setRepeats(+e.target.value)}
                />
              </Field>
            </div>
            <label className="flex gap-2 text-xs text-[var(--muted)]">
              <input
                type="checkbox"
                checked={amp}
                onChange={(e) => setAmp(e.target.checked)}
              />
              Enable RF amplifier
            </label>
            <div className="mt-5 flex justify-end gap-2">
              <button
                disabled={busy}
                className={button}
                onClick={() => setTx(undefined)}
              >
                Cancel
              </button>
              <button
                disabled={busy}
                className={button}
                onClick={() =>
                  run(async () => {
                    await signalRequest(`/signals/${tx.id}/transmit`, {
                      repeats,
                      tx_gain: txGain,
                      amp,
                      dry_run: true,
                    });
                    setNotice(
                      "Transmit parameters validated. No RF was emitted.",
                    );
                  })
                }
              >
                Dry run
              </button>
              <button
                disabled={busy}
                className="rounded-md bg-amber-300 px-4 py-2 text-xs font-medium text-black disabled:opacity-40"
                onClick={() =>
                  run(async () => {
                    await signalRequest(`/signals/${tx.id}/transmit`, {
                      repeats,
                      tx_gain: txGain,
                      amp,
                      dry_run: false,
                    });
                    setNotice("Saved IQ transmitted through HackRF");
                    setTx(undefined);
                  })
                }
              >
                {busy ? "Working…" : "Transmit now"}
              </button>
            </div>
            {error && (
              <p role="alert" className="mt-3 text-xs text-red-300">
                {error}
              </p>
            )}
            {notice && (
              <p role="status" className="mt-3 text-xs text-teal-200">
                {notice}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
  const bluetooth = (
    <div className="space-y-4">
      <div
        className={`${panel} flex flex-wrap items-center justify-between gap-4`}
      >
        <div>
          <h3 className="text-sm font-medium">Find nearby Bluetooth devices</h3>
          <p className="mt-1 max-w-xl text-xs text-[var(--muted)]">
            Listen for device announcements across all three advertising
            channels. Names and verified messages appear below; connection
            contents need a complete connection capture.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Bluetooth receiver source"
            className="field text-xs"
            value={demo ? "demo" : "hackrf"}
            onChange={(e) => writeSource(e.target.value === "demo")}
          >
            <option value="demo">Try with demo devices</option>
            <option value="hackrf">Listen with HackRF</option>
          </select>
          <button className={primary} disabled={busy || connecting} onClick={listenBle}>
            {busy || connecting ? "Preparing receiver…" : "Listen for Bluetooth devices"}
          </button>
        </div>
      </div>
                <details className={panel}>
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-widest text-[var(--muted)]">
          Bluetooth receiver options
        </summary>
        <p className="my-3 text-xs text-[var(--muted)]">
          Frequency, span and gain come from the main receiver controls.
          Listening for Bluetooth devices sets them for you.
        </p>
        {demo && (
          <div className="mb-3">
            <Field label="Demo waveform">
              <select
                className="field"
                value={demoClass}
                onChange={(e) => setDemoClass(e.target.value)}
              >
                {classes.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </Field>
          </div>
        )}
        <div className="space-y-2 text-xs text-[var(--muted)]">
          <label className="flex gap-2">
            <input
              type="checkbox"
              checked={decode}
              onChange={(e) => setBleOptions({ ble_decode: e.target.checked })}
            />
            Passive BLE 1M / 2M decoding
          </label>
          <label className="flex gap-2">
            <input
              type="checkbox"
              checked={hop}
              onChange={(e) => setBleOptions({ ble_hop: e.target.checked })}
            />
            Hop advertising channels 37 / 38 / 39
          </label>
        </div>
        <div className="mt-3 flex gap-2">
          {[2402, 2426, 2480].map((freq, i) => (
            <button
              className={`${button} flex-1 px-1`}
              key={freq}
              disabled={busy || connecting || mode === "playback"}
              title={`Listen only on channel ${37 + i} · ${freq} MHz`}
              onClick={() => {
                setHop(false);
                void controller.setFrequency(freq * 1e6);
              }}
            >
              CH {37 + i}
            </button>
          ))}
        </div>
      </details>
      {mode === "live" && (
            <BlePacketList
              key={session}
              result={frame?.ble ?? null}
              history={history}
              listening={running}
              rfStatus={frame?.tuner ?? {demo}}
              identificationRevision={deviceRevision}
              onClearHistory={clearHistory}
              getDevicePackets={getDevicePackets}
              tracks={tracks}
              activeTrackIds={active}
              selectedTrackId={selectedTrackId}
              onSelectTrack={(track) => setSelectedTrackId(track?.id)}
              onNameTrack={nameTrack}
            />
          )}
          <SignalInvestigationPanel
            subject={investigation}
            cursorHz={null}
            cursorPower={null}
            running={running}
            onClearSelection={() => setSelectedTrackId(undefined)}
            onNameTrack={nameTrack}
          />
    </div>
  );
  // Frames arrive many times a second; keep the controls object stable so the
  // scope, header and rail don't re-render at stream rate.
  const latest = useRef({ connect: connectHackrf, setGain, applyPreset, setUpconverter });
  useEffect(() => {
    latest.current = { connect: connectHackrf, setGain, applyPreset, setUpconverter };
  });
  const connectStable = useCallback(() => latest.current.connect(), []);
  const setGainStable = useCallback(
    (patch: Partial<HackrfGain>) => latest.current.setGain(patch),
    [],
  );
  const applyPresetStable = useCallback(
    (preset: HackrfPreset) => latest.current.applyPreset(preset),
    [],
  );
  const setUpconverterStable = useCallback(
    (id: UpconverterId) => latest.current.setUpconverter(id),
    [],
  );
  const dismiss = useCallback(() => {
    setError("");
    setNotice("");
  }, []);
  const trackKey = observations
    .map(
      (t) =>
        `${t.id}|${Math.round(t.center_freq_hz / 100)}|${Math.round(t.bandwidth_hz / 100)}|${t.name || t.label}`,
    )
    .join(";");
  // eslint-disable-next-line react-hooks/exhaustive-deps -- identity follows content, not each frame
  const stableTracks = useMemo(() => observations, [trackKey]);
  const fellBack = running && mode === "live" && !demo && frame?.tuner.demo === true;
  const synthetic = running && (frame?.tuner.demo ?? demo);
  const problem =
    error ||
    (fellBack
      ? `HackRF not available (${frame?.tuner.backend || "no backend"}) — showing synthetic data. Check that the HackRF is plugged in and hackrf_transfer is installed.`
      : "");
  const isConnecting = connecting || busy;
  const controls = useMemo<HackrfControls>(
    () => ({
      tracks: stableTracks,
      connect: connectStable,
      connecting: isConnecting,
      demo,
      setDemo: writeSource,
      synthetic,
      gain,
      setGain: setGainStable,
      applyPreset: applyPresetStable,
      upconverter,
      setUpconverter: setUpconverterStable,
      error: problem,
      notice,
      dismiss,
    }),
    [stableTracks, connectStable, isConnecting, demo, synthetic, gain, setGainStable, applyPresetStable, upconverter, setUpconverterStable, problem, notice, dismiss],
  );
  const nodes = { radio: radioNode, bluetooth };
  return (
    <HackrfContext.Provider value={controls}>
      <HackrfNodesContext.Provider value={nodes}>
        {children}
      </HackrfNodesContext.Provider>
    </HackrfContext.Provider>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5 text-[11px] text-[var(--muted)]">
      {label}
      {children}
    </label>
  );
}
