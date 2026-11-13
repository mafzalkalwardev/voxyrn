"""Per-account call pacing so a Google Voice number is not hammered.

Defaults: at least 45 seconds between calls on the same account, and at most
30 calls per rolling hour. Both are environment overrides.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class AccountPacer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._recent: dict[str, deque[float]] = {}

    def min_gap_s(self) -> float:
        return max(0.0, float(_int_env("ACCOUNT_MIN_GAP_SECONDS", 45)))

    def max_per_hour(self) -> int:
        return max(1, _int_env("ACCOUNT_MAX_CALLS_PER_HOUR", 30))

    def allow(self, account_id: str, now: float | None = None) -> tuple[bool, str]:
        """Return whether this account may dial now, and a short reason if not."""
        now = time.time() if now is None else now
        key = account_id or "default"
        with self._lock:
            last = self._last.get(key, 0.0)
            gap = self.min_gap_s()
            if last and now - last < gap:
                return False, f"wait {gap - (now - last):.0f}s before the next call on this account"
            window = self._recent.setdefault(key, deque())
            while window and now - window[0] > 3600:
                window.popleft()
            if len(window) >= self.max_per_hour():
                return False, f"account hit {self.max_per_hour()} calls in the last hour"
            return True, ""

    def mark(self, account_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        key = account_id or "default"
        with self._lock:
            self._last[key] = now
            self._recent.setdefault(key, deque()).append(now)


PACER = AccountPacer()
