"""Endpointing, partial/final events, hallucination filtering and the decode queue.

Everything here uses a fake model and the energy detector, so it runs without a
GPU or the Whisper weights. The last test is an integration check that skips
itself unless the model is cached and CUDA is available.
"""

import threading
import unittest

import numpy as np

from sigsense_api.stt import (
    DecodeResult,
    EnergyDetector,
    SessionConfig,
    SttService,
    StreamResampler,
    StreamingSession,
    Word,
    collapse_phrase_repeats,
    collapse_repeats,
    is_hallucination,
    normalise,
    normalise_text,
)

RATE = 16000


def tone(seconds: float, level: float = 0.3, f: float = 1000.0) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    rng = np.random.default_rng(3)
    return (level * np.sin(2 * np.pi * f * t) + 0.02 * rng.normal(size=t.size)).astype(np.float32)


def quiet(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(4)
    return (1e-4 * rng.normal(size=int(seconds * RATE))).astype(np.float32)


class FakeModel:
    """Records every call; returns scripted text (or a callable of the audio)."""

    name = "fake-large-v3"

    def __init__(self, text="roger that ten four", logprob=-0.2, no_speech=0.01):
        self.calls = []
        self.text, self.logprob, self.no_speech = text, logprob, no_speech

    def transcribe(self, audio, *, language, prompt, beam_size, word_timestamps):
        self.calls.append({"n": len(audio), "language": language, "prompt": prompt,
                           "beam": beam_size, "words": word_timestamps})
        text = self.text(audio) if callable(self.text) else self.text
        words = [Word(w, i * 0.3, i * 0.3 + 0.25, 0.9) for i, w in enumerate(text.split())] if word_timestamps else []
        return DecodeResult(text=text, words=words, avg_logprob=self.logprob,
                            no_speech_prob=self.no_speech, language="en")


class InlineService(SttService):
    """Runs decode jobs immediately on the caller's thread (deterministic tests)."""

    def __init__(self, model):
        super().__init__(transcriber=model)
        self.model = model

    def submit(self, priority, fn):
        fn(self.model)


def run(session, *chunks, step=1600):
    for chunk in chunks:
        for i in range(0, len(chunk), step):
            session.feed(chunk[i : i + step])


def make(model=None, **cfg):
    model = model or FakeModel()
    events = []
    config = SessionConfig(**cfg)
    session = StreamingSession(InlineService(model), config, events.append, detector=EnergyDetector())
    return session, events, model


class EndpointingTests(unittest.TestCase):
    def test_silence_produces_nothing(self):
        session, events, model = make()
        run(session, quiet(5))
        self.assertEqual(events, [])
        self.assertEqual(model.calls, [])

    def test_one_transmission_gives_partials_then_one_final(self):
        session, events, _ = make(partial_interval_s=0.5)
        run(session, quiet(0.5), tone(3.0), quiet(1.5))
        partials = [e for e in events if e["type"] == "partial"]
        finals = [e for e in events if e["type"] == "final"]
        self.assertGreaterEqual(len(partials), 2)
        self.assertEqual(len(finals), 1)
        self.assertEqual({e["id"] for e in events}, {"u1"})
        self.assertEqual(finals[0]["text"], "roger that ten four")
        self.assertEqual(len(finals[0]["words"]), 4)
        self.assertEqual(events[-1]["type"], "final")  # nothing revises it afterwards

    def test_final_waits_for_the_endpoint_silence(self):
        session, events, _ = make(endpoint_ms=800)
        run(session, quiet(0.3), tone(2.0), quiet(0.5))
        self.assertFalse([e for e in events if e["type"] == "final"])  # 0.5 s < 0.8 s
        run(session, quiet(0.6))
        self.assertEqual(len([e for e in events if e["type"] == "final"]), 1)

    def test_two_transmissions_get_separate_ids_and_times(self):
        session, events, _ = make()
        run(session, quiet(0.3), tone(1.5), quiet(1.2), tone(1.5), quiet(1.2))
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual([f["id"] for f in finals], ["u1", "u2"])
        self.assertLess(finals[0]["end_s"], finals[1]["start_s"])
        self.assertAlmostEqual(finals[0]["start_s"], 0.3, delta=0.5)

    def test_a_click_is_dropped_not_transcribed(self):
        session, events, model = make()
        run(session, quiet(0.3), tone(0.15), quiet(1.2))
        finals = [e for e in events if e["type"] == "final"]
        self.assertTrue(all(f.get("dropped") for f in finals))
        self.assertEqual(model.calls, [])  # never reached the model

    def test_long_monologue_is_split_at_the_cap(self):
        session, events, _ = make(max_utterance_s=4.0, partials=False)
        run(session, quiet(0.2), tone(9.0), quiet(1.0))
        finals = [e for e in events if e["type"] == "final" and not e.get("dropped")]
        self.assertGreaterEqual(len(finals), 2)

    def test_flush_finalizes_an_open_utterance(self):
        session, events, _ = make()
        run(session, quiet(0.2), tone(1.5))
        self.assertFalse([e for e in events if e["type"] == "final"])
        session.flush()
        self.assertEqual(len([e for e in events if e["type"] == "final"]), 1)

    def test_partials_use_greedy_and_finals_use_beam_with_words(self):
        session, _, model = make(partial_interval_s=0.5, beam_size=5)
        run(session, quiet(0.2), tone(2.5), quiet(1.2))
        partial_calls = [c for c in model.calls if not c["words"]]
        final_calls = [c for c in model.calls if c["words"]]
        self.assertTrue(partial_calls and all(c["beam"] == 1 for c in partial_calls))
        self.assertEqual(len(final_calls), 1)
        self.assertEqual(final_calls[0]["beam"], 5)

    def test_metadata_is_stamped_at_utterance_start(self):
        session, events, _ = make(source_label="GMRS 15", frequency_hz=462.5625e6, mode="NBFM")
        run(session, quiet(0.2), tone(1.0))
        session.update_meta(frequency_hz=467.0e6)  # retune mid-transmission
        run(session, quiet(1.2))
        final = [e for e in events if e["type"] == "final"][0]
        self.assertEqual(final["frequency_hz"], 462.5625e6)
        self.assertEqual(final["source_label"], "GMRS 15")


class FilterTests(unittest.TestCase):
    def test_hallucinated_sign_off_on_a_short_burst_is_dropped(self):
        session, events, _ = make(FakeModel("Thank you."))
        run(session, quiet(0.2), tone(0.8), quiet(1.2))
        final = [e for e in events if e["type"] == "final"][0]
        self.assertTrue(final["dropped"])

    def test_real_thanks_in_a_long_confident_transmission_is_kept(self):
        self.assertFalse(is_hallucination("Thank you.", voiced_s=4.0, confidence=0.9, no_speech=0.05))
        self.assertTrue(is_hallucination("Thank you.", voiced_s=1.0, confidence=0.9, no_speech=0.05))

    def test_repetition_loops_are_collapsed(self):
        self.assertEqual(collapse_repeats("copy copy copy copy copy copy over"), "copy copy copy over")
        self.assertEqual(collapse_repeats("ten four ten four ten four ten four ten four"), "ten four ten four ten four")
        self.assertEqual(collapse_repeats("roger roger"), "roger roger")

    def test_a_sentence_said_twice_in_too_little_audio_is_collapsed(self):
        sentence = "all units be advised the road is closed at mile marker forty two"
        doubled = f"{sentence}. {sentence}."
        # 13 words twice needs about 7 s of speech; only 4 s of audio.
        self.assertEqual(collapse_phrase_repeats(doubled, 4.0).lower().count("mile marker"), 1)
        # With enough audio it is a real repeat ("say again") and is kept.
        self.assertEqual(collapse_phrase_repeats(doubled, 9.0), doubled)
        # Short repeated acknowledgements are not touched.
        self.assertEqual(collapse_phrase_repeats("roger roger copy copy", 1.0), "roger roger copy copy")

    def test_phrase_repeat_rule_depends_on_how_much_audio_there_is(self):
        # A 4-word repeat is below the phrase threshold (collapse_repeats handles short loops).
        text = "base this is unit seven you are breaking up you are breaking up over"
        self.assertEqual(collapse_phrase_repeats(text, 2.0), text)
        # A 5-word unit twice needs about 2.8 s to speak.
        repeated = "unit nine at the north gate now at the north gate now over"
        self.assertEqual(collapse_phrase_repeats(repeated, 1.5), "unit nine at the north gate now over")
        self.assertEqual(collapse_phrase_repeats(repeated, 4.0), repeated)  # plenty of time: real repeat
        five = "go to the north gate go to the north gate"
        self.assertEqual(collapse_phrase_repeats(five, 1.5), "go to the north gate")

    def test_pure_repetition_is_a_hallucination(self):
        self.assertTrue(is_hallucination(" ".join(["la"] * 20), 5.0, 0.9, 0.0))

    def test_no_speech_with_low_confidence_is_dropped(self):
        self.assertTrue(is_hallucination("something", 3.0, 0.3, 0.9))

    def test_text_normalisation_for_scoring(self):
        self.assertEqual(normalise_text("10-4, Kilo-Alpha!"), "10 4 kilo alpha")

    def test_rolling_prompt_carries_recent_finals(self):
        session, _, model = make()
        run(session, quiet(0.2), tone(1.5), quiet(1.2))
        run(session, tone(1.5), quiet(1.2))
        second = [c for c in model.calls if c["words"]][1]
        self.assertIn("roger that ten four", second["prompt"])
        self.assertIn("Callsigns", second["prompt"])  # glossary


class PromptTests(unittest.TestCase):
    def test_small_models_get_a_short_prompt_without_history(self):
        model = FakeModel()
        model.name = "Systran/faster-distil-whisper-small.en"
        session, _, _ = make(model, prompt="Base, Dispatch")
        run(session, quiet(0.2), tone(1.5), quiet(1.2))
        run(session, tone(1.5), quiet(1.2))
        prompts = [c["prompt"] for c in model.calls if c["words"]]
        self.assertEqual(len(prompts), 2)
        for prompt in prompts:
            self.assertLess(len(prompt), 80)  # a long prompt makes small models loop
            self.assertNotIn("roger that ten four", prompt)  # no rolling history either
            self.assertIn("Base, Dispatch", prompt)

    def test_large_models_get_the_full_glossary_and_history(self):
        session, _, model = make()
        run(session, quiet(0.2), tone(1.5), quiet(1.2))
        run(session, tone(1.5), quiet(1.2))
        second = [c for c in model.calls if c["words"]][1]["prompt"]
        self.assertGreater(len(second), 300)


class AudioTests(unittest.TestCase):
    def test_agc_lifts_quiet_audio_but_caps_gain(self):
        quietish = tone(1.0, level=0.01)
        out = normalise(quietish)
        self.assertGreater(float(np.max(np.abs(out))), 0.05)
        self.assertLessEqual(float(np.max(np.abs(out))) / float(np.max(np.abs(quietish))), 10 ** (24 / 20) + 0.01)

    def test_resampler_keeps_the_tone_and_length_across_odd_chunks(self):
        for src in (48000, 44100, 8000):
            r = StreamResampler(src)
            t = np.arange(src * 2) / src
            x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
            rng = np.random.default_rng(0)
            out, i = [], 0
            while i < len(x):
                n = int(rng.integers(50, 5000))
                out.append(r.process(x[i : i + n]))
                i += n
            y = np.concatenate(out)
            self.assertAlmostEqual(len(y), 2 * RATE, delta=80)
            peak = np.fft.rfftfreq(len(y) - 2000, 1 / RATE)[np.argmax(np.abs(np.fft.rfft(y[1000:-1000])))]
            self.assertAlmostEqual(peak, 1000, delta=20)

    def test_feeding_int16_bytes_and_other_sample_rates_works(self):
        events = []
        session = StreamingSession(InlineService(FakeModel()), SessionConfig(sample_rate=48000), events.append, detector=EnergyDetector())
        t = np.arange(48000 * 2) / 48000
        x = np.concatenate([np.zeros(24000), 0.3 * np.sin(2 * np.pi * 1000 * t), np.zeros(48000)])
        pcm = (x * 32767).astype("<i2").tobytes()
        for i in range(0, len(pcm), 3000):
            session.feed_pcm16(pcm[i : i + 3000])
        self.assertEqual(len([e for e in events if e["type"] == "final" and not e.get("dropped")]), 1)

    def test_config_parsing_clamps_and_defaults(self):
        cfg = SessionConfig.from_message({"language": "auto", "endpoint_ms": 5, "beam_size": 99, "sample_rate": 10})
        self.assertIsNone(cfg.language)
        self.assertEqual((cfg.endpoint_ms, cfg.beam_size), (250, 8))
        self.assertGreaterEqual(cfg.sample_rate, 4000)
        self.assertEqual(SessionConfig.from_message({}).language, "en")


class QueueTests(unittest.TestCase):
    def test_finals_run_before_waiting_partials_and_each_job_runs_once(self):
        service = SttService(transcriber=FakeModel())
        order, gate = [], threading.Event()
        done = threading.Event()
        service.submit(1, lambda m: gate.wait(2))  # occupies the worker
        for name in ("partial-a", "partial-b"):
            service.submit(1, lambda m, n=name: order.append(n))
        service.submit(0, lambda m: order.append("final"))
        service.submit(1, lambda m: done.set())
        gate.set()
        self.assertTrue(done.wait(3))
        self.assertEqual(order[0], "final")
        self.assertEqual(sorted(order), ["final", "partial-a", "partial-b"])

    def test_a_failing_job_does_not_kill_the_worker(self):
        service = SttService(transcriber=FakeModel())
        ran = threading.Event()
        service.submit(0, lambda m: 1 / 0)
        service.submit(0, lambda m: ran.set())
        self.assertTrue(ran.wait(3))

    def test_only_one_partial_is_in_flight_per_session(self):
        class Deferred(InlineService):
            def __init__(self, model):
                super().__init__(model)
                self.pending = []

            def submit(self, priority, fn):
                self.pending.append((priority, fn))

        service = Deferred(FakeModel())
        events = []
        session = StreamingSession(service, SessionConfig(partial_interval_s=0.3), events.append, detector=EnergyDetector())
        run(session, quiet(0.2), tone(3.0))
        self.assertEqual(len([p for p, _ in service.pending if p == 1]), 1)  # stale partials aren't piled up


class IntegrationTests(unittest.TestCase):
    """Loads the real model; skipped without the weights and a GPU."""

    def test_real_model_loads_and_is_silent_on_silence(self):
        service = SttService()
        status = service.status()
        if not (status["available"] and status["cached"] and status["gpu"]):
            self.skipTest("Whisper model not cached or no CUDA device")
        service.ensure_loaded()
        res = service._transcriber.transcribe(np.zeros(RATE, dtype=np.float32), language="en",
                                              prompt=None, beam_size=1, word_timestamps=False)
        # Whisper itself often invents "Thank you." on silence; the session's
        # filter must catch whatever it returns.
        confidence = float(np.exp(res.avg_logprob))
        self.assertTrue(
            not normalise_text(res.text)
            or is_hallucination(res.text, voiced_s=1.0, confidence=confidence, no_speech=res.no_speech_prob),
            f"unfiltered hallucination on silence: {res.text!r}",
        )
        self.assertTrue(service.status()["ready"])


if __name__ == "__main__":
    unittest.main()
