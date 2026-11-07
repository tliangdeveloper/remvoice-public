# -*- coding: utf-8 -*-
"""Chinese display / Japanese voice mode."""
import unittest
from unittest import mock

from remvoice.chat import ChatService, ChatSession
from remvoice.llm import translate_to_japanese


BASE_CFG = {
    "persona": "你是助手。",
    "auto_punct": True,
    "api_base": "http://example.invalid",
    "api_key": "test",
    "model": "test-model",
    "temperature": 0.8,
    "max_tokens": 300,
    "voice_lang": "zh",
}
TONES = {
    "gentle": {
        "label": "温柔",
        "audio": "ref.wav",
        "prompt": "参考文本",
        "lang": "ja",
    }
}


class FakeClient:
    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, timeout=60):
        return self.reply


def make_service(voice_lang, translator):
    cfg = dict(BASE_CFG, voice_lang=voice_lang)
    calls = {}

    def synth(text, tone, speed, **kwargs):
        calls.setdefault("tts", []).append((text, kwargs.get("text_lang")))
        return b"RIFF"

    service = ChatService(
        cfg,
        TONES,
        "gentle",
        synthesize=synth,
        save=lambda data, prefix="": "/tmp/out.wav",
        client=FakeClient("（微笑）\n旅行者君，早安。"),
        stream_voice=False,
        translate=translator,
    )
    return service, cfg, calls


class VoiceLangTests(unittest.TestCase):
    def test_japanese_translation_goes_to_tts_only(self):
        translated = []

        def translator(text, cfg):
            translated.append(text)
            return "スバルくん、おはよう。"

        service, cfg, calls = make_service("ja", translator)
        session = ChatSession(cfg, [], saver=lambda _m: None)
        result = service.run("你好", session)
        self.assertEqual(result.speech, "旅行者君，早安。")
        self.assertEqual(result.tts_text, "スバルくん、おはよう。")
        self.assertEqual(translated, ["旅行者君，早安。"])
        self.assertEqual(calls["tts"][0][0], "スバルくん、おはよう。")
        self.assertEqual(calls["tts"][0][1], "ja")
        self.assertIn("translate", result.timings)

    def test_chinese_voice_does_not_translate(self):
        def translator(text, cfg):
            raise AssertionError("should not translate")

        service, cfg, calls = make_service("zh", translator)
        session = ChatSession(cfg, [], saver=lambda _m: None)
        result = service.run("你好", session)
        self.assertEqual(result.speech, "旅行者君，早安。")
        self.assertEqual(calls["tts"][0][0], "旅行者君，早安。")
        self.assertEqual(calls["tts"][0][1], "auto")

    def test_translation_failure_keeps_text(self):
        def translator(text, cfg):
            raise RuntimeError("translator down")

        service, cfg, calls = make_service("ja", translator)
        session = ChatSession(cfg, [], saver=lambda _m: None)
        result = service.run("你好", session)
        self.assertEqual(result.speech, "旅行者君，早安。")
        self.assertIn("日语翻译失败", result.tts_error)
        self.assertNotIn("tts", calls)

    def test_translate_helper_cleans_output(self):
        class FakeChat:
            def __init__(self, cfg):
                self.cfg = cfg

            def complete(self, messages, timeout=60):
                return '「スバルくん、おはよう。」'

        with mock.patch("remvoice.llm.ChatClient", FakeChat):
            out = translate_to_japanese("旅行者君，早安。", BASE_CFG)
        self.assertEqual(out, "スバルくん、おはよう。")

    def test_translate_helper_can_use_local_endpoint(self):
        class FakeChat:
            seen = None

            def __init__(self, cfg):
                self.cfg = cfg
                FakeChat.seen = cfg

            def complete(self, messages, timeout=60):
                return "おはよう。"

        cfg = dict(
            BASE_CFG,
            translate_api_base="http://127.0.0.1:11434/v1",
            translate_model="qwen2.5:7b",
            translate_api_key="local-key",
        )
        with mock.patch("remvoice.llm.ChatClient", FakeChat):
            translate_to_japanese("早安。", cfg)
        self.assertEqual(FakeChat.seen["api_base"], "http://127.0.0.1:11434/v1")
        self.assertEqual(FakeChat.seen["model"], "qwen2.5:7b")
        self.assertEqual(FakeChat.seen["api_key"], "local-key")


if __name__ == "__main__":
    unittest.main()
