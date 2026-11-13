"""Capacity for the one Google Voice account on a shared 4 OCPU / 24 GB host.

That account places one live call. The planner also reports how many calls
the CPU and RAM could carry after reservations. Those numbers are headroom.
Voxyrn does not admit a second account.

Defaults reserve MailForge and MongoDB (~2 GB) plus a future Ollama fleet
(6 GB and 1 core). Override with VOXYRN_RESERVED_RAM_GB and
VOXYRN_RESERVED_CORES. MAX_AGENTS=auto uses this planner. A positive
integer caps it further.

CPU, not RAM, is the limit. On this x86_64 host (4 vCPU), faster-whisper int8
with 1 thread transcribed 8 seconds of audio in 0.30 s for tiny (26x realtime)
and 0.51 s for base (16x realtime). Ampere Altra N1 cores are treated as 0.6
of that result (VOXYRN_ARM_STT_SCALE). Short utterances still finish well
under a second on one core, so speech-to-text is not what fills the CPUs.
Chromium's WebRTC thread is. About 0.7 core per live call keeps the audio
thread from stalling when several calls transcribe at once. Three cores left
after the Ollama reservation therefore admit 4 agents. RAM alone would admit
many more.
"""
from __future__ import annotations

import json
import os

from src.paths import runtime_base

GOOGLE_VOICE_CONCURRENT_CALLS = 1

# faster-whisper int8, 1 thread, 8 s of audio, measured on this 4-vCPU Xeon.
WHISPER_TINY_RTF = 26.5
WHISPER_BASE_RTF = 15.7
# Neoverse N1 versus that Xeon vCPU for int8 whisper. Conservative.
DEFAULT_ARM_STT_SCALE = 0.6

# Incremental cost of one live agent. Model weights are shared, not copied.
CHROMIUM_MB = 400
AGENT_PROCESS_MB = 100
# Shared once:
WHISPER_TINY_MB = 150
WHISPER_BASE_MB = 280
PIPER_MB = 70
LLM_1_5B_Q4_MB = 1100
PIPEWIRE_MB = 42
OS_AND_NGINX_MB = 700

# WebRTC plus a whisper burst without missing audio.
CORES_PER_LIVE_CALL = 0.7


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def google_voice_concurrent_calls() -> int:
    """Live calls one Google Voice number can carry at the same time."""
    return GOOGLE_VOICE_CONCURRENT_CALLS


def reserved_ram_gb() -> float:
    """MailForge/MongoDB (~2 GB) plus the Ollama reservation (6 GB)."""
    return max(0.0, _float_env("VOXYRN_RESERVED_RAM_GB", 8.0))


def reserved_cores() -> float:
    return max(0.0, _float_env("VOXYRN_RESERVED_CORES", 1.0))


def host_ram_gb() -> float:
    return max(1.0, _float_env("VOXYRN_RAM_GB", 24.0))


def host_cores() -> float:
    return max(1.0, _float_env("VOXYRN_CPU_COUNT", 4.0))


def stt_model() -> str:
    return os.getenv("STT_LOCAL_MODEL", "tiny").strip().lower() or "tiny"


def stt_rtf() -> float:
    """Realtime factor on one Ampere-class core after the measured x86 scale."""
    scale = _float_env("VOXYRN_ARM_STT_SCALE", DEFAULT_ARM_STT_SCALE)
    base = WHISPER_TINY_RTF if stt_model() in {"tiny", "tiny.en"} else WHISPER_BASE_RTF
    return max(1.0, base * scale)


def per_agent_mb() -> int:
    return CHROMIUM_MB + AGENT_PROCESS_MB


def shared_mb() -> int:
    whisper = WHISPER_TINY_MB if stt_model() in {"tiny", "tiny.en"} else WHISPER_BASE_MB
    return whisper + PIPER_MB + LLM_1_5B_Q4_MB + PIPEWIRE_MB + OS_AND_NGINX_MB


def ram_agent_limit() -> int:
    usable_mb = host_ram_gb() * 1024.0 - reserved_ram_gb() * 1024.0 - shared_mb()
    if per_agent_mb() <= 0:
        return 1
    return max(1, int(usable_mb // per_agent_mb()))


def cpu_agent_limit() -> int:
    free = host_cores() - reserved_cores()
    if free <= 0:
        return 1
    return max(1, int(free // CORES_PER_LIVE_CALL))


def max_agents() -> int:
    """Live calls on the one Google Voice account. One call per Chrome profile."""
    return configured_line_count()


def configured_line_count() -> int:
    """How many Chrome profiles the admin armed. Hard ceiling is 7."""
    raw = os.getenv("VOXYRN_LINES", "").strip()
    if not raw:
        path = runtime_base() / "data" / "lines.json"
        if path.is_file():
            try:
                raw = str(json.loads(path.read_text(encoding="utf-8")).get("lines") or "")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                raw = ""
    if not raw:
        raw = "1"
    try:
        count = int(raw)
    except ValueError:
        count = 1
    return max(1, min(7, count))


def sustainable_lines() -> int:
    """How many live calls fit after the reserved CPU and RAM. Ceiling is 7."""
    return max(1, min(7, cpu_agent_limit(), ram_agent_limit()))


def piper_rtf() -> float:
    from src.voice_catalog import VOICE_RTF

    return float(VOICE_RTF["piper"])


def bottleneck() -> str:
    if cpu_agent_limit() <= ram_agent_limit():
        return "cpu"
    return "ram"


def capacity_report() -> str:
    kind = bottleneck()
    return (
        f"Host {host_cores():.0f} cores / {host_ram_gb():.0f} GB, "
        f"reserving {reserved_cores():.0f} core and {reserved_ram_gb():.0f} GB "
        f"(MailForge/MongoDB and Ollama). "
        f"One Google Voice account places 1 live call per Chrome profile. "
        f"Configured lines: {configured_line_count()}. "
        f"Measured sustainable lines on this host: {sustainable_lines()}. "
        f"A second Google Voice account is not admitted. "
        f"One Chromium session is about {per_agent_mb()} MB "
        f"(Chromium {CHROMIUM_MB} MB, worker {AGENT_PROCESS_MB} MB). "
        f"Shared once: whisper {WHISPER_TINY_MB if stt_model() in {'tiny','tiny.en'} else WHISPER_BASE_MB} MB, "
        f"Piper {PIPER_MB} MB, local LLM {LLM_1_5B_Q4_MB} MB, "
        f"PipeWire {PIPEWIRE_MB} MB, OS/nginx {OS_AND_NGINX_MB} MB. "
        f"Host headroom after the reservation: RAM could hold {ram_agent_limit()} calls "
        f"and CPU could hold {cpu_agent_limit()} "
        f"({CORES_PER_LIVE_CALL} core per live call). "
        f"The tighter headroom is {kind}. Extra accounts are not admitted. "
        f"faster-whisper {stt_model()} int8, 1 thread, measured {WHISPER_TINY_RTF if stt_model() in {'tiny','tiny.en'} else WHISPER_BASE_RTF:.1f}x "
        f"realtime on x86; estimated {stt_rtf():.1f}x on one ARM core. "
        f"Piper lessac medium measured {piper_rtf():.1f}x realtime on the same x86 host "
        f"(about {piper_rtf() * 0.6:.0f}x if ARM is 0.6 of that)."
    )


def component_budget() -> dict:
    return {
        "host_cores": host_cores(),
        "host_ram_gb": host_ram_gb(),
        "reserved_cores": reserved_cores(),
        "reserved_ram_gb": reserved_ram_gb(),
        "per_agent_mb": per_agent_mb(),
        "chromium_mb": CHROMIUM_MB,
        "agent_process_mb": AGENT_PROCESS_MB,
        "shared_mb": shared_mb(),
        "whisper_mb": WHISPER_TINY_MB if stt_model() in {"tiny", "tiny.en"} else WHISPER_BASE_MB,
        "piper_mb": PIPER_MB,
        "llm_mb": LLM_1_5B_Q4_MB,
        "pipewire_mb": PIPEWIRE_MB,
        "ram_agents": ram_agent_limit(),
        "cpu_agents": cpu_agent_limit(),
        "max_agents": max_agents(),
        "configured_lines": configured_line_count(),
        "sustainable_lines": sustainable_lines(),
        "line_warning": (
            f"{configured_line_count()} lines exceeds measured capacity of {sustainable_lines()}"
            if configured_line_count() > sustainable_lines()
            else ""
        ),
        "bottleneck": bottleneck(),
        "stt_model": stt_model(),
        "stt_rtf_x86": WHISPER_TINY_RTF if stt_model() in {"tiny", "tiny.en"} else WHISPER_BASE_RTF,
        "stt_rtf_arm_estimate": round(stt_rtf(), 2),
        "piper_rtf_x86": piper_rtf(),
        "calls_per_account": GOOGLE_VOICE_CONCURRENT_CALLS,
        "cores_per_live_call": CORES_PER_LIVE_CALL,
    }
