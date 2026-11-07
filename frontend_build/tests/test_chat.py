# -*- coding: utf-8 -*-
"""Chat domain tests; no Tk and no network."""
import threading
import unittest

from remvoice.chat import ChatService, ChatSession
from remvoice.llm import ChatError
from support import scratch


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


class FakeClient:
    def __init__(self, reply="（微笑）\n旅行者君，早安！"):
        self.reply = reply
        self.calls = []

    def complete(self, messages, timeout=60):
        self.calls.append(messages)
        return self.reply


class FailingClient:
    def __init__(self, error):
        self.error = error

    def complete(self, messages, timeout=60):
        raise self.error


class CancelAfterReplyClient:
    def __init__(self, event):
        self.event = event

    def complete(self, messages, timeout=60):
        self.event.set()
        return "旅行者君，早安。"


class ChatSessionTests(unittest.TestCase):
    def test_request_contains_system_prompt(self):
        session = ChatSession(CFG, [], saver=lambda _m: None)
        session.add_user("你好")
        request = session.request()
        self.assertEqual(request[0]["role"], "system")
        self.assertEqual(request[-1]["content"], "你好")

    def test_rollback_last_user(self):
        session = ChatSession(CFG, [], saver=lambda _m: None)
        session.add_user("你好")
        session.add_assistant("早安")
        session.rollback_last_user()
        self.assertEqual(session.messages[-1]["content"], "早安")
        session.add_user("再试一次")
        session.rollback_last_user()
        self.assertEqual(session.messages[-1]["content"], "早安")

    def test_request_trims_to_recent_turns(self):
        session = ChatSession(CFG, [], max_turns=2, saver=lambda _m: None)
        for i in range(5):
            session.add_user("u%d" % i)
            session.add_assistant("a%d" % i)
        # Storage keeps history for the console; only the API request is trimmed.
        self.assertEqual(len(session.messages), 10)
        request = session.request()
        self.assertEqual(len(request), 5)  # system + 2 turns
        self.assertEqual(request[1]["content"], "u3")

    def test_save_is_called(self):
        saved = []
        session = ChatSession(CFG, [], saver=lambda messages: saved.append(list(messages)))
        session.add_user("你好")
        session.add_assistant("早安")
        session.save()
        self.assertEqual(saved[-1][-1]["content"], "早安")

    def test_pop_last_turn_returns_user_text(self):
        session = ChatSession(CFG, [], saver=lambda _m: None)
        session.add_user("你好")
        session.add_assistant("早安")
        self.assertEqual(session.pop_last_turn(), "你好")
        self.assertEqual(session.messages, [])

    def test_audio_metadata_and_export(self):
        import os

        session = ChatSession(CFG, [], saver=lambda _m: None)
        session.add_user("你好")
        session.add_assistant("早安")
        session.set_last_audio("/tmp/a.wav", "ja", "gentle")
        self.assertEqual(session.messages[-1]["audio"], "/tmp/a.wav")
        self.assertEqual(session.messages[-1]["voice_lang"], "ja")
        with scratch() as tmp:
            path = os.path.join(tmp, "chat.md")
            session.export_markdown(path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self.assertIn("你好", text)
        self.assertIn("/tmp/a.wav", text)

    def test_legacy_history_gets_increasing_timestamps(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        session = ChatSession(CFG, history, saver=lambda _m: None)
        stamps = [message["ts"] for message in session.messages]
        self.assertEqual(len(stamps), 3)
        self.assertLess(stamps[0], stamps[1])
        self.assertLess(stamps[1], stamps[2])

    def test_duplicate_timestamps_are_made_strict(self):
        history = [
            {"role": "user", "content": "a", "ts": 100.0},
            {"role": "assistant", "content": "b", "ts": 100.0},
        ]
        session = ChatSession(CFG, history, saver=lambda _m: None)
        stamps = [message["ts"] for message in session.messages]
        self.assertEqual(stamps[0], 100.0)
        self.assertGreater(stamps[1], stamps[0])

    def test_valid_timestamps_are_preserved(self):
        history = [
            {"role": "user", "content": "a", "ts": 100.0},
            {"role": "assistant", "content": "b", "ts": 200.0},
        ]
        session = ChatSession(CFG, history, saver=lambda _m: None)
        self.assertEqual([m["ts"] for m in session.messages], [100.0, 200.0])

    def test_missing_timestamps_stay_before_valid_ones(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b", "ts": 100.0},
        ]
        session = ChatSession(CFG, history, saver=lambda _m: None)
        stamps = [m["ts"] for m in session.messages]
        self.assertLess(stamps[0], 100.0)
        self.assertEqual(stamps[1], 100.0)


class RecordingMemory:
    """Stands in for MemoryKeeper: records what the chat turn hands over."""

    def __init__(self):
        self.calls = []

    def after_turn(self, messages):
        self.calls.append([m["role"] for m in messages])


class ExplodingMemory:
    def after_turn(self, messages):
        raise RuntimeError("memory broke")


class ChatServiceTests(unittest.TestCase):
    def make_service(self, client, synthesize=None, memory=None):
        if synthesize is None:
            synthesize = lambda text, tone, speed, **kwargs: b"RIFF"
        return ChatService(
            CFG,
            TONES,
            "gentle",
            synthesize=synthesize,
            save=lambda data, prefix="": "/tmp/out.wav",
            client=client,
            memory=memory,
        )

    def test_success(self):
        saved = []
        session = ChatSession(CFG, [], saver=lambda m: saved.append(list(m)))
        result = self.make_service(FakeClient()).run("你好", session, TONES["gentle"])
        self.assertEqual(result.error_kind, "")
        self.assertEqual(result.actions, ["微笑"])
        self.assertEqual(result.speech, "旅行者君，早安！")
        self.assertEqual(result.audio_path, "/tmp/out.wav")
        self.assertFalse(result.cancelled)
        self.assertEqual(session.messages[-1]["role"], "assistant")

    def test_auth_error_rolls_back_user(self):
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(
            FailingClient(ChatError("bad key", "auth", 401))
        ).run("你好", session)
        self.assertEqual(result.error_kind, "auth")
        self.assertEqual(result.status, 401)
        self.assertEqual(session.messages, [])

    def test_network_error_rolls_back_user(self):
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(
            FailingClient(ChatError("offline", "network"))
        ).run("你好", session)
        self.assertEqual(result.error_kind, "network")
        self.assertEqual(session.messages, [])

    def test_tts_error_keeps_text(self):
        def explode(text, tone, speed, **kwargs):
            raise RuntimeError("tts down")

        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(FakeClient(), explode).run("你好", session)
        self.assertEqual(result.speech, "旅行者君，早安！")
        self.assertEqual(result.tts_error, "tts down")
        self.assertEqual(result.audio_path, "")
        self.assertEqual(session.messages[-1]["role"], "assistant")

    def test_auto_speak_false_skips_tts(self):
        called = []
        cfg = dict(CFG, auto_speak=False)
        service = ChatService(
            cfg,
            TONES,
            "gentle",
            synthesize=lambda *args, **kwargs: called.append(args) or b"RIFF",
            save=lambda data, prefix="": "/tmp/out.wav",
            client=FakeClient(),
        )
        session = ChatSession(cfg, [], saver=lambda _m: None)
        result = service.run("你好", session)
        self.assertEqual(result.speech, "旅行者君，早安！")
        self.assertEqual(result.audio_paths, [])
        self.assertEqual(called, [])

    def test_cancelled_before_start(self):
        event = threading.Event()
        event.set()
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(FakeClient()).run("你好", session, cancel=event)
        self.assertTrue(result.cancelled)
        self.assertEqual(session.messages, [])

    def test_cancelled_after_reply_rolls_back(self):
        event = threading.Event()
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(CancelAfterReplyClient(event)).run(
            "你好", session, cancel=event
        )
        self.assertTrue(result.cancelled)
        self.assertEqual(session.messages, [])

    def test_action_only_skips_tts(self):
        called = []

        def synth(text, tone, speed, **kwargs):
            called.append(text)
            return b"RIFF"

        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(
            FakeClient("（只是安静地看着他）"), synth
        ).run("你好", session)
        self.assertEqual(result.speech, "")
        self.assertEqual(result.actions, ["只是安静地看着他"])
        self.assertEqual(called, [])

    def test_turn_is_handed_to_memory(self):
        memory = RecordingMemory()
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(FakeClient(), memory=memory).run("你好", session)
        self.assertEqual(result.error_kind, "")
        self.assertEqual(len(memory.calls), 1)
        self.assertEqual(memory.calls[0], ["user", "assistant"])

    def test_memory_failure_does_not_break_chat(self):
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = self.make_service(
            FakeClient(), memory=ExplodingMemory()
        ).run("你好", session)
        self.assertEqual(result.error_kind, "")
        self.assertEqual(result.speech, "旅行者君，早安！")

    def test_opening_adds_only_assistant_turn(self):
        client = FakeClient("（放下手中的托盘）\n旅行者君，晚上好。")
        service = self.make_service(client)
        session = ChatSession(CFG, [], saver=lambda _m: None)
        result = service.opening(session, TONES["gentle"])
        self.assertEqual(result.error_kind, "")
        self.assertEqual(result.speech, "旅行者君，晚上好。")
        self.assertEqual(result.audio_path, "/tmp/out.wav")
        self.assertEqual([m["role"] for m in session.messages], ["assistant"])
        # The scene hint is sent to the model, but never stored as a user turn.
        self.assertIn("系统的场景提示", client.calls[-1][-1]["content"])

    def test_opening_does_not_touch_memory(self):
        memory = RecordingMemory()
        service = self.make_service(FakeClient(), memory=memory)
        session = ChatSession(CFG, [], saver=lambda _m: None)
        service.opening(session, TONES["gentle"])
        self.assertEqual(memory.calls, [])


if __name__ == "__main__":
    unittest.main()
