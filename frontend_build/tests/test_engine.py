# -*- coding: utf-8 -*-
"""TTS request construction and warm-up behavior (no network, no GPU)."""
import io
import json
import os
import unittest
import wave
from unittest import mock

from remvoice import engine
from support import scratch


TONE = {"audio": "ref.wav", "prompt": "参考文本", "lang": "ja"}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return b"RIFF"


class StreamingResponse(FakeResponse):
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read1(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class SynthRequestTests(unittest.TestCase):
    def test_includes_sample_steps_and_timeout(self):
        with mock.patch.object(
            engine.urllib.request, "urlopen", return_value=FakeResponse()
        ) as opener:
            engine.synth("你好", TONE, 0.85)
        req = opener.call_args.args[0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["sample_steps"], engine.SAMPLE_STEPS)
        self.assertEqual(payload["text_split_method"], "cut0")
        self.assertEqual(payload["seed"], engine.TTS_SEED)
        self.assertEqual(opener.call_args.kwargs["timeout"], 600)

    def test_long_line_uses_cut2(self):
        with mock.patch.object(
            engine.urllib.request, "urlopen", return_value=FakeResponse()
        ) as opener:
            engine.synth("长" * 61, TONE, 0.85)
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["text_split_method"], "cut2")

    def test_explicit_split_method(self):
        with mock.patch.object(
            engine.urllib.request, "urlopen", return_value=FakeResponse()
        ) as opener:
            engine.synth("你好", TONE, 0.85, text_split_method="cut5")
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["text_split_method"], "cut5")
        self.assertEqual(payload["seed"], engine.TTS_SEED)

    def test_explicit_seed(self):
        with mock.patch.object(
            engine.urllib.request, "urlopen", return_value=FakeResponse()
        ) as opener:
            engine.synth("你好", TONE, 0.85, seed=-1)
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["seed"], -1)

    def test_streaming_response_is_reassembled(self):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00\x00" * 100)
        wav = buffer.getvalue()
        data_at = wav.find(b"data")
        header = wav[: data_at + 8]
        raw = wav[data_at + 8:]
        chunks = [header + raw[:100], raw[100:]]
        delivered = []
        with mock.patch.object(
            engine.urllib.request,
            "urlopen",
            return_value=StreamingResponse(chunks),
        ):
            result = engine.synth(
                "你好",
                TONE,
                0.85,
                streaming_mode=2,
                on_chunk=delivered.append,
            )
        self.assertEqual(delivered, chunks)
        with wave.open(io.BytesIO(result), "rb") as w:
            self.assertEqual(w.getnframes(), 100)
            self.assertEqual(w.getframerate(), 48000)

    def test_explicit_steps_and_timeout(self):
        with mock.patch.object(
            engine.urllib.request, "urlopen", return_value=FakeResponse()
        ) as opener:
            engine.synth("你好", TONE, 0.85, sample_steps=16, timeout=99)
        payload = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["sample_steps"], 16)
        self.assertEqual(opener.call_args.kwargs["timeout"], 99)


class SaveAudioTests(unittest.TestCase):
    def test_writes_wav_and_leaves_no_tmp(self):
        with scratch() as tmp:
            old = engine.SAVE_DIR
            engine.SAVE_DIR = tmp
            try:
                path = engine.save_audio(b"RIFFdata")
            finally:
                engine.SAVE_DIR = old
            with open(path, "rb") as f:
                self.assertEqual(f.read(), b"RIFFdata")
            self.assertEqual(
                [name for name in os.listdir(tmp) if name.endswith(".tmp")], []
            )


class WarmupTests(unittest.TestCase):
    def test_warmup_uses_one_short_line(self):
        with mock.patch.object(engine, "synth", return_value=b"") as synth:
            logs = []
            self.assertTrue(engine.warmup_tts(TONE, logs.append))
        self.assertEqual(synth.call_args.args[0], "你好。")
        self.assertEqual(synth.call_args.args[1], TONE)
        self.assertEqual(synth.call_args.args[2], 1.0)
        self.assertEqual(synth.call_args.kwargs["sample_steps"], engine.SAMPLE_STEPS)
        self.assertTrue(any("预热" in line for line in logs))

    def test_warmup_failure_does_not_raise(self):
        with mock.patch.object(engine, "synth", side_effect=RuntimeError("down")):
            self.assertFalse(engine.warmup_tts(TONE, lambda _m: None))


if __name__ == "__main__":
    unittest.main()
