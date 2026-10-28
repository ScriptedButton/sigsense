import type { LibraryItem } from "@/lib/api";

type LibraryRowProps = {
  item: LibraryItem;
  selected: boolean;
  onSelect: (item: LibraryItem) => void;
};

export function LibraryRow({ item, selected, onSelect }: LibraryRowProps) {
  const viewable = Boolean(item.pageId);
  const count =
    item.count > 0 ? `${item.count} proto` : "model only";

  const body = (
    <>
      <span className="min-w-0">
        <span className="block truncate font-mono text-[var(--foreground)]">
          {item.label}
        </span>
        <span className="text-xs text-[var(--muted)]">{item.source}</span>
      </span>
      <span className="shrink-0 text-right">
        {viewable ? (
          <span className="block font-mono text-xs text-[var(--signal)]">
            {selected ? "Viewing" : "View"}
          </span>
        ) : null}
        <span className="font-mono text-xs text-[var(--muted)]">{count}</span>
      </span>
    </>
  );

  if (!viewable) {
    return (
      <li className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
        {body}
      </li>
    );
  }

  return (
    <li>
      <button
        type="button"
        className={`flex min-h-11 w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--signal)] ${
          selected ? "bg-[var(--signal-dim)]" : "hover:bg-white/5"
        }`}
        aria-pressed={selected}
        aria-label={`View ${item.label}`}
        onClick={() => onSelect(item)}
      >
        {body}
      </button>
    </li>
  );
}
