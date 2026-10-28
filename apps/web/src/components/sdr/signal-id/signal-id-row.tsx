import { ConfidenceMeter } from "@/components/sdr/signal-id/confidence-meter";
import { SignalClassBadge } from "@/components/sdr/signal-id/signal-class-badge";
import { SignalIdentityHint } from "@/components/sdr/signal-id/signal-identity-hint";
import { formatBandwidth, formatFrequency } from "@/lib/sdr/format";
import { customLabelColor } from "@/lib/sdr/signal-id/backend-classes";
import { classInfo } from "@/lib/sdr/signal-id/labels";
import type { IdentifiedSignal } from "@/lib/sdr/signal-id/types";

type SignalIdRowProps = {
  signal: IdentifiedSignal;
  active: boolean;
  onTune: (signal: IdentifiedSignal) => void;
  /** Open the correction sheet for this signal. */
  onCorrect?: (signal: IdentifiedSignal) => void;
};

export function SignalIdRow({ signal, active, onTune, onCorrect }: SignalIdRowProps) {
  const info = classInfo(signal.label);
  const noise = signal.label === "NOISE";
  const snr = Math.round(signal.snrDb);
  const identity = signal.identities[0];

  const className = signal.backendLabel
    ? signal.unknown
      ? "Unknown"
      : signal.backendLabel
    : undefined;
  const badgeColor =
    signal.customLabel && signal.backendLabel
      ? customLabelColor(signal.backendLabel)
      : undefined;
  const learnedNote = signal.userCorrected
    ? "Corrected by you"
    : signal.learned
      ? `learned from ${signal.learned.examples} of your corrections`
      : "";
  const alternatives = signal.alternatives
    ?.map((a) => `${a.label} ${Math.round(a.confidence * 100)}%`)
    .join(", ");
  const tooltip = [
    learnedNote ? `Your corrections: ${learnedNote}` : "",
    signal.bandPlanNote ? `Band plan: ${signal.bandPlanNote}` : "",
    signal.unknown
      ? "Open-set: the classifier is not confident enough to name this."
      : "",
    alternatives ? `Next: ${alternatives}` : "",
    signal.evidence ?? "",
    identity?.description ?? "",
  ]
    .filter(Boolean)
    .join("\n");

  const description = [
    signal.title ?? (identity ? `${identity.name} (${info.name})` : info.name),
    `at ${formatFrequency(signal.frequencyHz)}`,
    `${formatBandwidth(signal.bandwidthHz)} wide`,
    `${snr} decibels above noise`,
    `${Math.round(signal.confidence * 100)} percent confidence.`,
    info.scheme ? `Tune and switch to ${info.scheme}.` : "",
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <li>
      <button
        type="button"
        className={`signal-row${active ? " signal-row-active" : ""}${signal.stale ? " opacity-60" : ""}`}
        onClick={() => onTune(signal)}
        aria-label={description}
        title={
          tooltip ||
          (signal.bandPlanNote
            ? `Identified from band plan: ${signal.bandPlanNote}`
            : (identity?.description ?? info.hint))
        }
      >
        <span className="flex items-center justify-between gap-2">
          <SignalClassBadge
            label={signal.label}
            muted={noise}
            text={className}
            color={badgeColor}
          />
          <span className="truncate font-mono text-sm tabular-nums text-ink">
            {formatFrequency(signal.frequencyHz)}
          </span>
        </span>

        <span className="signal-row-identity">
          {signal.title ? (
            <span className="signal-identity-name">{signal.title}</span>
          ) : identity ? (
            <SignalIdentityHint identity={identity} />
          ) : null}
        </span>

        <span className="mt-1.5 flex items-center justify-between gap-2">
          <span className="font-mono text-[0.65rem] tabular-nums text-muted">
            {formatBandwidth(signal.bandwidthHz)} · {snr} dB
            {signal.basis === "band-plan" ? " · band plan" : ""}
            {signal.basis === "backend-cnn" && signal.bandPlanNote
              ? ` · ${signal.bandPlanNote}`
              : ""}
            {signal.stale ? " · idle" : ""}
            {signal.userCorrected ? " · set by you" : ""}
            {!signal.userCorrected && signal.learned
              ? ` · learned ×${signal.learned.examples}`
              : ""}
          </span>
          <ConfidenceMeter value={signal.confidence} color={info.color} />
        </span>
      </button>
      {onCorrect && (
        <button
          type="button"
          className="signal-row-correct"
          aria-label={`Correct the label of the signal at ${formatFrequency(signal.frequencyHz)}`}
          title="Tell the model what this signal really is"
          onClick={() => onCorrect(signal)}
        >
          Correct…
        </button>
      )}
    </li>
  );
}
