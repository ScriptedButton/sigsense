"use client";

import { useState } from "react";

type ArtemisWaterfallProps = {
  src: string;
  title: string;
};

export function ArtemisWaterfall({ src, title }: ArtemisWaterfallProps) {
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");

  return (
    <div className="relative min-h-48 bg-black">
      {phase === "loading" ? (
        <p className="absolute left-4 top-4 text-xs text-[var(--muted)]">
          Loading waterfall…
        </p>
      ) : null}
      {phase === "error" ? (
        <p className="px-4 py-10 text-sm text-[var(--muted)]">
          Waterfall image unavailable for {title}.
        </p>
      ) : (
        <img
          src={src}
          alt={`Reference waterfall for ${title}`}
          className="max-h-[28rem] w-full object-contain"
          decoding="async"
          onLoad={() => setPhase("ready")}
          onError={() => setPhase("error")}
        />
      )}
    </div>
  );
}
