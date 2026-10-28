"""Server-side live speech transcription for demodulated radio audio.

Pipeline per session::

    PCM (any rate) -> resample 16 kHz -> 300-3400 Hz band-pass -> VAD / endpointing
      -> utterance buffer -> PARTIAL decodes while speaking (greedy, revisable)
                          -> FINAL decode at the end of the transmission
                             (beam search, word timestamps, hallucination filter)

One shared model serves every session. All decodes go through a single worker
thread with a priority queue (finals before partials, one partial in flight per
session, stale partials dropped) so concurrent sessions cannot exhaust the GPU.

The model and the voice detector are injectable so the endpointing logic is
unit-testable without a GPU (see tests/test_stt.py).
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Optional, Protocol

import numpy as np
from scipy import signal

log = logging.getLogger("sigsense.stt")

SAMPLE_RATE = 16_000
FRAME = 512  # 32 ms: what Silero VAD expects at 16 kHz
FRAME_S = FRAME / SAMPLE_RATE

DEFAULT_MODEL = os.environ.get(
    "SIGSENSE_STT_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2"
)
CPU_FALLBACK_MODEL = os.environ.get(
    "SIGSENSE_STT_CPU_MODEL", "Systran/faster-distil-whisper-small.en"
)

# Vocabulary the radio services use. Whisper conditions on this prompt, which
# markedly helps callsigns, ten-codes and phonetic spelling.
DEFAULT_GLOSSARY = (
    "Radio traffic. Callsigns like KAB1234, WQXY567, W1ABC. GMRS FRS channel 15. "
    "Ten-codes: 10-4, 10-20. Phonetic alphabet: alpha bravo charlie delta echo "
    "foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec "
    "romeo sierra tango uniform victor whiskey x-ray yankee zulu. Roger, copy, over."
)

# For small models: a long prompt makes them loop ("we're, we're, we're, ..."), so
# they get this one-liner (or nothing). Measured in docs/speech-decoder-research.md.
SHORT_GLOSSARY = "Two-way radio traffic."

# Phrases Whisper invents on silence/noise (training-data artefacts).
HALLUCINATIONS = {
    "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "please subscribe", "bye", "bye bye", "goodbye",
    "you", "the end", "so", "okay", "oh", "uh", "um", "hmm", "mm", "mhm",
    "subtitles by the amaraorg community", "subtitles by amaraorg",
    "transcribed by", "i'm sorry", "see you next time", "music",
}


# --------------------------------------------------------------------------
# Voice detection
# --------------------------------------------------------------------------


class SpeechDetector(Protocol):
    def reset(self) -> None: ...
    def prob(self, frame: np.ndarray) -> float: ...


class EnergyDetector:
    """Adaptive-threshold fallback: speech probability from level above the floor."""

    def __init__(self) -> None:
        self.floor_db = -60.0

    def reset(self) -> None:
        self.floor_db = -60.0

    def prob(self, frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(frame * frame)) + 1e-9)
        db = 20.0 * math.log10(rms)
        # Track the floor down fast, up slowly, so speech doesn't raise it.
        if db < self.floor_db:
            self.floor_db = db
        else:
            self.floor_db += (db - self.floor_db) * 0.002
        return float(np.clip((db - self.floor_db - 6.0) / 10.0, 0.0, 1.0))


class SileroDetector:
    """Streaming Silero VAD (the ONNX model bundled with faster-whisper).

    faster-whisper's own wrapper resets state on every call; this keeps the
    recurrent state and the 64-sample context across frames.
    """

    CONTEXT = 64

    def __init__(self) -> None:
        import onnxruntime
        from faster_whisper.vad import get_assets_path

        path = None
        for name in sorted(os.listdir(get_assets_path())):
            if name.startswith("silero_vad") and name.endswith(".onnx"):
                path = os.path.join(get_assets_path(), name)
        if path is None:
            raise RuntimeError("Silero VAD model not found")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(
            path, providers=["CPUExecutionProvider"], sess_options=opts
        )
        self.reset()

    def reset(self) -> None:
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def prob(self, frame: np.ndarray) -> float:
        x = np.concatenate([self.context, frame[None, :].astype(np.float32)], axis=1)
        out, self.h, self.c = self.session.run(
            None, {"input": x, "h": self.h, "c": self.c}
        )
        self.context = x[:, -self.CONTEXT :]
        return float(np.ravel(out)[0])


def make_detector() -> SpeechDetector:
    try:
        return SileroDetector()
    except Exception as exc:  # onnxruntime missing, model missing, ...
        log.warning("Silero VAD unavailable (%s); using energy detector", exc)
        return EnergyDetector()


# --------------------------------------------------------------------------
# Audio front end
# --------------------------------------------------------------------------


class StreamResampler:
    """Stateful mono resampler to 16 kHz (anti-aliased, no chunk-edge clicks)."""

    def __init__(self, src_rate: float, dst_rate: int = SAMPLE_RATE) -> None:
        self.src = float(src_rate)
        self.dst = dst_rate
        self._passthrough = abs(self.src - dst_rate) < 1
        if self._passthrough:
            return
        cutoff = 0.45 * min(self.src, dst_rate)
        taps = signal.firwin(255 if self.src > dst_rate else 63, cutoff, fs=self.src)
        self._b = taps.astype(np.float64)
        self._zi = np.zeros(len(self._b) - 1)
        self._step = self.src / dst_rate
        self._pos = 0.0  # fractional read position into the filtered stream
        self._tail = np.zeros(1)

    def process(self, x: np.ndarray) -> np.ndarray:
        if self._passthrough or x.size == 0:
            return x.astype(np.float32, copy=False)
        y, self._zi = signal.lfilter(self._b, [1.0], x.astype(np.float64), zi=self._zi)
        y = np.concatenate([self._tail, y])
        # Linear interpolation at fractional positions (exact for integer ratios).
        n_out = int((len(y) - 1 - self._pos) / self._step) + 1 if len(y) > 1 else 0
        if n_out <= 0:
            self._tail = y
            return np.zeros(0, dtype=np.float32)
        idx = self._pos + self._step * np.arange(n_out)
        i0 = np.floor(idx).astype(int)
        frac = idx - i0
        i1 = np.minimum(i0 + 1, len(y) - 1)
        out = y[i0] * (1 - frac) + y[i1] * frac
        consumed = int(idx[-1] + self._step)
        self._pos = idx[-1] + self._step - consumed
        self._tail = y[consumed:] if consumed < len(y) else np.zeros(0)
        if self._tail.size == 0:
            self._tail = np.zeros(0)
        return out.astype(np.float32)


class BandPass:
    """Streaming 300-3400 Hz band-pass: the band a narrowband voice channel carries."""

    def __init__(self, low: float = 300.0, high: float = 3400.0) -> None:
        self.sos = signal.butter(4, [low, high], btype="bandpass", fs=SAMPLE_RATE, output="sos")
        self.zi = np.zeros((self.sos.shape[0], 2))

    def process(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = signal.sosfilt(self.sos, x.astype(np.float64), zi=self.zi)
        return y.astype(np.float32)


def normalise(audio: np.ndarray, target_peak: float = 0.7, max_gain_db: float = 24.0) -> np.ndarray:
    """Remove DC and bring the utterance to a consistent level (quiet radios are common)."""
    if audio.size == 0:
        return audio
    audio = audio - float(np.mean(audio))
    peak = float(np.max(np.abs(audio))) + 1e-6
    gain = min(10 ** (max_gain_db / 20.0), target_peak / peak)
    return (audio * gain).astype(np.float32)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass
class Word:
    w: str
    start: float
    end: float
    p: float


@dataclass
class DecodeResult:
    text: str
    words: list[Word] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    language: str = "en"


class Transcriber(Protocol):
    def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: Optional[str],
        prompt: Optional[str],
        beam_size: int,
        word_timestamps: bool,
    ) -> DecodeResult: ...


class WhisperTranscriber:
    """faster-whisper (CTranslate2) wrapper, tuned for noisy short radio utterances."""

    def __init__(self, model: str, device: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        self.name = model
        self.device = device
        self.compute_type = compute_type
        try:
            self.model = WhisperModel(model, device=device, compute_type=compute_type, local_files_only=True)
        except Exception:
            # Not cached yet: allow a one-time download.
            self.model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio, *, language, prompt, beam_size, word_timestamps) -> DecodeResult:
        segments, info = self.model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=beam_size,
            best_of=1,
            temperature=[0.0, 0.2] if beam_size > 1 else 0.0,
            # Utterances are already endpointed; a rolling prompt is passed
            # explicitly, so don't also chain Whisper's own previous text.
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            word_timestamps=word_timestamps,
            without_timestamps=not word_timestamps,
            vad_filter=False,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            suppress_blank=True,
        )
        texts: list[str] = []
        words: list[Word] = []
        logprobs: list[float] = []
        nsp: list[float] = []
        for seg in segments:
            texts.append(seg.text.strip())
            logprobs.append(seg.avg_logprob)
            nsp.append(seg.no_speech_prob)
            for w in seg.words or []:
                words.append(Word(w.word, float(w.start), float(w.end), float(w.probability)))  # keeps Whisper's leading space
        return DecodeResult(
            text=" ".join(t for t in texts if t).strip(),
            words=words,
            avg_logprob=float(np.mean(logprobs)) if logprobs else -5.0,
            no_speech_prob=float(np.max(nsp)) if nsp else 1.0,
            language=getattr(info, "language", language or "en"),
        )


def _nvidia_smi_used_mb() -> Optional[int]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for line in out.splitlines():
            pid, mb = [s.strip() for s in line.split(",")]
            if int(pid) == os.getpid():
                return int(mb)
    except Exception:
        pass
    return None


class SttService:
    """Owns the shared model and the single decode worker."""

    def __init__(self, transcriber: Optional[Transcriber] = None) -> None:
        self._transcriber = transcriber
        self._lock = threading.Lock()
        self._loading = False
        self._error: Optional[str] = None
        self.model_name = DEFAULT_MODEL
        self.device = "unknown"
        self.compute_type = ""
        self._jobs: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._worker: Optional[threading.Thread] = None
        self.sessions = 0
        self.stats = {"decodes": 0, "last_decode_ms": 0.0, "queue_max": 0}

    # -- availability / loading ------------------------------------------

    @staticmethod
    def _cuda() -> bool:
        try:
            import ctranslate2

            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    @staticmethod
    def package_available() -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except Exception:
            return False

    def _resolve(self) -> tuple[str, str, str]:
        if self._cuda():
            return DEFAULT_MODEL, "cuda", os.environ.get("SIGSENSE_STT_COMPUTE", "float16")
        return CPU_FALLBACK_MODEL, "cpu", "int8"

    def model_cached(self) -> bool:
        try:
            # Same lookup faster-whisper does when loading (only the files it needs).
            from faster_whisper.utils import download_model

            download_model(self._resolve()[0], local_files_only=True)
            return True
        except Exception:
            return False

    def prompt_style(self) -> str:
        """"full" for large models; "short" for small ones, which loop on a long prompt."""
        name = str(getattr(self._transcriber, "name", None) or self._resolve()[0]).lower()
        return "short" if any(tag in name for tag in ("tiny", "base", "small")) else "full"

    def status(self) -> dict:
        model, device, compute = self._resolve()
        loaded = self._transcriber is not None
        return {
            "available": self.package_available() or loaded,
            "ready": loaded,
            "loading": self._loading,
            "error": self._error,
            "model": getattr(self._transcriber, "name", model),
            "device": getattr(self._transcriber, "device", device),
            "compute_type": getattr(self._transcriber, "compute_type", compute),
            "cached": True if loaded else self.model_cached(),
            "gpu": device == "cuda",
            "vram_mb": _nvidia_smi_used_mb() if loaded else None,
            "sessions": self.sessions,
            **self.stats,
        }

    def ensure_loaded(self) -> None:
        """Blocking, idempotent load; safe to call from several threads."""
        if self._transcriber is not None:
            return
        with self._lock:
            if self._transcriber is not None:
                return
            self._loading, self._error = True, None
            try:
                model, device, compute = self._resolve()
                t0 = time.time()
                self._transcriber = WhisperTranscriber(model, device, compute)
                self.model_name, self.device, self.compute_type = model, device, compute
                # Warm-up so the first real utterance doesn't pay CUDA init.
                self._transcriber.transcribe(
                    np.zeros(SAMPLE_RATE, dtype=np.float32),
                    language="en", prompt=None, beam_size=1, word_timestamps=False,
                )
                log.info("STT model %s on %s/%s loaded in %.1fs", model, device, compute, time.time() - t0)
            except Exception as exc:
                self._transcriber = None
                self._error = f"{type(exc).__name__}: {exc}"
                log.exception("STT model failed to load")
                raise
            finally:
                self._loading = False

    def load_in_background(self) -> None:
        if self._transcriber is not None or self._loading:
            return
        threading.Thread(target=lambda: self._safe_load(), daemon=True).start()

    def _safe_load(self) -> None:
        try:
            self.ensure_loaded()
        except Exception:
            pass

    # -- decode worker ----------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run, name="stt-worker", daemon=True)
            self._worker.start()

    def submit(self, priority: int, fn: Callable[[Transcriber], None]) -> None:
        """Queue a decode. priority 0 = final (first), 1 = partial."""
        self._ensure_worker()
        self._jobs.put((priority, next(self._seq), fn))
        self.stats["queue_max"] = max(self.stats["queue_max"], self._jobs.qsize())

    def _run(self) -> None:
        while True:
            _, _, fn = self._jobs.get()
            try:
                self.ensure_loaded()
                t0 = time.time()
                fn(self._transcriber)
                self.stats["decodes"] += 1
                self.stats["last_decode_ms"] = round((time.time() - t0) * 1000, 1)
            except Exception:
                log.exception("STT job failed")


_service: Optional[SttService] = None


def get_stt() -> SttService:
    global _service
    if _service is None:
        _service = SttService()
    return _service


# --------------------------------------------------------------------------
# Text clean-up
# --------------------------------------------------------------------------

_NORM = re.compile(r"[^a-z0-9' ]+")


def normalise_text(text: str) -> str:
    return " ".join(_NORM.sub("", text.lower().replace("-", " ")).split())


def collapse_repeats(text: str, max_repeats: int = 3) -> str:
    """Cut runaway loops ("copy copy copy copy ...") that Whisper emits on noise."""
    words = text.split()
    out: list[str] = []
    for n in (1, 2, 3, 4):
        out = []
        i = 0
        while i < len(words):
            out.append(words[i])
            i += 1
            span = len(out)
            if span >= n:
                unit = out[-n:]
                reps = 1
                j = i
                while words[j : j + n] == unit:
                    reps += 1
                    j += n
                if reps > max_repeats:
                    out.extend(unit * (max_repeats - 1))
                    i = j
        words = out
    return " ".join(words)


def collapse_phrase_repeats(text: str, audio_s: float, seconds_per_word: float = 0.28) -> str:
    """Drop a sentence Whisper emitted twice when the audio is too short to hold both.

    A genuine "say again" repeat takes real time, so a repeat is only removed when
    the repeated words could not have been spoken in the available audio.
    """
    words = text.split()
    norm = [normalise_text(w) for w in words]
    for n in range(len(words) // 2, 4, -1):  # units of at least 5 words, longest first
        for start in range(0, len(words) - 2 * n + 1):
            unit = norm[start : start + n]
            reps, i = 1, start + n
            while norm[i : i + n] == unit:
                reps += 1
                i += n
            if reps >= 2 and audio_s < reps * n * seconds_per_word:
                return " ".join(words[: start + n] + words[i:])
    return text


def is_hallucination(text: str, voiced_s: float, confidence: float, no_speech: float) -> bool:
    norm = normalise_text(text)
    if not norm:
        return True
    if norm in HALLUCINATIONS and (voiced_s < 2.5 or confidence < 0.7):
        return True
    if no_speech > 0.8 and confidence < 0.5:
        return True
    toks = norm.split()
    if len(toks) >= 6 and len(set(toks)) / len(toks) < 0.25:  # almost pure repetition
        return True
    return False


# --------------------------------------------------------------------------
# Streaming session
# --------------------------------------------------------------------------


@dataclass
class SessionConfig:
    language: Optional[str] = "en"
    prompt: str = ""
    sample_rate: float = SAMPLE_RATE
    endpoint_ms: int = 700
    partial_interval_s: float = 0.8
    min_utterance_s: float = 0.35
    max_utterance_s: float = 28.0
    # Defaults chosen on a held-out radio corpus (docs/speech-decoder-research.md):
    # VAD confirms speech a few frames in and the first word is quiet, so the whole
    # 1.2 s look-back is sent to the model; two detectors (raw + band-passed audio)
    # catch more speech than either alone; gating the hiss did not help.
    preroll_s: float = 1.2
    soft_threshold: float = 0.12
    gate_noise: bool = False  # attenuate hiss around the speech before decoding
    vad_source: str = "max"  # "raw" | "filtered" | "max" of the two
    lookback: str = "full"  # "soft": back over speech-like frames; "full": the whole look-back window
    on_threshold: float = 0.5
    off_threshold: float = 0.35
    onset_frames: int = 3
    band_pass: bool = True
    agc: bool = True
    use_glossary: bool = True
    context_prompt: bool = True
    partials: bool = True
    beam_size: int = 5
    source_label: str = ""
    frequency_hz: Optional[float] = None
    mode: str = ""

    @classmethod
    def from_message(cls, data: dict) -> "SessionConfig":
        cfg = cls()
        lang = data.get("language", "en")
        cfg.language = None if lang in (None, "", "auto") else str(lang)
        cfg.prompt = str(data.get("prompt", ""))[:600]
        for key in ("sample_rate", "partial_interval_s"):
            if key in data:
                try:
                    setattr(cfg, key, float(data[key]))
                except (TypeError, ValueError):
                    pass
        for key in ("endpoint_ms", "beam_size"):
            if key in data:
                try:
                    setattr(cfg, key, int(data[key]))
                except (TypeError, ValueError):
                    pass
        for key in ("band_pass", "agc", "use_glossary", "context_prompt", "partials", "gate_noise"):
            if key in data:
                setattr(cfg, key, bool(data[key]))
        cfg.endpoint_ms = int(np.clip(cfg.endpoint_ms, 250, 3000))
        cfg.beam_size = int(np.clip(cfg.beam_size, 1, 8))
        cfg.sample_rate = float(np.clip(cfg.sample_rate, 4000, 192000))
        for key, allowed in (("vad_source", ("raw", "filtered", "max")), ("lookback", ("soft", "full"))):
            if data.get(key) in allowed:
                setattr(cfg, key, data[key])
        cfg.source_label = str(data.get("source_label", ""))[:80]
        cfg.mode = str(data.get("mode", ""))[:8]
        try:
            cfg.frequency_hz = float(data["frequency_hz"]) if data.get("frequency_hz") is not None else None
        except (TypeError, ValueError):
            cfg.frequency_hz = None
        return cfg


class StreamingSession:
    """Turns a PCM stream into partial/final transcript events.

    `feed()` is cheap and synchronous (resample, filter, VAD per 32 ms frame);
    decoding happens on the service's worker and results arrive through `emit`
    (which may be called from that worker thread).
    """

    def __init__(
        self,
        service: SttService,
        config: SessionConfig,
        emit: Callable[[dict], None],
        detector: Optional[SpeechDetector] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.cfg = config
        self.emit = emit
        self.det = detector or make_detector()
        # A second detector (own recurrent state) when both signals are consulted.
        self.det2 = make_detector() if (detector is None and config.vad_source == "max") else None
        self.clock = clock
        self.resampler = StreamResampler(config.sample_rate)
        self.bandpass = BandPass() if config.band_pass else None
        self._pending = np.zeros(0, dtype=np.float32)  # raw 16 kHz: what the VAD sees
        self._pending_f = np.zeros(0, dtype=np.float32)  # band-passed: what the model hears
        self._t = 0.0  # stream time at the end of the last full frame
        # (raw frame, band-passed frame, speech probability)
        self._preroll: deque[tuple[np.ndarray, np.ndarray, float]] = deque(
            maxlen=max(1, int(config.preroll_s / FRAME_S))
        )
        self._speech_run = 0
        self._in_speech = False
        self._utt: list[np.ndarray] = []  # band-passed frames
        self._utt_p: list[float] = []
        self._utt_start = 0.0
        self._silence_frames = 0
        self._voiced_frames = 0
        self._last_partial_t = -1e9
        self._partial_in_flight = False
        self._ids = itertools.count(1)
        self._cur_id = ""
        self._meta: dict = {}
        self._history: deque[str] = deque(maxlen=4)  # recent finals for the rolling prompt
        self._lock = threading.Lock()
        self._closed = False
        self.service.sessions += 1

    # -- public API ---------------------------------------------------------

    def update_meta(self, **meta) -> None:
        """Frequency/mode/label stamped on utterances that start from now on."""
        for key in ("source_label", "frequency_hz", "mode"):
            if key in meta and meta[key] is not None:
                setattr(self.cfg, key, meta[key])

    def feed(self, samples: np.ndarray) -> None:
        """Feed float32 mono audio in [-1, 1] at `config.sample_rate`."""
        if self._closed or samples.size == 0:
            return
        x = self.resampler.process(samples.astype(np.float32, copy=False))
        # Silero was trained on full-band speech and is *less* sure of band-passed
        # narrowband audio, so it hears the raw signal; only the model gets the filtered one.
        f = self.bandpass.process(x) if self.bandpass is not None else x
        self._pending = np.concatenate([self._pending, x])
        self._pending_f = np.concatenate([self._pending_f, f])
        while self._pending.size >= FRAME:
            raw, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            filt, self._pending_f = self._pending_f[:FRAME], self._pending_f[FRAME:]
            self._on_frame(raw, filt)

    def feed_pcm16(self, data: bytes) -> None:
        self.feed(np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0)

    def flush(self) -> None:
        """End of stream: finalize any utterance in progress."""
        if self._in_speech:
            self._finalize()

    def close(self) -> None:
        if not self._closed:
            self.flush()
            self._closed = True
            self.service.sessions = max(0, self.service.sessions - 1)

    # -- state machine -------------------------------------------------------

    def _on_frame(self, raw: np.ndarray, frame: np.ndarray) -> None:
        self._t += FRAME_S
        if self.cfg.vad_source == "filtered":
            p = self.det.prob(frame)
        elif self.cfg.vad_source == "max" and self.det2 is not None:
            p = max(self.det.prob(raw), self.det2.prob(frame))
        else:
            p = self.det.prob(raw)
        if not self._in_speech:
            self._preroll.append((raw, frame, p))
            self._speech_run = self._speech_run + 1 if p >= self.cfg.on_threshold else 0
            if self._speech_run >= self.cfg.onset_frames:
                self._start_utterance()
            return

        self._utt.append(frame)
        self._utt_p.append(p)
        if p >= self.cfg.off_threshold:
            self._silence_frames = 0
            self._voiced_frames += 1
        else:
            self._silence_frames += 1

        utt_s = len(self._utt) * FRAME_S
        if self._silence_frames * FRAME_S * 1000 >= self.cfg.endpoint_ms:
            self._finalize()
        elif utt_s >= self.cfg.max_utterance_s:
            self._finalize()
        elif (
            self.cfg.partials
            and not self._partial_in_flight
            and self._t - self._last_partial_t >= self.cfg.partial_interval_s
            and self._voiced_frames * FRAME_S >= 0.5
        ):
            self._submit_partial()

    def _start_utterance(self) -> None:
        self._in_speech = True
        pre = list(self._preroll)
        # The VAD confirms speech a few frames in, but the first word usually
        # started earlier and quieter. Walk back over frames that already look
        # a little speech-like (tolerating short dips) to include it.
        first = max(0, len(pre) - self._speech_run)
        if self.cfg.lookback == "full":
            first = 0
        else:
            low = 0
            for i in range(first - 1, -1, -1):
                if pre[i][2] >= self.cfg.soft_threshold:
                    first, low = i, 0
                else:
                    low += 1
                    if low > 3:
                        break
            first = max(0, first - 3)  # a little margin before the earliest soft frame
        pre = pre[first:]
        self._utt = [f for _, f, _ in pre]
        self._utt_p = [q for _, _, q in pre]
        self._utt_start = self._t - len(self._utt) * FRAME_S
        self._preroll.clear()
        self._silence_frames = 0
        self._voiced_frames = self._speech_run
        self._last_partial_t = self._t
        self._cur_id = f"u{next(self._ids)}"
        self._meta = {
            "source_label": self.cfg.source_label,
            "frequency_hz": self.cfg.frequency_hz,
            "mode": self.cfg.mode,
        }

    def _assemble(self, frames: list[np.ndarray], probs: list[float]) -> np.ndarray:
        """Utterance audio for the model: hiss gated out around the speech, level set from the speech.

        Open-squelch receivers are *louder* between transmissions than during
        them, so a plain peak normaliser would scale the speech down to match
        the hiss. Frames far from any voiced frame are attenuated, and the gain
        is taken from the voiced frames only.
        """
        if not frames:
            return np.zeros(0, dtype=np.float32)
        p = np.asarray(probs, dtype=np.float32)
        if self.cfg.gate_noise and len(frames) == len(p):
            near = np.convolve((p >= 0.3).astype(np.float32), np.ones(11, dtype=np.float32), mode="same") > 0
            out, prev = [], 1.0
            for frame, keep in zip(frames, near):
                gain = 1.0 if keep else 0.1
                out.append(frame * np.linspace(prev, gain, FRAME, dtype=np.float32))
                prev = gain
            frames = out
        audio = np.concatenate(frames)
        if not self.cfg.agc:
            return audio
        voiced = [f for f, q in zip(frames, p) if q >= 0.35] if len(frames) == len(p) else []
        if voiced:
            rms = float(np.sqrt(np.mean(np.concatenate(voiced) ** 2))) + 1e-6
            peak = float(np.max(np.abs(audio))) + 1e-6
            gain = min(10 ** (24 / 20), 0.1 / rms, 0.95 / peak)
            return (audio * max(gain, 0.05)).astype(np.float32)
        return normalise(audio)

    def _audio(self, last_s: Optional[float] = None) -> np.ndarray:
        frames, probs = self._utt, self._utt_p
        if last_s is not None:
            n = int(last_s / FRAME_S)
            frames, probs = frames[-n:], probs[-n:]
        return self._assemble(frames, probs)

    def _prompt(self) -> Optional[str]:
        if self.service.prompt_style() == "short":
            parts = [SHORT_GLOSSARY] if self.cfg.use_glossary else []
            if self.cfg.prompt:
                parts.append(self.cfg.prompt[:120])
            return " ".join(parts) if parts else None
        parts = []
        if self.cfg.use_glossary:
            parts.append(DEFAULT_GLOSSARY)
        if self.cfg.prompt:
            parts.append(self.cfg.prompt)
        if self.cfg.context_prompt and self._history:
            parts.append(" ".join(self._history)[-200:])
        return " ".join(parts) if parts else None

    def _submit_partial(self) -> None:
        self._last_partial_t = self._t
        self._partial_in_flight = True
        uid, meta = self._cur_id, dict(self._meta)
        start, end_t = self._utt_start, self._t
        audio = self._audio(last_s=14.0)
        prompt, lang = self._prompt(), self.cfg.language
        queued_at = self.clock()

        def job(model: Transcriber) -> None:
            try:
                if self._closed:
                    return
                res = model.transcribe(audio, language=lang, prompt=prompt, beam_size=1, word_timestamps=False)
                text = collapse_phrase_repeats(collapse_repeats(res.text), len(audio) / SAMPLE_RATE)
                if text and not is_hallucination(text, len(audio) / SAMPLE_RATE, math.exp(res.avg_logprob), res.no_speech_prob):
                    self.emit({
                        "type": "partial", "id": uid, "text": text,
                        "start_s": round(start, 2), "end_s": round(end_t, 2),
                        "confidence": round(math.exp(res.avg_logprob), 3),
                        "language": res.language,
                        "latency_ms": int((self.clock() - queued_at) * 1000), **meta,
                    })
            finally:
                self._partial_in_flight = False

        self.service.submit(1, job)

    def _finalize(self) -> None:
        voiced_s = self._voiced_frames * FRAME_S
        uid, meta = self._cur_id, dict(self._meta)
        start = self._utt_start
        # Keep a short tail of silence so word endings aren't clipped.
        keep_tail = int(0.25 / FRAME_S)
        keep = len(self._utt) - max(0, self._silence_frames - keep_tail)
        end_t = start + keep * FRAME_S
        audio = self._assemble(self._utt[:keep], self._utt_p[:keep])
        self._in_speech = False
        self._utt = []
        self._utt_p = []
        self._speech_run = 0
        self._silence_frames = 0
        self._voiced_frames = 0
        self._preroll.clear()
        self.det.reset()
        if self.det2 is not None:
            self.det2.reset()
        if voiced_s < self.cfg.min_utterance_s or audio.size < FRAME * 4:
            self.emit({"type": "final", "id": uid, "text": "", "dropped": True, "reason": "too short", **meta})
            return
        prompt, lang, beam = self._prompt(), self.cfg.language, self.cfg.beam_size
        queued_at = self.clock()

        def job(model: Transcriber) -> None:
            res = model.transcribe(audio, language=lang, prompt=prompt, beam_size=beam, word_timestamps=True)
            text = collapse_phrase_repeats(collapse_repeats(res.text), len(audio) / SAMPLE_RATE)
            conf = float(math.exp(res.avg_logprob)) if res.avg_logprob > -50 else 0.0
            if res.words:
                conf = float(np.mean([w.p for w in res.words]))
            if is_hallucination(text, voiced_s, conf, res.no_speech_prob):
                self.emit({"type": "final", "id": uid, "text": "", "dropped": True, "reason": "no speech", **meta})
                return
            self._history.append(text)
            self.emit({
                "type": "final", "id": uid, "text": text,
                "start_s": round(start, 2), "end_s": round(end_t, 2),
                "words": [
                    {"w": w.w, "start": round(start + w.start, 2), "end": round(start + w.end, 2), "p": round(w.p, 3)}
                    for w in res.words
                ],
                "confidence": round(conf, 3), "language": res.language,
                "latency_ms": int((self.clock() - queued_at) * 1000), **meta,
            })

        self.service.submit(0, job)
