# -*- coding: utf-8 -*-
"""Small secret helpers backed by Windows DPAPI.

The API key is stored in ``chat_config.json``.  Keeping it as plain text makes
config backups and screenshots leak it, so the storage layer encrypts it with
``CryptProtectData``: the blob is bound to the current Windows user and cannot
be decrypted by another account or on another machine.

Failure is deliberately soft.  If DPAPI is unavailable (non-Windows, restricted
token), :func:`protect` returns ``None`` and the storage layer falls back to the
previous plain-text behaviour instead of breaking settings.
"""
import base64
import ctypes
import sys
from ctypes import wintypes

PREFIX = "dpapi:"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def available():
    """True when the current platform can use DPAPI."""
    return sys.platform.startswith("win")


def _blob(data):
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    ), buffer


def _free(pointer):
    try:
        ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(
            ctypes.cast(pointer, ctypes.c_void_p)
        )
    except Exception:
        pass


def protect(text):
    """Encrypt ``text`` for the current Windows user; return a token or None."""
    value = str(text or "")
    if not value or not available():
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        data = value.encode("utf-8")
        in_blob, keep = _blob(data)
        out_blob = _DATA_BLOB()
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "RemVoice API Key",
            None,
            None,
            None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
            ctypes.byref(out_blob),
        )
        del keep
        if not ok or not out_blob.pbData:
            return None
        try:
            encrypted = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _free(out_blob.pbData)
        return PREFIX + base64.b64encode(encrypted).decode("ascii")
    except Exception:
        return None


def unprotect(token):
    """Decrypt a token from :func:`protect`; return text or None."""
    value = str(token or "")
    if not value.startswith(PREFIX) or not available():
        return None
    try:
        encrypted = base64.b64decode(value[len(PREFIX):])
    except Exception:
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.POINTER(_DATA_BLOB),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DATA_BLOB),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p

        in_blob, keep = _blob(encrypted)
        out_blob = _DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(out_blob),
        )
        del keep
        if not ok or not out_blob.pbData:
            return None
        try:
            raw = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            _free(out_blob.pbData)
        return raw.decode("utf-8")
    except Exception:
        return None
