# -*- coding: utf-8 -*-
"""Pure text tests: these are the cases that used to live in selftest()."""
import unittest

from remvoice.text import clean_reply, parse_reply, prepare_tts_text, smart_punct


class ParseReplyTests(unittest.TestCase):
    def test_action_and_speech_lines(self):
        actions, speech = parse_reply(
            "（红着脸低下头，攥着围裙衣角）\n旅行者君，助手只是做了分内的事。"
        )
        self.assertEqual(actions, ["红着脸低下头，攥着围裙衣角"])
        self.assertEqual(speech, "旅行者君，助手只是做了分内的事。")

    def test_inline_parentheses_removed(self):
        actions, speech = parse_reply("（笑）旅行者君早。")
        self.assertEqual(actions, [])
        self.assertEqual(speech, "旅行者君早。")

    def test_plain_speech(self):
        actions, speech = parse_reply("早安，旅行者君。")
        self.assertEqual(actions, [])
        self.assertEqual(speech, "早安，旅行者君。")

    def test_action_only(self):
        actions, speech = parse_reply("（只是安静地看着他）")
        self.assertEqual(actions, ["只是安静地看着他"])
        self.assertEqual(speech, "")


class CleanReplyTests(unittest.TestCase):
    def test_keeps_full_width_punctuation(self):
        text = "旅行者君，早安！今天也要加油哦。"
        self.assertEqual(clean_reply(text), text)

    def test_keeps_question_and_ellipsis(self):
        text = "旅行者君，你回来了。肚子饿不饿？……"
        self.assertEqual(clean_reply(text), text)

    def test_drops_emoji_and_markdown(self):
        self.assertEqual(clean_reply("旅行者君😊 **早安**"), "旅行者君 早安")


class SmartPunctTests(unittest.TestCase):
    def test_question_mark(self):
        self.assertEqual(smart_punct("旅行者君今天累不累"), "旅行者君今天累不累？")

    def test_conjunction_comma(self):
        self.assertEqual(
            smart_punct("今天天气很好但是有点冷"), "今天天气很好，但是有点冷。"
        )

    def test_existing_punctuation_untouched(self):
        text = "旅行者君，助手永远相信你！"
        self.assertEqual(smart_punct(text), text)

    def test_protected_name(self):
        self.assertEqual(
            smart_punct("爱蜜莉雅大人和拉姆姐姐都在厨房"),
            "爱蜜莉雅大人和拉姆姐姐都在厨房。",
        )

    def test_empty(self):
        self.assertEqual(smart_punct(""), "")
        self.assertEqual(smart_punct("   "), "   ")


class PrepareTTSTextTests(unittest.TestCase):
    def test_strips_leading_period_and_merges_short_filler(self):
        self.assertEqual(
            prepare_tts_text("。嗯，助手在呢。"),
            "嗯 助手在呢。",
        )

    def test_merges_short_opening_clause(self):
        self.assertEqual(prepare_tts_text("好的，走吧。"), "好的 走吧。")

    def test_leaves_normal_sentence_alone(self):
        text = "旅行者君，早安。"
        self.assertEqual(prepare_tts_text(text), text)

    def test_keeps_leading_ellipsis(self):
        self.assertEqual(prepare_tts_text("……嗯。"), "……嗯。")


if __name__ == "__main__":
    unittest.main()
