"use client";

import { useCallback, useRef, useState } from "react";

/**
 * Holds the last live value while the pointer is over a region, so a rapidly
 * updating list does not reshuffle under the cursor mid-click.
 */
export function usePointerFreeze<T>(live: T): {
  value: T;
  frozen: boolean;
  bind: {
    onPointerEnter: () => void;
    onPointerLeave: () => void;
  };
} {
  const [frozen, setFrozen] = useState(false);
  const snapshotRef = useRef(live);

  if (!frozen) {
    snapshotRef.current = live;
  }

  const onPointerEnter = useCallback(() => {
    snapshotRef.current = live;
    setFrozen(true);
  }, [live]);

  const onPointerLeave = useCallback(() => {
    setFrozen(false);
  }, []);

  return {
    value: frozen ? snapshotRef.current : live,
    frozen,
    bind: { onPointerEnter, onPointerLeave },
  };
}
