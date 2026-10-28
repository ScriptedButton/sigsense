"""Measure the speech decoder on the radio corpus built by stt_corpus.py.

    python apps/api/scripts/stt_eval.py offline --corpus DIR [--configs a,b] [--json out.json]
    python apps/api/scripts/stt_eval.py stream  --corpus DIR [--snr 15] [--sessions 1]

offline  Per-transmission WER for several model/config choices (accuracy, speed, VRAM).
stream   The real streaming pipeline at real-time pacing: time to first partial, time
         from end of speech to the final, WER of what a user would see, false finals in
         the open-squelch gaps, and (with --sessions N) latency under concurrency.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sigsense_api import stt  # noqa: E402

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(token: str) -> str:
    if len(token) > 1 and token[0] == "0" or len(token) > 2:
        return " ".join(ONES[int(c)] for c in token)  # digit-by-digit (callsigns, frequencies)
    n = int(token)
    if n < 20:
        return ONES[n]
    return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])


def spoken(text: str) -> str:
    """Normalise to how it would be said, so '10-4' and 'ten four' score the same."""
    text = text.lower().replace("-", " ").replace(":", " ")
    text = re.sub(r"\bx[ -]?ray\b", "x ray", text)  # xray / x-ray / x ray score the same
    text = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    out = []
    for tok in text.split():
        if tok.isdigit():
            out.append(number_words(tok))
        else:
            m = re.fullmatch(r"([a-z]+?)(\d+)", tok)  # kab1234 -> kab one two three four
            out.append(f"{m.group(1)} {number_words(m.group(2))}" if m else tok)
    return " ".join(" ".join(out).split())


def wer(refs: list[str], hyps: list[str]) -> float:
    import jiwer

    return float(jiwer.wer([spoken(r) for r in refs], [spoken(h) or "<none>" for h in hyps]))


def load_clip16(path: Path) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32")
    return signal.resample_poly(x, 1, sr // 16000).astype(np.float32)


def vram_mb() -> int | None:
    return stt._nvidia_smi_used_mb()


# --------------------------------------------------------------------------- offline

CONFIGS = {
    # name: (repo, compute, beam, glossary, bandpass+agc)
    "turbo-b5": ("deepdml/faster-whisper-large-v3-turbo-ct2", "float16", 5, True, True),
    "turbo-greedy": ("deepdml/faster-whisper-large-v3-turbo-ct2", "float16", 1, True, True),
    "turbo-noprompt": ("deepdml/faster-whisper-large-v3-turbo-ct2", "float16", 5, False, True),
    "turbo-rawaudio": ("deepdml/faster-whisper-large-v3-turbo-ct2", "float16", 5, True, False),
    "turbo-int8": ("deepdml/faster-whisper-large-v3-turbo-ct2", "int8_float16", 5, True, True),
    "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", "float16", 5, True, True),
    "distil-small.en": ("Systran/faster-distil-whisper-small.en", "float16", 5, True, True),
    "distil-small.en-noprompt": ("Systran/faster-distil-whisper-small.en", "float16", 5, False, True),
    "distil-small.en-short": ("Systran/faster-distil-whisper-small.en", "float16", 5, "short", True),
}


def prep(audio16: np.ndarray, frontend: bool) -> np.ndarray:
    if not frontend:
        return audio16
    return stt.normalise(stt.BandPass().process(audio16))


def run_offline(args) -> dict:
    corpus = Path(args.corpus)
    meta = json.loads((corpus / "corpus.json").read_text())
    names = args.configs.split(",") if args.configs else list(CONFIGS)
    results = {}
    clips = {snr: {cid: load_clip16(corpus / "clips" / str(snr) / f"{cid}.wav") for cid in meta["clips"]} for snr in meta["snrs"]}
    total_audio = {snr: sum(len(a) for a in c.values()) / 16000 for snr, c in clips.items()}
    for name in names:
        repo, compute, beam, glossary, frontend = CONFIGS[name]
        before = vram_mb() or 0
        t0 = time.time()
        model = stt.WhisperTranscriber(repo, "cuda", compute)
        load_s = time.time() - t0
        model.transcribe(np.zeros(16000, dtype=np.float32), language="en", prompt=None, beam_size=1, word_timestamps=False)
        vram = (vram_mb() or 0) - before
        row = {"load_s": round(load_s, 1), "vram_mb": vram, "wer": {}, "rtf": {}}
        for snr in meta["snrs"]:
            hyps, refs, t_dec = [], [], 0.0
            for cid, audio in clips[snr].items():
                t = time.time()
                res = model.transcribe(prep(audio, frontend), language="en",
                                       prompt=(stt.SHORT_GLOSSARY if glossary == "short" else stt.DEFAULT_GLOSSARY) if glossary else None,
                                       beam_size=beam, word_timestamps=False)
                t_dec += time.time() - t
                text = stt.collapse_repeats(res.text)
                hyps.append("" if stt.is_hallucination(text, 3.0, np.exp(res.avg_logprob), res.no_speech_prob) else text)
                refs.append(meta["clips"][cid])
            row["wer"][str(snr)] = round(wer(refs, hyps), 4)
            row["rtf"][str(snr)] = round(t_dec / total_audio[snr], 3)
        results[name] = row
        print(name, json.dumps(row), flush=True)
        del model
        import gc

        gc.collect()
    return results


# --------------------------------------------------------------------------- streaming


class StreamRun:
    def __init__(self, service, wav: Path, timeline: list[dict], label: str, **cfg):
        self.service, self.timeline, self.label = service, timeline, label
        self.audio, self.sr = sf.read(wav, dtype="float32")
        self.events: list[tuple[float, dict]] = []
        config = stt.SessionConfig(sample_rate=self.sr, **cfg)
        self.session = stt.StreamingSession(service, config, lambda e: self.events.append((time.monotonic(), e)))
        self.t0 = 0.0

    def play(self, speed: float = 1.0) -> None:
        chunk = int(0.1 * self.sr)
        self.t0 = time.monotonic()
        for i in range(0, len(self.audio), chunk):
            due = self.t0 + (i / self.sr) / speed
            time.sleep(max(0.0, due - time.monotonic()))
            self.session.feed(self.audio[i : i + chunk])
        self.session.flush()
        # wait for the decode queue to drain
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and (self.service._jobs.qsize() or self.session._partial_in_flight):
            time.sleep(0.05)
        time.sleep(0.5)

    def score(self) -> dict:
        finals = [(w, e) for w, e in self.events if e["type"] == "final" and not e.get("dropped")]
        partials = [(w, e) for w, e in self.events if e["type"] == "partial"]
        refs, hyps, first_partial, final_lat = [], [], [], []
        used = set()
        for clip in self.timeline:
            lo, hi = clip["speech_start"] - 0.6, clip["speech_end"] + 1.2
            mine = [(i, w, e) for i, (w, e) in enumerate(finals) if lo <= (e["start_s"] + e["end_s"]) / 2 <= hi]
            used.update(i for i, _, _ in mine)
            hyps.append(" ".join(e["text"] for _, _, e in mine))
            refs.append(clip["text"])
            if mine:
                final_lat.append(max(w for _, w, _ in mine) - (self.t0 + clip["speech_end"]))
            ps = [w for w, e in partials if lo <= (e["start_s"] + e["end_s"]) / 2 <= hi]
            if ps:
                first_partial.append(min(ps) - (self.t0 + clip["speech_start"]))
        false_finals = [e["text"] for i, (_, e) in enumerate(finals) if i not in used]
        q = lambda v, p: round(float(np.percentile(v, p)), 2) if v else None  # noqa: E731
        return {
            "label": self.label,
            "clips": len(self.timeline),
            "clips_with_final": len(final_lat),
            "wer": round(wer(refs, hyps), 4),
            "first_partial_s": {"p50": q(first_partial, 50), "p90": q(first_partial, 90)},
            "final_after_speech_end_s": {"p50": q(final_lat, 50), "p90": q(final_lat, 90), "max": q(final_lat, 100)},
            "false_finals_in_gaps": len(false_finals),
            "false_final_text": false_finals[:5],
        }


def run_stream(args) -> dict:
    corpus = Path(args.corpus)
    meta = json.loads((corpus / "corpus.json").read_text())
    service = stt.SttService()
    service.ensure_loaded()
    snrs = [int(s) for s in args.snr.split(",")]
    cfg = {"beam_size": args.beam}
    out = {"model": service.status()["model"], "vram_mb": service.status()["vram_mb"], "runs": []}
    runs = []
    for i in range(args.sessions):
        snr = snrs[i % len(snrs)]
        info = meta["streams"][str(snr)]
        runs.append(StreamRun(service, corpus / info["file"], info["timeline"], f"snr{snr}#{i}", **cfg))
    threads = [threading.Thread(target=r.play) for r in runs]
    t = time.time()
    for th in threads:
        th.start()
    peak = {"vram": 0}
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            peak["vram"] = max(peak["vram"], vram_mb() or 0)
            time.sleep(1.0)

    threading.Thread(target=sample, daemon=True).start()
    for th in threads:
        th.join()
    stop.set()
    out["sessions"] = args.sessions
    out["wall_s"] = round(time.time() - t, 1)
    out["peak_vram_mb"] = peak["vram"]
    out["decodes"] = service.stats["decodes"]
    out["queue_max"] = service.stats["queue_max"]
    out["runs"] = [r.score() for r in runs]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["offline", "stream"])
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--configs", default="")
    ap.add_argument("--snr", default="15")
    ap.add_argument("--sessions", type=int, default=1)
    ap.add_argument("--beam", type=int, default=5)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    result = run_offline(args) if args.mode == "offline" else run_stream(args)
    text = json.dumps(result, indent=1)
    print(text)
    if args.json:
        Path(args.json).write_text(text)


if __name__ == "__main__":
    main()
