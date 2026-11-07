# -*- coding: utf-8 -*-
"""JSON persistence and application logging.

Two rules matter here:

* every JSON write is atomic (``.tmp`` + ``os.replace``) so a crash cannot
  leave a half-written config/history that silently resets to defaults;
* the previous file is kept as ``.bak`` so a corrupted write can be recovered.

All paths come from :mod:`remvoice.settings`; tests can call the small helpers
with their own temporary paths.
"""
import json
import os
import shutil
import threading
import time

from .persona import DEFAULT_CHAT_CONFIG
from .secrets import available as secrets_available
from .secrets import protect, unprotect
from .settings import (
    CHAT_ARCHIVE,
    CHAT_CONFIG,
    CHAT_HISTORY,
    CHAT_RECORD_DIR,
    LEGACY_CHAT_CONFIG,
)

LOG_ROTATE_BYTES = 2 * 1024 * 1024
TAIL_BYTES = 64 * 1024

API_KEY_FIELD = "api_key"
API_KEY_ENC_FIELD = "api_key_enc"


def _decrypt_api_key(cfg):
    """Replace an encrypted key with its plaintext form for in-memory use."""
    key = str(cfg.get(API_KEY_FIELD) or "")
    token = str(cfg.get(API_KEY_ENC_FIELD) or "")
    if not key and token:
        decoded = unprotect(token)
        if decoded:
            cfg[API_KEY_FIELD] = decoded
    return cfg


def _encrypt_api_key(cfg):
    """Return a copy safe to write to disk (plaintext key removed when possible)."""
    stored = dict(cfg)
    key = str(stored.get(API_KEY_FIELD) or "").strip()
    if key:
        token = protect(key)
        if token:
            stored[API_KEY_FIELD] = ""
            stored[API_KEY_ENC_FIELD] = token
    else:
        stored[API_KEY_FIELD] = ""
        stored.pop(API_KEY_ENC_FIELD, None)
    return stored


def _scrub_config_backup(sanitized_text):
    """Do not leave a plaintext key in the previous config copy."""
    backup = CHAT_CONFIG + ".bak"
    try:
        if not os.path.exists(backup):
            return
        with open(backup, encoding="utf-8-sig") as f:
            old = json.load(f)
        if not isinstance(old, dict):
            return
        if not str(old.get(API_KEY_FIELD) or "").strip():
            return
        with open(backup, "w", encoding="utf-8", newline="\n") as f:
            f.write(sanitized_text)
    except Exception:
        pass


def atomic_write_text(path, text):
    """Write ``text`` to ``path`` atomically and keep the previous file."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # Unique temp names avoid a stale/locked ``config.json.tmp`` left by an
    # editor or another process (seen in the a-gen work folder).
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        if os.path.exists(path):
            try:
                shutil.copyfile(path, path + ".bak")
            except OSError:
                pass
        for attempt in range(3):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.1)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _read_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def append_log(path, text):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_ROTATE_BYTES:
            try:
                os.replace(path, path + ".1")
            except OSError:
                pass
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {str(text).rstrip()}\n")
    except Exception:
        pass


def rotate_log(path, max_bytes=10 * 1024 * 1024, backups=2):
    """Rotate ``path`` to ``.1``/``.2`` once it grows past ``max_bytes``.

    Best effort: a log that another process still holds open must never break
    startup.  The service writes its stdout/stderr directly, so it is rotated
    before the next launch instead of from ``append_log``.
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return False
    except OSError:
        return False
    try:
        for index in range(max(1, int(backups)) - 1, 0, -1):
            source = "%s.%d" % (path, index)
            target = "%s.%d" % (path, index + 1)
            if os.path.exists(target):
                os.remove(target)
            if os.path.exists(source):
                os.replace(source, target)
        os.replace(path, path + ".1")
        return True
    except OSError:
        return False


def tail_file(path, max_lines=14, max_chars=1800):
    """Read only the tail of a log, even when it has grown to many MB."""
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-TAIL_BYTES, os.SEEK_END)
            except OSError:
                f.seek(0)
            data = f.read()
        text = data.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    text = "\n".join(lines[-max_lines:])
    if len(text) > max_chars:
        text = "…" + text[-max_chars:]
    return text


def load_chat_config():
    """Merge the saved config over defaults; fall back to ``.bak``/legacy path."""
    cfg = dict(DEFAULT_CHAT_CONFIG)
    new_path = os.path.abspath(CHAT_CONFIG)
    legacy_path = os.path.abspath(LEGACY_CHAT_CONFIG)
    paths = [CHAT_CONFIG, CHAT_CONFIG + ".bak"]
    if legacy_path != new_path:
        paths += [LEGACY_CHAT_CONFIG, LEGACY_CHAT_CONFIG + ".bak"]
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            saved = _read_json(path)
        except Exception:
            continue
        if not isinstance(saved, dict):
            continue
        # Keep unknown keys too: future chat settings can be read straight
        # from cfg without touching this merge every time.
        cfg.update(saved)
        _decrypt_api_key(cfg)
        # A pre-2.4.2 config has a plaintext key.  Rewrite it encrypted once;
        # if a stale encrypted token coexists with a plaintext key, the check
        # still catches it because ``saved`` (not the decrypted ``cfg``) is
        # inspected.
        needs_key_migration = bool(
            str(saved.get(API_KEY_FIELD) or "").strip()
        ) and secrets_available()
        if os.path.abspath(path) != new_path or needs_key_migration:
            # One-time migration: the old a-gen/work path may be protected by
            # Windows Controlled Folder Access for an unsigned exe.
            try:
                save_chat_config(cfg)
            except OSError:
                pass
        break
    return cfg


def save_chat_config(cfg):
    text = json.dumps(
        _encrypt_api_key(cfg), ensure_ascii=False, indent=2
    )
    atomic_write_text(CHAT_CONFIG, text)
    _scrub_config_backup(text)


def load_chat_history():
    for path in (CHAT_HISTORY, CHAT_HISTORY + ".bak"):
        if not os.path.exists(path):
            continue
        try:
            data = _read_json(path)
        except Exception:
            continue
        if isinstance(data, dict):
            data = data.get("messages", [])
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)][-200:]
    return []


def save_chat_history(messages):
    try:
        payload = {"version": 2, "messages": list(messages)[-200:]}
        atomic_write_text(
            CHAT_HISTORY, json.dumps(payload, ensure_ascii=False)
        )
    except Exception:
        pass


def append_chat_transcript(role, text):
    try:
        os.makedirs(CHAT_RECORD_DIR, exist_ok=True)
        path = os.path.join(
            CHAT_RECORD_DIR, "聊天记录_" + time.strftime("%Y%m%d") + ".txt"
        )
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {role}：{text}\n")
    except Exception:
        pass


def append_chat_archive(role, text, ts=None, audio="", voice_lang="", tone=""):
    """Append one raw turn to ``chat_archive.jsonl``.

    ``chat_history.json`` is deliberately capped at 200 messages for the
    request window.  This archive is the unbounded source of truth used when
    an old line must be recovered later; it is never injected into a prompt.
    """
    try:
        directory = os.path.dirname(CHAT_ARCHIVE)
        if directory:
            os.makedirs(directory, exist_ok=True)
        row = {
            "ts": float(ts if ts is not None else time.time()),
            "role": "assistant" if str(role).lower() in ("assistant", "助手", "rem") else "user",
            "content": str(text or ""),
        }
        if audio:
            row["audio"] = str(audio)
        if voice_lang:
            row["voice_lang"] = str(voice_lang)
        if tone:
            row["tone"] = str(tone)
        with open(CHAT_ARCHIVE, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_chat_archive(query="", limit=500, scan_bytes=8 * 1024 * 1024):
    """Read the newest archive rows, optionally filtered by a keyword.

    The archive is append-only, so the tail is the newest part.  ``scan_bytes``
    bounds the read for large files; a future SQLite index can replace it.
    """
    rows = []
    if not os.path.exists(CHAT_ARCHIVE):
        return rows
    try:
        with open(CHAT_ARCHIVE, "rb") as f:
            try:
                f.seek(-scan_bytes, os.SEEK_END)
            except OSError:
                f.seek(0)
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return rows
    needle = str(query or "").strip().lower()
    for line in reversed(data.splitlines()):
        line = line.strip()
        if not line:
            continue
        line = line.lstrip("\ufeff")
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if needle and needle not in str(row.get("content", "")).lower():
            continue
        rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    return rows
