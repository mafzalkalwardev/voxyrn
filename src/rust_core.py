"""Rust audio gate, with a Python copy used when the native module is not built.

The native module keeps an 8-second PCM ring and decides barge-in, the first
turn, ringback, and the voicemail beep without growing that buffer.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np

_NATIVE = None
_NATIVE_PATH: Path | None = None
_LOAD_ERROR: Exception | None = None
_SEARCHED = False

_RING_SAMPLES = 8 * 16_000
_SAMPLE_RATE = 16_000


def _candidate_libraries() -> list[Path]:
    override = os.getenv("VOXYRN_NATIVE", "").strip()
    paths: list[Path] = []
    if override:
        paths.append(Path(override).expanduser())
    root = Path(__file__).resolve().parent.parent / "rust" / "target"
    names = (
        "libvoxyrn_core.so",
        "voxyrn_core.so",
        "libvoxyrn_core.dylib",
        "voxyrn_core.dll",
    )
    for profile in ("release", "debug"):
        for name in names:
            paths.append(root / profile / name)
    return paths


def _load_native():
    global _NATIVE, _NATIVE_PATH, _LOAD_ERROR, _SEARCHED
    if _SEARCHED:
        return _NATIVE
    _SEARCHED = True
    for path in _candidate_libraries():
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location("voxyrn_core", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            _LOAD_ERROR = exc
            _NATIVE_PATH = path
            return None
        _NATIVE = module
        _NATIVE_PATH = path
        return module
    return None


def native_library_path() -> Path | None:
    _load_native()
    return _NATIVE_PATH


def load_error() -> Exception | None:
    _load_native()
    return _LOAD_ERROR


def native_implementation() -> str:
    _load_native()
    return "rust" if _NATIVE is not None else "python"


def _rms(frame: np.ndarray) -> float:
    audio = np.asarray(frame, dtype=np.float64).reshape(-1)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio * audio)))


def _should_barge(agent_speaking: bool, level: float, threshold: float) -> bool:
    return bool(agent_speaking) and level >= max(threshold * 2.0, threshold + 0.01)


def chunk_reply(text: str) -> list[str]:
    native = _load_native()
    if native is not None:
        return list(native.chunk_reply_text(text))
    return _chunk_reply_python(text)


def _chunk_reply_python(text: str) -> list[str]:
    out: list[str] = []
    start = 0
    chars = list(enumerate(text))
    for index, (pos, ch) in enumerate(chars):
        boundary = False
        if ch in ".!?":
            prev_digit = index > 0 and chars[index - 1][1].isdigit()
            next_digit = index + 1 < len(chars) and chars[index + 1][1].isdigit()
            next_space = index + 1 >= len(chars) or chars[index + 1][1].isspace()
            boundary = not (ch == "." and prev_digit and next_digit) and next_space
        elif ch in ",;" and len(text[start : pos + 1].split()) >= 6:
            boundary = True
        if boundary:
            piece = text[start : pos + 1].strip()
            if piece:
                out.append(piece)
            start = pos + 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


class _PythonEngine:
    """Same barge rule and fixed memory budget as the Rust engine."""

    def __init__(self) -> None:
        self._burst = 0
        self._silence = 0
        self._locked = ""

    def memory_bytes(self) -> int:
        return _RING_SAMPLES * 4

    def push(self, frame: np.ndarray, agent_speaking: bool, threshold: float) -> dict:
        audio = np.asarray(frame, dtype=np.float32).reshape(-1)
        level = _rms(audio)
        speech = audio.size > 0 and level >= threshold
        count = int(audio.size)
        if not self._locked:
            if speech:
                self._burst += count
                self._silence = 0
            elif self._burst:
                self._silence += count
            speech_secs = self._burst / _SAMPLE_RATE
            silence_secs = self._silence / _SAMPLE_RATE
            if speech_secs >= 2.2:
                self._locked = "greeting"
            elif 0.0 < speech_secs <= 1.2 and silence_secs >= 0.4:
                self._locked = "human"
        return {
            "rms": level,
            "barge_in": _should_barge(agent_speaking, level, threshold),
            "in_speech": speech,
            "first_turn": self._locked or "wait",
            "beep": False,
            "ringback": "idle",
            "implementation": "python",
            "backend": "cpu",
        }


class CallEngine:
    def __init__(self) -> None:
        native = _load_native()
        self._native = native.AudioEngine() if native is not None else None
        self._python = _PythonEngine()

    def memory_bytes(self) -> int:
        if self._native is not None:
            return int(self._native.memory_bytes())
        return self._python.memory_bytes()

    def push(self, frame, agent_speaking: bool = False, threshold: float = 0.02) -> dict:
        if self._native is not None:
            pcm = np.ascontiguousarray(frame, dtype=np.float32).reshape(-1).tobytes()
            decision = self._native.push_pcm(pcm, bool(agent_speaking), float(threshold))
            return dict(decision)
        return self._python.push(frame, agent_speaking, float(threshold))
