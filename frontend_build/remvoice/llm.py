# -*- coding: utf-8 -*-
"""OpenAI-compatible chat client.

The rest of the app talks to :class:`ChatClient`, not directly to urllib.
That keeps HTTP details, error classification and future features (streaming,
retries, other providers) in one place.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request


def is_local_endpoint(api_base):
    """True for loopback/mDNS endpoints that commonly run without an API key."""
    try:
        host = (urllib.parse.urlparse(str(api_base or "")).hostname or "").lower()
    except ValueError:
        return False
    return (
        host in ("localhost", "127.0.0.1", "::1")
        or host.startswith("127.")
        or host.endswith(".local")
    )


def _auth_headers(cfg, content_type=True):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = str((cfg or {}).get("api_key", "") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    return headers


class ModelsError(Exception):
    """Model list could not be fetched (bad key, unsupported endpoint...)."""


class ChatError(Exception):
    """A chat completion failed; ``kind`` is stable enough for UI mapping."""

    def __init__(self, message, kind="unknown", status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status


class ChatClient:
    """Minimal client for an OpenAI-compatible ``/chat/completions`` API."""

    def __init__(self, cfg, opener=None):
        self.cfg = cfg
        self._opener = opener or urllib.request.urlopen

    def complete(self, messages, timeout=60):
        cfg = self.cfg
        base = str(cfg.get("api_base", "")).rstrip("/")
        url = base + "/chat/completions"
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": float(cfg.get("temperature", 0.8)),
            "max_tokens": int(cfg.get("max_tokens", 300)),
            "stream": False,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=_auth_headers(cfg),
            method="POST",
        )
        try:
            with self._opener(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ChatError("API Key 无效或无权访问", "auth", exc.code)
            raise ChatError(f"接口返回 HTTP {exc.code}", "http", exc.code)
        except Exception as exc:
            raise ChatError(f"网络请求失败：{exc}", "network")

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except Exception:
            raise ChatError("模型返回格式异常", "format")
        if not content or not str(content).strip():
            raise ChatError("模型没有返回内容", "empty")
        return str(content).strip()

    def stream(self, messages, timeout=60):
        """Yield content deltas from an OpenAI-compatible SSE stream."""
        cfg = self.cfg
        base = str(cfg.get("api_base", "")).rstrip("/")
        url = base + "/chat/completions"
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": float(cfg.get("temperature", 0.8)),
            "max_tokens": int(cfg.get("max_tokens", 300)),
            "stream": True,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=_auth_headers(cfg),
            method="POST",
        )
        try:
            resp = self._opener(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ChatError("API Key 无效或无权访问", "auth", exc.code)
            raise ChatError(f"接口返回 HTTP {exc.code}", "http", exc.code)
        except Exception as exc:
            raise ChatError(f"网络请求失败：{exc}", "network")

        produced = False
        try:
            with resp:
                for raw in resp:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "ignore")
                    line = str(raw).strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except Exception:
                        continue
                    if delta:
                        produced = True
                        yield str(delta)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ChatError("API Key 无效或无权访问", "auth", exc.code)
            raise ChatError(f"接口返回 HTTP {exc.code}", "http", exc.code)
        except Exception as exc:
            raise ChatError(f"流式响应中断：{exc}", "network")
        if not produced:
            raise ChatError("模型没有返回内容", "empty")


def llm_chat(messages, cfg, timeout=60):
    """Backwards-compatible wrapper used by the selftest."""
    return ChatClient(cfg).complete(messages, timeout=timeout)


def translate_to_japanese(text, cfg, timeout=30):
    """Translate Chinese speech lines to natural spoken Japanese.

    Only the TTS copy is translated; the UI keeps the original Chinese.  The
    translation request deliberately carries no Rem persona/history, so it is
    short, cheap and cannot change the displayed conversation.
    """
    source = str(text or "").strip()
    if not source:
        return ""
    trans_cfg = dict(cfg)
    trans_cfg["temperature"] = 0.3
    trans_cfg["max_tokens"] = max(500, int(cfg.get("max_tokens", 300)))
    local_base = str(cfg.get("translate_api_base") or "").strip()
    if local_base:
        # Ollama/LM Studio/vLLM expose an OpenAI-compatible endpoint.
        trans_cfg["api_base"] = local_base
        trans_cfg["api_key"] = str(cfg.get("translate_api_key") or "local")
        trans_cfg["model"] = (
            str(cfg.get("translate_model") or "").strip()
            or cfg.get("model", "")
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是专业的日中口语翻译。把用户提供的中文台词翻译成自然、"
                "口语化的日语，保持女性、礼貌、温柔的语气。只输出日语文本，"
                "不要解释、不要加引号、不要保留中文、不要 Markdown。"
            ),
        },
        {"role": "user", "content": source},
    ]
    result = ChatClient(trans_cfg).complete(messages, timeout=timeout).strip()
    result = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", result).strip()
    result = result.strip("\"'“”‘’「」『』")
    result = re.sub(r"^(日语|日文|Japanese)\s*[:：]\s*", "", result)
    if not result:
        raise ChatError("日语翻译为空", "empty")
    return result


def fetch_models(api_base, api_key, timeout=15):
    base = str(api_base or "").rstrip("/")
    headers = {"Accept": "application/json"}
    if str(api_key or "").strip():
        headers["Authorization"] = "Bearer " + str(api_key).strip()
    req = urllib.request.Request(
        base + "/models",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ModelsError("API Key 无效或无权访问，请检查 Key")
        raise ModelsError("该服务不支持模型列表接口，请手动填写模型名")
    except Exception:
        raise ModelsError("网络失败：请检查 API 地址和网络")
    try:
        data = json.loads(raw)
        ids = sorted({item["id"] for item in data["data"]})
    except Exception:
        raise ModelsError("该服务不支持模型列表接口，请手动填写模型名")
    if not ids:
        raise ModelsError("服务返回了空模型列表，请手动填写模型名")
    return ids
