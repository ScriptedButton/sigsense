/** Tool surface the in-app assistant may use. Each tool maps 1:1 onto a validated receiver-control action. */
import type { ReceiverAction } from "@/lib/sdr/receiver-control/protocol";

export type AssistantTool = {
  name: ReceiverAction | "get_found_signals" | "listen_to_found_signal" | "ui_snapshot" | "ui_click" | "ui_set" | "ui_key" | "ui_read";
  description: string;
  input_schema: { type: "object"; properties: Record<string, unknown>; additionalProperties: false; required?: string[] };
};

const num = (description: string) => ({ type: "number", description });
const bool = (description: string) => ({ type: "boolean", description });
const none = { type: "object" as const, properties: {}, additionalProperties: false as const };

export const ASSISTANT_TOOLS: AssistantTool[] = [
  { name: "get_state", description: "Current receiver state: connection status, frequency, offset, mode, gain, volume, errors.", input_schema: none },
  {
    name: "tune",
    description: "Tune to a frequency in Hz, optionally switching demodulation mode. Use this for 'go to 7.2 MHz', 'tune to NOAA weather', etc.",
    input_schema: {
      type: "object", additionalProperties: false, required: ["frequencyHz"],
      properties: {
        frequencyHz: num("Frequency in Hz (e.g. 7200000 for 7.2 MHz)"),
        scheme: { type: "string", enum: ["WBFM", "NBFM", "AM", "USB", "LSB", "CW"], description: "Demodulation mode" },
        bandwidthHz: num("Filter bandwidth in Hz"),
        center: bool("Also move the hardware center frequency"),
      },
    },
  },
  {
    name: "configure",
    description: "Change receiver settings: volume, squelch, gain, mode, bandwidth, noise reduction, AFC, etc. Only include fields to change.",
    input_schema: {
      type: "object", additionalProperties: false,
      properties: {
        frequencyHz: num("Center frequency in Hz"), offsetHz: num("Tuning offset from center in Hz"),
        bandwidthHz: num("Filter bandwidth in Hz"), sampleRate: num("Sample rate in Hz"),
        gainDb: num("Tuner gain 0-50 dB"), volume: num("Volume 0-1"), squelch: num("Squelch 0-100"), ppm: num("Frequency correction ppm"),
        scheme: { type: "string", enum: ["WBFM", "NBFM", "AM", "USB", "LSB", "CW"] },
        audioNoiseReduction: { type: "string", enum: ["off", "low", "medium", "high"] },
        autoGain: bool("Automatic gain"), autoSquelch: bool("Automatic squelch"), afc: bool("Automatic frequency control"),
        biasTee: bool("Bias tee power"), voiceIsolation: bool("AI voice isolation"), audioSpeechFilter: bool("Speech band filter"),
      },
    },
  },
  {
    name: "connect",
    description: "Connect a radio source. 'demo' needs no hardware. 'kiwi', 'openwebrx' and 'rtltcp' take a url.",
    input_schema: {
      type: "object", additionalProperties: false, required: ["type"],
      properties: { type: { type: "string", enum: ["demo", "rtltcp", "kiwi", "openwebrx"] }, url: { type: "string" }, password: { type: "string" } },
    },
  },
  { name: "stop", description: "Stop reception and disconnect.", input_schema: none },
  { name: "get_signals", description: "Strongest signal peaks currently visible in the spectrum (frequency, power, dB above noise floor).", input_schema: none },
  {
    name: "find_signal", description: "Strongest spectrum peak, or the peak nearest a given frequency.",
    input_schema: { type: "object", additionalProperties: false, properties: { frequencyHz: num("Frequency to search near, in Hz") } },
  },
  {
    name: "get_schedule",
    description: "Shortwave broadcast schedule: what is on air now/soon, filterable by band, language, or text (station, country).",
    input_schema: {
      type: "object", additionalProperties: false,
      properties: {
        when: { type: "string", enum: ["now", "soon", "all"] }, bandId: { type: "string", description: "Shortwave band id, e.g. 49m" },
        language: { type: "string" }, query: { type: "string", description: "Free text match, e.g. a station name" },
      },
    },
  },
  { name: "get_display", description: "Current display settings (palette, speed, ranges, markers, frequency format).", input_schema: none },
  {
    name: "set_display",
    description: "Change display settings. Only include fields to change.",
    input_schema: {
      type: "object", additionalProperties: false,
      properties: {
        waterfallPalette: { type: "string", enum: ["cubehelix", "thermal", "viridis", "ocean", "mono"] },
        waterfallSpeed: { type: "string", enum: ["slow", "normal", "fast"] },
        waterfallDirection: { type: "string", enum: ["top", "bottom"] },
        dbRange: { type: "string", enum: ["auto", "wide", "normal", "tight"] },
        noiseFilter: { type: "string", enum: ["off", "low", "medium", "high"] },
        freqTarget: { type: "string", enum: ["tune", "center"] },
        freqFormat: { type: "string", enum: ["auto", "mhz", "khz", "hz"] },
        waterfallRangeDb: num("Waterfall range 16-60 dB"), waterfallFloorOffsetDb: num("Waterfall floor offset -12 to 12 dB"),
        markerMode: { type: "string", description: "Signal label mode (off / strongest / all)" },
      },
    },
  },
  {
    name: "get_found_signals",
    description: "Signals the always-on background shortwave finder has ALREADY found and confirmed (frequency, band, speech ratio, confirmations, last seen), best first. Check this first for any 'find me a signal' request.",
    input_schema: none,
  },
  {
    name: "listen_to_found_signal",
    description: "Start listening to a finding from get_found_signals: takes the receiver from the background service, connects, tunes AM with speech cleanup. Use this (not tune) to play a found signal.",
    input_schema: { type: "object", additionalProperties: false, required: ["frequencyHz"], properties: { frequencyHz: num("frequencyHz of a finding, in Hz") } },
  },
  {
    name: "ui_snapshot",
    description: "List the controls currently visible on screen (buttons, tabs, sliders, selects, inputs) with ids, labels and values. Use this to find any control the dedicated tools don't cover. Pass a filter word to narrow the list.",
    input_schema: { type: "object", additionalProperties: false, properties: { filter: { type: "string", description: "Only controls whose label contains this text" } } },
  },
  {
    name: "ui_click",
    description: "Click a control by its ui_snapshot id (buttons, tabs, toggles, links). Take a fresh ui_snapshot afterwards if the screen changed.",
    input_schema: { type: "object", additionalProperties: false, required: ["id"], properties: { id: { type: "string" }, confirm: bool("Only after the user agreed to a destructive action") } },
  },
  {
    name: "ui_set",
    description: "Set a slider, number/text input, select (by option text) or checkbox by ui_snapshot id.",
    input_schema: { type: "object", additionalProperties: false, required: ["id", "value"], properties: { id: { type: "string" }, value: { type: ["string", "number", "boolean"] } } },
  },
  {
    name: "ui_key",
    description: "Press a keyboard shortcut (key name like 'm' or 'ArrowUp', optional modifiers).",
    input_schema: { type: "object", additionalProperties: false, required: ["key"], properties: { key: { type: "string" }, ctrl: bool("Ctrl/Cmd held"), shift: bool("Shift held"), alt: bool("Alt held") } },
  },
  {
    name: "ui_read",
    description: "Read the text currently shown on screen (transcripts, results, status, station info). Optional filter keeps only lines containing a word.",
    input_schema: { type: "object", additionalProperties: false, properties: { filter: { type: "string" } } },
  },
  { name: "autofinder_state", description: "State of the autonomous shortwave finder.", input_schema: none },
  {
    name: "autofinder_start", description: "Start the autonomous finder, which scans for and listens to interesting shortwave signals by itself.",
    input_schema: { type: "object", additionalProperties: false, properties: { maxListenMs: num("Max listen time per signal, 10000-300000 ms") } },
  },
  { name: "autofinder_stop", description: "Stop the autonomous finder.", input_schema: none },
  { name: "autofinder_skip", description: "Skip the signal the autonomous finder is on.", input_schema: none },
];

export const ASSISTANT_SYSTEM_PROMPT = `You are the built-in assistant of SigSense, a software-defined radio receiver web app. The user types plain-language requests; you carry them out by calling tools that control the live receiver, then reply in one or two short sentences saying what you did.

Rules:
- Act first; don't ask permission for reversible receiver actions. Ask a question only when a request is truly ambiguous.
- Convert units yourself (MHz/kHz to Hz). Pick a sensible mode if none is given: AM for broadcast/airband/shortwave broadcasts, WBFM for FM broadcast, NBFM for voice/FM utility, USB above 10 MHz and LSB below for amateur voice, CW for Morse.
- If the receiver isn't connected (status not "playing"), connect "demo" or tell the user what to connect before tuning.
- Check results from tools; if one errors, say so plainly and try a fix once.
- For 'find/play me a signal' requests: call get_found_signals first and call listen_to_found_signal on the best finding. Only start the autofinder if there are no findings.
- You can operate ANY control in the app: when no dedicated tool fits, call ui_snapshot (with a filter word), then ui_click / ui_set by id; use ui_read to read what's on screen. Prefer dedicated tools for tuning and receiver settings. Ids are valid until the next ui_snapshot.
- Before anything destructive (delete, reset, clear, remove) ask the user first; only pass confirm=true after they agree.
- Voice requests may be mis-transcribed; interpret generously ("gain up" means raise gain a few dB: read the state, then set gainDb).
- Never invent signal or station information; use get_signals / get_schedule.
- Keep replies terse. No markdown headings.`;
