# -*- coding: utf-8 -*-
"""OpenAI-compatible SSE parsing."""
import json
import unittest

from remvoice.llm import ChatClient, ChatError


CFG = {
    "api_base": "http://example.invalid",
    "api_key": "test",
    "model": "test-model",
    "temperature": 0.8,
    "max_tokens": 300,
}


class FakeSSE:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def __iter__(self):
        return iter(self.lines)


def delta(text):
    return (
        "data: "
        + json.dumps({"choices": [{"delta": {"content": text}}]})
        + "\n"
    ).encode("utf-8")


class StreamTests(unittest.TestCase):
    def test_yields_content_deltas(self):
        lines = [delta("你"), delta("好"), b"data: [DONE]\n"]
        client = ChatClient(CFG, opener=lambda req, timeout=60: FakeSSE(lines))
        self.assertEqual(list(client.stream([{"role": "user", "content": "hi"}])), ["你", "好"])

    def test_ignores_non_data_lines(self):
        lines = [b": keepalive\n", delta("好"), b"data: [DONE]\n"]
        client = ChatClient(CFG, opener=lambda req, timeout=60: FakeSSE(lines))
        self.assertEqual(list(client.stream([])), ["好"])

    def test_empty_stream_raises(self):
        client = ChatClient(CFG, opener=lambda req, timeout=60: FakeSSE([]))
        with self.assertRaises(ChatError) as ctx:
            list(client.stream([]))
        self.assertEqual(ctx.exception.kind, "empty")


if __name__ == "__main__":
    unittest.main()
