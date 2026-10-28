"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { LastCallChip } from "@/components/sdr/dvoice/last-call-chip";
import { FrequencyKeypad } from "@/components/sdr/frequency-keypad";
import { HackrfGainSheet } from "@/components/sdr/hackrf-gain-sheet";
import { SCHEME_LABELS } from "@/components/sdr/mode-scheme-chips";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";
import { haptic } from "@/lib/haptics";
import {
  addFavourite,
  getFavourites,
  removeFavourite,
  subscribeFavourites,
  type Favourite,
} from "@/lib/sdr/favourites";
import { BAND_PRESETS } from "@/lib/sdr/format";
import type { DemodScheme } from "@/lib/sdr/types";

/** Tuning step per demodulator: what one nudge should mean on that band. */
const STEP_HZ: Record<DemodScheme, number> = {
  WBFM: 100_000,
  NBFM: 12_500,
  AM: 10_000,
  USB: 1_000,
  LSB: 1_000,
  CW: 100,
};

const SCHEMES = Object.keys(SCHEME_LABELS) as DemodScheme[];
const HOLD_MS = 450;
const REPEAT_MS = 110;

function formatMhz(hz: number): string {
  return hz >= 1e6
    ? (hz / 1e6).toFixed(hz % 1000 === 0 ? 3 : 4)
    : `${(hz / 1e3).toFixed(1)} k`;
}

function Chip({
  label,
  active,
  title,
  onTap,
  onLong,
  variant,
}: {
  label: string;
  active?: boolean;
  title?: string;
  onTap: () => void;
  onLong?: () => void;
  variant?: "fav";
}) {
  // The timer and "a long press just fired" flag are only touched in handlers.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fired = useRef(false);

  const clear = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const onPointerDown = useCallback(() => {
    if (!onLong) return;
    fired.current = false;
    clear();
    timer.current = setTimeout(() => {
      fired.current = true;
      haptic([12, 30, 12]);
      onLong();
    }, HOLD_MS);
  }, [onLong, clear]);

  const onClick = useCallback(() => {
    if (fired.current) {
      fired.current = false; // the click that ends a long press does nothing
      return;
    }
    haptic(6);
    onTap();
  }, [onTap]);

  return (
    <button
      type="button"
      className={`dock-chip ${active ? "dock-chip-active" : ""} ${variant === "fav" ? "dock-chip-fav" : ""}`}
      aria-pressed={active}
      title={title}
      onPointerDown={onPointerDown}
      onPointerUp={clear}
      onPointerLeave={clear}
      onPointerCancel={clear}
      onContextMenu={onLong ? (e) => e.preventDefault() : undefined}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

/**
 * Thumb-reach strip for phones: nudge or type a frequency, volume, mute,
 * stop, then mode / favourites / presets in one scrolling row.
 */
export function MobileDock() {
  const controller = useRadioController();
  const state = useSdrState();
  const favourites = useSyncExternalStore(
    subscribeFavourites,
    getFavourites,
    () => [] as Favourite[],
  );
  const [keypad, setKeypad] = useState(false);
  const [gainOpen, setGainOpen] = useState(false);
  const [announce, setAnnounce] = useState("");
  const [muted, setMuted] = useState(false);
  const lastVolume = useRef(state.volume || 0.8);

  const live = state.status === "playing";
  const listenHz = state.frequencyHz + state.offsetHz;
  const step = STEP_HZ[state.scheme];

  const retune = useCallback(
    (hz: number) => {
      // The listen frequency drives tuning; retune the LO if it leaves the span.
      controller.tuneToHz(hz);
    },
    [controller],
  );

  // Hold a nudge button to keep stepping. Refs are only used inside handlers.
  const repeat = useRef<{
    start?: ReturnType<typeof setTimeout>;
    loop?: ReturnType<typeof setInterval>;
  }>({});
  const stopRepeat = useCallback(() => {
    if (repeat.current.start) clearTimeout(repeat.current.start);
    if (repeat.current.loop) clearInterval(repeat.current.loop);
    repeat.current = {};
  }, []);
  useEffect(() => stopRepeat, [stopRepeat]);

  const onNudgeDown = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      const dir = event.currentTarget.dataset.dir === "-1" ? -1 : 1;
      stopRepeat();
      const go = () => {
        const s = controller.getState();
        controller.tuneToHz(
          s.frequencyHz + s.offsetHz + dir * STEP_HZ[s.scheme],
        );
      };
      go();
      haptic(6);
      repeat.current.start = setTimeout(() => {
        repeat.current.loop = setInterval(go, REPEAT_MS);
      }, HOLD_MS);
    },
    [controller, stopRepeat],
  );

  const pct = Math.round(state.volume * 100);
  const toggleMute = () => {
    haptic(8);
    if (muted || state.volume === 0) {
      controller.setVolume(lastVolume.current || 0.8);
      setMuted(false);
    } else {
      lastVolume.current = state.volume;
      controller.setVolume(0);
      setMuted(true);
    }
  };

  const saveCurrent = () => {
    const fav = addFavourite(listenHz, state.scheme);
    setAnnounce(`Saved ${fav.label} MHz`);
    haptic([10, 30, 10]);
  };

  return (
    <div className="mobile-dock" role="region" aria-label="Now tuning">
      <div className="dock-row dock-tune">
        <button
          type="button"
          className="dock-nudge"
          aria-label={`Tune down ${step >= 1000 ? step / 1000 + " kilohertz" : step + " hertz"}`}
          data-dir="-1"
          onPointerDown={onNudgeDown}
          onPointerUp={stopRepeat}
          onPointerLeave={stopRepeat}
          onPointerCancel={stopRepeat}
        >
          ◀
        </button>
        <button
          type="button"
          className="dock-freq"
          aria-label={`Frequency ${formatMhz(listenHz)} megahertz, ${SCHEME_LABELS[state.scheme]}. Tap to enter a frequency.`}
          onClick={() => setKeypad(true)}
        >
          <span className="dock-freq-value font-mono tabular-nums">
            {formatMhz(listenHz)}
          </span>
          <span className="dock-freq-unit">MHz</span>
          <span className="dock-freq-mode">
            {live && <span className="dock-live" aria-hidden />}
            {SCHEME_LABELS[state.scheme]}
          </span>
        </button>
        <button
          type="button"
          className="dock-nudge"
          aria-label={`Tune up ${step >= 1000 ? step / 1000 + " kilohertz" : step + " hertz"}`}
          data-dir="1"
          onPointerDown={onNudgeDown}
          onPointerUp={stopRepeat}
          onPointerLeave={stopRepeat}
          onPointerCancel={stopRepeat}
        >
          ▶
        </button>
      </div>

      <div className="dock-row dock-audio">
        <button
          type="button"
          className="dock-icon"
          aria-label={muted || state.volume === 0 ? "Unmute" : "Mute"}
          aria-pressed={muted || state.volume === 0}
          onClick={toggleMute}
        >
          {muted || state.volume === 0 ? "🔇" : "🔊"}
        </button>
        <input
          type="range"
          className="slider dock-volume"
          min={0}
          max={100}
          value={pct}
          aria-label="Volume"
          aria-valuetext={`${pct}%`}
          onChange={(e) => {
            setMuted(false);
            controller.setVolume(Number(e.target.value) / 100);
          }}
        />
        <span className="dock-volume-value font-mono tabular-nums" aria-hidden>
          {pct}
        </span>
        {state.connection === "hackrf" && (
          <button
            type="button"
            className="dock-text-btn"
            aria-haspopup="dialog"
            onClick={() => setGainOpen(true)}
          >
            Gain
          </button>
        )}
        {live && (
          <button
            type="button"
            className="dock-text-btn dock-stop"
            onClick={() => {
              haptic(15);
              void controller.stop();
            }}
          >
            Stop
          </button>
        )}
      </div>

      <LastCallChip />
      <div
        className="dock-row dock-chips"
        role="group"
        aria-label="Mode, favourites and presets"
      >
        {SCHEMES.map((scheme) => (
          <Chip
            key={scheme}
            label={SCHEME_LABELS[scheme]}
            active={state.scheme === scheme}
            onTap={() => controller.setScheme(scheme)}
          />
        ))}
        <span className="dock-sep" aria-hidden />
        <Chip
          label="★ Save"
          title="Save the current frequency"
          onTap={saveCurrent}
        />
        {favourites.map((fav) => (
          <Chip
            key={fav.id}
            variant="fav"
            label={fav.label}
            title="Tap to tune · press and hold to remove"
            active={
              Math.abs(listenHz - fav.frequencyHz) < 500 &&
              state.scheme === fav.scheme
            }
            onTap={() => {
              controller.setScheme(fav.scheme);
              retune(fav.frequencyHz);
            }}
            onLong={() => {
              removeFavourite(fav.id);
              setAnnounce(`Removed ${fav.label}`);
            }}
          />
        ))}
        {BAND_PRESETS.map((preset) => (
          <Chip
            key={preset.id}
            label={preset.label}
            active={
              Math.abs(listenHz - preset.frequencyHz) < 500 &&
              state.scheme === preset.scheme
            }
            title="Tap to tune · press and hold to save as a favourite"
            onTap={() => {
              controller.setScheme(preset.scheme);
              void controller.setFrequency(preset.frequencyHz);
              controller.setOffset(0);
            }}
            onLong={() => {
              addFavourite(preset.frequencyHz, preset.scheme);
              setAnnounce(`Saved ${preset.label}`);
            }}
          />
        ))}
      </div>

      <span className="sr-only" role="status" aria-live="polite">
        {announce}
      </span>

      {keypad && (
        <FrequencyKeypad
          frequencyHz={listenHz}
          onChange={retune}
          onClose={() => setKeypad(false)}
        />
      )}
      {gainOpen && <HackrfGainSheet onClose={() => setGainOpen(false)} />}
    </div>
  );
}
