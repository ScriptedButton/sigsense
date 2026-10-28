export const SECTIONS = [
  { id: "receiver", label: "Receiver" },
  { id: "analyze", label: "Analyze" },
  { id: "library", label: "Library" },
] as const;

export type SectionId = (typeof SECTIONS)[number]["id"];
