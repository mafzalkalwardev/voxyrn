"""Headless TTS -> Pulse sink -> STT -> script -> TTS latency probe.

Run while PipeWire is up:
  XDG_RUNTIME_DIR=... python -m src.voice_loop_bench
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

from src.script_engine import builtin_templates, ScriptSession


def _espeak(text: str, dest: Path, speed: float = 1.0) -> None:
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        raise RuntimeError("espeak-ng is required for the local loop")
    subprocess.run(
        [espeak, "-w", str(dest), "-s", str(int(150 * speed)), text],
        check=True,
        capture_output=True,
        timeout=20,
    )


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate() or 1)


def _play_and_capture(wav: Path, sink: str) -> tuple[float, float, bytes]:
    """Return startup latency, play wall time, and the monitor recording."""
    from src.linux_audio import play_wav_to_pulse_sink

    proc = subprocess.Popen(
        ["parec", f"--device={sink}.monitor", "--format=s16le", "--rate=16000", "--channels=1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.05)
    started = time.perf_counter()
    play_wav_to_pulse_sink(wav, sink)
    wall = time.perf_counter() - started
    time.sleep(0.1)
    proc.terminate()
    audio = proc.stdout.read() if proc.stdout else b""
    overhead = max(0.0, wall - _wav_seconds(wav))
    return overhead, wall, audio


def _stt(audio: bytes) -> tuple[str, float]:
    import numpy as np
    from faster_whisper import WhisperModel

    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < 1600:
        return "", 0.0
    model = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=1, download_root="/tmp/fw-models")
    started = time.perf_counter()
    segments, _info = model.transcribe(samples, language="en", beam_size=1, vad_filter=False)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, time.perf_counter() - started


def run(sink: str = "voxyrn_to_gv") -> dict:
    script = next(item for item in builtin_templates() if item.script_id == "b2b-appointment")
    session = ScriptSession(
        script,
        {"first_name": "Sam", "company": "INDUS Web Agency", "product": script.product, "agent_name": "Alex"},
    )
    opening = session.opening()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        opener_wav = root / "opener.wav"
        reply_wav = root / "reply.wav"
        prospect = root / "prospect.wav"
        t0 = time.perf_counter()
        _espeak(opening.line, opener_wav)
        tts_open = time.perf_counter() - t0
        open_overhead, open_wall, _agent_audio = _play_and_capture(opener_wav, sink)
        t_prospect = time.perf_counter()
        _espeak("Who is this?", prospect)
        prospect_tts = time.perf_counter() - t_prospect
        _overhead, _wall, captured = _play_and_capture(prospect, "voxyrn_from_gv")
        heard, stt_s = _stt(captured)
        think = time.perf_counter()
        turn = session.hear(heard or "Who is this?")
        think_s = time.perf_counter() - think
        t1 = time.perf_counter()
        _espeak(turn.line, reply_wav, speed=script.speed)
        tts_reply = time.perf_counter() - t1
        reply_overhead, reply_wall, _reply_audio = _play_and_capture(reply_wav, sink)
    return {
        "tts_open_s": round(tts_open, 3),
        "sink_overhead_s": round(open_overhead, 3),
        "opener_wall_s": round(open_wall, 3),
        "prospect_tts_s": round(prospect_tts, 3),
        "stt_s": round(stt_s, 3),
        "script_s": round(think_s, 3),
        "tts_reply_s": round(tts_reply, 3),
        "reply_overhead_s": round(reply_overhead, 3),
        "reply_wall_s": round(reply_wall, 3),
        "heard": heard,
        "reply": turn.line,
        "state": turn.state,
    }


if __name__ == "__main__":
    print(run())
