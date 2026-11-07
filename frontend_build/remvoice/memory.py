# -*- coding: utf-8 -*-
"""Long-term chat memory: a decaying memory card plus a rolling summary.

Why this shape
--------------
SurfCore's memory card (score -> clarity -> forget) fits a single-user desktop
companion well: no vector database, no embedding service, one JSON file, one
prompt block.  RemVoice is a single-process Tk app, so the two parts that only
exist because SurfCore has a separate QQ bridge process are dropped: there is
no inbox file, the chat history itself is the source and a timestamp watermark
decides what has already been distilled.

Two layers are injected into the system prompt:

* ``summary`` - a rolling paragraph about everything that already fell out of
  the request window (keeps continuity after 20 turns);
* ``items``   - short third-person facts, preferences, promises and events,
  each with an importance (1-100) and a clarity that decays one step per day::

      vivid -> clear -> fuzzy -> trace -> forgotten

Only the *rendered* block goes into the prompt, always inside a character
budget.  The full archive is append-only JSONL and is never injected; it is
only searched from the memory panel.
"""
import difflib
import json
import os
import re
import threading
import time

from .settings import APP_LOG, MEMORY_ARCHIVE, MEMORY_PATH
from .storage import append_log, atomic_write_text

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

CLARITY_ORDER = ("vivid", "clear", "fuzzy", "trace")
CLARITY_LABEL = {"vivid": "清晰", "clear": "清楚", "fuzzy": "模糊", "trace": "印象"}
CATEGORY_LABEL = {
    "owner": "关于旅行者君",
    "world": "世界观",
    "event": "一起经历",
    "promise": "约定",
    "preference": "偏好",
    "first": "最初",
    "manual": "手记",
}

DEFAULTS = {
    "memory_enabled": True,
    # Ask the model to distil new dialogue.  Turn it off to stay fully local:
    # the rule-based fallback still records strong lines, just less neatly.
    "memory_llm": True,
    "memory_inject_chars": 1400,
    "memory_max_items": 120,
    "memory_recent_days": 7,
    "memory_fuzzy_after_days": 7,
    "memory_forget_after_days": 60,
    "memory_min_owner_importance": 65,
    "memory_maintain_messages": 4,
}

_STRONG_EMOTION = (
    "喜欢", "想念", "难过", "委屈", "哭", "抱抱", "对不起", "谢谢", "陪",
    "永远", "害怕", "孤独", "寂寞", "开心", "幸福", "重要", "英雄",
)
_EMOTION = (
    "累", "烦", "生气", "高兴", "担心", "讨厌", "爱", "心疼", "安慰",
    "温柔", "安心", "寂寞", "压力",
)
_PROMISE = ("答应", "约定", "说好", "以后", "下次", "记得", "不要忘", "发誓", "一定")
_FIRST = ("第一次", "初次", "头一次", "最早", "刚认识", "纪念日", "生日")
_PREFERENCE = ("喜欢", "讨厌", "不吃", "爱吃", "习惯", "想学", "害怕", "过敏", "最怕")
_TIME_WORD = ("明天", "后天", "下周", "周末", "早上", "晚上", "考试", "上班", "加班", "出差")

_SENTENCE_END = "。！？!?…~～"

EMPTY = {
    "version": 1,
    "summary": "",
    "upto_ts": 0.0,
    "items": [],
    "stats": {"created": 0, "forgotten": 0, "merged": 0, "last_decay_day": ""},
    "updated_at": 0.0,
}


def option(cfg, key):
    value = (cfg or {}).get(key, DEFAULTS[key])
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def new_id(seed=""):
    return "mem_%x_%s" % (int(time.time() * 1000), (seed or os.urandom(3).hex())[:6])


# ---------------------------------------------------------------------------
# Scoring (pure functions)
# ---------------------------------------------------------------------------


def rule_importance(text, category="owner"):
    """Heuristic 1-100 score for one line of dialogue.

    Mirrors the idea behind SurfCore's ``scoreMemory``: cheap, deterministic and
    explainable.  It is only used when the model is off or unavailable, and as
    a small boost on top of model-supplied importance.
    """
    text = str(text or "").strip()
    if not text:
        return 5
    score = 20
    if category == "owner":
        score += 42
    elif category == "event":
        score += 30
    elif category == "promise":
        score += 24
    elif category == "preference":
        score += 18
    elif category == "first":
        score += 30

    strong = sum(1 for word in _STRONG_EMOTION if word in text)
    mild = sum(1 for word in _EMOTION if word in text and word not in _STRONG_EMOTION)
    score += min(24, strong * 8)
    score += min(12, mild * 3)
    score += min(6, text.count("！") + text.count("!"))

    if any(word in text for word in _PROMISE):
        score += 16
    if any(word in text for word in _FIRST):
        score += 20
    if any(word in text for word in _PREFERENCE):
        score += 10
    if any(word in text for word in _TIME_WORD):
        score += 8
    if len(text) < 6:
        score -= 12
    if len(text) > 60:
        score -= 4
    return max(5, min(100, score))


def _norm(text):
    text = str(text or "").lower()
    text = re.sub(r"[\s，。！？、,.!?…~～:：;；\"'“”‘’（）()【】\[\]]", "", text)
    return text


def is_duplicate(text, items, threshold=0.82):
    """True when ``text`` says (nearly) the same thing as an existing item."""
    target = _norm(text)
    if len(target) < 4:
        return False
    for item in items:
        other = _norm(item.get("text", ""))
        if not other:
            continue
        if target == other:
            return True
        if len(target) >= 8 and len(other) >= 8 and (target in other or other in target):
            return True
        if difflib.SequenceMatcher(None, target, other).ratio() >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------


def _day_key(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _days_between(old_ts, now_ts):
    if not old_ts:
        return 0
    try:
        return max(0.0, (now_ts - float(old_ts)) / 86400.0)
    except (TypeError, ValueError):
        return 0.0


def target_clarity(item, now, cfg):
    """Clarity this item *should* have today (before the one-step rule)."""
    importance = float(item.get("importance", 50))
    recent_days = float(option(cfg, "memory_recent_days"))
    fuzzy_after = float(option(cfg, "memory_fuzzy_after_days"))
    owner_floor = float(option(cfg, "memory_min_owner_importance"))
    age = _days_between(item.get("last_seen_at") or item.get("created_at"), now)

    if item.get("pinned"):
        return "vivid"

    retention = importance + min(12, int(item.get("recall_count", 0)) * 2)
    if age <= recent_days:
        retention += 18
    elif age <= recent_days + 2:
        retention += 8
    if age > fuzzy_after:
        penalty = 0.35 if importance < owner_floor else 0.12
        retention -= (age - fuzzy_after) * penalty
    if importance >= owner_floor:
        retention = max(55.0, retention)

    if retention >= 75:
        return "vivid"
    if retention >= 52:
        return "clear"
    if retention >= 32:
        return "fuzzy"
    return "trace"


def should_forget(item, now, cfg):
    if item.get("pinned"):
        return False
    importance = float(item.get("importance", 50))
    owner_floor = float(option(cfg, "memory_min_owner_importance"))
    if importance >= owner_floor:
        return False
    forgotten_after = float(option(cfg, "memory_forget_after_days"))
    age = _days_between(item.get("last_seen_at") or item.get("created_at"), now)
    return age > forgotten_after and importance < 50 and item.get("clarity") == "trace"


def advance(item, now, cfg):
    """Move one clarity step toward ``target_clarity``; return True if changed."""
    current = item.get("clarity", "vivid")
    if current not in CLARITY_ORDER:
        current = "vivid"
    target = target_clarity(item, now, cfg)
    index = CLARITY_ORDER.index(current)
    target_index = CLARITY_ORDER.index(target)
    if target_index > index:
        index += 1
    elif target_index < index:
        index -= 1
    new = CLARITY_ORDER[index]
    if new != current:
        item["clarity"] = new
        return True
    return False


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MemoryStore:
    """JSON-backed memory card.  One writer, atomic saves, best-effort reads."""

    def __init__(self, path=None, archive_path=None, now=None, cfg=None):
        self.path = path or MEMORY_PATH
        self.archive_path = archive_path or MEMORY_ARCHIVE
        self._now = now or time.time
        self.cfg = cfg if cfg is not None else {}
        self._lock = threading.RLock()
        self.data = self._load()

    # -- persistence -----------------------------------------------------
    @staticmethod
    def _blank():
        return json.loads(json.dumps(EMPTY, ensure_ascii=False))

    def _load(self):
        data = self._blank()
        for path in (self.path, self.path + ".bak"):
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8-sig") as f:
                    loaded = json.load(f)
            except Exception:
                continue
            if not isinstance(loaded, dict):
                continue
            if isinstance(loaded.get("items"), list):
                data["items"] = [i for i in loaded["items"] if isinstance(i, dict)]
            for key in ("summary",):
                if isinstance(loaded.get(key), str):
                    data[key] = loaded[key]
            for key in ("upto_ts", "updated_at"):
                try:
                    data[key] = float(loaded.get(key) or 0)
                except (TypeError, ValueError):
                    pass
            stats = loaded.get("stats")
            if isinstance(stats, dict):
                data["stats"].update(stats)
            break
        return data

    def save(self):
        with self._lock:
            self.data["updated_at"] = self._now()
            try:
                atomic_write_text(
                    self.path, json.dumps(self.data, ensure_ascii=False, indent=1)
                )
            except Exception as exc:  # never break a chat over memory I/O
                self.log("记忆卡保存失败：%s" % exc)

    @staticmethod
    def log(message):
        append_log(APP_LOG, "记忆 · " + str(message))

    # -- basic accessors -------------------------------------------------
    @property
    def items(self):
        with self._lock:
            return list(self.data["items"])

    @property
    def summary(self):
        return self.data.get("summary", "")

    def set_summary(self, text):
        with self._lock:
            self.data["summary"] = str(text or "").strip()

    @property
    def upto_ts(self):
        try:
            return float(self.data.get("upto_ts") or 0)
        except (TypeError, ValueError):
            return 0.0

    def set_upto_ts(self, ts):
        with self._lock:
            self.data["upto_ts"] = float(ts or 0)

    def stats(self):
        with self._lock:
            return dict(self.data.get("stats", {}))

    def bump(self, key, delta=1):
        with self._lock:
            stats = self.data.setdefault("stats", {})
            stats[key] = int(stats.get(key, 0)) + delta

    # -- mutation --------------------------------------------------------
    def add(
        self,
        text,
        category="owner",
        importance=50,
        ts=None,
        source_ids=None,
        pinned=False,
        archive=True,
    ):
        """Insert a memory unless it duplicates one; returns the stored item."""
        text = re.sub(r"\s+", " ", str(text or "")).strip(" 。.，,")
        if not text:
            return None
        if len(text) > 120:
            text = text[:119] + "…"
        category = category if category in CATEGORY_LABEL else "owner"
        now = float(ts or self._now())
        with self._lock:
            existing = self.data["items"]
            target = _norm(text)
            for item in existing:
                if _norm(item.get("text", "")) == target or is_duplicate(
                    text, [item]
                ):
                    self._touch(item, now)
                    item["importance"] = max(
                        float(item.get("importance", 0)), float(importance)
                    )
                    self.bump("merged")
                    self.save()
                    return item
            item = {
                "id": new_id(),
                "text": text,
                "category": category,
                "importance": max(1.0, min(100.0, float(importance))),
                "clarity": "vivid",
                "pinned": bool(pinned),
                "created_at": now,
                "last_seen_at": now,
                "recall_count": 0,
                "source_ids": list(source_ids or []),
            }
            existing.append(item)
            self.bump("created")
            if archive:
                self.archive([item])
            self._trim()
            self.save()
            return item

    @staticmethod
    def _touch(item, now):
        item["last_seen_at"] = float(now)
        item["recall_count"] = int(item.get("recall_count", 0)) + 1
        index = CLARITY_ORDER.index(item.get("clarity", "vivid"))
        item["clarity"] = CLARITY_ORDER[max(0, index - 1)]

    def _trim(self):
        limit = max(10, int(option(self.cfg, "memory_max_items")))
        items = self.data["items"]
        if len(items) <= limit:
            return
        # Archive before dropping, then evict the least important / oldest
        # unpinned entries.  Pinned rows are never evicted.
        victims = [i for i in items if not i.get("pinned")]
        victims.sort(
            key=lambda i: (float(i.get("importance", 0)), i.get("last_seen_at", 0))
        )
        drop = len(items) - limit
        removed = []
        for item in victims[:drop]:
            items.remove(item)
            removed.append(item)
        if removed:
            self.archive(removed)

    def forget(self, item_id):
        with self._lock:
            item = self._find(item_id)
            if not item or item.get("pinned"):
                return False
            self.data["items"].remove(item)
            self.archive([item])
            self.bump("forgotten")
            self.save()
            return True

    def pin(self, item_id, pinned=True):
        with self._lock:
            item = self._find(item_id)
            if not item:
                return False
            item["pinned"] = bool(pinned)
            if pinned:
                item["clarity"] = "vivid"
            self.save()
            return True

    def _find(self, item_id):
        for item in self.data["items"]:
            if item.get("id") == item_id:
                return item
        return None

    def get(self, item_id):
        with self._lock:
            return dict(self._find(item_id) or {})

    # -- decay -----------------------------------------------------------
    def maintain_daily(self, now=None, cfg=None):
        """Apply at most one clarity step per missed day (capped at 30)."""
        now = float(now or self._now())
        cfg = cfg if cfg is not None else self.cfg
        with self._lock:
            stats = self.data.setdefault("stats", {})
            today = _day_key(now)
            last = stats.get("last_decay_day") or ""
            if last == today:
                return 0
            try:
                missed = (
                    int(
                        (
                            time.mktime(time.strptime(today, "%Y-%m-%d"))
                            - time.mktime(time.strptime(last, "%Y-%m-%d"))
                        )
                        / 86400
                    )
                    if last
                    else 1
                )
            except Exception:
                missed = 1
            missed = max(1, min(30, missed))
            changed = 0
            for item in list(self.data["items"]):
                for _ in range(missed):
                    if advance(item, now, cfg):
                        changed += 1
                if should_forget(item, now, cfg):
                    self.data["items"].remove(item)
                    self.archive([item])
                    self.bump("forgotten")
                    changed += 1
            stats["last_decay_day"] = today
            self.save()
            return changed

    # -- rendering -------------------------------------------------------
    def _ranked(self):
        items = [i for i in self.data["items"] if i.get("clarity") != "trace"]
        traces = [i for i in self.data["items"] if i.get("clarity") == "trace"]
        key = lambda i: (
            0 if i.get("pinned") else 1,
            -float(i.get("importance", 0)),
            -float(i.get("last_seen_at") or 0),
        )
        return sorted(items, key=key), sorted(traces, key=key)

    @staticmethod
    def _line(item):
        text = str(item.get("text", "")).strip()
        return "- " + text if text else ""

    def prompt_block(self, cfg=None):
        """The text injected into the system prompt, or ``""``."""
        cfg = cfg if cfg is not None else (self.cfg or {})
        if not option(cfg, "memory_enabled"):
            return ""
        budget = max(300, int(option(cfg, "memory_inject_chars")))
        summary = self.summary.strip()
        ranked, traces = self._ranked()
        who = str(cfg.get("user_name") or "旅行者君")

        head = "【你和%s的记忆（真实发生过的事，不是新对话）】" % who
        tail = "（记忆只作参考，不要逐条复述。）"

        parts = []
        used = 0
        if summary:
            room = min(int(budget * 0.42), budget - 140)
            if room >= 40:
                text = summary if len(summary) <= room else summary[: room - 1] + "…"
                parts.append("【前情摘要】\n" + text)
                used += len(text) + 8

        limit = budget - len(head) - len(tail) - 24 - used
        rows = []
        for item in ranked:
            line = self._line(item)
            if not line:
                continue
            if len("\n".join(rows + [line])) > limit:
                break
            rows.append(line)
        if not rows:
            for item in traces[:2]:
                line = self._line(item)
                if line and len("\n".join(rows + [line])) <= limit:
                    rows.append(line)
        if rows:
            parts.append("【长期记忆】\n" + "\n".join(rows))
        if not parts:
            return ""
        return head + "\n" + "\n\n".join(parts) + "\n" + tail

    def markdown(self, cfg=None):
        cfg = cfg if cfg is not None else (self.cfg or {})
        lines = ["# RemVoice 记忆卡", ""]
        if self.summary:
            lines += ["## 前情摘要", "", self.summary, ""]
        per_clarity = {}
        for item in sorted(
            self.data["items"], key=lambda i: -float(i.get("importance", 0))
        ):
            per_clarity.setdefault(item.get("clarity", "vivid"), []).append(item)
        for clarity in CLARITY_ORDER:
            group = per_clarity.get(clarity)
            if not group:
                continue
            lines += ["## %s（%d 条）" % (CLARITY_LABEL[clarity], len(group)), ""]
            for item in group:
                stamp = time.strftime(
                    "%Y-%m-%d", time.localtime(item.get("created_at") or 0)
                )
                label = CATEGORY_LABEL.get(item.get("category"), "记忆")
                star = "★ " if item.get("pinned") else ""
                lines.append(
                    "- %s%s（%s · %s · 重要度 %d）"
                    % (
                        star,
                        item.get("text", ""),
                        label,
                        stamp,
                        int(item.get("importance", 0)),
                    )
                )
            lines.append("")
        if not self.data["items"]:
            lines.append("（还没有记忆）")
        lines.append("")
        lines.append(
            "提示词占用：%d / %d 字"
            % (len(self.prompt_block(cfg)), int(option(cfg, "memory_inject_chars")))
        )
        return "\n".join(lines)

    def capacity(self, cfg=None):
        cfg = cfg if cfg is not None else (self.cfg or {})
        block = self.prompt_block(cfg)
        budget = max(200, int(option(cfg, "memory_inject_chars")))
        return {
            "chars": len(block),
            "budget": budget,
            "ratio": len(block) / float(budget) if budget else 0.0,
            "items": len(self.data["items"]),
            "summary": len(self.summary),
        }

    # -- archive and search ---------------------------------------------
    def archive(self, items):
        """Append items to the never-injected JSONL archive (best effort)."""
        if not items:
            return
        try:
            directory = os.path.dirname(self.archive_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            stamp = int(self._now() * 1000)
            with open(self.archive_path, "a", encoding="utf-8") as f:
                for item in items:
                    row = dict(item)
                    row.setdefault("archived_at", self._now())
                    row["archive_id"] = "arc_%d_%s" % (stamp, new_id()[4:9])
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            self.log("全量档案写入失败：%s" % exc)

    def archive_rows(self, query="", limit=200, scan_bytes=8 * 1024 * 1024):
        rows = []
        path = self.archive_path
        if not os.path.exists(path):
            return rows
        try:
            with open(path, "rb") as f:
                try:
                    f.seek(-scan_bytes, os.SEEK_END)
                except OSError:
                    f.seek(0)
                data = f.read().decode("utf-8", "replace")
        except Exception:
            return rows
        needle = _norm(query)
        for line in reversed(data.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if needle and needle not in _norm(row.get("text", "")):
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
        return rows

    def search(self, query, limit=60):
        needle = _norm(query)
        if not needle:
            return self.items[:limit]
        hits = [i for i in self.data["items"] if needle in _norm(i.get("text", ""))]
        if len(hits) < limit:
            seen = {i.get("id") for i in hits}
            for row in self.archive_rows(query, limit=limit):
                if row.get("id") not in seen:
                    hits.append(row)
                if len(hits) >= limit:
                    break
        return hits[:limit]

    def clear(self, keep_summary=False, now=None):
        """Empty the card; the watermark jumps to now so old chat is not re-ingested."""
        with self._lock:
            for item in self.data["items"]:
                self.archive([item])
            self.data["items"] = []
            if not keep_summary:
                self.data["summary"] = ""
            self.data["upto_ts"] = float(now or self._now())
            self.save()


# ---------------------------------------------------------------------------
# Keeper: decides when to distil and does it off the chat path
# ---------------------------------------------------------------------------

DISTILL_PROMPT = """你是聊天记录整理器，为角色扮演应用维护「长期记忆」。

只输出一个 JSON 对象，不要解释、不要 Markdown 代码块：
{"summary": "前情摘要", "facts": [{"text": "一句话事实", "category": "owner|event|promise|preference|world", "importance": 1-5}]}

规则：
1. summary 是【已有摘要】+【新对话】的合并摘要，第三人称，只写发生过的事和情绪脉络，不用评价，不超过 300 字。
2. facts 只记「设定级」信息：身份、称呼、偏好、禁忌、约定、承诺、重要经历、关系变化。不要记「这轮打了招呼」这类空话。
3. 每条 fact ≤ 40 字，第三人称，必须能脱离上下文看懂，不要写「他说」这种指代。
4. 每轮最多 4 条；没有值得记的就返回空数组。
5. 新信息与已有记忆冲突时，以新信息为准写一条，并在 text 末尾加「（更新）」。
6. 不要输出隐私凭据、密钥、链接。
"""


class MemoryKeeper:
    """Coordinates distillation; safe to call from the chat worker thread."""

    def __init__(
        self, store, cfg, client_factory=None, log=None, now=None, keep_messages=40
    ):
        self.store = store
        self.cfg = cfg
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._log = log or (lambda message: MemoryStore.log(message))
        self._now = now or time.time
        # How many trailing messages ChatSession still sends verbatim.  Anything
        # inside that window must not be distilled again.
        self.keep_messages = max(2, int(keep_messages))
        self.busy = False
        self.last_error = ""
        self.last_run = 0.0

    # -- helpers ---------------------------------------------------------
    def enabled(self):
        return option(self.cfg, "memory_enabled")

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        from .llm import ChatClient

        return ChatClient(self.cfg)

    def after_turn(self, messages):
        """Queue a maintenance pass; never blocks or raises into the chat."""
        if not self.enabled():
            return None
        thread = threading.Thread(
            target=self._run_safe, args=(messages, False), daemon=True
        )
        thread.start()
        return thread

    def _run_safe(self, messages, force):
        try:
            self.maintain(messages, force=force)
        except Exception as exc:  # pragma: no cover - defensive
            self.last_error = str(exc)
            self._log("整理失败（已忽略）：%s" % exc)

    # -- main ------------------------------------------------------------
    def pending(self, messages):
        """Messages that fell out of the request window and are not consumed."""
        cutoff = len(messages) - self.keep_messages
        if cutoff <= 0:
            return []
        upto = self.store.upto_ts
        rows = []
        for message in messages[:cutoff]:
            if not isinstance(message, dict):
                continue
            try:
                ts = float(message.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ts > upto:
                rows.append(message)
        return rows

    def should_run(self, messages, force=False):
        if force:
            return True
        pending = self.pending(messages)
        need = max(1, int(option(self.cfg, "memory_maintain_messages")))
        return len(pending) >= need

    def maintain(self, messages, force=False):
        """One distillation pass.  Returns a small report dict."""
        report = {"added": 0, "updated": 0, "messages": 0, "summary": 0, "mode": "idle"}
        if not self.enabled():
            report["mode"] = "disabled"
            return report
        if not self._lock.acquire(blocking=force):
            report["mode"] = "busy"
            return report
        self.busy = True
        try:
            changed = self.store.maintain_daily(self._now())
            report["decayed"] = changed
            pending = self.pending(messages)
            need = max(1, int(option(self.cfg, "memory_maintain_messages")))
            if not force and len(pending) < need:
                report["mode"] = "idle"
                return report
            if not pending:
                report["mode"] = "empty"
                return report
            chunk, consumed = self._chunk(pending)
            report["messages"] = len(chunk)
            if not chunk:
                report["mode"] = "empty"
                return report
            if option(self.cfg, "memory_llm"):
                try:
                    result = self._distil(chunk)
                    report["mode"] = "llm"
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = str(exc)
                    self._log("模型提炼失败，改用本地规则：%s" % exc)
                    result = self._fallback(chunk)
                    report["mode"] = "fallback"
            else:
                result = self._fallback(chunk)
                report["mode"] = "local"

            summary = str(result.get("summary") or "").strip()
            if summary and summary != self.store.summary:
                self.store.set_summary(summary[:600])
                report["summary"] = len(summary)
            for fact in result.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                text = str(fact.get("text") or "").strip()
                if not text:
                    continue
                category = str(fact.get("category") or "owner")
                try:
                    level = float(fact.get("importance") or 3)
                except (TypeError, ValueError):
                    level = 3.0
                level = 20.0 + max(0.0, min(5.0, level)) * 16.0
                boost = max(0, rule_importance(text, category) - 60) * 0.4
                level = min(100.0, level + boost)
                before = len(self.store.items)
                item = self.store.add(
                    text,
                    category=category,
                    importance=level,
                    ts=consumed,
                    source_ids=[
                        m.get("id") for m in chunk if m.get("id")
                    ][-4:],
                )
                if item is not None:
                    if len(self.store.items) > before:
                        report["added"] += 1
                    else:
                        report["updated"] += 1
            self.store.set_upto_ts(consumed)
            self.store.save()
            self.last_run = self._now()
            return report
        finally:
            self.busy = False
            self._lock.release()

    @staticmethod
    def _chunk(pending, max_messages=80, max_chars=6000):
        chunk = []
        consumed = 0.0
        size = 0
        for message in pending[:max_messages]:
            text = str(message.get("content", ""))
            if size + len(text) > max_chars and chunk:
                break
            chunk.append(message)
            size += len(text) + 8
            try:
                consumed = float(message.get("ts") or 0) or consumed
            except (TypeError, ValueError):
                pass
        return chunk, consumed

    def _conversation_text(self, chunk):
        who = str(self.cfg.get("user_name") or "旅行者君")
        lines = []
        for message in chunk:
            role = who if message.get("role") == "user" else "助手"
            text = re.sub(r"\s+", " ", str(message.get("content", ""))).strip()
            if text:
                lines.append("%s：%s" % (role, text))
        return "\n".join(lines)

    def _distil(self, chunk):
        existing = sorted(
            self.store.items, key=lambda i: -float(i.get("importance", 0))
        )[:20]
        known = "\n".join("- " + str(i.get("text", "")) for i in existing) or "（无）"
        payload = (
            "【已有摘要】\n"
            + (self.store.summary or "（无）")
            + "\n\n【对方称呼】\n"
            + str(self.cfg.get("user_name") or "旅行者君")
            + "\n\n【已有记忆】\n"
            + known
            + "\n\n【新对话】\n"
            + self._conversation_text(chunk)
        )
        cfg = dict(self.cfg)
        cfg["temperature"] = 0.2
        cfg["max_tokens"] = 700
        client = self._client()
        raw = client.complete(
            [
                {"role": "system", "content": DISTILL_PROMPT},
                {"role": "user", "content": payload},
            ],
            timeout=float(self.cfg.get("timeout", 60)),
        )
        return _parse_json(raw)

    def _fallback(self, chunk):
        """Local, model-free pass: keep strong user lines + a crude summary."""
        who = str(self.cfg.get("user_name") or "旅行者君")
        facts = []
        for message in chunk:
            if message.get("role") != "user":
                continue
            text = re.sub(r"\s+", " ", str(message.get("content", ""))).strip()
            if not text:
                continue
            score = rule_importance(text, "owner")
            if score >= 72 and len(text) >= 6:
                facts.append(
                    {
                        "text": text[:40],
                        "category": "owner",
                        "importance": min(5, max(1, round(score / 20.0))),
                    }
                )
        facts = facts[-4:]
        lines = []
        for message in chunk:
            text = re.sub(r"\s+", " ", str(message.get("content", ""))).strip()
            if not text:
                continue
            cut = max(text.rfind(ch) for ch in _SENTENCE_END) if any(
                ch in text for ch in _SENTENCE_END
            ) else -1
            if cut > 4:
                text = text[: cut + 1]
            prefix = (who if message.get("role") == "user" else "助手") + "："
            lines.append(prefix + text)
        # Keep the old summary as a prefix: a local pass must never lose the
        # older part of the story just because the model is unavailable.
        merged = (self.store.summary + " " + " ".join(lines[-6:])).strip()
        return {"summary": merged[-300:], "facts": facts}


def _parse_json(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.S).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("记忆 JSON 不是对象")
    return data


def inject_into_persona(persona, block):
    """Insert the memory block before the trailing output-format contract.

    ``persona`` ends with the highest-priority output rules; the memory block
    must not land after them or the model may start narrating memories as if
    they were fresh dialogue.
    """
    persona = str(persona or "")
    block = str(block or "").strip()
    if not block:
        return persona
    marker = "【输出格式"
    index = persona.rfind(marker)
    if index > 0:
        return persona[:index] + block + "\n\n" + persona[index:]
    return persona + "\n\n" + block
