# -*- coding: utf-8 -*-
"""Local OpenAI-compatible endpoints may run without an API key."""
import json
import unittest

from remvoice.llm import ChatClient, is_local_endpoint


CFG = {
    "api_base": "http://127.0.0.1:11434/v1",
    "api_key": "",
    "model": "qwen2.5:7b",
    "temperature": 0.8,
    "max_tokens": 300,
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return self.payload


class LocalEndpointTests(unittest.TestCase):
    def test_loopback_and_mdns_are_local(self):
        for base in (
            "http://localhost:11434/v1",
            "http://127.0.0.1:1234/v1",
            "http://127.0.0.2:8000/v1",
            "http://[::1]:9880",
            "http://desk.local:8000/v1",
        ):
            self.assertTrue(is_local_endpoint(base), base)

    def test_remote_endpoint_is_not_local(self):
        for base in ("https://api.deepseek.com", "http://192.168.1.9:8000/v1", ""):
            self.assertFalse(is_local_endpoint(base), base)

    def test_empty_key_omits_authorization_header(self):
        captured = {}

        def opener(req, timeout=60):
            captured["req"] = req
            return FakeResponse(
                json.dumps(
                    {"choices": [{"message": {"content": "你好"}}]}
                ).encode("utf-8")
            )

        client = ChatClient(CFG, opener=opener)
        self.assertEqual(client.complete([{"role": "user", "content": "hi"}]), "你好")
        self.assertIsNone(captured["req"].get_header("Authorization"))

    def test_key_adds_authorization_header(self):
        captured = {}

        def opener(req, timeout=60):
            captured["req"] = req
            return FakeResponse(
                json.dumps(
                    {"choices": [{"message": {"content": "你好"}}]}
                ).encode("utf-8")
            )

        cfg = dict(CFG, api_key="secret")
        client = ChatClient(cfg, opener=opener)
        client.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(
            captured["req"].get_header("Authorization"), "Bearer secret"
        )


if __name__ == "__main__":
    unittest.main()
