# -*- coding: utf-8 -*-
"""Memory card tests: pure logic, no Tk, no network."""
import json
import os
import time
import unittest

from remvoice.chat import ChatSession
from remvoice.memory import (
    CLARITY_ORDER,
    MemoryKeeper,
    MemoryStore,
    inject_into_persona,
    is_duplicate,
    rule_importance,
    target_clarity,
)
from support import scratch

CFG = {
    "persona": "你是助手。\n\n【输出格式（最高优先级）】\n- 动作单独一行。",
    "memory_enabled": True,
    "memory_llm": True,
    "memory_inject_chars": 1400,
    "memory_max_items": 120,
    "memory_recent_days": 7,
    "memory_fuzzy_after_days": 7,
    "memory_forget_after_days": 60,
    "memory_min_owner_importance": 65,
    "memory_maintain_messages": 4,
    "api_base": "http://example.invalid",
    "api_key": "test",
    "model": "test-model",
    "max_tokens": 300,
}


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, messages, timeout=60):
        self.calls.append(messages)
        return self.reply


class FailingClient:
    def complete(self, messages, timeout=60):
        raise RuntimeError("网络失败")


def make_store(tmp, cfg=None, now=None):
    path = os.path.join(tmp, "chat_memory.json")
    archive = os.path.join(tmp, "chat_memory_full.jsonl")
    return MemoryStore(path=path, archive_path=archive, cfg=cfg or CFG, now=now)


def messages(count, step=60.0, start=None):
    start = time.time() - count * step if start is None else start
    rows = []
    for i in range(count):
        rows.append(
            {
                "id": "m%d" % i,
                "role": "user" if i % 2 == 0 else "assistant",
                "content": ("旅行者君说第%d句话，今天很累，想早点休息。" % i)
                if i % 2 == 0
                else "（轻轻点头）\n助手知道了，旅行者君先休息吧。",
                "ts": start + i * step,
            }
        )
    return rows


class ScoringTests(unittest.TestCase):
    def test_empty_is_minimum(self):
        self.assertEqual(rule_importance(""), 5)

    def test_promise_beats_plain(self):
        self.assertGreater(
            rule_importance("旅行者君答应助手，以后一定早点回来。"),
            rule_importance("旅行者君今天吃了面包。"),
        )

    def test_duplicate_detection(self):
        items = [{"text": "旅行者君讨厌青椒，从来不吃的。"}]
        self.assertTrue(is_duplicate("旅行者君讨厌青椒。", items))
        self.assertFalse(is_duplicate("旅行者君喜欢甜点。", items))


class StoreTests(unittest.TestCase):
    def test_add_persists_and_dedupes(self):
        with scratch() as tmp:
            store = make_store(tmp)
            store.add("旅行者君讨厌青椒。", "preference", 80)
            store.add("旅行者君讨厌青椒。", "preference", 80)
            store.add("旅行者君讨厌青椒", "preference", 70)
            self.assertEqual(len(store.items), 1)
            reloaded = make_store(tmp)
            self.assertEqual(len(reloaded.items), 1)
            self.assertEqual(reloaded.items[0]["importance"], 80)

    def test_prompt_block_and_budget(self):
        with scratch() as tmp:
            store = make_store(tmp)
            store.add("旅行者君在准备考试，最近压力很大。" * 3, "event", 90)
            store.set_summary("两个人一起度过了安静的午后。" * 10)
            block = store.prompt_block(CFG)
            self.assertIn("长期记忆", block)
            self.assertIn("前情摘要", block)
            self.assertLessEqual(len(block), CFG["memory_inject_chars"])

    def test_disabled_renders_nothing(self):
        with scratch() as tmp:
            store = make_store(tmp)
            store.add("旅行者君喜欢甜点。", "preference", 70)
            cfg = dict(CFG, memory_enabled=False)
            self.assertEqual(store.prompt_block(cfg), "")
            self.assertEqual(store.capacity(cfg)["chars"], 0)

    def test_custom_name_is_used(self):
        with scratch() as tmp:
            store = make_store(tmp)
            store.add("小满喜欢甜点。", "preference", 70)
            block = store.prompt_block(dict(CFG, user_name="小满"))
            self.assertIn("你和小满的记忆", block)
            self.assertNotIn("旅行者君", block.split("【长期记忆】")[0])

    def test_search_hits_live_and_archive(self):
        with scratch() as tmp:
            store = make_store(tmp)
            kept = store.add("旅行者君答应周末一起做蛋包饭。", "promise", 70)
            gone = store.add("旅行者君提过想学吉他。", "preference", 60)
            self.assertTrue(store.forget(gone["id"]))
            self.assertEqual(store.search("蛋包饭")[0]["id"], kept["id"])
            archived = store.search("吉他")
            self.assertTrue(archived)
            self.assertEqual(archived[0]["text"], gone["text"])

    def test_capacity_ratio(self):
        with scratch() as tmp:
            store = make_store(tmp)
            store.add("旅行者君讨厌青椒。", "preference", 80)
            cap = store.capacity(CFG)
            self.assertGreater(cap["chars"], 0)
            self.assertLess(cap["ratio"], 1.0)
            self.assertEqual(cap["items"], 1)


class DecayTests(unittest.TestCase):
    def test_one_step_per_day(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            item = store.add("旅行者君随口说了句天气不错。", "owner", 40, ts=now)
            self.assertEqual(item["clarity"], "vivid")
            store.maintain_daily(now=now + 86400)
            self.assertEqual(store.items[0]["clarity"], "clear")
            # Inside recent_days the target stays "clear"; it only fades later.
            store.maintain_daily(now=now + 10 * 86400)
            self.assertEqual(store.items[0]["clarity"], "fuzzy")

    def test_same_day_is_idempotent(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            store.add("旅行者君随口说了句天气不错。", "owner", 40, ts=now)
            store.maintain_daily(now=now + 86400)
            self.assertEqual(store.maintain_daily(now=now + 86400), 0)

    def test_pinned_never_decays(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            store.add("旅行者君第一次叫助手的名字。", "first", 100, ts=now, pinned=True)
            store.maintain_daily(now=now + 200 * 86400)
            self.assertEqual(store.items[0]["clarity"], "vivid")
            self.assertEqual(len(store.items), 1)

    def test_owner_importance_keeps_clear(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            store.add("旅行者君说过助手是他的英雄。", "owner", 80, ts=now)
            store.maintain_daily(now=now + 300 * 86400)
            self.assertEqual(store.items[0]["clarity"], "clear")
            self.assertIn(
                target_clarity(store.items[0], now + 300 * 86400, CFG),
                ("vivid", "clear"),
            )

    def test_low_importance_is_forgotten(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            store.add("旅行者君说今天有点热。", "owner", 30, ts=now)
            store.maintain_daily(now=now + 86400)
            store.maintain_daily(now=now + 120 * 86400)
            self.assertEqual(store.items, [])
            self.assertTrue(store.archive_rows("有点热"))


class InjectionTests(unittest.TestCase):
    def test_block_goes_before_output_contract(self):
        persona = "人设。\n\n【输出格式（最高优先级）】\n- 动作单独一行。"
        merged = inject_into_persona(persona, "【长期记忆】\n- 一条")
        self.assertLess(merged.index("【长期记忆"), merged.index("【输出格式"))
        self.assertTrue(merged.rstrip().endswith("动作单独一行。"))

    def test_session_request_injects_memory(self):
        with scratch() as tmp:
            store = make_store(tmp)
            store.add("旅行者君讨厌青椒。", "preference", 80)
            session = ChatSession(
                CFG, [], saver=lambda _m: None, memory=store
            )
            session.add_user("今天吃什么？")
            request = session.request()
            self.assertEqual(request[0]["role"], "system")
            self.assertIn("讨厌青椒", request[0]["content"])
            self.assertLess(
                request[0]["content"].index("讨厌青椒"),
                request[0]["content"].index("【输出格式"),
            )


class KeeperTests(unittest.TestCase):
    def test_watermark_prevents_double_ingest(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            reply = json.dumps(
                {
                    "summary": "旅行者君和助手一起准备了晚饭。",
                    "facts": [
                        {
                            "text": "旅行者君在准备考试，最近很累。",
                            "category": "event",
                            "importance": 4,
                        }
                    ],
                },
                ensure_ascii=False,
            )
            client = FakeClient(reply)
            keeper = MemoryKeeper(
                store, CFG, client_factory=lambda: client, now=lambda: now
            )
            history = messages(50)
            report = keeper.maintain(history, force=True)
            self.assertEqual(report["mode"], "llm")
            self.assertEqual(report["added"], 1)
            self.assertIn("准备考试", store.items[0]["text"])
            self.assertIn("一起准备了晚饭", store.summary)
            first_upto = store.upto_ts
            self.assertGreater(first_upto, 0)

            second = keeper.maintain(history, force=True)
            self.assertEqual(second["mode"], "empty")
            self.assertEqual(store.upto_ts, first_upto)
            self.assertEqual(len(store.items), 1)

    def test_model_failure_falls_back_locally(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            keeper = MemoryKeeper(
                store, CFG, client_factory=lambda: FailingClient(), now=lambda: now
            )
            report = keeper.maintain(messages(50), force=True)
            self.assertEqual(report["mode"], "fallback")
            self.assertIn("网络", keeper.last_error)
            self.assertTrue(store.summary)

    def test_local_mode_never_calls_model(self):
        with scratch() as tmp:
            now = time.time()
            store = make_store(tmp, now=lambda: now)
            client = FakeClient("{}")
            cfg = dict(CFG, memory_llm=False)
            keeper = MemoryKeeper(store, cfg, client_factory=lambda: client, now=lambda: now)
            report = keeper.maintain(messages(50), force=True)
            self.assertEqual(report["mode"], "local")
            self.assertEqual(client.calls, [])

    def test_disabled_keeper_does_nothing(self):
        with scratch() as tmp:
            store = make_store(tmp)
            cfg = dict(CFG, memory_enabled=False)
            keeper = MemoryKeeper(store, cfg)
            self.assertEqual(keeper.maintain(messages(50), force=True)["mode"], "disabled")
            self.assertIsNone(keeper.after_turn(messages(50)))


class ClarityOrderTests(unittest.TestCase):
    def test_order_is_well_formed(self):
        self.assertEqual(CLARITY_ORDER, ("vivid", "clear", "fuzzy", "trace"))


if __name__ == "__main__":
    unittest.main()
