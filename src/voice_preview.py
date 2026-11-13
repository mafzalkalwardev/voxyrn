"""Short local preview. Piper is used when its onnx file is installed."""
from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

from src.paths import runtime_base


def piper_model(voice_id: str) -> Path | None:
    root = runtime_base() / "models" / "piper"
    candidate = root / f"{voice_id}.onnx"
    if candidate.is_file():
        return candidate
    return None


def synthesize_preview(
    text: str,
    engine: str,
    voice: str,
    speed: float = 1.0,
    pitch: float = 0.0,
) -> tuple[bytes, str]:
    spoken = " ".join((text or "").split())[:400] or "Hello."
    speed = min(1.4, max(0.6, float(speed or 1.0)))
    pitch = min(1.0, max(-1.0, float(pitch or 0.0)))
    if engine == "piper":
        data = _piper_wav(voice, spoken, speed)
        if data:
            return data, f"Piper {voice}. Pitch is not applied; Piper has no pitch control."
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        return _silence(), "No espeak or Piper model is installed, so this is silence."
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        dest = Path(handle.name)
    words_per_minute = str(int(150 * speed))
    espeak_pitch = str(int(50 + pitch * 30))
    subprocess.run(
        [espeak, "-w", str(dest), "-s", words_per_minute, "-p", espeak_pitch, spoken],
        check=False,
        capture_output=True,
        timeout=20,
    )
    data = dest.read_bytes() if dest.is_file() else b""
    dest.unlink(missing_ok=True)
    note = "espeak preview."
    if engine == "piper":
        note = f"Piper file for {voice} is not downloaded yet. espeak spoke the line instead."
    return data or _silence(), note


def _piper_wav(voice_id: str, text: str, speed: float) -> bytes | None:
    model = piper_model(voice_id)
    if model is None:
        return None
    try:
        from piper import PiperVoice
        from piper.config import SynthesisConfig
    except ImportError:
        return None
    loaded = PiperVoice.load(str(model))
    chunks = list(loaded.synthesize(text, SynthesisConfig(length_scale=1.0 / speed)))
    if not chunks:
        return None
    pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(chunks[0].sample_rate)
        handle.writeframes(pcm)
    return buf.getvalue()


def _silence() -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()
