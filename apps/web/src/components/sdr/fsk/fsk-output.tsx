"use client";

import { useEffect, useRef } from "react";

type FskOutputProps = {
  text: string;
  enabled: boolean;
};

export function FskOutput({ text, enabled }: FskOutputProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [text.length]);

  return (
    <div
      ref={scrollerRef}
      className="transcript-pane mt-3 font-mono text-sm leading-relaxed"
      aria-live="polite"
      aria-relevant="additions"
    >
      {text.length === 0 ? (
        <p className="font-sans text-sm text-muted">
          {enabled
            ? "Characters appear here as mark/space frames decode."
            : "Enable Decode to read FSK or RTTY from the current demod."}
        </p>
      ) : (
        <pre className="m-0 whitespace-pre-wrap break-words text-ink">{text}</pre>
      )}
    </div>
  );
}
