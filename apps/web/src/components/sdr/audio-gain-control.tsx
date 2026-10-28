"use client";

import { useHackrf } from "@/components/HackrfPanel";
import { useRadioController, useSdrState } from "@/components/sdr/sdr-provider";

export function AudioGainControl() {
  const controller = useRadioController();
  const state = useSdrState();
  const remote =
    state.connection === "openwebrx" || state.connection === "kiwi";
  const hackrf = useHackrf();
  const stages = state.connection === "hackrf";

  return (
    <fieldset className="control-group">
      <legend className="control-label">
        {stages ? "Audio & HackRF gain" : "Audio & RF"}
      </legend>

      <label className="block text-xs text-muted">
        Volume · {Math.round(state.volume * 100)}%
        <input
          type="range"
          className="slider mt-1"
          min={0}
          max={100}
          value={Math.round(state.volume * 100)}
          onChange={(e) => controller.setVolume(Number(e.target.value) / 100)}
        />
      </label>

      {stages ? (
        <>
          <label
            className="mt-3 block text-xs text-muted"
            title="LNA: the first amplifier, right at the antenna. Raise it for weak signals; too much overloads on strong ones and creates false signals. Steps of 8 dB."
          >
            LNA gain · {hackrf.gain.lna} dB
            <input
              type="range"
              className="slider mt-1"
              min={0}
              max={40}
              step={8}
              value={hackrf.gain.lna}
              onChange={(e) => hackrf.setGain({ lna: Number(e.target.value) })}
            />
          </label>
          <label
            className="mt-2 block text-xs text-muted"
            title="VGA: the baseband amplifier after the mixer. Raise it until the noise floor sits a little above the bottom of the display. Steps of 2 dB."
          >
            VGA gain · {hackrf.gain.vga} dB
            <input
              type="range"
              className="slider mt-1"
              min={0}
              max={62}
              step={2}
              value={hackrf.gain.vga}
              onChange={(e) => hackrf.setGain({ vga: Number(e.target.value) })}
            />
          </label>
          <label
            className="mt-2 flex items-center gap-2 text-sm text-ink"
            title="A +14 dB amplifier in front of the LNA. Helps with very weak signals, but overloads easily near strong transmitters. Leave off unless you need it."
          >
            <input
              type="checkbox"
              checked={hackrf.gain.amp}
              onChange={(e) => hackrf.setGain({ amp: e.target.checked })}
            />
            RF amp (+14 dB)
          </label>
        </>
      ) : (
        <>
      <label className="mt-3 flex items-center gap-2 text-sm text-ink">
        <input
          type="checkbox"
          checked={state.autoGain}
          disabled={remote}
          onChange={(e) => void controller.setAutoGain(e.target.checked)}
        />
        Automatic gain
      </label>

      <label className="mt-2 block text-xs text-muted">
        Gain · {state.autoGain ? "AGC" : `${state.gainDb} dB`}
        <input
          type="range"
          className="slider mt-1"
          min={0}
          max={50}
          step={0.5}
          disabled={remote || state.autoGain}
          value={state.gainDb}
          onChange={(e) => void controller.setGain(Number(e.target.value))}
        />
      </label>

        </>
      )}

      <label className="mt-3 block text-xs text-muted">
        PPM correction · {state.ppm}
        <input
          type="range"
          className="slider mt-1"
          min={-100}
          max={100}
          step={1}
          disabled={remote || stages}
          title={stages ? "RTL-SDR only; not available on HackRF" : undefined}
          value={state.ppm}
          onChange={(e) => void controller.setPpm(Number(e.target.value))}
        />
      </label>

      <label className="mt-3 flex items-center gap-2 text-sm text-ink">
        <input
          type="checkbox"
          checked={state.biasTee}
          title={stages ? "RTL-SDR only; not available on HackRF" : undefined}
          disabled={remote || stages}
          onChange={(e) => void controller.setBiasTee(e.target.checked)}
        />
        Bias-T
      </label>
      {stages && (
        <p className="control-hint">
          PPM and Bias-T are RTL-SDR settings. HackRF gain is split into LNA
          (coarse, 8 dB steps) and VGA (fine, 2 dB steps); changing either
          restarts the stream briefly.
        </p>
      )}
      {remote && (
        <p className="mt-2 text-[0.7rem] leading-snug text-muted">
          Gain, PPM, and bias tee are set on the{" "}
          {state.connection === "kiwi" ? "KiwiSDR" : "OpenWebRX+"} server.
          Volume and squelch still apply here.
        </p>
      )}
    </fieldset>
  );
}
