"""The single Google Voice account, Main line.

Voxyrn places one live call on voxyrn_to_gv / voxyrn_from_gv.monitor.
A second saved account is not admitted.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from src.capacity import configured_line_count, google_voice_concurrent_calls, max_agents


def slot_audio(index: int) -> tuple[str, str, str]:
    """1-based slot. Returns TTS sink, speaker sink, and capture monitor."""
    if index < 1:
        raise ValueError("agent slots are 1-based")
    speaker = f"voxyrn_from_gv_{index}"
    return f"voxyrn_to_gv_{index}", speaker, f"{speaker}.monitor"


@dataclass
class AgentSlot:
    index: int
    account_id: str
    email: str
    profile_name: str
    loopback_device: str
    capture_device: str
    pulse_sink: str
    pulse_source: str
    state: str = "idle"
    restarts: int = 0
    last_error: str = ""
    active_calls: int = 0


@dataclass
class FleetPlan:
    slots: list[AgentSlot]
    queued_accounts: list[dict]
    max_agents: int

    def as_parallel_profiles(self) -> list[dict]:
        return [
            {
                "profile_name": slot.profile_name,
                "loopback_device": slot.loopback_device,
                "capture_device": slot.capture_device,
                "account_id": slot.account_id,
                "account_email": slot.email,
                "pulse_sink": slot.pulse_sink,
                "pulse_source": slot.pulse_source,
            }
            for slot in self.slots
        ]


class Fleet:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.slots: list[AgentSlot] = []
        self.queued: list[dict] = []
        self.max_agents = 1

    def plan(self, accounts: list[dict]) -> FleetPlan:
        enabled = [row for row in accounts if row.get("enabled", True)]
        account = enabled[0] if enabled else None
        count = configured_line_count() if account else 0
        slots: list[AgentSlot] = []
        if account is not None:
            from src.gv_lines import line_plan

            for row in line_plan(count):
                slots.append(
                    AgentSlot(
                        index=row["index"],
                        account_id=str(account.get("id") or ""),
                        email=str(account.get("email") or ""),
                        profile_name=row["profile_name"],
                        loopback_device=row["loopback_device"],
                        capture_device=row["capture_device"],
                        pulse_sink=row["pulse_sink"],
                        pulse_source=row["pulse_source"],
                        state="idle" if row["signed_in"] else "needs_signin",
                    )
                )
        with self._lock:
            self.slots = slots
            self.queued = []
            self.max_agents = max_agents()
        return FleetPlan(slots=slots, queued_accounts=[], max_agents=max_agents())

    def mark(self, index: int, state: str, error: str = "") -> None:
        with self._lock:
            for slot in self.slots:
                if slot.index == index:
                    slot.state = state
                    if error:
                        slot.last_error = error
                    if state == "active":
                        slot.active_calls = google_voice_concurrent_calls()
                    elif state in {"idle", "crashed", "queued"}:
                        slot.active_calls = 0
                    return

    def note_restart(self, index: int) -> int:
        with self._lock:
            for slot in self.slots:
                if slot.index == index:
                    slot.restarts += 1
                    slot.state = "restarting"
                    return slot.restarts
        return 0

    def snapshot(self) -> dict:
        with self._lock:
            active = sum(1 for slot in self.slots if slot.state == "active")
            return {
                "max_agents": self.max_agents,
                "active_agents": active,
                "queued_accounts": len(self.queued),
                "slots": [
                    {
                        "index": slot.index,
                        "email": slot.email,
                        "profile_name": slot.profile_name,
                        "state": slot.state,
                        "restarts": slot.restarts,
                        "active_calls": slot.active_calls,
                        "loopback_device": slot.loopback_device,
                        "capture_device": slot.capture_device,
                        "last_error": slot.last_error,
                        "calls_allowed": google_voice_concurrent_calls(),
                    }
                    for slot in self.slots
                ],
            }


FLEET = Fleet()


def prepare_slot_audio(slot: AgentSlot) -> None:
    """Create this agent's null sinks. Safe to call when Pulse is down."""
    from src.linux_audio import ensure_null_sink, pactl_available

    if not pactl_available():
        return
    ensure_null_sink(slot.loopback_device, f"Voxyrn_TTS_agent_{slot.index}")
    ensure_null_sink(slot.pulse_sink, f"Voxyrn_speaker_agent_{slot.index}")
