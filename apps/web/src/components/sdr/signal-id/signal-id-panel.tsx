"use client";

import { useCallback, useMemo, useState } from "react";

import {
  useRadioController,
  useSdrState,
  useSignalIdState,
} from "@/components/sdr/sdr-provider";
import { useCorrectionHost } from "@/components/sdr/signal-id/correction-host";
import { SignalIdControls } from "@/components/sdr/signal-id/signal-id-controls";
import { SignalIdFiltersBar } from "@/components/sdr/signal-id/signal-id-filters";
import { SignalIdList } from "@/components/sdr/signal-id/signal-id-list";
import { CATALOG_COUNT } from "@/lib/sdr/signal-id/catalog";
import {
  applySignalIdFilters,
  DEFAULT_SIGNAL_ID_FILTERS,
  type SignalIdFilters,
} from "@/lib/sdr/signal-id/filters";
import type { SignalIdSensitivity } from "@/lib/sdr/signal-id/engine";
import type { IdentifiedSignal } from "@/lib/sdr/signal-id/types";

export function SignalIdPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const scan = useSignalIdState();
  const [sensitivity, setSensitivity] = useState<SignalIdSensitivity>(() =>
    controller.getSignalIdSensitivity(),
  );
  const [listFrozen, setListFrozen] = useState(false);
  const [filters, setFilters] = useState<SignalIdFilters>(
    DEFAULT_SIGNAL_ID_FILTERS,
  );
  const live = radio.status === "playing";
  const corrections = useCorrectionHost();

  const filtered = useMemo(
    () => applySignalIdFilters(scan.signals, filters),
    [scan.signals, filters],
  );

  const onToggle = useCallback(() => {
    controller.setSignalIdEnabled(!scan.enabled);
  }, [controller, scan.enabled]);

  const onSensitivityChange = useCallback(
    (value: SignalIdSensitivity) => {
      setSensitivity(value);
      controller.setSignalIdSensitivity(value);
    },
    [controller],
  );

  const onTune = useCallback(
    (signal: IdentifiedSignal) => {
      controller.tuneToSignal(signal.offsetHz, signal.scheme);
    },
    [controller],
  );

  return (
    <fieldset className="control-group">
      <legend className="control-label">Signal identification</legend>

      <SignalIdControls
        enabled={scan.enabled}
        locked={radio.connection === "hackrf"}
        sensitivity={sensitivity}
        onToggle={onToggle}
        onSensitivityChange={onSensitivityChange}
      />

      <p className="mt-2 text-xs text-muted" aria-live="polite">
        {statusLabel(
          scan.enabled,
          live,
          filtered.length,
          scan.signals.length,
          scan.noiseFloorDb,
          listFrozen,
        )}
      </p>

      {scan.source === "offline-fallback" && (
        <p className="mt-2 text-xs text-warn" role="status">
          Offline fallback · the backend is unreachable, so the small on-device
          model is running. Results are less reliable.
        </p>
      )}

      {scan.error && scan.source !== "offline-fallback" && (
        <p className="mt-2 text-xs text-danger" role="alert">
          {scan.error}
        </p>
      )}

      <div className="mt-2">
        <SignalIdFiltersBar
          filters={filters}
          onChange={setFilters}
          matchedCount={filtered.length}
          totalCount={scan.signals.length}
        />
      </div>

      <div className="mt-2">
        <SignalIdList
          signals={filtered}
          enabled={scan.enabled}
          live={live}
          offsetHz={radio.offsetHz}
          onTune={onTune}
          onFrozenChange={setListFrozen}
          filteredEmpty={scan.signals.length > 0 && filtered.length === 0}
          onCorrect={corrections.correct}
        />
      </div>

      <div className="mt-2 flex items-center justify-between gap-2">
        <button
          type="button"
          className="btn-secondary"
          onClick={corrections.manage}
          title="Everything you've taught the model"
        >
          Your corrections
          {corrections.total !== null ? ` (${corrections.total})` : ""}
        </button>
        {scan.source === "offline-fallback" && (
          <span className="text-[0.65rem] text-muted">
            Needs the API to learn
          </span>
        )}
      </div>


      <p className="mt-2 text-[0.65rem] leading-relaxed text-muted">
        {scan.source === "offline-fallback"
          ? "Classifying with the small on-device model until the backend is reachable again. "
          : radio.connection === "hackrf"
            ? "Modulation families are identified from the HackRF's raw IQ. Licensed channels are separate frequency hints. "
            : "RTL sources use raw IQ to identify modulation families. Remote receivers use spectrum rows. Exact protocols require decoder evidence; uncertain results stay unknown. "}
        Matches are then checked against a {CATALOG_COUNT.toLocaleString()}
        -entry signal catalog. Select a signal to tune it and switch the
        demodulator to match.
      </p>
    </fieldset>
  );
}

function statusLabel(
  enabled: boolean,
  live: boolean,
  matched: number,
  total: number,
  noiseFloorDb: number,
  listFrozen: boolean,
): string {
  if (!enabled) return "Off";
  if (!live) return "Armed · waiting for RF";
  if (total === 0) return "Scanning…";
  const floor = Number.isFinite(noiseFloorDb) ? Math.round(noiseFloorDb) : null;
  const countLabel =
    matched === total
      ? `${total} ${total === 1 ? "signal" : "signals"}`
      : `${matched} of ${total}`;
  const base =
    floor === null ? countLabel : `${countLabel} · noise floor ${floor} dB`;
  return listFrozen ? `${base} · paused` : base;
}
