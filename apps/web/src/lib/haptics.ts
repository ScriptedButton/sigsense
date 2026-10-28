/** Short tap feedback. iOS Safari has no Vibration API, so this is a no-op there. */
export function haptic(ms: number | number[] = 10): void {
  try {
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(ms);
    }
  } catch {
    /* vibration blocked */
  }
}
