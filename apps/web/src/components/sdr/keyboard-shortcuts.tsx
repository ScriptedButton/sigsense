"use client";

import { useEffect, useRef, useState } from "react";

import { useHackrf } from "@/components/HackrfPanel";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import type { ConnectionMode } from "@/lib/sdr/types";

const SHORTCUTS: { keys: string[]; action: string }[] = [
  { keys: ["Space"], action: "Stop, or restart the last source" },
  { keys: ["Shift", "←", "→"], action: "Tune down / up 1 kHz" },
  { keys: ["Ctrl", "Shift", "←", "→"], action: "Tune down / up 100 Hz" },
  { keys: ["←", "→"], action: "Pan the scope (scope focused)" },
  { keys: ["+", "−"], action: "Zoom the scope (scope focused)" },
  { keys: ["?"], action: "Show or hide this list" },
  { keys: ["Esc"], action: "Close this list" },
];

/** Keys that belong to the focused control, not the global shortcuts. */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return /^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(target.tagName) ||
    target.getAttribute("role") === "tab" ||
    target.getAttribute("role") === "slider";
}

/**
 * Global shortcuts plus a "?" help dialog. Space toggles the radio; Shift+arrows
 * fine-tune. Ignored while a form control, button or tab has focus so it never
 * hijacks the control's own keys.
 */
export function KeyboardShortcuts() {
  const controller = useRadioController();
  const state = useSdrState();
  const hackrf = useHackrf();
  const [open, setOpen] = useState(false);
  const lastSource = useRef<ConnectionMode>("disconnected");
  const live = useRef({ state, hackrf });

  useEffect(() => {
    live.current = { state, hackrf };
    if (state.status === "playing") lastSource.current = state.connection;
  }, [state, hackrf]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.altKey || event.metaKey) return;
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      if (isTyping(event.target)) return;
      const { state: s, hackrf: h } = live.current;

      if (event.key === "?") {
        event.preventDefault();
        setOpen((value) => !value);
        return;
      }
      if (event.key === " " && !event.ctrlKey && !event.shiftKey) {
        event.preventDefault();
        if (s.status === "playing" || s.status === "starting") {
          void controller.stop();
        } else if (s.status === "idle" || s.status === "error") {
          // Only sources that need no address can restart from a keypress.
          const source = lastSource.current;
          if (source === "demo") void controller.connectDemo();
          else if (source === "device") void controller.connectDevice();
          else if (source === "hackrf") h.connect();
        }
        return;
      }
      if (
        event.shiftKey &&
        (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
        s.status === "playing"
      ) {
        event.preventDefault();
        const step = event.ctrlKey ? 100 : 1_000;
        const dir = event.key === "ArrowLeft" ? -1 : 1;
        controller.tuneToHz(s.frequencyHz + s.offsetHz + dir * step);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [controller]);

  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  return (
    <>
      <button
        type="button"
        className="mode-chip"
        aria-label="Keyboard shortcuts"
        title="Keyboard shortcuts (?)"
        onClick={() => setOpen(true)}
      >
        ?
      </button>
      {open && (
        <div
          className="shortcuts-backdrop"
          onClick={() => setOpen(false)}
          role="presentation"
        >
          <div
            className="shortcuts-panel"
            role="dialog"
            aria-modal="true"
            aria-label="Keyboard shortcuts"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 className="text-sm font-medium">Keyboard shortcuts</h2>
            <dl className="shortcuts-list">
              {SHORTCUTS.map((item) => (
                <div key={item.action} className="contents">
                  <dt>
                    {item.keys.map((key) => (
                      <kbd key={key}>{key}</kbd>
                    ))}
                  </dt>
                  <dd>{item.action}</dd>
                </div>
              ))}
            </dl>
            <button
              ref={closeRef}
              type="button"
              className="btn-secondary"
              onClick={() => setOpen(false)}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </>
  );
}
