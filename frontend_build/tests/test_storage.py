# -*- coding: utf-8 -*-
"""Persistence helpers: atomic writes, .bak recovery and config merge."""
import json
import os
import unittest

from remvoice import storage
from support import scratch


class AtomicWriteTests(unittest.TestCase):
    def test_writes_and_keeps_backup(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "sub", "chat.json")
            storage.atomic_write_text(path, json.dumps({"a": 1}))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": 1})
            storage.atomic_write_text(path, json.dumps({"a": 2}))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": 2})
            with open(path + ".bak", encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": 1})

    def test_no_tmp_left_behind(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "x.json")
            storage.atomic_write_text(path, "{}")
            self.assertFalse(os.path.exists(path + ".tmp"))


class HistoryReadTests(unittest.TestCase):
    def test_accepts_utf8_bom(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "chat_history.json")
            with open(path, "w", encoding="utf-8-sig") as f:
                json.dump([{"role": "user", "content": "你好"}], f, ensure_ascii=False)
            old = storage.CHAT_HISTORY
            storage.CHAT_HISTORY = path
            try:
                history = storage.load_chat_history()
            finally:
                storage.CHAT_HISTORY = old
            self.assertEqual(history[-1]["content"], "你好")


class ArchiveTests(unittest.TestCase):
    def test_archive_keeps_both_roles(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "chat_archive.jsonl")
            old = storage.CHAT_ARCHIVE
            storage.CHAT_ARCHIVE = path
            try:
                self.assertTrue(storage.append_chat_archive("user", "你好"))
                self.assertTrue(
                    storage.append_chat_archive(
                        "assistant",
                        "早安",
                        audio="a.wav",
                        voice_lang="ja",
                        tone="gentle",
                    )
                )
            finally:
                storage.CHAT_ARCHIVE = old
            with open(path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
            self.assertEqual(rows[1]["audio"], "a.wav")
            self.assertEqual(rows[1]["voice_lang"], "ja")
            self.assertEqual(rows[1]["tone"], "gentle")

    def test_daily_transcript_keeps_both_roles(self):
        with scratch() as tmp:
            old = storage.CHAT_RECORD_DIR
            storage.CHAT_RECORD_DIR = tmp
            try:
                storage.append_chat_transcript("旅行者君", "你好")
                storage.append_chat_transcript("助手", "早安")
            finally:
                storage.CHAT_RECORD_DIR = old
            names = [n for n in os.listdir(tmp) if n.endswith(".txt")]
            self.assertEqual(len(names), 1)
            with open(os.path.join(tmp, names[0]), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("旅行者君：你好", text)
            self.assertIn("助手：早安", text)

    def test_read_archive_newest_first_and_filter(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "chat_archive.jsonl")
            old = storage.CHAT_ARCHIVE
            storage.CHAT_ARCHIVE = path
            try:
                storage.append_chat_archive("user", "早安")
                storage.append_chat_archive("assistant", "晚上好")
                storage.append_chat_archive("user", "早点睡")
                rows = storage.read_chat_archive(limit=2)
                hits = storage.read_chat_archive("晚")
            finally:
                storage.CHAT_ARCHIVE = old
            self.assertEqual([row["content"] for row in rows], ["早点睡", "晚上好"])
            self.assertEqual([row["content"] for row in hits], ["晚上好"])


class RotateLogTests(unittest.TestCase):
    def test_rotates_when_too_large(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "service.log")
            with open(path, "wb") as f:
                f.write(b"x" * 32)
            self.assertTrue(storage.rotate_log(path, max_bytes=16, backups=2))
            self.assertFalse(os.path.exists(path))
            with open(path + ".1", "rb") as f:
                self.assertEqual(f.read(), b"x" * 32)

    def test_keeps_small_log(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "service.log")
            with open(path, "wb") as f:
                f.write(b"x" * 4)
            self.assertFalse(storage.rotate_log(path, max_bytes=16))
            self.assertTrue(os.path.exists(path))


class ConfigReadTests(unittest.TestCase):
    def test_unknown_keys_survive_merge(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "chat_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"timeout": 30, "model": "custom"}, f)
            old = storage.CHAT_CONFIG
            storage.CHAT_CONFIG = path
            try:
                cfg = storage.load_chat_config()
            finally:
                storage.CHAT_CONFIG = old
            self.assertEqual(cfg["timeout"], 30)
            self.assertEqual(cfg["model"], "custom")
            self.assertIn("persona", cfg)


@unittest.skipUnless(storage.secrets_available(), "DPAPI is Windows-only")
class ApiKeyStorageTests(unittest.TestCase):
    def test_plaintext_key_migrates_and_backup_is_scrubbed(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "chat_config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"api_key": "sk-plain", "model": "old"}, f)
            old = storage.CHAT_CONFIG
            storage.CHAT_CONFIG = path
            try:
                cfg = storage.load_chat_config()
                with open(path, encoding="utf-8") as f:
                    raw = f.read()
                with open(path + ".bak", encoding="utf-8") as f:
                    backup = f.read()
                again = storage.load_chat_config()
            finally:
                storage.CHAT_CONFIG = old
            self.assertEqual(cfg["api_key"], "sk-plain")
            self.assertNotIn("sk-plain", raw)
            self.assertIn("api_key_enc", raw)
            self.assertNotIn("sk-plain", backup)
            self.assertEqual(again["api_key"], "sk-plain")

    def test_clearing_key_removes_encrypted_token(self):
        with scratch() as tmp:
            path = os.path.join(tmp, "chat_config.json")
            old = storage.CHAT_CONFIG
            storage.CHAT_CONFIG = path
            try:
                storage.save_chat_config({"api_key": "sk-test"})
                storage.save_chat_config({"api_key": ""})
                with open(path, encoding="utf-8") as f:
                    raw = f.read()
            finally:
                storage.CHAT_CONFIG = old
            self.assertNotIn("api_key_enc", raw)
            self.assertNotIn("sk-test", raw)


if __name__ == "__main__":
    unittest.main()
