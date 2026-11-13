"""
Low-latency TTS routed directly to a sounddevice output device (CABLE Input).

Engine priority:
  1. edge-tts  — Microsoft Neural TTS (internet required, ~400 ms latency, high quality)
  2. pyttsx3   — Windows SAPI TTS (offline, ~800 ms latency, adequate quality)

The best voice for Tony is "en-US-GuyNeural" (edge-tts).
Fallback SAPI voice: first English male found, else Zira.

TTS cache: common short phrases (greetings, fillers, fallbacks) are pre-generated
at startup so they play with near-zero latency on first use.

Usage:
    tts = RealtimeTTS(device_index=1, voice="en-US-GuyNeural")
    tts.speak("Hi, this is Tony from Indus Transports.")    # blocks
    t = tts.speak_async("Are you still dispatching solo?")  # non-blocking thread
    tts.stop()    # interrupt immediately
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger("GoogleVoiceAgent")

VOICE_GUY       = "en-US-GuyNeural"          # primary — natural male
VOICE_CHRIS     = "en-US-ChristopherNeural"   # backup male
_PIPER_FILLERS = ("Got it.", "Sure.", "Absolutely.", "Of course.", "One moment.")
_PIPER_VOICES: dict[str, object] = {}
_PIPER_VOICE_LOCK = threading.Lock()
_PIPER_SYNTH_LOCK = threading.Lock()


# ------------------------------------------------------------------ #
# Main public class
# ------------------------------------------------------------------ #

class RealtimeTTS:
    """Thread-safe TTS that routes synthesised speech to a sounddevice output."""

    def __init__(
        self,
        device_index: int,
        voice: str = VOICE_GUY,
        use_edge_tts: bool = True,
        engine: str | None = None,
        piper_model_path: str | Path | None = None,
        piper_length_scale: float = 1.07,
        piper_noise_scale: float = 0.5,
        piper_noise_w_scale: float = 0.92,
        use_cache: bool = True,
        allow_sapi_fallback: bool = False,
        audio_observer: Optional[Callable[[np.ndarray, int], None]] = None,
        pulse_sink: str | None = None,
    ):
        self.device_index = device_index
        self.pulse_sink = pulse_sink or ""
        self.voice = voice
        self._piper_phrase_cache: _PiperPhraseCache | None = None
        requested_engine = (engine or os.getenv("TTS_ENGINE", "edge")).strip().lower()
        if requested_engine in {"piper", "piper-tts"}:
            self._engine = "piper"
            self._piper_voice = _load_piper_voice(piper_model_path)
            self._piper_length_scale = _bounded_float(piper_length_scale, 0.75, 1.35, "piper_length_scale")
            self._piper_noise_scale = _bounded_float(piper_noise_scale, 0.0, 1.5, "piper_noise_scale")
            self._piper_noise_w_scale = _bounded_float(piper_noise_w_scale, 0.0, 1.5, "piper_noise_w_scale")
            if use_cache:
                self._piper_phrase_cache = _PiperPhraseCache(
                    self._piper_voice,
                    self._piper_length_scale,
                    self._piper_noise_scale,
                    self._piper_noise_w_scale,
                )
        elif requested_engine == "kokoro":
            self._engine = "kokoro"
            self._piper_voice = None
            self._piper_length_scale = 1.07
            self._piper_noise_scale = 0.5
            self._piper_noise_w_scale = 0.92
            self._kokoro_voice_name = os.getenv("KOKORO_VOICE", "af_heart")
            try:
                # Slightly under 1.0 = warmer, more human pacing for sales calls
                self._kokoro_speed = float(os.getenv("KOKORO_SPEED", "0.88"))
            except ValueError:
                self._kokoro_speed = 0.88
            self._kokoro_speed = _bounded_float(self._kokoro_speed, 0.7, 1.2, "kokoro_speed")
            try:
                self._kokoro_sentence_pause = float(os.getenv("KOKORO_SENTENCE_PAUSE", "0.30"))
            except ValueError:
                self._kokoro_sentence_pause = 0.30
            try:
                self._kokoro_clause_pause = float(os.getenv("KOKORO_CLAUSE_PAUSE", "0.16"))
            except ValueError:
                self._kokoro_clause_pause = 0.16
            self._kokoro = _load_kokoro(
                os.getenv("KOKORO_MODEL_PATH", "models/kokoro/kokoro-v1.0.onnx"),
                os.getenv("KOKORO_VOICES_PATH", "models/kokoro/voices-v1.0.bin"),
            )
        elif requested_engine in {"edge", "edge-tts"}:
            self._engine = "edge-tts" if use_edge_tts and _edge_available() else "sapi"
            self._piper_voice = None
            self._piper_length_scale = 1.07
            self._piper_noise_scale = 0.5
            self._piper_noise_w_scale = 0.92
        else:
            raise ValueError(
                f"Unsupported TTS_ENGINE '{requested_engine}'. "
                "Use 'edge', 'piper', or 'kokoro'."
            )
        self._use_edge = self._engine == "edge-tts"
        self._allow_sapi_fallback = allow_sapi_fallback
        self._lock = threading.Lock()
        self._speaking = threading.Event()
        self._audio_observer = audio_observer

        # TTS cache — pre-warms common phrases so first-word latency is minimal
        self._cache: Optional["TTSCache"] = None  # type: ignore[name-defined]
        if use_cache and self._use_edge:
            from src.tts_cache import TTSCache
            self._cache = TTSCache(tts_voice=voice)
            self._cache.warm()

        from src.voice_playback import describe_audio_device

        logger.info(
            "RealtimeTTS: engine=%s voice=%s cache=%s sapi_fallback=%s device=%s",
            self._engine,
            voice,
            "enabled" if self._cache else "disabled",
            self._allow_sapi_fallback,
            describe_audio_device(device_index),
        )

    def prewarm_line(self, text: str) -> None:
        """Pre-synthesize an exact phrase (e.g. opening) so it matches live reply voice."""
        if self._piper_phrase_cache is not None and text.strip():
            self._piper_phrase_cache.ensure(text)
            return
        if self._cache is not None and text.strip():
            self._cache.ensure(text)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def speak(self, text: str, interrupt: bool = True) -> None:
        """Synthesise and play synchronously. Blocks until playback completes."""
        if not text.strip():
            logger.info("RealtimeTTS: empty text skipped")
            return
        if interrupt:
            self.stop()
        with self._lock:
            self._speaking.set()
            try:
                self._play(text)
            finally:
                self._speaking.clear()

    def speak_async(self, text: str, interrupt: bool = True) -> threading.Thread:
        """Synthesise and play in a daemon thread. Returns the thread."""
        if interrupt and self.is_speaking():
            self.stop()
        t = threading.Thread(target=self.speak, args=(text, False), daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        """Stop any in-progress playback immediately."""
        try:
            from src.linux_audio import stop_pulse_playback

            stop_pulse_playback()
        except Exception:
            pass
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self._speaking.clear()

    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def play_filler(self) -> None:
        """Play a short acknowledgement without interrupting current speech."""
        if self._piper_phrase_cache is not None:
            import random

            phrase = random.choice(_PIPER_FILLERS)
            threading.Thread(
                target=self.speak,
                args=(phrase, False),
                daemon=True,
                name="TTSFiller",
            ).start()
            return
        if self._cache is None:
            return
        phrase = self._cache.random_filler()
        if self._cache.get(phrase):
            threading.Thread(
                target=self.speak,
                args=(phrase, False),
                daemon=True,
                name="TTSFiller",
            ).start()

    def speak_text_chunked(self, text: str, interrupt: bool = True) -> None:
        """Speak each sentence sequentially for lower perceived latency."""
        from src.conversation_agent import split_sentences

        parts = split_sentences(text)
        if not parts:
            return
        if interrupt:
            self.stop()
        for i, part in enumerate(parts):
            self.speak(part, interrupt=(interrupt and i == 0))

    def _emit(self, data, rate: int) -> None:
        _play_numpy_to_device(data, rate, self.device_index, pulse_sink=self.pulse_sink or None)

    def set_audio_observer(self, callback: Optional[Callable[[np.ndarray, int], None]]) -> None:
        """Receive synthesized Tony audio when playback starts."""
        self._audio_observer = callback

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _play(self, text: str) -> None:
        if self._engine == "kokoro":
            prepared = _prepare_piper_text(text)
            if not prepared:
                logger.info("RealtimeTTS: empty text skipped")
                return
            logger.info("RealtimeTTS: generating speech with Kokoro (%d chars)", len(prepared))
            data, rate = _render_kokoro_phrase(
                prepared,
                self._kokoro,
                self._kokoro_voice_name,
                self._kokoro_speed,
                sentence_pause=getattr(self, "_kokoro_sentence_pause", 0.30),
                clause_pause=getattr(self, "_kokoro_clause_pause", 0.16),
            )
            self._notify_audio_observer(data, rate)
            self._emit(data, rate)
            return
        if self._engine == "piper":
            prepared = _prepare_piper_text(text)
            if not prepared:
                logger.info("RealtimeTTS: empty text skipped")
                return
            cached = self._piper_phrase_cache.get(prepared) if self._piper_phrase_cache else None
            if cached is not None:
                data, rate = cached
                logger.info("RealtimeTTS: serving Piper phrase from cache (%d chars)", len(prepared))
            else:
                logger.info("RealtimeTTS: generating speech with Piper (%d chars)", len(prepared))
                data, rate = _render_piper_phrase(
                    prepared,
                    self._piper_voice,
                    length_scale=self._piper_length_scale,
                    noise_scale=self._piper_noise_scale,
                    noise_w_scale=self._piper_noise_w_scale,
                )
                if self._piper_phrase_cache is not None:
                    self._piper_phrase_cache.store(prepared, data, rate)
            duration = len(data) / float(rate) if rate else 0.0
            logger.info(
                "RealtimeTTS: audio generated (engine=piper, rate=%d, duration=%.2fs)",
                rate,
                duration,
            )
            self._notify_audio_observer(data, rate)
            self._emit(data, rate)
            return

        # Try cache first — zero network latency for pre-warmed phrases
        if self._cache is not None:
            cached_bytes = self._cache.get(text)
            if cached_bytes:
                logger.info("RealtimeTTS: serving '%s' from cache (%d bytes)", text[:40], len(cached_bytes))
                try:
                    data, rate = _decode_mp3(cached_bytes)
                    self._notify_audio_observer(data, rate)
                    self._emit(data, rate)
                    return
                except Exception as exc:
                    logger.debug("RealtimeTTS: cache playback failed (%s); falling through to synthesis", exc)

        if self._use_edge:
            last_exc: Exception | None = None
            for attempt in range(1, 3):
                try:
                    logger.info(
                        "RealtimeTTS: generating speech with edge-tts (%d chars, attempt %d)",
                        len(text),
                        attempt,
                    )
                    data, rate = _edge_synthesize(text, self.voice)
                    duration = len(data) / float(rate) if rate else 0.0
                    logger.info(
                        "RealtimeTTS: audio generated (engine=edge-tts, voice=%s, rate=%d, duration=%.2fs)",
                        self.voice,
                        rate,
                        duration,
                    )
                    self._notify_audio_observer(data, rate)
                    self._emit(data, rate)
                    return
                except Exception as exc:
                    last_exc = exc
                    logger.warning("edge-tts attempt %d failed: %s", attempt, exc)
            if self._allow_sapi_fallback:
                logger.warning(
                    "RealtimeTTS: edge-tts failed after retries (%s); using pyttsx3 fallback",
                    last_exc,
                )
                _pyttsx3_to_device(text, self.device_index, self._audio_observer)
                return
            logger.error(
                "RealtimeTTS: edge-tts failed and SAPI fallback is disabled — skipping playback: %s",
                last_exc,
            )
            return
        if self._allow_sapi_fallback:
            logger.info("RealtimeTTS: generating fallback TTS WAV with pyttsx3 (%d chars)", len(text))
            _pyttsx3_to_device(text, self.device_index, self._audio_observer)
        else:
            logger.error("RealtimeTTS: edge-tts unavailable and SAPI fallback is disabled")

    def _notify_audio_observer(self, data: np.ndarray, samplerate: int) -> None:
        callback = self._audio_observer
        if not callable(callback):
            return
        audio = np.asarray(data, dtype=np.float32).copy()

        def _run() -> None:
            try:
                callback(audio, int(samplerate))
            except Exception as exc:
                logger.debug("RealtimeTTS audio observer failed: %s", exc)

        threading.Thread(target=_run, daemon=True, name="TTSRecordingObserver").start()


def validate_tts_output_device(device_index: int) -> None:
    """Raise a clear error if the configured output device cannot be opened."""
    from src.voice_playback import probe_output_device

    ok, detail = probe_output_device(device_index)
    if not ok:
        raise RuntimeError(
            f"TTS output device [{device_index}] is unavailable: {detail}. "
            "Pick a playable CABLE Input/output device from the Audio Devices page."
        )


# ------------------------------------------------------------------ #
# edge-tts helpers
# ------------------------------------------------------------------ #

def _edge_available() -> bool:
    try:
        import edge_tts   # noqa: F401
        import soundfile  # noqa: F401
        return True
    except ImportError:
        return False


class _PiperPhraseCache:
    """In-memory PCM cache so repeated Piper lines do not resynthesize."""

    def __init__(self, voice, length_scale: float, noise_scale: float, noise_w_scale: float):
        self._voice = voice
        self._length_scale = length_scale
        self._noise_scale = noise_scale
        self._noise_w_scale = noise_w_scale
        self._items: dict[str, tuple[np.ndarray, int]] = {}
        self._lock = threading.Lock()

    def get(self, text: str) -> tuple[np.ndarray, int] | None:
        key = _prepare_piper_text(text).casefold()
        if not key:
            return None
        with self._lock:
            item = self._items.get(key)
        if item is None:
            return None
        return item[0].copy(), item[1]

    def store(self, text: str, data: np.ndarray, rate: int) -> None:
        key = _prepare_piper_text(text).casefold()
        if not key:
            return
        with self._lock:
            self._items[key] = (np.asarray(data, dtype=np.float32).copy(), int(rate))

    def ensure(self, text: str) -> None:
        if self.get(text) is not None:
            return
        prepared = _prepare_piper_text(text)
        if not prepared:
            return
        data, rate = _render_piper_phrase(
            prepared,
            self._voice,
            length_scale=self._length_scale,
            noise_scale=self._noise_scale,
            noise_w_scale=self._noise_w_scale,
        )
        self.store(prepared, data, rate)


def piper_voice_paths(model_path: str | Path | None, base: Path | None = None) -> tuple[Path, Path]:
    """Resolve the ONNX voice and the matching .onnx.json config."""
    raw_path = model_path or os.getenv(
        "PIPER_MODEL_PATH",
        "models/piper/en_US-lessac-medium.onnx",
    )
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        if base is None:
            from src.paths import runtime_base

            base = runtime_base()
        path = base / path
    path = path.resolve()
    return path, Path(str(path) + ".json")


def _load_piper_voice(model_path: str | Path | None):
    """Load one Piper voice model and reuse it for later TTS instances."""
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise RuntimeError(
            "Piper TTS is selected but piper-tts is not installed. "
            "Install the project dependencies before using TTS_ENGINE=piper."
        ) from exc

    path, config_path = piper_voice_paths(model_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Piper voice model was not found: {path}. "
            "Set PIPER_MODEL_PATH to a local .onnx voice model."
        )
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Piper voice config was not found: {config_path}. "
            "Keep the matching .onnx.json file next to the .onnx model."
        )
    cache_key = str(path)
    with _PIPER_VOICE_LOCK:
        cached = _PIPER_VOICES.get(cache_key)
        if cached is not None:
            return cached
        try:
            voice = PiperVoice.load(path, config_path=config_path)
        except Exception as exc:
            raise RuntimeError(f"Could not load Piper voice model '{path}': {exc}") from exc
        _PIPER_VOICES[cache_key] = voice
        logger.info("Piper voice loaded: %s", path.name)
        return voice


def _bounded_float(value: float, minimum: float, maximum: float, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= numeric <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return numeric


def _prepare_piper_text(text: str) -> str:
    """Make generated dialogue easier for Piper/Kokoro to deliver naturally."""
    from src.speech_style import humanize_for_speech

    return humanize_for_speech(text)


def _clean_tts_text(text: str) -> str:
    from src.speech_style import clean_tts_text

    return clean_tts_text(text)


def _project_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path(__file__).resolve().parent.parent / resolved
    return resolved


_KOKORO = None
_KOKORO_KEY = ""
_KOKORO_LOCK = threading.Lock()


def _load_kokoro(model_path: str | Path, voices_path: str | Path):
    """Load the Kokoro ONNX voice once and reuse it."""
    global _KOKORO, _KOKORO_KEY
    model = _project_path(model_path)
    voices = _project_path(voices_path)
    if not model.is_file() or not voices.is_file():
        raise FileNotFoundError(
            "Kokoro voice files are missing. "
            f"Expected {model} and {voices}."
        )
    key = str(model)
    with _KOKORO_LOCK:
        if _KOKORO is not None and _KOKORO_KEY == key:
            return _KOKORO
        from kokoro_onnx import Kokoro

        _KOKORO = Kokoro(str(model), str(voices))
        _KOKORO_KEY = key
        logger.info("Kokoro voice loaded: %s", model.name)
        return _KOKORO


def _render_kokoro_phrase(
    text: str,
    kokoro,
    voice_name: str,
    speed: float,
    *,
    sentence_pause: float = 0.30,
    clause_pause: float = 0.16,
) -> tuple[np.ndarray, int]:
    """Kokoro pauses at sentences/clauses; tuned for warm sales delivery."""
    return _kokoro_synthesize(
        text,
        kokoro,
        voice_name,
        speed,
        sentence_pause=sentence_pause,
        clause_pause=clause_pause,
    )


def _kokoro_synthesize(
    text: str,
    kokoro,
    voice_name: str,
    speed: float,
    *,
    sentence_pause: float = 0.30,
    clause_pause: float = 0.16,
) -> tuple[np.ndarray, int]:
    samples, rate = kokoro.create(
        text,
        voice=voice_name,
        speed=speed,
        lang="en-us",
        sentence_pause=sentence_pause,
        clause_pause=clause_pause,
    )
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        raise RuntimeError("Kokoro produced no audio")
    return _soften_piper_audio(audio, int(rate)), int(rate)


def _render_piper_phrase(
    text: str,
    voice,
    *,
    length_scale: float = 1.07,
    noise_scale: float = 0.5,
    noise_w_scale: float = 0.92,
) -> tuple[np.ndarray, int]:
    """Speak each sentence on its own, with a short breath between them."""
    import re

    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    if not parts:
        raise ValueError("Cannot synthesize empty text")
    chunks: list[np.ndarray] = []
    rate = 22050
    for index, part in enumerate(parts):
        audio, rate = _piper_synthesize(
            part,
            voice,
            length_scale=length_scale,
            noise_scale=noise_scale,
            noise_w_scale=noise_w_scale,
        )
        if index:
            chunks.append(np.zeros(int(rate * 0.16), dtype=np.float32))
        chunks.append(audio)
    if len(chunks) == 1:
        return chunks[0], rate
    return np.concatenate(chunks), rate


def _piper_synthesize(
    text: str,
    voice,
    *,
    length_scale: float = 1.07,
    noise_scale: float = 0.5,
    noise_w_scale: float = 0.92,
) -> tuple[np.ndarray, int]:
    """Synthesize Piper PCM through its WAV API and return float32 mono audio."""
    if voice is None:
        raise RuntimeError("Piper TTS was selected without a loaded voice model")
    if not str(text).strip():
        raise ValueError("Cannot synthesize empty text")

    from piper.config import SynthesisConfig
    import inspect

    with _PIPER_SYNTH_LOCK:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = Path(tf.name)
        try:
            with wave.open(str(wav_path), "wb") as wav_file:
                if "syn_config" in inspect.signature(voice.synthesize_wav).parameters:
                    voice.synthesize_wav(
                        text,
                        wav_file,
                        syn_config=SynthesisConfig(
                            length_scale=length_scale,
                            noise_scale=noise_scale,
                            noise_w_scale=noise_w_scale,
                            normalize_audio=False,
                            volume=0.9,
                        ),
                    )
                else:
                    voice.synthesize_wav(text, wav_file)
            import soundfile as sf

            data, rate = sf.read(str(wav_path), dtype="float32")
        finally:
            wav_path.unlink(missing_ok=True)

    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    if audio.size == 0:
        raise RuntimeError("Piper produced no audio")
    return _soften_piper_audio(audio, int(rate)), int(rate)


def _soften_piper_audio(audio: np.ndarray, rate: int) -> np.ndarray:
    """Keep peaks off the ceiling and fade the edges so phrases do not click."""
    audio = np.asarray(audio, dtype=np.float32).copy()
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.82:
        audio *= 0.82 / peak
    fade = max(1, int(rate * 0.008))
    if audio.size > fade * 2:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]
    return audio


def _edge_synthesize(text: str, voice: str) -> tuple[np.ndarray, int]:
    """Call edge-tts and decode the returned MP3 to a float32 numpy array."""
    import asyncio
    import edge_tts

    async def _gather() -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    try:
        asyncio.get_running_loop()
        running_async = True
    except RuntimeError:
        running_async = False

    if running_async:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            mp3_bytes = pool.submit(asyncio.run, _gather()).result()
    else:
        mp3_bytes = asyncio.run(_gather())
    return _decode_mp3(mp3_bytes)


def _decode_mp3(mp3_bytes: bytes) -> tuple[np.ndarray, int]:
    """
    Decode MP3 bytes to float32 numpy.
    Tries soundfile first (works if libsndfile ≥ 1.1.0 built with MP3).
    Falls back to an ffmpeg subprocess if not.
    """
    import soundfile as sf

    try:
        data, rate = sf.read(io.BytesIO(mp3_bytes), dtype="float32")
        return data, rate
    except Exception:
        pass

    import shutil, subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "edge-tts returned MP3 but soundfile cannot decode it and ffmpeg is missing. "
            "Install ffmpeg (https://ffmpeg.org) or use pyttsx3 engine instead."
        )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3f:
        mp3f.write(mp3_bytes)
        mp3_path = Path(mp3f.name)

    wav_path = mp3_path.with_suffix(".wav")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(mp3_path), "-ar", "24000", "-ac", "1", str(wav_path)],
            capture_output=True, check=True,
        )
        import soundfile as sf
        data, rate = sf.read(str(wav_path), dtype="float32")
        return data, rate
    finally:
        mp3_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


# ------------------------------------------------------------------ #
# pyttsx3 fallback
# ------------------------------------------------------------------ #

def _pyttsx3_to_device(
    text: str,
    device_index: int,
    audio_observer: Optional[Callable[[np.ndarray, int], None]] = None,
) -> None:
    """Synthesise via pyttsx3 to a temp WAV, then play to target device."""
    from src.tts import save_text_to_speech
    from src.voice_playback import play_wav_to_device

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        save_text_to_speech(text, tmp)
        logger.info("RealtimeTTS: TTS file generated for playback: %s", tmp)
        if callable(audio_observer):
            try:
                import soundfile as sf
                data, rate = sf.read(str(tmp), dtype="float32")
                threading.Thread(
                    target=lambda: audio_observer(np.asarray(data, dtype=np.float32), int(rate)),
                    daemon=True,
                    name="TTSRecordingObserver",
                ).start()
            except Exception as exc:
                logger.debug("RealtimeTTS fallback audio observer failed: %s", exc)
        play_wav_to_device(tmp, device_index, block=True)
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------ #
# Shared playback util
# ------------------------------------------------------------------ #

def _play_numpy_to_device(
    data: np.ndarray,
    samplerate: int,
    device_index: int,
    pulse_sink: str | None = None,
) -> None:
    from src.voice_playback import _stream_audio_to_device

    _stream_audio_to_device(
        data.astype(np.float32),
        samplerate,
        device_index,
        block=True,
        pulse_sink=pulse_sink,
    )


def save_edge_tts_wav(text: str, output_path: str | Path, voice: str = VOICE_GUY) -> Path:
    """Write voicemail/script audio with the same edge-tts voice as live calls."""
    return save_tts_wav(text, output_path, engine="edge", voice=voice)


def save_tts_wav(
    text: str,
    output_path: str | Path,
    *,
    engine: str = "edge",
    voice: str = VOICE_GUY,
    piper_model_path: str | Path | None = None,
    piper_length_scale: float = 1.07,
    piper_noise_scale: float = 0.5,
    piper_noise_w_scale: float = 0.92,
) -> Path:
    """Write script audio using the selected TTS backend."""
    import soundfile as sf

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not text.strip():
        raise ValueError("Cannot synthesize empty text")
    if engine.strip().lower() == "kokoro":
        kokoro = _load_kokoro(
            os.getenv("KOKORO_MODEL_PATH", "models/kokoro/kokoro-v1.0.onnx"),
            os.getenv("KOKORO_VOICES_PATH", "models/kokoro/voices-v1.0.bin"),
        )
        try:
            speed = float(os.getenv("KOKORO_SPEED", "0.88"))
        except ValueError:
            speed = 0.88
        try:
            sentence_pause = float(os.getenv("KOKORO_SENTENCE_PAUSE", "0.30"))
        except ValueError:
            sentence_pause = 0.30
        try:
            clause_pause = float(os.getenv("KOKORO_CLAUSE_PAUSE", "0.16"))
        except ValueError:
            clause_pause = 0.16
        data, rate = _render_kokoro_phrase(
            _prepare_piper_text(text),
            kokoro,
            os.getenv("KOKORO_VOICE", "af_heart"),
            speed,
            sentence_pause=sentence_pause,
            clause_pause=clause_pause,
        )
        sf.write(str(output_path), data, rate)
        logger.info("Kokoro WAV saved: %s (%d bytes)", output_path, output_path.stat().st_size)
        return output_path
    if engine.strip().lower() == "piper":
        data, rate = _render_piper_phrase(
            _prepare_piper_text(text),
            _load_piper_voice(piper_model_path),
            length_scale=_bounded_float(piper_length_scale, 0.75, 1.35, "piper_length_scale"),
            noise_scale=_bounded_float(piper_noise_scale, 0.0, 1.5, "piper_noise_scale"),
            noise_w_scale=_bounded_float(piper_noise_w_scale, 0.0, 1.5, "piper_noise_w_scale"),
        )
        sf.write(str(output_path), data, rate)
        logger.info("Piper WAV saved: %s (%d bytes)", output_path, output_path.stat().st_size)
        return output_path
    if not _edge_available():
        from src.tts import save_text_to_speech

        return save_text_to_speech(text, output_path)
    try:
        data, rate = _edge_synthesize(text, voice)
    except Exception as exc:
        # Keep voicemail/script generation working if Microsoft rotates DRM again.
        logger.warning("edge-tts WAV synthesis failed (%s); falling back to pyttsx3", exc)
        from src.tts import save_text_to_speech

        return save_text_to_speech(text, output_path)
    sf.write(str(output_path), data, rate)
    logger.info("edge-tts WAV saved: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path
