"use client";

import { useState } from "react";
import { Header } from "@/components/Header";
import { AnalyzePanel } from "@/components/AnalyzePanel";
import { LiveSDRPanel } from "@/components/LiveSDRPanel";
import { LibraryPanel } from "@/components/LibraryPanel";

type Tab = "analyze" | "live" | "library";

export default function Home() {
  const [tab, setTab] = useState<Tab>("analyze");

  return (
    <>
      <Header tab={tab} onTabChange={setTab} />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        {tab === "analyze" ? <AnalyzePanel /> : null}
        {tab === "live" ? <LiveSDRPanel /> : null}
        {tab === "library" ? <LibraryPanel /> : null}
      </main>
      <footer className="border-t border-[var(--line)] px-6 py-4 text-center text-xs text-[var(--muted)]">
        SigSense · open-set SDR classification · local inference
      </footer>
    </>
  );
}
