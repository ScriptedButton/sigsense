"use client";

type Tab = "analyze" | "live" | "library";

type Props = {
  tab: Tab;
  onTabChange: (tab: Tab) => void;
};

export function Header({ tab, onTabChange }: Props) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "analyze", label: "Analyze" },
    { id: "live", label: "Live SDR" },
    { id: "library", label: "Library" },
  ];

  return (
    <header className="border-b border-[var(--line)] bg-[var(--panel)]/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-end justify-between gap-6 px-6 pb-0 pt-6">
        <div className="pb-5">
          <p className="font-[family-name:var(--font-display)] text-3xl tracking-tight text-[var(--signal)] md:text-4xl">
            SigSense
          </p>
          <p className="mt-1 max-w-md text-sm text-[var(--muted)]">
            ML signal intelligence for IQ captures and waterfall images
          </p>
        </div>
        <nav className="flex gap-1" aria-label="Primary">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onTabChange(t.id)}
              className={`relative px-4 py-3 text-sm transition-colors ${
                tab === t.id
                  ? "text-[var(--signal)]"
                  : "text-[var(--muted)] hover:text-[var(--foreground)]"
              }`}
            >
              {t.label}
              {tab === t.id ? (
                <span className="absolute inset-x-2 bottom-0 h-0.5 bg-[var(--signal)]" />
              ) : null}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
}
