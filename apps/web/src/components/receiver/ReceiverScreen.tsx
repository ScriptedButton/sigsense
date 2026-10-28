"use client";

import { HackrfProvider } from "@/components/HackrfPanel";
import { CorrectionHost } from "@/components/sdr/signal-id/correction-host";
import { SdrApp } from "@/components/sdr/sdr-app";
import { SdrProvider } from "@/components/sdr/sdr-provider";
import type { SectionId } from "@/lib/sections";

type ReceiverScreenProps = {
  onSectionChange: (section: SectionId) => void;
};

export function ReceiverScreen({ onSectionChange }: ReceiverScreenProps) {
  return (
    <SdrProvider>
      <HackrfProvider>
        <CorrectionHost>
          <SdrApp onSectionChange={onSectionChange} />
        </CorrectionHost>
      </HackrfProvider>
    </SdrProvider>
  );
}
