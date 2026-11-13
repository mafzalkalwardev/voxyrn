"""Local Google Voice account registry.

Windows stores the password with DPAPI. Linux stores it with an HMAC keystream
keyed by VOXYRN_SECRET_KEY (or a local secret file). The password is not
written in plaintext on either platform.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

from src.paths import runtime_base


ACCOUNTS_FILE = runtime_base() / "data" / "gv_accounts.json"
SESSION_MARKER = ".gv_session_ok"


def _secret_key() -> bytes:
    secret = os.getenv("VOXYRN_SECRET_KEY", "").strip().encode("utf-8")
    if len(secret) >= 16:
        return hashlib.sha256(b"voxyrn-gv-v1" + secret).digest()
    path = runtime_base() / "data" / ".gv_secret"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return hashlib.sha256(b"voxyrn-gv-v1" + path.read_bytes()).digest()


def _keystream(nonce: bytes, length: int) -> bytes:
    key = _secret_key()
    blocks = []
    counter = 0
    while sum(len(block) for block in blocks) < length:
        blocks.append(hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def _protect_portable(value: str) -> str:
    nonce = secrets.token_bytes(16)
    raw = value.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(nonce, len(raw))))
    mac = hmac.new(_secret_key(), nonce + cipher, hashlib.sha256).digest()
    blob = base64.b64encode(nonce + mac + cipher).decode("ascii")
    return "portable:" + blob


def _unprotect_portable(value: str) -> str:
    blob = base64.b64decode(value.split(":", 1)[1])
    nonce, mac, cipher = blob[:16], blob[16:48], blob[48:]
    expected = hmac.new(_secret_key(), nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("Google Voice password could not be decrypted")
    raw = bytes(a ^ b for a, b in zip(cipher, _keystream(nonce, len(cipher))))
    return raw.decode("utf-8")


def _protect_windows(value: str) -> str:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data)
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

    source, keepalive = _blob(value.encode("utf-8"))
    output = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "Google Voice Agent", None, None, None, 0,
        ctypes.byref(output),
    ):
        raise ctypes.WinError()
    try:
        raw = ctypes.string_at(output.pbData, output.cbData)
        return base64.b64encode(raw).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _unprotect_windows(value: str) -> str:
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data)
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf

    source, keepalive = _blob(base64.b64decode(value))
    output = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _protect(value: str) -> str:
    if not value:
        return ""
    if sys.platform == "win32" and hasattr(ctypes, "windll"):
        return _protect_windows(value)
    return _protect_portable(value)


def _unprotect(value: str) -> str:
    if not value:
        return ""
    if value.startswith("portable:"):
        return _unprotect_portable(value)
    if sys.platform == "win32" and hasattr(ctypes, "windll"):
        return _unprotect_windows(value)
    raise RuntimeError("This password was encrypted on Windows and cannot be read here")


def make_profile_name(name: str, email: str) -> str:
    stem = name or email.split("@", 1)[0] or "google_voice"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return (stem or "google_voice")[:60]


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    from src.linux_audio import map_windows_audio_name

    email = str(row.get("email") or "").strip()
    return {
        "id": str(row.get("id") or uuid.uuid4().hex),
        "name": str(row.get("name") or email or "Google Voice").strip(),
        "email": email,
        "profile_name": str(row.get("profile_name") or make_profile_name("", email)).strip(),
        "loopback_device": map_windows_audio_name(
            str(row.get("loopback_device") or ""), kind="loopback"
        ),
        "capture_device": map_windows_audio_name(
            str(row.get("capture_device") or ""), kind="capture"
        ),
        "enabled": bool(row.get("enabled", True)),
        "password_enc": str(row.get("password_enc") or ""),
    }


def load_accounts(*, include_password: bool = False) -> list[dict[str, Any]]:
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = [_normalize(row) for row in raw if isinstance(row, dict)]
    for row in rows:
        row["has_password"] = bool(row["password_enc"])
        if include_password:
            try:
                row["password"] = _unprotect(row["password_enc"])
            except Exception:
                row["password"] = ""
        row.pop("password_enc", None)
    return rows


def save_account(payload: dict[str, Any]) -> dict[str, Any]:
    existing: list[dict[str, Any]] = []
    if ACCOUNTS_FILE.exists():
        try:
            existing = [
                _normalize(row)
                for row in json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
                if isinstance(row, dict)
            ]
        except (OSError, json.JSONDecodeError):
            existing = []
    account_id = str(payload.get("id") or uuid.uuid4().hex)
    current = next((row for row in existing if row["id"] == account_id), None)
    merged = dict(current or {})
    merged.update({k: v for k, v in payload.items() if k != "password"})
    merged["id"] = account_id
    if payload.get("password"):
        merged["password_enc"] = _protect(str(payload["password"]))
    account = _normalize(merged)
    if current is None and existing:
        raise ValueError(
            "Voxyrn uses one Google Voice account, Main line. Edit that account instead of adding another."
        )
    if not str(payload.get("name") or "").strip():
        account["name"] = "Main line"
    if not account["email"]:
        raise ValueError("Google account email is required")
    if not account["profile_name"]:
        raise ValueError("Chrome profile name is required")
    duplicate = next(
        (row for row in existing if row["id"] != account_id and row["profile_name"].casefold() == account["profile_name"].casefold()),
        None,
    )
    if duplicate:
        raise ValueError("Each account must use a distinct Chrome profile")
    updated = [account if row["id"] == account_id else row for row in existing]
    if current is None:
        updated.append(account)
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    return next(row for row in load_accounts() if row["id"] == account_id)


def delete_account(account_id: str) -> bool:
    if not ACCOUNTS_FILE.exists():
        return False
    raw = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    kept = [row for row in raw if str(row.get("id")) != account_id]
    if len(kept) == len(raw):
        return False
    ACCOUNTS_FILE.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return True


def session_marker_path(profile_name: str) -> Path:
    return runtime_base() / "chrome_profiles" / profile_name / SESSION_MARKER


def mark_session_ready(profile_name: str) -> None:
    marker = session_marker_path(profile_name)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("Google Voice session verified\n", encoding="utf-8")


def public_accounts() -> list[dict[str, Any]]:
    rows = load_accounts()
    for row in rows:
        row["session_ready"] = session_marker_path(row["profile_name"]).exists()
        row["audio_ready"] = bool(row["loopback_device"] and row["capture_device"])
    return rows

