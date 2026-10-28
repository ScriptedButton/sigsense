const REPO = "AresValley/Artemis-DB";
const REF = "main";

export type ArtemisValue<T> = { value: T; description?: string };

/** signal.json shape we read. Extra fields from Artemis are ignored. */
export type ArtemisRecord = {
  pageid: string;
  title: string;
  category?: string[];
  frequency?: ArtemisValue<number>[];
  bandwidth?: ArtemisValue<number>[];
  modulation?: ArtemisValue<string>[];
  mode?: ArtemisValue<string>[];
  location?: ArtemisValue<string>[];
  "short description"?: string;
  description?: string;
};

export type ArtemisAssets = {
  imageUrl: string;
  audioUrl: string;
  recordUrl: string;
  sourceUrl: string;
};

export type ArtemisFacts = {
  frequencies: string;
  bandwidth: string;
  modulation: string;
  mode: string;
  location: string;
  categories: string;
  summary: string;
  wikiUrl: string;
};

/** Media for one Artemis-DB page. Rejects anything that is not a decimal id. */
export function artemisAssets(pageId: string): ArtemisAssets | null {
  if (!/^\d+$/.test(pageId)) return null;
  const base = `https://raw.githubusercontent.com/${REPO}/${REF}/static/${pageId}`;
  return {
    imageUrl: `${base}/media/1.png`,
    audioUrl: `${base}/media/1.ogg`,
    recordUrl: `${base}/signal.json`,
    sourceUrl: `https://github.com/${REPO}/tree/${REF}/static/${pageId}`,
  };
}

export function sigidWikiUrl(title: string): string {
  const slug = title.trim().replace(/ /g, "_");
  return `https://www.sigidwiki.com/wiki/${encodeURIComponent(slug)}`;
}

export function formatHz(hz: number): string {
  if (!Number.isFinite(hz)) return "";
  const abs = Math.abs(hz);
  if (abs >= 1e9) return `${trim(hz / 1e9)} GHz`;
  if (abs >= 1e6) return `${trim(hz / 1e6)} MHz`;
  if (abs >= 1e3) return `${trim(hz / 1e3)} kHz`;
  return `${trim(hz)} Hz`;
}

function trim(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

/** Wiki markup to plain text. Rendered as text, never as HTML. */
export function plainSignalText(value: string): string {
  return value
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function joinLimited(parts: string[], limit = 4): string {
  const clean = parts.filter(Boolean);
  if (clean.length <= limit) return clean.join(", ");
  return `${clean.slice(0, limit).join(", ")} +${clean.length - limit} more`;
}

function named(value: string, description?: string): string {
  const name = value.trim();
  const detail = description?.trim();
  if (!detail || detail.toLowerCase() === name.toLowerCase()) return name;
  return `${name} (${detail})`;
}

export function describeArtemis(record: ArtemisRecord): ArtemisFacts {
  const summarySource = record["short description"] || record.description || "";
  const summary = plainSignalText(summarySource).slice(0, 420);
  return {
    frequencies: joinLimited(
      (record.frequency ?? []).map((item) => formatHz(Number(item.value))),
    ),
    bandwidth: joinLimited(
      (record.bandwidth ?? []).map((item) => formatHz(Number(item.value))),
    ),
    modulation: joinLimited(
      (record.modulation ?? []).map((item) => named(String(item.value), item.description)),
    ),
    mode: joinLimited(
      (record.mode ?? []).map((item) => named(String(item.value), item.description)),
      3,
    ),
    location: joinLimited(
      (record.location ?? []).map((item) => String(item.value).trim()),
      3,
    ),
    categories: (record.category ?? []).slice(0, 6).join(", "),
    summary,
    wikiUrl: sigidWikiUrl(record.title || ""),
  };
}
