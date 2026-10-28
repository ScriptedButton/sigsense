"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { AnalyzePanel } from "@/components/AnalyzePanel";
import { LibraryPanel } from "@/components/LibraryPanel";
import { SectionPage } from "@/components/SectionPage";
import type { SectionId } from "@/lib/sections";

const ReceiverScreen = dynamic(
  () => import("@/components/receiver/ReceiverScreen").then((mod) => mod.ReceiverScreen),
  {
    ssr: false,
    loading: () => (
      <div className="sdr-shell grid min-h-dvh place-items-center">
        <p className="text-sm text-muted">Opening receiver…</p>
      </div>
    ),
  },
);

export default function Home() {
  const [tab, setTab] = useState<SectionId>("receiver");

  return (
    <>
      {/* Stays mounted so a live RTL-SDR or HackRF session survives switching sections. */}
      <div hidden={tab !== "receiver"} className="contents">
        <ReceiverScreen onSectionChange={setTab} />
      </div>
      {tab !== "receiver" ? (
        <SectionPage section={tab} onSectionChange={setTab}>
          {tab === "analyze" ? <AnalyzePanel /> : null}
          {tab === "library" ? <LibraryPanel /> : null}
        </SectionPage>
      ) : null}
    </>
  );
}
