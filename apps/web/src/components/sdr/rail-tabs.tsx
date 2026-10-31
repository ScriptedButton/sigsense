"use client";

export type RailTab<T extends string> = {
  id: T;
  label: string;
  /** Tooltip, e.g. what lives in the tab and its shortcut. */
  title?: string;
  /** Optional live indicator (e.g. decoder running). */
  badge?: string | number | boolean;
};

type RailTabsProps<T extends string> = {
  tabs: readonly RailTab<T>[];
  value: T;
  onChange: (id: T) => void;
  label: string;
  size?: "md" | "sm";
};

/**
 * Segmented control for the side rail. Keeps specialized panels one click away
 * without stacking every decoder as a peer of Tune / Mode.
 */
export function RailTabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  size = "md",
}: RailTabsProps<T>) {
  return (
    <div
      className={`rail-tabs ${size === "sm" ? "rail-tabs-sm" : ""}`}
      role="tablist"
      aria-label={label}
    >
      {tabs.map((tab) => {
        const selected = tab.id === value;
        const showBadge =
          tab.badge === true ||
          (typeof tab.badge === "number" && tab.badge > 0) ||
          (typeof tab.badge === "string" && tab.badge.length > 0);

        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`rail-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`rail-panel-${tab.id}`}
            className={`rail-tab ${selected ? "rail-tab-active" : ""}`}
            title={tab.title}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => {
              const step =
                event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
              if (!step) return;
              event.preventDefault();
              const index = tabs.findIndex((t) => t.id === tab.id);
              const next = tabs[(index + step + tabs.length) % tabs.length];
              onChange(next.id);
              requestAnimationFrame(() =>
                document.getElementById(`rail-tab-${next.id}`)?.focus(),
              );
            }}
          >
            <span>{tab.label}</span>
            {showBadge && (
              <span className="rail-tab-badge" aria-hidden>
                {tab.badge === true ? "" : tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
