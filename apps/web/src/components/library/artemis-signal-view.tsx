"use client";

import { useMemo } from "react";

import { ArtemisAudio } from "@/components/library/artemis-audio";
import { ArtemisFactsList } from "@/components/library/artemis-facts";
import { ArtemisWaterfall } from "@/components/library/artemis-waterfall";
import type { LibraryItem } from "@/lib/api";
import {
  artemisAssets,
  describeArtemis,
  sigidWikiUrl,
} from "@/lib/library/artemis-record";
import { useArtemisRecord } from "@/lib/library/use-artemis-record";

type ArtemisSignalViewProps = {
  item: LibraryItem | null;
};

export function ArtemisSignalView({ item }: ArtemisSignalViewProps) {
  const pageId = item?.pageId ?? null;
  const assets = pageId ? artemisAssets(pageId) : null;
  const { record, loading, error } = useArtemisRecord(pageId);
  const facts = useMemo(
    () => (record ? describeArtemis(record) : null),
    [record],
  );
  const title = record?.title || item?.label || "Signal";
  const wiki = facts?.wikiUrl || (item ? sigidWikiUrl(item.label) : "");

  if (!item || !assets) {
    return (
      <div className="flex min-h-48 items-center px-5 py-8">
        <p className="max-w-[36ch] text-sm leading-relaxed text-[var(--muted)]">
          Choose an Artemis reference to see its waterfall and play the sample.
          Built-in classes stay in the list for matching, and have no wiki capture.
        </p>
      </div>
    );
  }

  return (
    <article aria-labelledby="artemis-signal-title">
      <div className="border-b border-[var(--line)] px-5 py-4">
        <p className="text-xs text-[var(--muted)]">Artemis #{pageId}</p>
        <h3
          id="artemis-signal-title"
          className="mt-1 font-[family-name:var(--font-display)] text-2xl text-[var(--foreground)]"
        >
          {title}
        </h3>
      </div>
      <ArtemisWaterfall key={assets.imageUrl} src={assets.imageUrl} title={title} />
      <div className="space-y-4 px-5 py-4">
        <ArtemisAudio key={assets.audioUrl} src={assets.audioUrl} title={title} />
        <ArtemisFactsList
          facts={facts}
          notes={item.notes ?? ""}
          loading={loading}
          error={error}
        />
        <p className="flex flex-wrap gap-x-4 gap-y-2 text-sm">
          <a
            href={wiki}
            target="_blank"
            rel="noopener noreferrer"
            className="min-h-11 text-[var(--signal)] underline-offset-2 hover:underline"
          >
            SigID wiki
          </a>
          <a
            href={assets.sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="min-h-11 text-[var(--signal)] underline-offset-2 hover:underline"
          >
            Artemis record
          </a>
        </p>
        <p className="max-w-[62ch] text-xs leading-relaxed text-[var(--muted)]">
          Reference waterfall and audio from Artemis-DB (sigidwiki.com content, GPL-3).
          These are wiki screenshots, so treat a library match as a hint.
        </p>
      </div>
    </article>
  );
}
