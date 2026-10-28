"use client";

import {
  useRadioController,
  useScannerState,
  useSdrState,
} from "@/components/sdr/sdr-provider";
import { ScannerAddChannel } from "@/components/sdr/scanner/scanner-add-channel";
import { ScannerChannelList } from "@/components/sdr/scanner/scanner-channel-list";
import { ScannerControls } from "@/components/sdr/scanner/scanner-controls";
import { findBank, scanableChannels } from "@/lib/sdr/scanner/banks";

/**
 * Programmable memory scanner — bank select, dwell/hold, start/skip/hold.
 * Lives on the Radio rail with GMRS and shortwave.
 */
export function ScannerPanel() {
  const controller = useRadioController();
  const radio = useSdrState();
  const scan = useScannerState();

  const bank = findBank(scan.banks, scan.config.activeBankId);
  const scanable = bank ? scanableChannels(bank).length : 0;
  const connected = radio.connection !== "disconnected";
  const active =
    scan.status === "scanning" ||
    scan.status === "held" ||
    scan.status === "paused";

  return (
    <fieldset className="control-group">
      <legend className="control-label">Memory scanner</legend>
      <p className="mb-2 text-[0.65rem] leading-relaxed text-muted">
        Hop a channel bank · stop on squelch · hold / skip · add the dial to
        Custom
      </p>

      <label className="mb-2 flex flex-col gap-1">
        <span className="text-[0.65rem] font-medium text-muted">Bank</span>
        <select
          className="scanner-select"
          value={scan.config.activeBankId}
          onChange={(e) => controller.setScannerBank(e.target.value)}
        >
          {scan.banks.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name} ({b.channels.length})
            </option>
          ))}
        </select>
      </label>

      <ScannerControls
        status={scan.status}
        connected={connected}
        scanableCount={scanable}
        dwellMs={scan.config.dwellMs}
        holdMs={scan.config.holdMs}
        stopOnActivity={scan.config.stopOnActivity}
        resumeOnQuiet={scan.config.resumeOnQuiet}
        onStart={() => void controller.startScanner()}
        onStop={() => controller.stopScanner()}
        onPause={() => controller.pauseScanner()}
        onResume={() => controller.resumeScanner()}
        onSkip={() => void controller.skipScanner()}
        onHold={() => controller.holdScanner()}
        onDwellChange={(ms) => controller.patchScannerConfig({ dwellMs: ms })}
        onHoldMsChange={(ms) => controller.patchScannerConfig({ holdMs: ms })}
        onStopOnActivityChange={(v) =>
          controller.patchScannerConfig({ stopOnActivity: v })
        }
        onResumeOnQuietChange={(v) =>
          controller.patchScannerConfig({ resumeOnQuiet: v })
        }
      />

      {scan.error && (
        <p className="mt-2 text-[0.65rem] text-warn" role="status">
          {scan.error}
        </p>
      )}

      {active && (
        <p className="mt-2 text-[0.65rem] text-accent" role="status">
          {scan.status === "scanning" && `Scanning · ${scan.hopCount} hops`}
          {scan.status === "held" &&
            (scan.holdReason === "activity"
              ? "Held on activity"
              : "Held · Skip or Resume to continue")}
          {scan.status === "paused" && "Paused"}
        </p>
      )}

      {bank && (
        <ScannerChannelList
          bank={bank}
          currentChannelId={scan.currentChannelId}
          listenHz={radio.frequencyHz + radio.offsetHz}
          scanning={active}
        />
      )}

      <ScannerAddChannel
        listenHz={radio.frequencyHz + radio.offsetHz}
        scheme={radio.scheme}
        bankId={scan.config.activeBankId}
        bankBuiltin={bank?.builtin ?? true}
      />
    </fieldset>
  );
}
