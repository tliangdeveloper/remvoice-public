# -*- coding: utf-8 -*-
"""Reply parsing, punctuation cleanup and name substitution.

These functions are pure (no Tk, no network), so they are cheap to unit test
and can be reused by future chat features such as streaming replies.
"""
import re

CONJUNCTIONS = [
    "但是", "可是", "不过", "所以", "因此", "然后", "接着", "而且", "还有",
    "因为", "如果", "虽然", "即使", "只要", "只有", "还是", "或者", "于是",
    "结果", "另外", "其实", "总之", "比如",
]
TIME_ADVERBS = ["今天", "现在", "刚才", "其实", "总之", "不过", "然后", "而且", "所以"]
PARTICLES = "呢啊呀啦哦嘛"
PROTECTED_NAMES = ["旅行者君", "助手"]
PUNCT_CHARS = "，。！？、；：…—～,.!?;:"
QUESTION_WORDS = ["吗", "呢", "么", "什么", "怎么", "为什么", "哪", "几", "多少", "难道", "是不是"]
EXCL_MARKS = ["太好了", "好棒", "太", "真", "加油", "快点", "不要", "讨厌", "不行", "小心"]


def apply_names(text, user_name, user_short):
    value = str(text or "")
    if user_name:
        value = value.replace("旅行者君", str(user_name))
    if user_short:
        value = value.replace("旅行者", str(user_short))
    return value



def parse_reply(text, add_punct=True):
    """Split a reply into action lines and speech text.

    Returns (actions: list[str], speech: str). Lines fully wrapped in full
    or half width parentheses are actions; the rest are speech.
    """
    actions = []
    speech_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        is_action = False
        if (
            len(stripped) >= 2
            and stripped[0] == "（"
            and stripped[-1] == "）"
            and stripped[1:-1].strip()
        ):
            actions.append(stripped[1:-1].strip())
            is_action = True
        elif (
            len(stripped) >= 2
            and stripped[0] == "("
            and stripped[-1] == ")"
            and stripped[1:-1].strip()
        ):
            actions.append(stripped[1:-1].strip())
            is_action = True
        if not is_action:
            speech_lines.append(stripped)

    if not actions:
        speech_lines = [line.strip() for line in text.splitlines() if line.strip()]

    cleaned = []
    for line in speech_lines:
        line = re.sub(r"[（(][^（）()]*[）)]", "", line).strip()
        if not line:
            continue
        if add_punct and line[-1] not in "。！？～…!?.":
            line += "。"
        cleaned.append(line)
    speech = "".join(cleaned)
    return actions, speech


def smart_punct(text):
    """Insert missing punctuation conservatively; never modify existing marks."""
    if not text.strip():
        return text
    text = text.strip()

    spans = []
    for word in PROTECTED_NAMES:
        start = 0
        while True:
            i = text.find(word, start)
            if i < 0:
                break
            spans.append((i, i + len(word)))
            start = i + 1

    def inside_name(p):
        return any(s < p < e for s, e in spans)

    def prev_ok(p):
        if p <= 0:
            return False
        prev = text[p - 1]
        return prev not in PUNCT_CHARS and not prev.isspace() and not prev.isascii()

    result = text
    if text[-1] not in "。！？～…!?.":
        is_question = any(w in text for w in QUESTION_WORDS)
        is_question = is_question or bool(re.search(r"[\u4e00-\u9fff]不[\u4e00-\u9fff]", text))
        is_exclaim = any(w in text for w in EXCL_MARKS)
        if is_question:
            result += "？"
        elif is_exclaim:
            result += "！"
        else:
            result += "。"

    positions = set()
    for word in CONJUNCTIONS:
        start = 0
        while True:
            i = text.find(word, start)
            if i < 0:
                break
            start = i + 1
            if i == 0:
                continue
            if prev_ok(i) and not inside_name(i):
                positions.add(i)

    for word in TIME_ADVERBS:
        if text.startswith(word):
            j = len(word)
            rest = text[j:]
            cjk_count = sum(1 for ch in rest if "\u4e00" <= ch <= "\u9fff")
            if cjk_count >= 10 and j < len(text) and text[j] not in PUNCT_CHARS and not inside_name(j):
                positions.add(j)

    run_start = 0
    i = 0
    while i <= len(text):
        if i == len(text) or text[i] in "，。！？、；：…":
            run = text[run_start:i]
            cjk_count = sum(1 for ch in run if "\u4e00" <= ch <= "\u9fff")
            if cjk_count >= 18:
                spot = None
                for word in CONJUNCTIONS:
                    k = run.find(word)
                    while k != -1:
                        p = run_start + k
                        if p > run_start and prev_ok(p) and not inside_name(p):
                            spot = p
                            break
                        k = run.find(word, k + 1)
                    if spot is not None:
                        break
                if spot is None:
                    for ch in PARTICLES:
                        k = run.find(ch)
                        while k != -1:
                            p = run_start + k + 1
                            if p < run_start + len(run) and text[p] not in PUNCT_CHARS and not inside_name(p):
                                spot = p
                                break
                            k = run.find(ch, k + 1)
                        if spot is not None:
                            break
                if spot is not None:
                    positions.add(spot)
            run_start = i + 1
        i += 1

    for p in sorted(positions, reverse=True):
        if p < len(result) and result[p] != "，":
            result = result[:p] + "，" + result[p:]
    return result


def clean_reply(text):
    allowed = set("。．.！？!?，,、；;：:…—～\"'“”‘’（）() ")
    out = []
    for ch in text:
        if (
            ("\u4e00" <= ch <= "\u9fff")
            or ("\u3040" <= ch <= "\u30ff")
            or ch in allowed
            or (ch.isascii() and ch.isalnum())
        ):
            out.append(ch)
    text = "".join(out)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_LEADING_PUNCT = "，。、；：,.!?;:"


def prepare_tts_text(text, max_short=3):
    """Normalize text for GPT-SoVITS without changing the displayed reply.

    GPT-SoVITS prepends a leading ``。`` when the first clause is shorter than
    four characters, which adds an unnatural pause.  Strip an accidental
    leading punctuation mark and turn ``嗯，助手在呢`` into ``嗯 助手在呢`` so
    the first segment is long enough while the filler is still spoken.
    """
    value = str(text).strip()
    while value and value[0] in _LEADING_PUNCT:
        value = value[1:].lstrip()

    # Only merge a genuinely short opening clause (e.g. 嗯/啊/好的/不要).
    pattern = re.compile(
        r"^([^，。！？、；：…,.!?;:\s]{1,%d})[，,、]\s*(.+)$" % max(1, int(max_short)),
        re.S,
    )
    match = pattern.match(value)
    if match:
        head, rest = match.group(1), match.group(2).strip()
        if rest:
            value = head + " " + rest
    return value
