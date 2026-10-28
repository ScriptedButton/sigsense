"use client";

import { useEffect, useState } from "react";

type ArtemisAudioProps = {
  src: string;
  title: string;
};

export function ArtemisAudio({ src, title }: ArtemisAudioProps) {
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    setMissing(false);
    const controller = new AbortController();
    fetch(src, { method: "HEAD", signal: controller.signal })
      .then((res) => {
        if (!res.ok) setMissing(true);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setMissing(true);
      });
    return () => controller.abort();
  }, [src]);

  if (missing) {
    return (
      <p className="text-sm text-[var(--muted)]">No audio sample for this signal.</p>
    );
  }

  return (
    <audio
      key={src}
      controls
      preload="none"
      src={src}
      className="h-11 w-full"
      aria-label={`Audio sample of ${title}`}
      onError={() => setMissing(true)}
    />
  );
}
