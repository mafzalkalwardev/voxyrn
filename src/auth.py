"""Operator accounts, sessions, CSRF, and login rate limiting.

Passwords are stored as PBKDF2-SHA256 hashes. Sessions are signed cookies.
A login attempt budget is kept in memory for this process (systemd runs one
worker). The admin account is created from VOXYRN_ADMIN_USER and
VOXYRN_ADMIN_PASSWORD when the user store is empty.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from src.paths import runtime_base

_LOCK = threading.Lock()
_ATTEMPTS: dict[str, list[float]] = {}
_WINDOW_SECONDS = 15 * 60
_MAX_FAILURES = 8
_PBKDF2_ROUNDS = 210_000
_DUMMY_HASH = ""


def users_path() -> Path:
    override = os.getenv("VOXYRN_USERS_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return runtime_base() / "data" / "users.json"


def _server_mode() -> bool:
    return os.getenv("VOXYRN_SERVER", "").strip().lower() in {"1", "true", "yes", "on"}


def cookie_secure() -> bool:
    raw = os.getenv("VOXYRN_COOKIE_SECURE", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return _server_mode()


def hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ROUNDS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_b64, digest_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(actual, expected)


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("voxyrn-dummy-password")
    return _DUMMY_HASH


def _read_users() -> list[dict]:
    path = users_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("users", [])
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict) and row.get("username")]


def _write_users(users: list[dict]) -> None:
    path = users_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def list_public_users() -> list[dict]:
    with _LOCK:
        return [_public(row) for row in _read_users()]


def _role(value: str) -> str:
    role = (value or "agent").strip().lower()
    if role == "admin":
        return "admin"
    return "agent"


def _status(value: str, *, role: str) -> str:
    status = (value or "").strip().lower()
    if status in {"pending", "active", "suspended"}:
        return status
    return "active" if role == "admin" else "pending"


def _public(row: dict) -> dict:
    role = _role(str(row.get("role") or "agent"))
    if str(row.get("role") or "") == "operator":
        role = "agent"
    status = str(row.get("status") or "").strip().lower()
    if status not in {"pending", "active", "suspended"}:
        status = "active"
    return {
        "username": str(row.get("username") or ""),
        "role": role,
        "status": status,
        "script_id": str(row.get("script_id") or ""),
        "lead_list": str(row.get("lead_list") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def public_user(username: str) -> Optional[dict]:
    username = (username or "").strip()
    if not username:
        return None
    with _LOCK:
        for row in _read_users():
            if str(row.get("username")) == username:
                return _public(row)
    return None


def users_exist() -> bool:
    with _LOCK:
        return bool(_read_users())


def ensure_bootstrap_admin() -> Optional[dict]:
    """Create the admin from the environment when that username does not exist."""
    username = os.getenv("VOXYRN_ADMIN_USER", "admin").strip() or "admin"
    password = os.getenv("VOXYRN_ADMIN_PASSWORD", "")
    if not password:
        return None
    with _LOCK:
        users = _read_users()
        if any(str(row.get("username")) == username for row in users):
            return _public(next(row for row in users if str(row.get("username")) == username))
        row = {
            "username": username,
            "password_hash": hash_password(password),
            "role": "admin",
            "status": "active",
            "script_id": "",
            "lead_list": "",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        users.append(row)
        _write_users(users)
        return _public(row)


def authenticate(username: str, password: str) -> Optional[dict]:
    username = (username or "").strip()
    with _LOCK:
        users = _read_users()
    stored = ""
    match = None
    for row in users:
        if str(row.get("username")) == username:
            match = row
            stored = str(row.get("password_hash") or "")
            break
    if not verify_password(password, stored or _dummy_hash()):
        return None
    if match is None:
        return None
    return _public(match)


def can_dial(user: Optional[dict]) -> bool:
    if not user:
        return False
    return str(user.get("status") or "active") == "active"


def create_user(
    username: str,
    password: str,
    role: str = "agent",
    status: str = "",
    script_id: str = "",
    lead_list: str = "",
) -> dict:
    username = (username or "").strip()
    role = _role(role)
    if not username or len(username) < 3 or len(username) > 32:
        raise ValueError("Username must be 3–32 characters")
    if any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in username):
        raise ValueError("Username may contain letters, numbers, dots, underscores, and hyphens")
    if len(password or "") < 10:
        raise ValueError("Password must be at least 10 characters")
    with _LOCK:
        users = _read_users()
        if any(str(row.get("username")).lower() == username.lower() for row in users):
            raise ValueError("That username already exists")
        row = {
            "username": username,
            "password_hash": hash_password(password),
            "role": role,
            "status": _status(status, role=role),
            "script_id": str(script_id or ""),
            "lead_list": str(lead_list or ""),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        users.append(row)
        _write_users(users)
        return _public(row)


def update_user(username: str, **fields) -> dict:
    """Admin updates status, script, or lead list. Passwords stay hashed."""
    username = (username or "").strip()
    with _LOCK:
        users = _read_users()
        match = next((row for row in users if str(row.get("username")) == username), None)
        if match is None:
            raise ValueError("User not found")
        if "status" in fields:
            status = str(fields["status"] or "").strip().lower()
            if status not in {"pending", "active", "suspended"}:
                raise ValueError("Status must be pending, active, or suspended")
            match["status"] = status
        if "script_id" in fields:
            match["script_id"] = str(fields["script_id"] or "")
        if "lead_list" in fields:
            match["lead_list"] = str(fields["lead_list"] or "")
        if "role" in fields:
            match["role"] = _role(str(fields["role"]))
        _write_users(users)
        return _public(match)


def ashley_password_path() -> Path:
    override = os.getenv("VOXYRN_ASHLEY_PASSWORD_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return runtime_base() / "data" / "ashley.password"


def ensure_ashley() -> None:
    """Create Ashley once. The password is written to a root-only file and never logged."""
    with _LOCK:
        users = _read_users()
        if any(str(row.get("username")).lower() == "ashley" for row in users):
            return
        password = secrets.token_urlsafe(18)
        row = {
            "username": "Ashley",
            "password_hash": hash_password(password),
            "role": "agent",
            "status": "active",
            "script_id": "truck-dispatch-sales",
            "lead_list": "truck-dispatch",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        users.append(row)
        _write_users(users)
        path = ashley_password_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            users.pop()
            _write_users(users)
            return
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            os.write(fd, (password + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o400)
        except OSError:
            pass
        del password


def client_ip(request) -> str:
    if os.getenv("VOXYRN_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    with _LOCK:
        hits = [stamp for stamp in _ATTEMPTS.get(ip, []) if now - stamp < _WINDOW_SECONDS]
        _ATTEMPTS[ip] = hits
        return len(hits) >= _MAX_FAILURES


def record_failure(ip: str) -> None:
    now = time.time()
    with _LOCK:
        hits = [stamp for stamp in _ATTEMPTS.get(ip, []) if now - stamp < _WINDOW_SECONDS]
        hits.append(now)
        _ATTEMPTS[ip] = hits


def clear_failures(ip: str) -> None:
    with _LOCK:
        _ATTEMPTS.pop(ip, None)


def reset_rate_limits() -> None:
    with _LOCK:
        _ATTEMPTS.clear()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str, provided: str) -> bool:
    if not expected or not provided:
        return False
    try:
        return hmac.compare_digest(expected, provided)
    except (TypeError, ValueError):
        return False


def setup_allowed(request) -> bool:
    """Local first-run password form. Never available behind the public proxy."""
    if users_exist():
        return False
    if os.getenv("VOXYRN_ADMIN_PASSWORD", "").strip():
        return False
    if _server_mode():
        return False
    if request.headers.get("x-forwarded-for") or request.headers.get("forwarded"):
        return False
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost"}


def safe_next(value: str) -> str:
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/dashboard"
    if value.startswith("/login") or value.startswith("/setup"):
        return "/dashboard"
    return value
