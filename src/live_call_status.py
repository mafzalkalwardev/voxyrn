"""Shared live-call status for CRM / Live Run dashboards.

The dialer process writes JSON; the web app reads it for realtime UI.
End-of-call results stay visible until ``result_until`` expires.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.paths import runtime_base

STATUS_PATH = runtime_base() / "logs" / "live_call_status.json"
RESULT_HOLD_SECONDS = 8.0

_lock = threading.Lock()

_IDLE = {
    "phase": "idle",
    "phone": "",
    "name": "",
    "outcome": "",
    "state": "",
    "index": 0,
    "message": "",
    "updated_at": "",
    "result_until": 0.0,
    "active": False,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATUS_PATH)


def publish_live_call(
    *,
    phase: str,
    phone: str = "",
    name: str = "",
    outcome: str = "",
    state: str = "",
    index: int = 0,
    message: str = "",
    hold_result: bool = False,
    hold_seconds: float = RESULT_HOLD_SECONDS,
) -> dict[str, Any]:
    """Publish the current live-call phase for the CRM / run UI."""
    result_until = 0.0
    if hold_result:
        # Keep the end result on screen briefly before vanishing.
        phase = "result"
        result_until = time.time() + max(0.05, float(hold_seconds))

    payload = {
        "phase": phase,
        "phone": phone or "",
        "name": name or "",
        "outcome": (outcome or state or phase or "").strip(),
        "state": state or "",
        "index": int(index or 0),
        "message": message or "",
        "updated_at": _now_iso(),
        "result_until": result_until,
        "active": phase not in {"idle", "result"},
    }
    with _lock:
        _write(payload)
    return payload


def clear_live_call(message: str = "") -> dict[str, Any]:
    payload = dict(_IDLE)
    payload["updated_at"] = _now_iso()
    payload["message"] = message
    with _lock:
        _write(payload)
    return payload


def get_live_call() -> dict[str, Any]:
    """Return current status; auto-clear expired result banners."""
    with _lock:
        if not STATUS_PATH.is_file():
            return dict(_IDLE)
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return dict(_IDLE)

    if not isinstance(data, dict):
        return dict(_IDLE)

    result_until = float(data.get("result_until") or 0.0)
    phase = str(data.get("phase") or "idle")
    if phase == "result" and result_until and time.time() >= result_until:
        return clear_live_call()
    data.setdefault("active", phase not in {"idle", "result"})
    return data
