# -*- coding: utf-8 -*-
"""Chat domain: conversation state and one chat turn.

The streaming path is the default:

1. read the LLM SSE stream and update the bubble as lines arrive;
2. after the full Chinese reply arrives, synthesize the TTS copy in one
   streaming request (Japanese mode translates it first);
3. hand chunks to ffplay while the app keeps the WAV for replay/export.

The blocking path remains for tests/older clients and for
``stream_voice: false``.
"""
import threading
import time
import uuid
from dataclasses import dataclass, field

from .engine import merge_wavs, save_audio, synth
from .llm import ChatClient, ChatError, translate_to_japanese
from .memory import inject_into_persona
from .settings import MAX_HISTORY_TURNS, SPEED, STREAM_TTS_MODE, STREAM_VOICE
from .storage import save_chat_history
from .text import clean_reply, parse_reply, prepare_tts_text, smart_punct


@dataclass
class ChatResult:
    """Everything one chat turn produced; errors do not raise to the UI."""

    reply: str = ""
    actions: list = field(default_factory=list)
    speech: str = ""
    tts_text: str = ""
    audio_path: str = ""
    audio_paths: list = field(default_factory=list)
    cancelled: bool = False
    error_kind: str = ""
    error: str = ""
    status: int = None
    tts_error: str = ""
    timings: dict = field(default_factory=dict)


def opening_prompt(cfg, now=None):
    """A scene hint that asks the model to start a conversation by itself.

    It is sent as a temporary user message inside a throw-away session, so it
    never appears in the visible history or the memory archive.
    """
    who = str((cfg or {}).get("user_name") or "旅行者君")
    moment = time.localtime(time.time() if now is None else now)
    hour = moment.tm_hour
    if hour < 5:
        period = "深夜"
    elif hour < 11:
        period = "早上"
    elif hour < 14:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    stamp = time.strftime("%Y-%m-%d %H:%M", moment)
    return (
        "（这是系统的场景提示，不是%s说的话。现在是%s（%s）。"
        "请你主动开口，对%s说一句自然、简短的话，开启这段对话。"
        "可以提时间、天气或你们最近聊过的事，但不要编造没发生过的具体经历。"
        "只输出助手的动作和台词，严格保持人设与输出格式；"
        "不要提到这条提示，不要替%s说话。）" % (who, stamp, period, who, who)
    )


class ChatSession:
    """Conversation history with persistence and a hard turn limit.

    The stored list keeps metadata (id/timestamp/audio) for the chat console;
    only role/content is sent to the API.
    """

    MAX_STORED = 200

    def __init__(
        self, cfg, history=None, max_turns=MAX_HISTORY_TURNS, saver=None, memory=None
    ):
        self.cfg = cfg
        self.max_turns = max(1, int(max_turns))
        self.messages = self._load(history or [])
        self._saver = saver or save_chat_history
        self.memory = memory
        self.trim()

    @classmethod
    def _load(cls, history):
        """Normalize history and keep every timestamp strictly increasing.

        Old chat histories had no ``ts`` field, and the previous normalizer
        filled every missing value with one ``time.time()`` call, so a whole
        migrated conversation shared a single timestamp.  That breaks the
        memory watermark and makes exported times meaningless.  Only missing
        or duplicate timestamps get a synthetic value; valid timestamps are
        preserved.
        """
        rows = list(history)
        valid = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            try:
                value = float(raw.get("ts") or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                valid.append(value)
        base = (min(valid) - len(rows)) if valid else (time.time() - len(rows))
        messages = []
        last_ts = 0.0
        for index, raw in enumerate(rows):
            if not raw:
                continue
            message = cls._normalize(raw)
            try:
                ts = float(message.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            if ts <= 0:
                ts = base + index
            if ts <= last_ts:
                ts = last_ts + 0.001
            message["ts"] = ts
            last_ts = ts
            messages.append(message)
        return messages

    @staticmethod
    def _normalize(message):
        if not isinstance(message, dict):
            return {
                "id": uuid.uuid4().hex,
                "role": "user",
                "content": str(message),
                "ts": 0,
            }
        role = message.get("role", "user")
        return {
            "id": message.get("id") or uuid.uuid4().hex,
            "role": role if role in ("user", "assistant") else "user",
            "content": str(message.get("content", "")),
            "ts": message.get("ts") or 0,
            "audio": message.get("audio", ""),
            "voice_lang": message.get("voice_lang", ""),
            "tone": message.get("tone", ""),
        }

    def _new(self, role, text):
        message = {
            "id": uuid.uuid4().hex,
            "role": role,
            "content": str(text),
            "ts": time.time(),
        }
        self.messages.append(message)
        self.trim()
        return message

    def trim(self):
        if len(self.messages) > self.MAX_STORED:
            self.messages = self.messages[-self.MAX_STORED:]

    def add_user(self, text):
        return self._new("user", text)

    def add_assistant(self, text):
        return self._new("assistant", text)

    def rollback_last_user(self):
        if self.messages and self.messages[-1].get("role") == "user":
            self.messages.pop()

    def request(self):
        """System prompt plus the recent conversation for the API.

        The memory card is folded into the system prompt *before* the output
        contract so the model still sees the highest-priority format rules
        last.  Extra metadata (id/audio/timestamp) is deliberately stripped.
        """
        persona = self.cfg.get("persona", "")
        if self.memory is not None:
            try:
                persona = inject_into_persona(
                    persona, self.memory.prompt_block(self.cfg)
                )
            except Exception:
                persona = self.cfg.get("persona", "")
        system = {"role": "system", "content": persona}
        window = self.messages[-self.max_turns * 2:]
        return [system] + [
            {"role": m["role"], "content": m["content"]} for m in window
        ]

    def last_user_text(self):
        for message in reversed(self.messages):
            if message.get("role") == "user":
                return message.get("content", "")
        return ""

    def pop_last_turn(self):
        """Remove the trailing assistant + user messages and return the text."""
        text = ""
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.messages.pop()
        if self.messages and self.messages[-1].get("role") == "user":
            text = self.messages.pop().get("content", "")
        self.save()
        return text

    def set_last_audio(self, path, voice_lang="", tone=""):
        if not path:
            return
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                message["audio"] = path
                message["voice_lang"] = voice_lang
                message["tone"] = tone
                self.save()
                return

    def save(self):
        self.trim()
        self._saver(self.messages)

    def clear(self):
        self.messages = []
        self.save()

    def export_markdown(self, path):
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        lines = ["# RemVoice 聊天导出", ""]
        for message in self.messages:
            role = "你" if message.get("role") == "user" else "助手"
            stamp = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(message.get("ts") or 0)
            )
            lines.append(f"## {role} · {stamp}")
            lines.append("")
            lines.append(message.get("content", ""))
            audio = message.get("audio", "")
            if audio:
                lines.append("")
                lines.append(f"语音：`{audio}`")
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path


class ChatService:
    """Run one complete chat turn: LLM reply, text parsing, TTS synthesis."""

    def __init__(
        self,
        cfg,
        tones,
        default_tone,
        speed=SPEED,
        synthesize=synth,
        save=save_audio,
        client=None,
        stream_voice=STREAM_VOICE,
        translate=translate_to_japanese,
        memory=None,
    ):
        self.cfg = cfg
        self.tones = tones
        self.default_tone = default_tone
        self.speed = speed
        self._synth = synthesize
        self._save = save
        self._client = client
        self.stream_voice = stream_voice
        self._translate = translate
        self.memory = memory

    def _remember(self, session):
        """Hand the finished turn to the memory keeper (background, optional)."""
        if self.memory is None:
            return
        try:
            self.memory.after_turn(list(session.messages))
        except Exception:
            pass

    def _pick_tone(self, tone):
        if tone:
            return tone
        if self.default_tone in self.tones:
            return self.tones[self.default_tone]
        return next(iter(self.tones.values()))

    def _voice_text(self, speech):
        """Return (tts_text, text_lang, translate_seconds) for the voice."""
        lang = str(self.cfg.get("voice_lang", "zh")).lower()
        started = time.perf_counter()
        if lang in ("ja", "jp", "japanese", "日本語"):
            translated = self._translate(speech, self.cfg)
            translated = prepare_tts_text(translated)
            return translated, "ja", round(time.perf_counter() - started, 2)
        text = prepare_tts_text(
            smart_punct(speech) if self.cfg.get("auto_punct", True) else speech
        )
        return text, "auto", 0.0

    def _save_chunk(
        self,
        sentences,
        tone,
        audio_paths,
        on_audio,
        on_chunk=None,
        cancel=None,
        text_lang="auto",
    ):
        text = prepare_tts_text("".join(sentences))
        if not text:
            return ""
        kwargs = {"streaming_mode": STREAM_TTS_MODE, "text_lang": text_lang}
        if on_chunk is not None:
            kwargs["on_chunk"] = on_chunk
        if cancel is not None:
            kwargs["cancel"] = cancel
        data = self._synth(text, tone, self.speed, **kwargs)
        path = self._save(data, prefix="聊天_")
        audio_paths.append(path)
        if on_audio is not None:
            on_audio(path)
        return path

    def run(
        self,
        user_text,
        session,
        tone=None,
        cancel=None,
        on_reply=None,
        on_text=None,
        on_audio=None,
        on_audio_chunk=None,
        remember=True,
    ):
        cancel = cancel or threading.Event()
        if cancel.is_set():
            return ChatResult(cancelled=True)

        session.add_user(user_text)
        client = self._client or ChatClient(self.cfg)
        if self.stream_voice and hasattr(client, "stream"):
            return self._run_streaming(
                client,
                session,
                tone,
                cancel,
                on_reply,
                on_text,
                on_audio,
                on_audio_chunk,
                remember=remember,
            )
        return self._run_blocking(
            client,
            session,
            tone,
            cancel,
            on_reply,
            on_text,
            on_audio,
            remember=remember,
        )

    def opening(
        self,
        session,
        tone=None,
        cancel=None,
        on_text=None,
        on_audio=None,
        on_audio_chunk=None,
    ):
        """One assistant-initiated turn.

        The scene hint is executed in a throw-away session, so the visible
        history only gains Rem's reply.  Memory is not touched: there is no
        user turn to distill.
        """
        cancel = cancel or threading.Event()
        if cancel.is_set():
            return ChatResult(cancelled=True)
        temp = ChatSession(
            self.cfg,
            history=session.messages,
            max_turns=session.max_turns,
            saver=lambda _messages: None,
            memory=session.memory,
        )
        result = self.run(
            opening_prompt(self.cfg),
            temp,
            tone=tone,
            cancel=cancel,
            on_text=on_text,
            on_audio=on_audio,
            on_audio_chunk=on_audio_chunk,
            remember=False,
        )
        if result.error_kind or result.cancelled or not result.reply:
            return result
        session.add_assistant(result.reply)
        session.save()
        return result

    def _run_blocking(
        self,
        client,
        session,
        tone,
        cancel,
        on_reply,
        on_text,
        on_audio,
        remember=True,
    ):
        started = time.perf_counter()
        try:
            reply = client.complete(
                session.request(),
                timeout=float(self.cfg.get("timeout", 60)),
            )
        except ChatError as exc:
            session.rollback_last_user()
            return ChatResult(
                error_kind=exc.kind,
                error=str(exc),
                status=exc.status,
                timings={"llm": round(time.perf_counter() - started, 2)},
            )
        except Exception as exc:
            session.rollback_last_user()
            return ChatResult(
                error_kind="unknown",
                error=str(exc),
                timings={"llm": round(time.perf_counter() - started, 2)},
            )
        llm_seconds = time.perf_counter() - started

        if cancel.is_set():
            session.rollback_last_user()
            return ChatResult(cancelled=True)

        session.add_assistant(reply)
        session.save()
        if remember:
            self._remember(session)

        actions, speech = parse_reply(reply, add_punct=False)
        speech = clean_reply(speech)
        if on_text is not None:
            on_text(actions, speech)
        if on_reply is not None:
            on_reply(actions, speech)

        result = ChatResult(
            reply=reply,
            actions=actions,
            speech=speech,
            timings={"llm": round(llm_seconds, 2)},
        )
        if not speech:
            result.timings["total"] = round(time.perf_counter() - started, 2)
            return result
        if cancel.is_set():
            result.cancelled = True
            result.timings["total"] = round(time.perf_counter() - started, 2)
            return result
        if not self.cfg.get("auto_speak", True):
            result.timings["total"] = round(time.perf_counter() - started, 2)
            return result

        try:
            tts_text, tts_lang, translate_seconds = self._voice_text(speech)
        except Exception as exc:
            result.tts_error = f"日语翻译失败：{exc}"
            result.timings["total"] = round(time.perf_counter() - started, 2)
            return result
        result.tts_text = tts_text
        result.timings["translate"] = translate_seconds

        tts_started = time.perf_counter()
        try:
            path = self._save_chunk(
                [tts_text],
                self._pick_tone(tone),
                result.audio_paths,
                on_audio,
                text_lang=tts_lang,
            )
            result.audio_path = path
        except Exception as exc:
            result.tts_error = str(exc) or exc.__class__.__name__
        result.timings["tts"] = round(time.perf_counter() - tts_started, 2)
        result.timings["total"] = round(time.perf_counter() - started, 2)
        return result

    def _run_streaming(
        self,
        client,
        session,
        tone,
        cancel,
        on_reply,
        on_text,
        on_audio,
        on_audio_chunk=None,
        remember=True,
    ):
        started = time.perf_counter()
        tone_obj = self._pick_tone(tone)
        raw_parts = []
        actions = []
        speech_parts = []
        audio_paths = []
        tts_error = ""
        first_token = None
        first_audio = None
        line_buffer = ""

        def emit_text():
            if on_text is None:
                return
            on_text(list(actions), clean_reply("".join(speech_parts)))

        def handle_line(line):
            line = line.strip()
            if not line:
                return
            if (
                len(line) >= 2
                and line[0] in "（("
                and line[-1] in "）)"
            ):
                actions.append(line[1:-1].strip())
                emit_text()
                return
            speech_parts.append(line)
            emit_text()

        stream = client.stream(
            session.request(), timeout=float(self.cfg.get("timeout", 60))
        )
        try:
            for delta in stream:
                if cancel.is_set():
                    try:
                        stream.close()
                    except Exception:
                        pass
                    session.rollback_last_user()
                    return ChatResult(cancelled=True)
                if first_token is None:
                    first_token = time.perf_counter() - started
                raw_parts.append(delta)
                line_buffer += delta
                while "\n" in line_buffer:
                    line, line_buffer = line_buffer.split("\n", 1)
                    handle_line(line)
            if line_buffer.strip():
                handle_line(line_buffer)
        except ChatError as exc:
            session.rollback_last_user()
            return ChatResult(
                error_kind=exc.kind,
                error=str(exc),
                status=exc.status,
                timings={"first_token": first_token or 0},
            )
        except Exception as exc:
            session.rollback_last_user()
            return ChatResult(
                error_kind="unknown",
                error=str(exc),
                timings={"first_token": first_token or 0},
            )

        if cancel.is_set():
            session.rollback_last_user()
            return ChatResult(cancelled=True)

        reply = "".join(raw_parts).strip()
        if not reply:
            session.rollback_last_user()
            return ChatResult(error_kind="empty", error="模型没有返回内容")

        session.add_assistant(reply)
        session.save()
        if remember:
            self._remember(session)

        final_actions, final_speech = parse_reply(reply, add_punct=False)
        final_speech = clean_reply(final_speech)
        if on_text is not None:
            on_text(final_actions, final_speech)
        if on_reply is not None:
            on_reply(final_actions, final_speech)

        result = ChatResult(
            reply=reply,
            actions=final_actions,
            speech=final_speech,
            cancelled=cancel.is_set(),
            audio_paths=audio_paths,
            timings={
                "first_token": round(first_token, 2) if first_token else None,
                "total": round(time.perf_counter() - started, 2),
            },
        )
        if not final_speech:
            return result
        if cancel.is_set():
            result.cancelled = True
            return result
        if not self.cfg.get("auto_speak", True):
            return result

        try:
            tts_text, tts_lang, translate_seconds = self._voice_text(final_speech)
        except Exception as exc:
            result.tts_error = f"日语翻译失败：{exc}"
            result.timings["total"] = round(time.perf_counter() - started, 2)
            return result
        result.tts_text = tts_text
        result.timings["translate"] = translate_seconds

        tts_started = time.perf_counter()
        try:
            path = self._save_chunk(
                [tts_text],
                tone_obj,
                audio_paths,
                on_audio,
                on_chunk=on_audio_chunk,
                cancel=cancel,
                text_lang=tts_lang,
            )
            result.audio_path = path
            first_audio = time.perf_counter() - started
        except Exception as exc:
            result.tts_error = str(exc) or exc.__class__.__name__
        result.timings["tts"] = round(time.perf_counter() - tts_started, 2)
        result.timings["first_audio"] = (
            round(first_audio, 2) if first_audio else None
        )
        result.timings["total"] = round(time.perf_counter() - started, 2)
        result.cancelled = cancel.is_set()
        return result
