# -*- coding: utf-8 -*-
"""Streaming chat: sentence chunks, callbacks, cancel and TTS errors."""
import threading
import unittest

from remvoice.chat import ChatService, ChatSession
from remvoice.llm import ChatError


CFG = {
    "persona": "你是助手。",
    "auto_punct": True,
    "api_base": "http://example.invalid",
    "api_key": "test",
    "model": "test-model",
    "temperature": 0.8,
    "max_tokens": 300,
}
TONES = {
    "gentle": {
        "label": "温柔",
        "audio": "ref.wav",
        "prompt": "参考文本",
        "lang": "ja",
    }
}


class FakeStreamClient:
    def __init__(self, parts):
        self.parts = parts
        self.closed = False

    def stream(self, messages, timeout=60):
        try:
            for part in self.parts:
                yield part
        finally:
            self.closed = True


class CancelStreamClient:
    def __init__(self, event):
        self.event = event

    def stream(self, messages, timeout=60):
        yield "（微笑）\n"
        yield "旅行者君，早安。"
        self.event.set()
        yield "今天也辛苦了。"


class ChatServiceStreamTests(unittest.TestCase):
    def make_service(self, client, synthesize=None):
        if synthesize is None:
            synthesize = lambda text, tone, speed, **kwargs: b"RIFF"
        return ChatService(
            CFG,
            TONES,
            "gentle",
            synthesize=synthesize,
            save=lambda data, prefix="": "/tmp/%d.wav" % len(data),
            client=client,
        )

    def test_sentence_chunks_and_callbacks(self):
        synth_texts = []
        audio = []
        texts = []

        def synth(text, tone, speed, **kwargs):
            synth_texts.append(text)
            return ("RIFF-%d" % len(synth_texts)).encode()

        client = FakeStreamClient(
            ["（微笑）\n", "旅行者君，早", "安。今天也", "辛苦了。"]
        )
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(client, synth).run(
            "你好",
            session,
            on_text=lambda actions, speech: texts.append((list(actions), speech)),
            on_audio=lambda path: audio.append(path),
        )
        self.assertEqual(result.error_kind, "")
        self.assertEqual(result.speech, "旅行者君，早安。今天也辛苦了。")
        self.assertEqual(result.actions, ["微笑"])
        self.assertEqual(len(synth_texts), 1)
        self.assertEqual(synth_texts[0], "旅行者君，早安。今天也辛苦了。")
        self.assertEqual(len(audio), 1)
        self.assertEqual(len(result.audio_paths), 1)
        self.assertTrue(texts)

    def test_short_first_sentence_is_merged(self):
        synth_texts = []
        client = FakeStreamClient(["（微笑）\n", "。嗯，助手在呢。旅行者君。"])
        session = ChatSession(CFG, [], saver=lambda _m: None)

        def synth(text, tone, speed, **kwargs):
            synth_texts.append(text)
            return b"RIFF"

        result = self.make_service(client, synth).run("你好", session)
        self.assertEqual(result.error_kind, "")
        self.assertTrue(synth_texts)
        self.assertFalse(synth_texts[0].startswith("。"))

    def test_cancel_mid_stream_rolls_back(self):
        event = threading.Event()
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(CancelStreamClient(event)).run(
            "你好", session, cancel=event
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(session.messages, [])

    def test_tts_error_keeps_text(self):
        def explode(text, tone, speed, **kwargs):
            raise RuntimeError("tts down")

        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(
            FakeStreamClient(["旅行者君，早安。"]), explode
        ).run("你好", session)
        self.assertEqual(result.speech, "旅行者君，早安。")
        self.assertEqual(result.tts_error, "tts down")
        self.assertEqual(result.audio_paths, [])
        self.assertEqual(session.messages[-1]["role"], "assistant")

    def test_stream_error_rolls_back(self):
        class BadClient:
            def stream(self, messages, timeout=60):
                yield "旅行者君，"
                raise ChatError("offline", "network")

        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(BadClient()).run("你好", session)
        self.assertEqual(result.error_kind, "network")
        self.assertEqual(session.messages, [])


if __name__ == "__main__":
    unittest.main()
