"""
Speech-to-text using Groq Whisper API.

Uses whisper-large-v3 by default so short phone answers are transcribed more reliably.
Audio is accepted as a float32 numpy array and converted internally to 16-bit PCM WAV.

Usage:
    stt = GroqWhisperSTT(api_key="gsk_...")
    text = stt.transcribe(audio_array, samplerate=16000)
"""

from __future__ import annotations

import io
import logging
import os
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

_MIN_DURATION_S = 0.18    # keep short live-call answers like "yes" and "no"
_MAX_DURATION_S = 60.0    # Groq Whisper max; truncate longer segments
_QUIET_SPEECH_FLOOR = 0.03
_QUIET_SPEECH_TARGET = 0.22
# Whisper often invents these when the clip is mostly noise.
_HALLUCINATIONS = frozenset({
    "thank you",
    "thanks for watching",
    "you",
    "bye",
    "subtitles by the amara.org community",
})

# Carrier-specific STT prompt prefix that improves transcription accuracy
# for freight dispatch vocabulary (truck types, MC numbers, lanes, etc.)
_STT_CONTEXT_PREFIX = (
    "Freight dispatch call. Trucking terms: dry van, flatbed, reefer, step deck, "
    "hotshot, box truck, sprinter van, power only, car hauler, own authority, MC number, "
    "DOT number, deadhead, TONU, detention, drop and hook, loadboard, factoring, quick pay, "
    "rate per mile, RPM, preferred lanes, dispatcher percentage. "
)


class GroqWhisperSTT:
    def __init__(
        self,
        api_key: str,
        model: str = "whisper-large-v3",
        language: str = "en",
        retry_count: int = 2,
        use_stt_context: bool = True,
    ):
        if not api_key:
            raise ValueError("api_key is required for GroqWhisperSTT")
        self._client = Groq(api_key=api_key)
        self.model = model
        self.language = language
        self.retry_count = max(0, retry_count)
        self.use_stt_context = use_stt_context
        # Tracks the reason for the last empty transcription (for diagnostics)
        self.last_empty_reason: str = ""

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def transcribe(
        self,
        audio: np.ndarray,
        samplerate: int = 16000,
        prompt: Optional[str] = None,
    ) -> str:
        """
        Transcribe float32 audio to text.

        audio      — 1-D float32 numpy array, values in [-1, 1]
        samplerate — audio sample rate (16000 recommended for Whisper)
        prompt     — optional text hint to improve accuracy (e.g. carrier name, truck type)

        Automatically prepends freight-dispatch context to prompt for better
        domain-specific transcription accuracy.

        Returns the transcript string, or "" if too short / empty result.
        Retries up to self.retry_count times on empty or failure.
        """
        self.last_empty_reason = ""
        duration = len(audio) / max(samplerate, 1)
        logger.info(
            "STT started: model=%s language=%s duration=%.2fs samplerate=%d",
            self.model,
            self.language,
            duration,
            samplerate,
        )

        if duration < _MIN_DURATION_S:
            self.last_empty_reason = f"audio too short ({duration:.2f}s)"
            logger.info("STT empty: %s", self.last_empty_reason)
            return ""

        if duration > _MAX_DURATION_S:
            logger.warning("STT: truncating %.1fs audio to %.0fs", duration, _MAX_DURATION_S)
            audio = audio[: int(_MAX_DURATION_S * samplerate)]

        raw_peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        audio = _condition_stt_audio(audio)
        # Caller prompt is forwarded unchanged. Conversation loop already adds
        # carrier context; an extra prefix here would break that contract.
        enriched_prompt = (prompt or "").strip()

        wav_bytes = _float32_to_wav(audio, samplerate)

        last_exc: Optional[Exception] = None
        attempts = self.retry_count + 1
        for attempt in range(1, attempts + 1):
            try:
                kwargs: dict = dict(
                    file=("audio.wav", wav_bytes, "audio/wav"),
                    model=self.model,
                    language=self.language,
                    response_format="text",
                    temperature=0,
                )
                if enriched_prompt:
                    kwargs["prompt"] = enriched_prompt

                result = self._client.audio.transcriptions.create(**kwargs)
                text = getattr(result, "text", result)
                transcript = str(text).strip()
                if _is_low_energy_hallucination(transcript, raw_peak):
                    self.last_empty_reason = f"ignored low-energy hallucination ({transcript!r})"
                    logger.info("STT empty: %s", self.last_empty_reason)
                    return ""
                if transcript:
                    self.last_empty_reason = ""
                    logger.info("STT result (attempt %d/%d): %s", attempt, attempts, transcript)
                    return transcript
                else:
                    self.last_empty_reason = f"transcription returned no text (attempt {attempt})"
                    logger.info("STT empty: %s", self.last_empty_reason)
                    if attempt < attempts:
                        time.sleep(0.15 * attempt)  # brief back-off before retry
            except Exception as exc:
                last_exc = exc
                self.last_empty_reason = f"exception on attempt {attempt}: {exc}"
                logger.error("STT failed (attempt %d/%d): %s", attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(0.2 * attempt)

        if last_exc:
            logger.error("STT gave up after %d attempts: %s", attempts, last_exc)
        return ""


class LocalWhisperSTT:
    """faster-whisper on this machine. The model files stay under models/stt."""

    def __init__(
        self,
        model_size: str = "base",
        model_dir: str | None = None,
        language: str = "en",
        retry_count: int = 1,
        use_stt_context: bool = True,
    ):
        self.model = model_size or "base"
        self.language = language
        self.retry_count = max(0, retry_count)
        self.use_stt_context = use_stt_context
        self.last_empty_reason: str = ""
        raw = model_dir or os.getenv("STT_MODEL_DIR", "models/stt")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        self.model_dir = path
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self.model_dir.mkdir(parents=True, exist_ok=True)
            compute_type = os.getenv("STT_COMPUTE_TYPE", "int8").strip() or "int8"
            try:
                cpu_threads = int(os.getenv("STT_THREADS", "1"))
            except ValueError:
                cpu_threads = 1
            self._model = WhisperModel(
                self.model,
                device="cpu",
                compute_type=compute_type,
                cpu_threads=max(1, cpu_threads),
                download_root=str(self.model_dir),
                local_files_only=True,
            )
        return self._model

    def transcribe(
        self,
        audio: np.ndarray,
        samplerate: int = 16000,
        prompt: Optional[str] = None,
    ) -> str:
        self.last_empty_reason = ""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        duration = len(audio) / max(samplerate, 1)
        if duration < _MIN_DURATION_S:
            self.last_empty_reason = f"audio too short ({duration:.2f}s)"
            return ""
        if duration > _MAX_DURATION_S:
            audio = audio[: int(_MAX_DURATION_S * samplerate)]
        raw_peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        audio = _condition_stt_audio(audio)
        if samplerate != 16000 and audio.size:
            target = int(len(audio) * 16000 / samplerate)
            positions = np.linspace(0, len(audio) - 1, max(target, 1))
            audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
        hint = (prompt or "").strip() if self.use_stt_context else ""
        try:
            # Phone / WASAPI loopback clips often get wiped by Whisper's internal VAD.
            # Prefer no VAD first, then one VAD retry if the decode looks empty/garbage.
            transcript = self._decode(audio, hint, vad_filter=False)
            if not transcript or _is_garbage_transcript(transcript):
                retry = self._decode(audio, hint, vad_filter=True)
                if retry and not _is_garbage_transcript(retry):
                    transcript = retry
                elif _is_garbage_transcript(transcript):
                    transcript = ""
        except Exception as exc:
            self.last_empty_reason = f"local whisper failed: {exc}"
            logger.error("Local STT failed: %s", exc)
            return ""
        if _is_low_energy_hallucination(transcript, raw_peak):
            self.last_empty_reason = f"ignored low-energy hallucination ({transcript!r})"
            return ""
        if _is_garbage_transcript(transcript):
            self.last_empty_reason = f"ignored garbage transcript ({transcript!r})"
            return ""
        if not transcript:
            self.last_empty_reason = "local whisper returned no text"
        return transcript

    def _decode(self, audio: np.ndarray, hint: str, *, vad_filter: bool) -> str:
        segments, _info = self._load().transcribe(
            audio,
            language=self.language,
            vad_filter=vad_filter,
            beam_size=1,
            temperature=0.0,
            initial_prompt=hint or None,
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _condition_stt_audio(audio: np.ndarray) -> np.ndarray:
    """Lift quiet speech into the range Whisper hears, and leave silence alone."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return audio
    audio = audio - float(np.mean(audio))
    peak = float(np.max(np.abs(audio)))
    if _QUIET_SPEECH_FLOOR <= peak < _QUIET_SPEECH_TARGET:
        audio = audio * (_QUIET_SPEECH_TARGET / peak)
    return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)


def _is_low_energy_hallucination(transcript: str, peak: float) -> bool:
    if peak >= _QUIET_SPEECH_FLOOR:
        return False
    normalized = " ".join(transcript.casefold().split()).strip(" .!?,")
    return normalized in _HALLUCINATIONS


def _is_garbage_transcript(transcript: str) -> bool:
    """Reject Whisper collapses like 'BLEEEEE…' that phone loopback sometimes triggers."""
    text = " ".join((transcript or "").split()).strip()
    if not text:
        return False
    compact = "".join(ch for ch in text.casefold() if ch.isalnum())
    if len(compact) >= 12 and len(set(compact)) <= 3:
        return True
    if len(text) >= 20:
        # One character dominates the whole string.
        counts = {}
        for ch in compact:
            counts[ch] = counts.get(ch, 0) + 1
        if counts and max(counts.values()) / max(len(compact), 1) >= 0.7:
            return True
    return False


def _float32_to_wav(audio: np.ndarray, samplerate: int) -> bytes:
    """Convert float32 [-1, 1] numpy array to 16-bit mono PCM WAV bytes."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()
