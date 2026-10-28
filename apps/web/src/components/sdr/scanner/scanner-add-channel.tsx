"use client";

import { useState } from "react";

import {
  useRadioController,
} from "@/components/sdr/sdr-provider";
import { formatFrequency } from "@/lib/sdr/format";
import type { DemodScheme } from "@/lib/sdr/types";

type ScannerAddChannelProps = {
  listenHz: number;
  scheme: DemodScheme;
  bankId: string;
  bankBuiltin: boolean;
};

/**
 * Adds the current listen frequency to the active bank.
 * Builtin banks are read-only for deletes but accept lockouts; adds go to Custom
 * when the active bank is builtin.
 */
export function ScannerAddChannel({
  listenHz,
  scheme,
  bankId,
  bankBuiltin,
}: ScannerAddChannelProps) {
  const controller = useRadioController();
  const [label, setLabel] = useState("");
  const targetBankId = bankBuiltin ? "custom" : bankId;

  return (
    <div className="scanner-add mt-3 pt-2">
      <p className="mb-1.5 text-[0.65rem] font-medium text-muted">
        Add dial · {formatFrequency(listenHz)} · {scheme}
        {bankBuiltin ? " → Custom" : ""}
      </p>
      <div className="flex gap-1.5">
        <input
          type="text"
          className="scanner-input flex-1"
          placeholder="Label (optional)"
          value={label}
          maxLength={40}
          onChange={(e) => setLabel(e.target.value)}
        />
        <button
          type="button"
          className="btn-secondary shrink-0"
          onClick={() => {
            controller.addScannerChannel({
              label:
                label.trim() ||
                formatFrequency(listenHz).replace(/\s/g, ""),
              frequencyHz: listenHz,
              scheme,
              bankId: targetBankId,
            });
            setLabel("");
            if (bankBuiltin) {
              controller.setScannerBank("custom");
            }
          }}
        >
          Add
        </button>
      </div>
    </div>
  );
}
