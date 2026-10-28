"use client";

import {
  DWELL_OPTIONS_MS,
  HOLD_OPTIONS_MS,
  type ScannerStatus,
} from "@/lib/sdr/scanner/types";

type ScannerControlsProps = {
  status: ScannerStatus;
  connected: boolean;
  scanableCount: number;
  dwellMs: number;
  holdMs: number;
  stopOnActivity: boolean;
  resumeOnQuiet: boolean;
  onStart: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  onSkip: () => void;
  onHold: () => void;
  onDwellChange: (ms: number) => void;
  onHoldMsChange: (ms: number) => void;
  onStopOnActivityChange: (v: boolean) => void;
  onResumeOnQuietChange: (v: boolean) => void;
};

export function ScannerControls({
  status,
  connected,
  scanableCount,
  dwellMs,
  holdMs,
  stopOnActivity,
  resumeOnQuiet,
  onStart,
  onStop,
  onPause,
  onResume,
  onSkip,
  onHold,
  onDwellChange,
  onHoldMsChange,
  onStopOnActivityChange,
  onResumeOnQuietChange,
}: ScannerControlsProps) {
  const idle = status === "idle";
  const scanning = status === "scanning";
  const held = status === "held";
  const paused = status === "paused";
  const canStart = connected && scanableCount > 0;

  return (
    <div className="scanner-controls">
      <div className="flex flex-wrap gap-1.5">
        {idle ? (
          <button
            type="button"
            className="btn-primary"
            disabled={!canStart}
            onClick={onStart}
          >
            Scan
          </button>
        ) : (
          <button type="button" className="btn-secondary" onClick={onStop}>
            Stop
          </button>
        )}
        {(scanning || held) && (
          <button type="button" className="btn-secondary" onClick={onPause}>
            Pause
          </button>
        )}
        {(paused || held) && (
          <button type="button" className="btn-secondary" onClick={onResume}>
            Resume
          </button>
        )}
        {!idle && (
          <button type="button" className="btn-secondary" onClick={onSkip}>
            Skip
          </button>
        )}
        {(scanning || paused) && (
          <button type="button" className="btn-secondary" onClick={onHold}>
            Hold
          </button>
        )}
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-[0.65rem] font-medium text-muted">Dwell</span>
          <select
            className="scanner-select"
            value={dwellMs}
            disabled={!idle && !paused}
            onChange={(e) => onDwellChange(Number(e.target.value))}
          >
            {DWELL_OPTIONS_MS.map((o) => (
              <option key={o.id} value={o.ms}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[0.65rem] font-medium text-muted">
            Hold after quiet
          </span>
          <select
            className="scanner-select"
            value={holdMs}
            onChange={(e) => onHoldMsChange(Number(e.target.value))}
          >
            {HOLD_OPTIONS_MS.map((o) => (
              <option key={o.id} value={o.ms}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="mt-2 flex flex-col gap-1.5">
        <label className="scanner-check">
          <input
            type="checkbox"
            checked={stopOnActivity}
            onChange={(e) => onStopOnActivityChange(e.target.checked)}
          />
          <span>Stop on squelch open</span>
        </label>
        <label className="scanner-check">
          <input
            type="checkbox"
            checked={resumeOnQuiet}
            disabled={!stopOnActivity}
            onChange={(e) => onResumeOnQuietChange(e.target.checked)}
          />
          <span>Resume after quiet hold</span>
        </label>
      </div>
    </div>
  );
}
