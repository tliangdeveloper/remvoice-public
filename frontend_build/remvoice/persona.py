# -*- coding: utf-8 -*-
"""Default assistant persona, output contract and persona-card import.

This repository intentionally ships a generic persona.  Put your own character
text in ``config.json`` / ``chat_config.json`` (or import a persona card), and
only use voices/assets you have the rights to use.
"""
import json
import os

PERSONA_FIRST_LINE = (
    "你的名字是「助手」，一个运行在本机的语音伴侣。"
    "你称呼用户为「旅行者君」，语气自然、温柔、简洁。"
)
OLD_PERSONA_PREFIX = "你是一个本地语音伴侣"
OUTPUT_FORMAT = (
    "【输出格式（最高优先级，覆盖前文所有输出规则）】\n"
    "- 动作、神态、心理描写：独占一行，用中文全角括号「（）」包裹，例如：（轻轻点头）\n"
    "- 台词：独占一行，纯文本，不加任何括号或符号包裹；\n"
    "- 每回合输出：动作 0~2 行 + 台词 1~3 行，动作行在前、台词行在后；\n"
    "- 台词行内禁止出现（）【】{}、*、#、Markdown、表情符号；\n"
    "- 禁止把动作混进台词行，动作必须单独一行。"
)
PERSONA = PERSONA_FIRST_LINE + (
    "\n\n你是一个陪伴型本地语音助手，记得用户说过的重要事情，回答简短自然。"
    "\n不要替用户说话，不要编造没有发生过的具体事实。"
    "\n\n" + OUTPUT_FORMAT
)

DEFAULT_CHAT_CONFIG = {
    "api_base": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.8,
    "max_tokens": 300,
    "persona": PERSONA,
    "user_name": "旅行者君",
    "user_short": "旅行者",
    "models_cache": [],
    "models_fetched_at": "",
    "auto_punct": True,
    "voice_lang": "zh",
    "auto_speak": True,
    "display_turns": 8,
    "translate_api_base": "",
    "translate_api_key": "",
    "translate_model": "",
    "memory_enabled": True,
    "memory_llm": True,
    "memory_inject_chars": 1400,
    "memory_max_items": 120,
    "memory_recent_days": 7,
    "memory_fuzzy_after_days": 7,
    "memory_forget_after_days": 60,
    "memory_min_owner_importance": 65,
    "memory_maintain_messages": 4,
}


def load_persona_from_card(card_path):
    if not card_path or not os.path.exists(card_path):
        return None
    try:
        with open(card_path, encoding="utf-8-sig") as f:
            card = json.load(f)
        entry = list(card["data"]["prompts"].values())[0]["data"]
    except Exception:
        return None
    blocks = [
        PERSONA_FIRST_LINE,
        str(entry.get("description", "")).strip(),
        str(entry.get("creator_notes", "")).strip(),
        "性格补充参考：" + str(entry.get("personality", "")).strip(),
    ]
    return "\n\n".join(blocks) + "\n\n" + OUTPUT_FORMAT
