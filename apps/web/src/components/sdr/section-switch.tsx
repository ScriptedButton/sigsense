"use client";

import { SECTIONS, type SectionId } from "@/lib/sections";

type SectionSwitchProps = {
  current: SectionId;
  onChange: (section: SectionId) => void;
};

export function SectionSwitch({ current, onChange }: SectionSwitchProps) {
  return (
    <nav className="app-header-modes" aria-label="SigSense">
      {SECTIONS.map((section) => (
        <button
          key={section.id}
          type="button"
          aria-current={section.id === current ? "page" : undefined}
          onClick={() => onChange(section.id)}
        >
          {section.label}
        </button>
      ))}
    </nav>
  );
}
