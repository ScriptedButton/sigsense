"use client";

import type { ReactNode } from "react";
import { SectionSwitch } from "@/components/sdr/section-switch";
import type { SectionId } from "@/lib/sections";

type SectionPageProps = {
  section: SectionId;
  onSectionChange: (section: SectionId) => void;
  children: ReactNode;
};

/** Analyze and Library use the receiver's top bar and tokens so the app reads as one tool. */
export function SectionPage({ section, onSectionChange, children }: SectionPageProps) {
  return (
    <div className="sdr-shell sdr-page">
      <header className="app-header">
        <div className="app-header-brand">
          <h1 className="app-header-title">SigSense</h1>
          <p className="app-header-tagline">RTL-SDR · HackRF · BLE</p>
          <SectionSwitch current={section} onChange={onSectionChange} />
        </div>
      </header>
      <main className="sdr-page-body">{children}</main>
      <footer className="status-bar">
        <span>SigSense · open-set SDR classification · local inference</span>
      </footer>
    </div>
  );
}
