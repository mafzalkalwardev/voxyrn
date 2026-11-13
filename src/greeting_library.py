"""Match a mailbox greeting against clips saved from earlier calls.

The fingerprint is a normalized band spectrum. No extra model is required.
Only clips already labeled as a mailbox are stored.
"""

from __future__ import annotations

import hashlib
import logging
import wave
from pathlib import Path

import numpy as np

from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000
# Short human hellos are ~0.5–0.9s. Require a longer opening before matching
# so a "hello" cannot fingerprint-match a saved mailbox clip.
_MIN_SECONDS = 1.6
_MAX_SECONDS = 3.5
_MATCH_COSINE = 0.97
_MAX_CLIPS = 200


def spectral_fingerprint(audio: np.ndarray, samplerate: int = _SAMPLERATE) -> np.ndarray | None:
    """Return a unit vector for the opening of `audio`, or None if it is too quiet."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size < int(samplerate * _MIN_SECONDS):
        return None
    samples = samples[: int(samplerate * _MAX_SECONDS)]
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if rms < 0.008:
        return None

    frame = 512
    hop = 256
    bands = 32
    window = np.hanning(frame).astype(np.float32)
    specs: list[np.ndarray] = []
    for start in range(0, samples.size - frame, hop):
        chunk = samples[start : start + frame] * window
        specs.append(np.abs(np.fft.rfft(chunk)))
    if len(specs) < 8:
        return None

    mean_mag = np.mean(np.stack(specs, axis=0), axis=0)
    freqs = np.fft.rfftfreq(frame, 1.0 / float(samplerate))
    edges = np.geomspace(200.0, 3400.0, bands + 1)
    values: list[float] = []
    for index in range(bands):
        mask = (freqs >= edges[index]) & (freqs < edges[index + 1])
        values.append(float(mean_mag[mask].mean()) if np.any(mask) else 0.0)
    vector = np.log1p(np.asarray(values, dtype=np.float64))
    norm = float(np.linalg.norm(vector))
    if norm < 1e-6:
        return None
    return (vector / norm).astype(np.float32)


class GreetingLibrary:
    """Saved mailbox openings. A strong match is a second vote, not the only one."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory is not None else (
            runtime_base() / "data" / "voicemail_greetings"
        )
        self._prints: list[np.ndarray] = []
        self._load()

    def matches(self, audio: np.ndarray, samplerate: int = _SAMPLERATE) -> bool:
        current = spectral_fingerprint(audio, samplerate)
        if current is None:
            return False
        return any(float(np.dot(current, saved)) >= _MATCH_COSINE for saved in self._prints)

    def save(self, audio: np.ndarray, samplerate: int = _SAMPLERATE) -> bool:
        """Store a confirmed mailbox opening. Screening audio and hellos never come here."""
        current = spectral_fingerprint(audio, samplerate)
        if current is None:
            return False
        if any(float(np.dot(current, saved)) >= _MATCH_COSINE for saved in self._prints):
            return False
        if len(self._prints) >= _MAX_CLIPS:
            logger.info("[VM] greeting library is full (%d clips); not saving another", _MAX_CLIPS)
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(np.round(current, 4).tobytes()).hexdigest()[:16]
        path = self.directory / f"{digest}.wav"
        _write_wav(path, np.asarray(audio, dtype=np.float32).reshape(-1)[: int(samplerate * _MAX_SECONDS)], samplerate)
        self._prints.append(current)
        logger.info("[VM] saved mailbox greeting %s", path.name)
        return True

    def _load(self) -> None:
        if not self.directory.is_dir():
            return
        paths = sorted(self.directory.glob("*.wav"))[:_MAX_CLIPS]
        for path in paths:
            try:
                audio, rate = _read_wav(path)
            except Exception as exc:
                logger.debug("[VM] skip greeting %s: %s", path.name, exc)
                continue
            current = spectral_fingerprint(audio, rate)
            if current is not None:
                self._prints.append(current)
        if self._prints:
            logger.info("[VM] loaded %d saved mailbox greetings", len(self._prints))


def _write_wav(path: Path, audio: np.ndarray, samplerate: int) -> None:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(samplerate)
        handle.writeframes(pcm.tobytes())


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate() or _SAMPLERATE
        frames = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
    if width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
    else:
        audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    return audio, rate
