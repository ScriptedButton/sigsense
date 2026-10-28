import { classInfo } from "@/lib/sdr/signal-id/labels";
import type { DisplayClass } from "@/lib/sdr/signal-id/types";

type SignalClassBadgeProps = {
  label: DisplayClass;
  /** Dim the badge for classes the operator can safely ignore. */
  muted?: boolean;
  /** Overrides the badge text, e.g. the backend CNN's own class name. */
  text?: string;
  /** Overrides the class colour, e.g. for a label the user invented. */
  color?: string;
};

export function SignalClassBadge({
  label,
  muted = false,
  text,
  color,
}: SignalClassBadgeProps) {
  const info = { ...classInfo(label), ...(color ? { color } : {}) };
  return (
    <span
      className="signal-badge"
      style={{
        color: info.color,
        borderColor: `color-mix(in oklch, ${info.color} 45%, transparent)`,
        background: `color-mix(in oklch, ${info.color} 14%, transparent)`,
        opacity: muted ? 0.65 : 1,
      }}
      title={info.name}
    >
      {text ?? info.short}
    </span>
  );
}
