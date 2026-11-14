"""
Full-duplex realtime conversation loop orchestrator.

The loop is intentionally explicit about state transitions:
capture starts first, Tony speaks, Tony's echo tail is suppressed briefly, then
carrier speech is segmented, transcribed, answered, and the cycle repeats.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional
import difflib



import numpy as np

from src.audio_capture import AudioCapture
from src.call_session import CallSession, CallState
from src.conversation_agent import ConversationAgent
from src.greeting_library import GreetingLibrary
from src.hotkey_listener import HotkeyListener
from src.human_detector import HumanDetector
from src.paths import runtime_base
from src.realtime_diagnostics import DIAGNOSTICS_PATH, write_live_diagnostics
from src.realtime_tts import RealtimeTTS
from src.rust_core import CallEngine
from src.stt import GroqWhisperSTT
from src.vad import EnergyVAD, VADConfig
from src.voicemail_detector import (
    VoicemailAudioClassifier,
    VoicemailDetector,
    campaign_reason,
)


logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000
_VM_EVIDENCE_MAX_SECONDS = 30.0
_DEAD_AIR_SECONDS = 4.0
_SCREENING_LISTEN_SECONDS = 25.0
_OPENING_MAX_SAMPLES = int(_SAMPLERATE * 3.5)
_BASE_DIR = runtime_base()
_SEGMENT_DIR = _BASE_DIR / "logs" / "realtime_segments"

STATE_WAITING_FOR_ANSWER = "WAITING_FOR_ANSWER"
STATE_SPEAKING_OPENING = "SPEAKING_OPENING"
STATE_LISTENING = "LISTENING"
STATE_CAPTURING_SPEECH = "CAPTURING_SPEECH"
STATE_TRANSCRIBING = "TRANSCRIBING"
STATE_THINKING = "THINKING"
STATE_SPEAKING_REPLY = "SPEAKING_REPLY"
STATE_SILENCE_TIMEOUT = "SILENCE_TIMEOUT"
STATE_CALL_ENDED = "CALL_ENDED"


class ConversationLoop:
    """Orchestrates capture -> VAD -> STT -> LLM -> TTS until stopped."""

    def __init__(
        self,
        capture_device_hint: str,
        tts: RealtimeTTS,
        agent: ConversationAgent,
        stt: GroqWhisperSTT,

        vad_config: Optional[VADConfig] = None,
        calibrate_frames: int = 40,
        stt_prompt: str = "Indus Transports freight dispatch",
        transcript_path: Optional[Path] = None,
        recording_path: Optional[Path] = None,
        answered_speak_delay: float = 0.0,
        wait_for_human_audio: bool = False,
        human_audio_timeout: float = 8.0,
        listen_after_tts_delay_ms: int = 120,
        min_speech_seconds: float = 0.4,
        max_silence_seconds: float = 8.0,
        silence_does_not_end_call: bool = True,
        capture_rms_log_interval_seconds: float = 1.0,
        realtime_debug: bool = True,
        diagnostics_path: Optional[Path] = None,
        capture_factory: Optional[Callable[..., AudioCapture]] = None,
        voicemail_detect_seconds: float = 15.0,
        use_thinking_fillers: bool = True,
        filler_probability: float = 0.7,
        stream_llm_replies: bool = True,
        voicemail_play_on_greeting: bool = True,
        voicemail_greeting_frames_required: int = 6,
        dead_air_seconds: float = _DEAD_AIR_SECONDS,
        screening_purpose: str = "",
        screening_already_spoken: bool = False,
        greeting_library_dir: Optional[Path] = None,
    ):
        self.capture_device_hint = capture_device_hint
        self.tts = tts
        self.agent = agent
        self.stt = stt
        cfg = vad_config or VADConfig()
        cfg.min_speech_seconds = min_speech_seconds
        self._vad = EnergyVAD(cfg)
        self._rust = CallEngine()
        self._rust_first_turn = "wait"
        self._rust_ringback = "idle"
        self._calibrate_frames = max(0, int(calibrate_frames))
        self._stt_prompt = stt_prompt
        self._transcript_path = transcript_path
        self._recording_path = recording_path

        self._answered_speak_delay = answered_speak_delay
        self._wait_for_human_audio = wait_for_human_audio
        self._human_audio_timeout = human_audio_timeout
        self._listen_after_tts_delay_s = max(0.0, listen_after_tts_delay_ms / 1000.0)
        self._min_speech_seconds = max(0.0, min_speech_seconds)
        self._max_silence_seconds = max(0.0, max_silence_seconds)
        self._silence_does_not_end_call = silence_does_not_end_call
        self._rms_log_interval = max(0.1, capture_rms_log_interval_seconds)
        self._realtime_debug = realtime_debug
        self._diagnostics_path = diagnostics_path or DIAGNOSTICS_PATH
        self._capture_factory = capture_factory or AudioCapture

        self._stop_event = threading.Event()
        self._takeover_event = threading.Event()
        self._speech_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)

        self._calibration_event = threading.Event()
        self._answer_confirmed = threading.Event()
        self._response_active = threading.Event()

        self._capture: Optional[AudioCapture] = None
        self._hotkeys: Optional[HotkeyListener] = None
        self._session: Optional[CallSession] = None
        self._transcript_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._recording_segments: list[tuple[int, np.ndarray]] = []
        self._recording_start_monotonic = 0.0
        self._recording_open = False

        set_audio_observer = getattr(self.tts, "set_audio_observer", None)
        if callable(set_audio_observer):
            try:
                set_audio_observer(self._record_tony_audio)
            except Exception as exc:
                logger.debug("[RECORDING] TTS audio observer setup skipped: %s", exc)

        self._state_lock = threading.Lock()
        self._diag_lock = threading.Lock()
        self._state = STATE_WAITING_FOR_ANSWER
        self._loop_iteration = 0
        self._capture_suppress_until = 0.0
        self._last_speech_activity = time.monotonic()
        self._next_silence_log = time.monotonic() + self._max_silence_seconds
        self._next_rms_log = time.monotonic()
        self._speech_started_wall = ""
        self._speech_started_monotonic = 0.0
        self._last_capture_rms = 0.0
        self._last_segment_path = ""
        self._last_stt_text = ""
        self._last_empty_stt_reason = ""
        self._last_tts_text = ""
        self._last_tts_duration = 0.0
        self._last_voicemail_classifier_label = ""

        # Tony echo semantic suppression settings
        self._toney_echo_similarity_threshold = 0.86
        self._toney_echo_min_transcript_chars = 12


        # Voicemail detection (first N seconds after call connect)
        self._voicemail_detector = VoicemailAudioClassifier(samplerate=_SAMPLERATE)
        self._voicemail_evidence_detector = VoicemailDetector()
        self._human_detector = HumanDetector()

        self._voicemail_detect_seconds = max(0.0, float(voicemail_detect_seconds))
        self._voicemail_check_deadline_monotonic = 0.0
        self._voicemail_detected = False
        self._use_thinking_fillers = use_thinking_fillers
        self._filler_probability = max(0.0, min(1.0, float(filler_probability)))
        self._stream_llm_replies = stream_llm_replies
        self._voicemail_play_on_greeting = voicemail_play_on_greeting
        self._vm_greeting_frames_required = max(1, int(voicemail_greeting_frames_required))
        self._vm_greeting_hits = 0
        self._vm_answer_started_monotonic = 0.0
        self._vm_latest_transcript = ""
        self._vm_human_detected = False
        self._vm_candidate_confirmations = 0
        self._dead_air_seconds = max(0.5, float(dead_air_seconds))
        self._screening_purpose = screening_purpose or ""
        self._screening_already_spoken = bool(screening_already_spoken)
        self._screening_spoken = False
        self._screening_needed = False
        self._screening_cleared = False
        self._screening_hold_until = 0.0
        self._no_answer = False
        self._dom_voicemail = False
        self._greeting_library = GreetingLibrary(greeting_library_dir)
        self._opening_frames = []
        self._opening_samples = 0
        self._greeting_matched = False
        self._next_greeting_match = 0.0
        self._last_inbound_segment = None


    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        session: Optional[CallSession] = None,
        opening_line: Optional[str] = None,
        auto_opening: bool = True,
    ) -> None:
        self._session = session
        self._stop_event.clear()
        self._takeover_event.clear()
        self._calibration_event.clear()
        self._answer_confirmed.clear()
        self._response_active.clear()

        self._loop_iteration = 0
        self._last_speech_activity = time.monotonic()

        # Reset voicemail detection window for this call.
        self._voicemail_detector.reset()
        self._voicemail_check_deadline_monotonic = (
            self._last_speech_activity + self._voicemail_detect_seconds
        )
        self._voicemail_detected = False
        self._last_voicemail_classifier_label = ""
        self._vm_greeting_hits = 0
        self._vm_answer_started_monotonic = self._last_speech_activity
        self._vm_latest_transcript = ""
        self._vm_human_detected = False
        self._vm_candidate_confirmations = 0
        self._screening_spoken = False
        self._screening_needed = False
        self._screening_cleared = False
        self._screening_hold_until = 0.0
        self._no_answer = False
        self._dom_voicemail = False
        self._opening_frames = []
        self._opening_samples = 0
        self._greeting_matched = False
        self._next_greeting_match = 0.0
        self._last_inbound_segment = None
        if self._screening_already_spoken:
            self._screening_spoken = True
            self._screening_hold_until = time.monotonic() + _SCREENING_LISTEN_SECONDS

        self._next_silence_log = self._last_speech_activity + self._max_silence_seconds
        self._set_state(STATE_WAITING_FOR_ANSWER, "call connected; starting capture before TTS")

        if self._wait_for_human_audio or self._answered_speak_delay > 0:
            logger.info(
                "[LOOP] Opening wait configured "
                "(wait_for_human_audio=%s answered_speak_delay=%.2fs timeout=%.2fs)",
                self._wait_for_human_audio,
                self._answered_speak_delay,
                self._human_audio_timeout,
            )

        t_capture: Optional[threading.Thread] = None
        t_response: Optional[threading.Thread] = None

        try:
            self._hotkeys = HotkeyListener(
                on_takeover=self._handle_takeover,
                on_resume=self._handle_resume,
                on_stop=self._handle_stop,
            )
            self._hotkeys.start()

            self._capture = self._capture_factory(
                device_name_hint=self.capture_device_hint,
                samplerate=_SAMPLERATE,
            )
            self._open_recording()
            logger.info("[CAPTURE] Starting AudioCapture before opening TTS")
            self._capture.start()
            self._wait_for_capture_ready()

            t_capture = threading.Thread(target=self._capture_loop, daemon=True, name="Capture")
            t_response = threading.Thread(target=self._response_loop, daemon=True, name="Response")
            t_capture.start()
            t_response.start()

            if auto_opening and not self._stop_event.is_set():
                # Critical fix: do NOT speak until the carrier answers.
                # We wait for inbound human audio evidence (VAD) or an explicit
                # answered_speak_delay timeout configured by the caller.
                self._wait_before_opening()
                self._hold_opening_for_machine_greeting()
                self._classify_before_opening()
                self._end_if_dead_air()

            if auto_opening and not self._stop_event.is_set() and not self._no_answer:
                if self._screening_spoken:
                    logger.info(
                        "[CALL] Name and reason already given; waiting for a person or a mailbox"
                    )
                else:
                    line = opening_line or self.agent.opening_line()
                    if line:
                        logger.info("[CALL] Tony opening (after answer): %s", line)
                        if hasattr(self.tts, "prewarm_line"):
                            self.tts.prewarm_line(line)
                        self._write_transcript("Tony", line)
                        self._play_tts_blocking(line, STATE_SPEAKING_OPENING, "opening")
                    else:
                        logger.warning("[CALL] Opening line empty; entering listening mode")
                        self._set_state(STATE_LISTENING, "opening line empty")

            self._response_active.set()
            if self._state not in (STATE_LISTENING, STATE_SILENCE_TIMEOUT):
                self._set_state(STATE_LISTENING, "conversation loop active")
            logger.info(
                "[LOOP] Conversation active; hotkeys: Ctrl+Shift+T takeover, "
                "Ctrl+Shift+R resume, Ctrl+Shift+S stop"
            )

            self._stop_event.wait()
        finally:
            self._set_state(STATE_CALL_ENDED, "conversation loop stopping")
            if self._capture:
                self._capture.stop()
            if self._hotkeys:
                self._hotkeys.stop()
            self.tts.stop()
            self._close_recording()

            if t_capture:
                t_capture.join(timeout=3.0)
            if t_response:
                self._response_active.set()
                t_response.join(timeout=3.0)
            logger.info("[LOOP] ConversationLoop finished")

    def stop(self) -> None:
        # Stop audio immediately; run() cleanup may spend several seconds
        # joining capture/response workers after the remote call has ended.
        try:
            self.tts.stop()
        except Exception:
            pass
        self._response_active.set()
        self._stop_event.set()

    def is_agent_busy(self) -> bool:
        """True while TTS/LLM/STT work is in progress (ignore transient GV UI gaps)."""
        busy_states = {
            STATE_SPEAKING_OPENING,
            STATE_SPEAKING_REPLY,
            STATE_THINKING,
            STATE_TRANSCRIBING,
        }
        if self._state in busy_states:
            return True
        try:
            return bool(self.tts.is_speaking())
        except Exception:
            return False

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def note_dom_voicemail(self) -> None:
        """The call panel showed a mailbox cue. Audio still has to agree before we drop."""
        if self._dom_voicemail or self._voicemail_detected:
            return
        self._dom_voicemail = True
        logger.info("[DOM] voicemail cue on the call panel")

    def in_takeover(self) -> bool:
        return self._takeover_event.is_set()

    def diagnostics_snapshot(self) -> dict:
        return self._diagnostics_payload()

    def _wait_before_opening(self) -> None:
        if self._wait_for_human_audio:
            calibrated = self._wait_stop_or_event(self._calibration_event, timeout=10.0)
            if self._stop_event.is_set():
                return
            if calibrated:
                logger.info("[CALL] VAD calibrated; listening for inbound human audio")
            else:
                logger.warning("[CALL] VAD calibration timed out; proceeding with opening wait")

            listen_for = self._human_audio_timeout
            if self._session is not None:
                listen_for = min(listen_for, self._dead_air_seconds)
            logger.info(
                "[CALL] Waiting for inbound human audio (timeout=%.1fs)",
                listen_for,
            )
            human_heard = self._wait_stop_or_event(
                self._answer_confirmed,
                timeout=listen_for,
            )
            if self._stop_event.is_set():
                return
            if human_heard:
                logger.info("[CALL] Inbound audio confirmed")
            else:
                logger.info("[CALL] No inbound audio in %.1fs", listen_for)
        elif self._answered_speak_delay > 0:
            logger.info(
                "[CALL] Waiting %.1fs before opening line",
                self._answered_speak_delay,
            )
            self._stop_event.wait(timeout=self._answered_speak_delay)

    def _hold_opening_for_machine_greeting(self) -> None:
        """A person pauses after hello. A mailbox keeps talking or beeps.

        Skip the sales opening when that pattern shows up, so the drop can
        start at the tone instead of talking over the greeting. If the answer
        wait timed out with no inbound audio, speak immediately.
        """
        if self._stop_event.is_set() or self._voicemail_detected:
            return
        if not self._answer_confirmed.is_set():
            return
        deadline = time.monotonic() + 3.2
        heard_speech = False
        quiet_since = time.monotonic()
        while time.monotonic() < deadline and not self._stop_event.is_set():
            detector = self._voicemail_detector
            if detector.beep_ready:
                self._confirm_voicemail("beep tone", 0.95)
                return
            duration = detector.continuous_greeting_duration_seconds
            if duration >= 0.2:
                heard_speech = True
            if duration >= 2.8 and detector.machine_pattern_detected:
                self._confirm_voicemail("long scripted greeting", 0.9)
                return
            if heard_speech and detector.trailing_silence_seconds >= 0.28:
                return
            # The confirm event can fire before the classifier counts a frame.
            # Give that frame a moment, then treat a still-quiet line as a person.
            if not heard_speech and time.monotonic() - quiet_since >= 0.45:
                return
            time.sleep(0.05)

    def _wait_stop_or_event(self, event: threading.Event, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return event.is_set()
            if event.wait(timeout=min(0.05, remaining)):
                return True
        return event.is_set()

    # ------------------------------------------------------------------ #
    # State and diagnostics
    # ------------------------------------------------------------------ #

    def _set_state(self, state: str, reason: str = "") -> None:
        with self._state_lock:
            self._state = state
        suffix = f" reason={reason}" if reason else ""
        logger.info("[LOOP] iteration=%d state=%s%s", self._loop_iteration, state, suffix)
        self._write_diagnostics()

    def _diagnostics_payload(self) -> dict:
        capture_diag = self._capture_diagnostics()
        return {
            "state": self._state,
            "loop_iteration": self._loop_iteration,
            "capture_device": self.capture_device_hint,
            "capture_device_index": capture_diag.get("selected_device_index"),
            "capture_device_name": capture_diag.get("selected_device_name", ""),
            "capture_mode": capture_diag.get("capture_mode", ""),
            "capture_rms": self._last_capture_rms,
            "rust_first_turn": self._rust_first_turn,
            "rust_ringback": self._rust_ringback,
            "vad_threshold": self._vad.config.speech_threshold,
            "vad_detected": self._vad.is_in_speech,
            "speech_started_at": self._speech_started_wall,
            "speech_ended_at": "",
            "speech_duration_seconds": 0.0,
            "captured_speech_path": self._last_segment_path,
            "last_stt_text": self._last_stt_text,
            "last_empty_stt_reason": self._last_empty_stt_reason,
            "last_tts_text": self._last_tts_text,
            "last_tts_duration_seconds": self._last_tts_duration,
            "voicemail_classifier_result": self._last_voicemail_classifier_label,
            "silence_seconds": max(0.0, time.monotonic() - self._last_speech_activity),
        }

    def _write_diagnostics(self, **extra) -> None:
        payload = self._diagnostics_payload()
        payload.update(extra)
        try:
            with self._diag_lock:
                write_live_diagnostics(payload, self._diagnostics_path)
        except Exception as exc:
            logger.debug("Realtime diagnostics write skipped: %s", exc)

    def _capture_diagnostics(self) -> dict:
        if not self._capture:
            return {}
        diag_fn = getattr(self._capture, "diagnostics", None)
        if callable(diag_fn):
            try:
                data = diag_fn()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "selected_device_index": getattr(self._capture, "selected_device_index", None),
            "selected_device_name": getattr(self._capture, "selected_device_name", ""),
            "capture_mode": getattr(self._capture, "capture_mode", ""),
        }

    def _wait_for_capture_ready(self) -> None:
        if not self._capture:
            return
        wait_ready = getattr(self._capture, "wait_ready", None)
        ready = True
        if callable(wait_ready):
            try:
                ready = bool(wait_ready(timeout=5.0))
            except TypeError:
                ready = bool(wait_ready(5.0))
            except Exception:
                ready = False
        diag = self._capture_diagnostics()
        logger.info(
            "[CAPTURE] Device diagnostics: ready=%s mode=%s index=%s name='%s' threshold=%.4f",
            ready,
            diag.get("capture_mode", ""),
            diag.get("selected_device_index"),
            diag.get("selected_device_name", ""),
            self._vad.config.speech_threshold,
        )
        if not ready:
            logger.warning("[CAPTURE] Capture device did not report ready within 5s")
        self._write_diagnostics()

    # ------------------------------------------------------------------ #
    # Transcript and recording helpers
    # ------------------------------------------------------------------ #

    def _normalize_text_for_similarity(self, text: str) -> str:
        # Lowercase, strip punctuation-ish chars, collapse whitespace.
        t = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in (text or ""))
        return " ".join(t.split())

    def _is_tony_echo_transcript(self, stt_text: str) -> bool:
        """Return True if stt_text likely matches Tony's last TTS output."""
        if not stt_text:
            return False
        toney = self._last_tts_text or ""
        if not toney:
            return False

        stt_norm = self._normalize_text_for_similarity(stt_text)
        tts_norm = self._normalize_text_for_similarity(toney)

        if len(stt_norm) < self._toney_echo_min_transcript_chars:
            return False

        # Fast path: substring match (handles partial transcription)
        if stt_norm in tts_norm or tts_norm in stt_norm:
            return True

        ratio = difflib.SequenceMatcher(None, stt_norm, tts_norm).ratio()
        logger.debug(
            "[ECHO] similarity ratio=%.3f stt='%s' tts='%s'",
            ratio,
            stt_norm,
            tts_norm,
        )
        return ratio >= self._toney_echo_similarity_threshold

    def _write_transcript(self, speaker: str, text: str) -> None:

        if not self._transcript_path or not text:
            return
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {speaker}: {text}\n"
        try:
            with self._transcript_lock:
                self._transcript_path.parent.mkdir(parents=True, exist_ok=True)
                with self._transcript_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            logger.info("[TRANSCRIPT] wrote speaker=%s path=%s", speaker, self._transcript_path)
        except OSError as exc:
            logger.warning("[TRANSCRIPT] write error: %s", exc)

    def _open_recording(self) -> None:
        if not self._recording_path:
            return
        try:
            self._recording_path.parent.mkdir(parents=True, exist_ok=True)
            with self._recording_lock:
                self._recording_segments = []
                self._recording_start_monotonic = time.monotonic()
                self._recording_open = True
            logger.info("[RECORDING] mixed call audio path=%s", self._recording_path)
        except OSError as exc:
            self._recording_open = False
            logger.warning("[RECORDING] setup error: %s", exc)

    def _write_recording_frame(self, frame: np.ndarray) -> None:
        self._record_audio_segment(frame, _SAMPLERATE, offset_seconds=None)

    def _record_tony_audio(self, audio: np.ndarray, samplerate: int) -> None:
        """Add Tony's synthesized TTS to the mixed call recording timeline."""
        self._record_audio_segment(audio, samplerate, offset_seconds=None)

    def _record_audio_segment(
        self,
        audio: np.ndarray,
        samplerate: int,
        *,
        offset_seconds: Optional[float],
    ) -> None:
        try:
            mono = np.asarray(audio, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)
            if int(samplerate) != _SAMPLERATE:
                mono = _resample_mono(mono, int(samplerate), _SAMPLERATE)
            else:
                mono = mono.astype(np.float32, copy=True)
            with self._recording_lock:
                if not self._recording_open or not self._recording_start_monotonic:
                    return
                if offset_seconds is None:
                    offset_seconds = time.monotonic() - self._recording_start_monotonic
                start_sample = max(0, int(round(offset_seconds * _SAMPLERATE)))
                self._recording_segments.append((start_sample, mono))
        except Exception as exc:
            logger.debug("[RECORDING] frame skipped: %s", exc)

    def _close_recording(self) -> None:
        with self._recording_lock:
            segments = list(self._recording_segments)
            self._recording_segments = []
            recording_path = self._recording_path
            self._recording_open = False
        if not recording_path:
            return
        try:
            mixed = _mix_recording_segments(segments)
            pcm = (np.clip(mixed, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(recording_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(_SAMPLERATE)
                wf.writeframes(pcm.tobytes())
            logger.info("[RECORDING] finalized mixed path=%s", recording_path)
        except Exception as exc:
            logger.warning("[RECORDING] close error: %s", exc)

    def _write_speech_segment(self, audio: np.ndarray) -> str:
        if not self._realtime_debug:
            return ""
        try:
            _SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
            name = f"speech_{time.strftime('%Y%m%d_%H%M%S')}_{self._loop_iteration + 1:03d}.wav"
            path = _SEGMENT_DIR / name
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(_SAMPLERATE)
                wf.writeframes(pcm.tobytes())
            return str(path)
        except Exception as exc:
            logger.debug("[VAD] speech segment file write skipped: %s", exc)
            return ""

    # ------------------------------------------------------------------ #
    # Hotkey handlers
    # ------------------------------------------------------------------ #

    def _handle_takeover(self) -> None:
        logger.info("[TAKEOVER] AI paused; human takeover active")
        self._takeover_event.set()
        self.tts.stop()

    def _handle_resume(self) -> None:
        logger.info("[RESUME] AI resumed")
        self._takeover_event.clear()
        self._vad.reset()
        _drain(self._speech_q)
        self._set_state(STATE_LISTENING, "manual resume")

    def _handle_stop(self) -> None:
        logger.info("[STOP] Ending conversation")
        self.tts.stop()
        self.stop()

    # ------------------------------------------------------------------ #
    # Capture thread
    # ------------------------------------------------------------------ #

    def _capture_loop(self) -> None:
        logger.info("[CAPTURE] Capture processing thread started")
        calibration: list[np.ndarray] = []
        if self._calibrate_frames == 0:
            self._calibration_event.set()

        while not self._stop_event.is_set():
            capture_error = getattr(self._capture, "last_error", None)
            if isinstance(capture_error, BaseException):
                logger.error("[CAPTURE] AudioCapture failed: %s", capture_error)
                self.stop()
                return

            frame = self._capture.read(timeout=0.05) if self._capture else None
            now = time.monotonic()
            if frame is None:
                self._maybe_end_screening_wait(now)
                self._maybe_log_silence(now)
                continue

            frame = np.asarray(frame, dtype=np.float32)
            self._write_recording_frame(frame)
            threshold = self._vad.config.speech_threshold
            speaking = bool(self.tts.is_speaking())
            try:
                gate = self._rust.push(frame, agent_speaking=speaking, threshold=float(threshold))
                rms = float(gate["rms"])
            except Exception:
                rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                gate = {"barge_in": False, "first_turn": "wait", "ringback": "idle"}
            self._last_capture_rms = rms
            self._rust_first_turn = str(gate.get("first_turn") or "wait")
            self._rust_ringback = str(gate.get("ringback") or "idle")

            self._log_rms_if_due(now, rms, threshold)

            if speaking:
                if gate["barge_in"]:
                    logger.info(
                        "[CAPTURE] Barge-in audio while TTS speaking rms=%.4f threshold=%.4f; stopping Tony",
                        rms,
                        threshold,
                    )
                    self.tts.stop()
                    self._answer_confirmed.set()
                    self._capture_suppress_until = time.monotonic() + 0.08
                self._vad.reset()
                self._write_diagnostics(vad_detected=False)
                continue

            if now < self._capture_suppress_until:
                self._vad.reset()
                self._write_diagnostics(vad_detected=False)
                continue

            self._maybe_calibrate(calibration, frame, rms)

            if not self._answer_confirmed.is_set() and rms >= threshold:
                self._answer_confirmed.set()
                logger.info("[CAPTURE] Inbound audio evidence rms=%.4f threshold=%.4f", rms, threshold)

            if self._takeover_event.is_set():
                continue

            before = self._vad.is_in_speech
            # Collect raw voicemail audio during the configured post-answer window.
            if not self._voicemail_detected and time.monotonic() <= self._voicemail_check_deadline_monotonic:
                try:
                    label = self._voicemail_detector.process_frame(frame, samplerate=_SAMPLERATE)
                    if label != self._last_voicemail_classifier_label:
                        self._last_voicemail_classifier_label = label
                        logger.info(
                            "[VM] audio_classifier_result=%s window_remaining=%.1fs rms=%.4f",
                            label,
                            max(0.0, self._voicemail_check_deadline_monotonic - time.monotonic()),
                            rms,
                        )
                    if label == "voicemail_greeting":
                        self._vm_greeting_hits += 1
                        logger.debug("[VM] Possible voicemail greeting (%d/%d)", self._vm_greeting_hits, self._vm_greeting_frames_required)
                    elif label == "live_call" and self._last_inbound_segment is not None:
                        # Short live pickup already heard — do not let fingerprint matching
                        # override the classifier without a beep/keyword.
                        if not self._vm_human_detected and not self._voicemail_detected:
                            self._lock_human_pickup(
                                "audio classifier live_call after inbound speech",
                                0.8,
                            )
                except Exception as exc:
                    logger.debug("[VM] detection frame error: %s", exc)
                self._remember_opening_frame(frame)
                if (
                    now >= self._next_greeting_match
                    and not self._vm_human_detected
                    and self._opening_samples >= int(_SAMPLERATE * 1.6)
                ):
                    self._next_greeting_match = now + 0.3
                    self._maybe_match_greeting()

            # Keep evaluating cached evidence after the raw-audio sampling window.
            # A long voicemail greeting may not be transcribed until that window ends.
            if not self._voicemail_detected and self._evaluate_voicemail_evidence(now):
                return

            segment = self._vad.process_frame(frame)

            after = self._vad.is_in_speech
            self._write_diagnostics(vad_detected=after or rms >= threshold)

            if not before and after:
                self._speech_started_monotonic = now
                self._speech_started_wall = _wall_ts()
                self._set_state(
                    STATE_CAPTURING_SPEECH,
                    f"speech_start={self._speech_started_wall} rms={rms:.4f}",
                )
                logger.info(
                    "[VAD] speech start timestamp=%s rms=%.4f threshold=%.4f",
                    self._speech_started_wall,
                    rms,
                    threshold,
                )

            if segment is not None:
                self._handle_speech_segment(segment, now)

            self._maybe_log_silence(now)

    def _evaluate_voicemail_evidence(self, now: float) -> bool:
        """Apply the Auto Dialer 2-of-5 voicemail gate and stable confirmation.

        A beep or mailbox phrase still wins even if the greeting started with
        "hi". A saved-greeting fingerprint alone must never drop a live human —
        it only counts as supporting evidence with another hard signal.
        """
        if self._voicemail_detected:
            return False

        answer_elapsed = max(0.0, now - self._vm_answer_started_monotonic)
        transcript = self._vm_latest_transcript
        if self._voicemail_detector.beep_ready:
            return self._confirm_voicemail("beep tone", 0.95)
        if VoicemailDetector.keyword_count(transcript) > 0:
            return self._confirm_voicemail("voicemail keyword", 0.9)
        if getattr(self, "_dom_voicemail", False):
            return self._confirm_voicemail("DOM voicemail cue", 0.9)
        if self._maybe_end_screening_wait(now):
            return True
        if (
            VoicemailDetector.is_screening_transcript(transcript)
            and not getattr(self, "_screening_spoken", False)
        ):
            self._screening_needed = True
            return False
        if self._vm_human_detected:
            return False
        # Fingerprint match is a second vote only — never enough alone, and
        # never after the audio classifier already called this a live pickup.
        if getattr(self, "_greeting_matched", False):
            classifier = (self._last_voicemail_classifier_label or "").strip().lower()
            hard_support = (
                self._voicemail_detector.beep_ready
                or VoicemailDetector.keyword_count(transcript) > 0
                or getattr(self, "_dom_voicemail", False)
                or (
                    self._voicemail_detector.machine_pattern_detected
                    and self._voicemail_detector.continuous_greeting_duration_seconds >= 2.5
                )
                or (
                    classifier == "voicemail_greeting"
                    and self._voicemail_detector.continuous_greeting_duration_seconds >= 2.5
                )
            )
            if hard_support:
                return self._confirm_voicemail("saved greeting + hard signal", 0.92)
            logger.info(
                "[VM] saved greeting match held (needs beep/keyword/long machine cue); "
                "classifier=%s human=%s",
                classifier or "none",
                self._vm_human_detected,
            )
        if answer_elapsed > _VM_EVIDENCE_MAX_SECONDS:
            self._vm_candidate_confirmations = 0
            return False
        machine_pattern = (
            self._voicemail_play_on_greeting
            and self._vm_greeting_hits >= self._vm_greeting_frames_required
            and self._voicemail_detector.machine_pattern_detected
        )
        result = self._voicemail_evidence_detector.classify(
            transcript=self._vm_latest_transcript,
            answer_elapsed_seconds=answer_elapsed,
            continuous_greeting_duration_seconds=(
                self._voicemail_detector.continuous_greeting_duration_seconds
            ),
            beep_detected=self._voicemail_detector.beep_ready,
            beep_confidence=1.0 if self._voicemail_detector.beep_ready else 0.0,
            repeated_machine_pattern=machine_pattern,
            dom_voicemail=getattr(self, "_dom_voicemail", False),
            human_detected=self._vm_human_detected,
        )

        if result.candidate and result.confidence >= 0.85:
            self._vm_candidate_confirmations += 1
        else:
            self._vm_candidate_confirmations = max(0, self._vm_candidate_confirmations - 1)

        if result.factors:
            logger.debug(
                "[VM] evidence confidence=%.2f confirmations=%d/3 elapsed=%.1fs factors=%s",
                result.confidence,
                self._vm_candidate_confirmations,
                answer_elapsed,
                ", ".join(result.factors),
            )

        if self._vm_candidate_confirmations < 2:
            return False
        return self._confirm_voicemail(", ".join(result.factors), result.confidence)

    def _confirm_voicemail(self, reason: str, confidence: float) -> bool:
        if self._voicemail_detected:
            return False
        self._voicemail_detected = True
        self._vm_human_detected = False
        detail = f"voicemail confirmed: {reason}"
        logger.info("[VM] %s (confidence=%.2f)", detail, confidence)
        try:
            self.tts.stop()
        except Exception:
            pass
        # Only learn greetings from hard mailbox proof — never from fingerprint-only
        # or polluted short hellos that previously false-triggered.
        reason_l = (reason or "").lower()
        if "beep" in reason_l or "keyword" in reason_l:
            self._save_confirmed_greeting()
        if self._session is not None and not self._session.is_terminal():
            try:
                self._session.outcome = "voicemail"
                self._session.transition(CallState.VOICEMAIL, detail)
            except ValueError:
                pass
        self.stop()
        return True

    def _inbound_activity(self) -> bool:
        detector = self._voicemail_detector
        if getattr(detector, "beep_ready", False):
            return True
        duration = float(getattr(detector, "continuous_greeting_duration_seconds", 0.0) or 0.0)
        if duration >= 0.2:
            return True
        if (self._vm_latest_transcript or "").strip():
            return True
        return self._last_inbound_segment is not None

    def _end_if_dead_air(self) -> None:
        """An answered line that never speaks is not a pitch and not a recording."""
        if self._session is None or self._stop_event.is_set() or self._voicemail_detected:
            return
        if self._screening_spoken or self._inbound_activity():
            return
        deadline = self._vm_answer_started_monotonic + self._dead_air_seconds
        while time.monotonic() < deadline and not self._stop_event.is_set():
            if self._voicemail_detected or self._screening_spoken or self._inbound_activity():
                return
            time.sleep(0.05)
        if (
            self._stop_event.is_set()
            or self._voicemail_detected
            or self._screening_spoken
            or self._inbound_activity()
        ):
            return
        self._mark_no_answer()

    def _maybe_end_screening_wait(self, now: float) -> bool:
        if (
            not getattr(self, "_screening_spoken", False)
            or getattr(self, "_screening_cleared", False)
            or self._vm_human_detected
            or self._voicemail_detected
        ):
            return False
        hold_until = getattr(self, "_screening_hold_until", 0.0)
        if hold_until and now >= hold_until:
            logger.info("[CALL] Screening wait ended with no person and no mailbox")
            self._mark_no_answer()
            return True
        return False

    def _mark_no_answer(self) -> None:
        if self._voicemail_detected or self._no_answer:
            return
        self._no_answer = True
        logger.info("[CALL] No one spoke; hanging up and dialing the next number")
        if self._session is not None and not self._session.is_terminal():
            try:
                self._session.outcome = "no_answer"
                self._session.transition(CallState.ENDED, "no answer: silent line")
            except ValueError:
                pass
        self.stop()

    def _classify_before_opening(self) -> None:
        """Read the first thing they say before the sales pitch starts."""
        if self._session is None or self._stop_event.is_set() or self._voicemail_detected:
            return
        if not self._inbound_activity():
            glance = time.monotonic() + 0.3
            while time.monotonic() < glance and not self._stop_event.is_set():
                if self._inbound_activity() or self._voicemail_detected:
                    break
                time.sleep(0.05)
        if not self._inbound_activity() or self._voicemail_detected or self._stop_event.is_set():
            return
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not self._stop_event.is_set() and not self._voicemail_detected:
            segment = self._last_inbound_segment
            if segment is not None:
                self._transcribe_for_detection(segment)
                return
            time.sleep(0.05)

    def _transcribe_for_detection(self, segment: np.ndarray) -> None:
        if (self._vm_latest_transcript or "").strip() or self._stop_event.is_set():
            return
        try:
            transcript = self.stt.transcribe(
                segment,
                samplerate=_SAMPLERATE,
                prompt=self._stt_prompt,
            )
        except Exception as exc:
            logger.debug("[STT] opening classify failed: %s", exc)
            return
        transcript = (transcript or "").strip()
        if not transcript:
            return
        self._vm_latest_transcript = transcript
        logger.info("[STT] opening_text=%s", transcript)
        if VoicemailDetector.is_screening_transcript(transcript):
            self._speak_screening_line()
            return
        self._evaluate_voicemail_evidence(time.monotonic())

    def _screening_response_line(self) -> str:
        agent = getattr(self.agent, "agent_name", "")
        company = getattr(self.agent, "company_name", "")
        context = getattr(self.agent, "company_context", "")
        if not isinstance(agent, str) or not agent.strip():
            agent = "Alex"
        if not isinstance(company, str) or not company.strip():
            company = "our team"
        if not isinstance(context, str):
            context = ""
        purpose = campaign_reason(context, self._screening_purpose)
        return f"{agent} from {company}, calling about {purpose}."

    def _speak_screening_line(self) -> None:
        if self._screening_spoken or self._stop_event.is_set() or self._voicemail_detected:
            return
        self._screening_spoken = True
        self._screening_hold_until = time.monotonic() + _SCREENING_LISTEN_SECONDS
        line = self._screening_response_line()
        logger.info("[CALL] Screening prompt — saying name and reason, staying on the line")
        self._write_transcript("Tony", line)
        self._play_tts_blocking(line, STATE_SPEAKING_OPENING, "screening")

    def _remember_opening_frame(self, frame: np.ndarray) -> None:
        try:
            if self.tts.is_speaking():
                return
        except Exception:
            return
        if self._opening_samples >= _OPENING_MAX_SAMPLES:
            return
        chunk = np.asarray(frame, dtype=np.float32).reshape(-1)
        self._opening_frames.append(chunk.copy())
        self._opening_samples += int(chunk.size)
        while self._opening_samples > _OPENING_MAX_SAMPLES and self._opening_frames:
            dropped = self._opening_frames.pop(0)
            self._opening_samples -= int(dropped.size)

    def _lock_human_pickup(self, reason: str, confidence: float = 0.85) -> None:
        """Treat the far side as a live person until a beep/keyword proves otherwise."""
        if self._voicemail_detected or self._vm_human_detected:
            return
        self._vm_human_detected = True
        self._vm_candidate_confirmations = 0
        self._greeting_matched = False
        logger.info(
            "[VM] Human pickup locked confidence=%.2f reasons=%s",
            confidence,
            reason,
        )

    def _maybe_match_greeting(self) -> None:
        if (
            self._greeting_matched
            or self._voicemail_detected
            or self._vm_human_detected
            or self._opening_samples < int(_SAMPLERATE * 1.6)
        ):
            return
        try:
            audio = np.concatenate(self._opening_frames)
            if self._greeting_library.matches(audio):
                self._greeting_matched = True
                logger.info("[VM] opening matches a saved mailbox greeting")
        except Exception as exc:
            logger.debug("[VM] greeting match skipped: %s", exc)

    def _save_confirmed_greeting(self) -> None:
        library = getattr(self, "_greeting_library", None)
        frames = getattr(self, "_opening_frames", None) or []
        if library is None or not frames:
            return
        try:
            library.save(np.concatenate(frames))
        except Exception as exc:
            logger.debug("[VM] greeting save skipped: %s", exc)

    def _maybe_calibrate(
        self,
        calibration: list[np.ndarray],
        frame: np.ndarray,
        rms: float,
    ) -> None:
        if self._calibration_event.is_set() or self._calibrate_frames <= 0:
            return
        quiet_limit = max(0.002, self._vad.config.speech_threshold * 0.65)
        if rms > quiet_limit or self._vad.is_in_speech:
            return
        calibration.append(frame)
        if len(calibration) >= self._calibrate_frames:
            old = self._vad.config.speech_threshold
            new = self._vad.calibrate_threshold(calibration)
            self._calibration_event.set()
            logger.info(
                "[CAPTURE] VAD calibrated old_threshold=%.4f new_threshold=%.4f quiet_frames=%d",
                old,
                new,
                len(calibration),
            )
            self._write_diagnostics(vad_threshold=new)

    def _handle_speech_segment(self, segment: np.ndarray, ended_monotonic: float) -> None:
        self._last_inbound_segment = np.asarray(segment, dtype=np.float32).reshape(-1).copy()
        duration = len(segment) / float(_SAMPLERATE)
        ended_wall = _wall_ts()
        path = self._write_speech_segment(segment)
        self._last_segment_path = path
        self._last_speech_activity = ended_monotonic
        self._next_silence_log = ended_monotonic + self._max_silence_seconds
        logger.info(
            "[VAD] speech end timestamp=%s duration=%.2fs captured_speech_path=%s",
            ended_wall,
            duration,
            path or "<not saved>",
        )
        self._write_diagnostics(
            speech_ended_at=ended_wall,
            speech_duration_seconds=duration,
            captured_speech_path=path,
        )

        if duration < self._min_speech_seconds:
            logger.info(
                "[VAD] segment dropped: duration %.2fs below MIN_SPEECH_SECONDS %.2fs",
                duration,
                self._min_speech_seconds,
            )
            self._set_state(STATE_LISTENING, "short segment dropped")
            return

        # A brief inbound burst right after answer is almost always a live "hello",
        # not a mailbox monologue. Lock human immediately so fingerprint matching
        # cannot race ahead of STT (which may be slow or fail).
        if (
            not self._response_active.is_set()
            and not self._voicemail_detected
            and 0.35 <= duration <= 2.5
            and not VoicemailDetector.keyword_count(self._vm_latest_transcript)
        ):
            self._lock_human_pickup(
                f"short inbound burst {duration:.2f}s before opening",
                0.82,
            )

        if self._speech_q.full():
            logger.warning("[VAD] speech queue full; dropping %.2fs segment", duration)
            self._set_state(STATE_LISTENING, "speech queue full")
            return

        self._speech_q.put(segment)
        if self._response_active.is_set():
            logger.info("[VAD] queued %.2fs speech segment for STT", duration)
        else:
            logger.info(
                "[VAD] queued %.2fs speech segment for STT after opening completes",
                duration,
            )
            # Do not wait for Ashley's opening to finish — mailbox keywords
            # often arrive in the first greeting while TTS is still speaking.
            self._kick_early_voicemail_stt(segment)
            self._set_state(STATE_LISTENING, "speech queued until response loop activates")

    def _kick_early_voicemail_stt(self, segment: np.ndarray) -> None:
        """Transcribe inbound audio during the opening for mailbox/human cues."""
        if self._voicemail_detected or self._stop_event.is_set():
            return
        audio = np.asarray(segment, dtype=np.float32).reshape(-1).copy()
        worker = threading.Thread(
            target=self._early_voicemail_stt_worker,
            args=(audio,),
            daemon=True,
            name="EarlyVoicemailSTT",
        )
        worker.start()

    def _early_voicemail_stt_worker(self, audio: np.ndarray) -> None:
        if self._voicemail_detected or self._stop_event.is_set():
            return
        try:
            transcript = (self.stt.transcribe(audio, samplerate=_SAMPLERATE) or "").strip()
        except Exception as exc:
            logger.warning("[VM] early STT failed: %s", exc)
            return
        if not transcript or self._voicemail_detected or self._stop_event.is_set():
            return
        if self._is_tony_echo_transcript(transcript):
            return
        logger.info("[VM] early STT during opening: %s", transcript)
        self._vm_latest_transcript = transcript
        duration = len(audio) / float(_SAMPLERATE)
        if VoicemailDetector.keyword_count(transcript) > 0:
            self._write_transcript("Prospect", transcript)
            self._evaluate_voicemail_evidence(time.monotonic())
            return
        if VoicemailDetector.is_screening_transcript(transcript):
            self._speak_screening_line()
            return
        human = self._human_detector.classify(
            transcript=transcript,
            speech_duration_seconds=duration,
            answer_elapsed_seconds=max(
                0.0, time.monotonic() - self._vm_answer_started_monotonic
            ),
            has_speech_like=True,
        )
        if human.detected:
            self._write_transcript("Prospect", transcript)
            self._lock_human_pickup(
                ", ".join(human.reasons) or "early human transcript",
                human.confidence,
            )

    def _log_rms_if_due(self, now: float, rms: float, threshold: float) -> None:
        if now < self._next_rms_log:
            return
        self._next_rms_log = now + self._rms_log_interval
        if self._state in (STATE_LISTENING, STATE_CAPTURING_SPEECH, STATE_SILENCE_TIMEOUT):
            diag = self._capture_diagnostics()
            logger.info(
                "[AUDIO] state=%s rms=%.4f vad_threshold=%.4f vad_detected=%s "
                "capture_device='%s' index=%s iteration=%d",
                self._state,
                rms,
                threshold,
                self._vad.is_in_speech or rms >= threshold,
                diag.get("selected_device_name") or self.capture_device_hint,
                diag.get("selected_device_index"),
                self._loop_iteration,
            )
            self._write_diagnostics()

    def _maybe_log_silence(self, now: float) -> None:
        if (
            self._max_silence_seconds <= 0
            or not self._response_active.is_set()
            or self._takeover_event.is_set()
            or self.tts.is_speaking()
        ):
            return
        if self._state not in (STATE_LISTENING, STATE_SILENCE_TIMEOUT):
            return
        silence_for = now - self._last_speech_activity
        if silence_for < self._max_silence_seconds or now < self._next_silence_log:
            return
        self._next_silence_log = now + self._max_silence_seconds
        self._set_state(STATE_SILENCE_TIMEOUT, f"no carrier speech for {silence_for:.1f}s")
        logger.info(
            "[LOOP] silence timeout %.1fs reached; silence_does_not_end_call=%s",
            silence_for,
            self._silence_does_not_end_call,
        )
        if not self._silence_does_not_end_call:
            self.stop()

    # ------------------------------------------------------------------ #
    # Response thread
    # ------------------------------------------------------------------ #

    def _response_loop(self) -> None:
        logger.info("[RESPONSE] Response thread started; waiting for LISTENING")
        while not self._stop_event.is_set():
            if self._response_active.wait(timeout=0.25):
                break
        if self._stop_event.is_set():
            return
        logger.info("[RESPONSE] Response thread active")

        while not self._stop_event.is_set():
            try:
                audio_segment = self._speech_q.get(timeout=0.25)
            except queue.Empty:
                continue

            if self._takeover_event.is_set():
                logger.info("[RESPONSE] segment ignored during human takeover")
                continue

            # --- Barge-in detection ---
            # If carrier spoke while Tony was still speaking, stop TTS immediately
            # so Tony doesn't finish his reply over the carrier's interruption.
            if self.tts.is_speaking():
                logger.info("[RESPONSE] Barge-in detected: carrier spoke during TTS; stopping Tony")
                self.tts.stop()
                # Brief wait for audio pipeline to settle
                time.sleep(0.05)
            if self._stop_event.is_set():
                break

            # Re-check TTS state right before STT; unit tests expect that if
            # TTS is speaking, we do not even call STT.
            if self.tts.is_speaking():
                logger.info("[RESPONSE] segment skipped because TTS is speaking")
                continue

            import random

            if (
                self._use_thinking_fillers
                and random.random() < self._filler_probability
                and hasattr(self.tts, "play_filler")
            ):
                try:
                    self.tts.play_filler()
                except Exception as exc:
                    logger.debug("[RESPONSE] filler skipped: %s", exc)

            self._loop_iteration += 1
            duration = len(audio_segment) / float(_SAMPLERATE)
            self._set_state(STATE_TRANSCRIBING, f"audio_duration={duration:.2f}s")


            # Build a per-call enriched STT prompt including known carrier context
            dynamic_prompt = (
                getattr(self.agent, "build_stt_prompt", lambda: self._stt_prompt)()
                or self._stt_prompt
            )

            try:
                transcript = self.stt.transcribe(

                    audio_segment,
                    samplerate=_SAMPLERATE,
                    prompt=dynamic_prompt,
                )

            except Exception as exc:
                self._last_empty_stt_reason = f"exception: {exc}"
                self._write_diagnostics(last_empty_stt_reason=self._last_empty_stt_reason)
                logger.error("[STT] Failed: %s", exc)
                self._set_state(STATE_LISTENING, "STT exception; waiting for retry")
                continue

            if self._stop_event.is_set():
                logger.info("[STT] Result discarded because the call already ended")
                return
            if not transcript:
                reason = getattr(self.stt, "last_empty_reason", "") or "STT returned empty text"

                self._last_empty_stt_reason = reason
                self._write_diagnostics(last_empty_stt_reason=reason)
                logger.info("[STT] Empty reason=%s", reason)
                self._set_state(STATE_LISTENING, "empty STT; waiting for next speech")
                continue

            # Tony self-talk suppression: if STT transcript looks like our last TTS output,
            # treat it as echo and ignore it.
            if self._is_tony_echo_transcript(transcript):
                self._last_empty_stt_reason = "suppressed_tony_echo_similarity"
                self._write_diagnostics(last_empty_stt_reason=self._last_empty_stt_reason)
                logger.info("[STT] Suppressed Tony echo transcript=%s", transcript)
                self._set_state(STATE_LISTENING, "suppressed tony echo")
                continue

            self._last_stt_text = transcript
            self._last_empty_stt_reason = ""
            logger.info("[STT] response_text=%s", transcript)

            self._write_diagnostics(last_stt_text=transcript, last_empty_stt_reason="")
            self._write_transcript("Prospect", transcript)

            answer_elapsed = max(0.0, time.monotonic() - self._vm_answer_started_monotonic)
            self._vm_latest_transcript = transcript
            if VoicemailDetector.is_screening_transcript(transcript):
                if not self._screening_spoken:
                    self._speak_screening_line()
                logger.info("[CALL] Screening prompt kept on the line; not a mailbox")
                self._set_state(STATE_LISTENING, "waiting after screening")
                return
            self._screening_cleared = True
            if self._evaluate_voicemail_evidence(time.monotonic()):
                return

            human = self._human_detector.classify(
                transcript=transcript,
                speech_duration_seconds=duration,
                answer_elapsed_seconds=answer_elapsed,
                has_speech_like=True,
            )
            if human.detected and not self._screening_spoken:
                self._vm_human_detected = True
                self._vm_candidate_confirmations = 0
                logger.info(
                    "[VM] Human pickup locked confidence=%.2f reasons=%s",
                    human.confidence,
                    ", ".join(human.reasons),
                )
            elif human.detected and self._screening_spoken:
                logger.info("[CALL] Person spoke after screening; continuing the sale")

            self._set_state(STATE_THINKING, "generating LLM reply")
            if self._stream_llm_replies and hasattr(self.agent, "respond_to_streaming"):
                spoken_parts: list[str] = []

                def _on_clause(clause: str) -> None:
                    if self._stop_event.is_set() or not clause.strip():
                        return
                    spoken_parts.append(clause)
                    self._write_transcript("Tony", clause)
                    self._last_tts_text = " ".join(spoken_parts)
                    # Same TTS path for every sentence — avoids mixed neural + SAPI voices.
                    self._play_tts_blocking(clause, STATE_SPEAKING_REPLY, "reply")

                response = self.agent.respond_to_streaming(transcript, _on_clause)
            else:
                response = self.agent.respond_to(transcript)
                if not response:
                    logger.info("[RESPONSE] Empty LLM response; returning to listening")
                    self._set_state(STATE_LISTENING, "empty LLM response")
                    continue
                logger.info("[RESPONSE] Tony: %s", response)
                self._write_transcript("Tony", response)
                self._last_tts_text = response
                self._play_tts_async_like(response, STATE_SPEAKING_REPLY, "reply")

            if self._stop_event.is_set():
                return
            if not response:
                logger.info("[RESPONSE] Empty LLM response; returning to listening")
                self._set_state(STATE_LISTENING, "empty LLM response")
                continue

            logger.info("[RESPONSE] Tony (full): %s", response)
            if self._stream_llm_replies and hasattr(self.agent, "respond_to_streaming"):
                self._last_tts_text = response


            if self.agent.should_end_call():
                logger.info("[RESPONSE] Agent requested graceful call end")
                goodbye = self.agent.goodbye_line()
                if goodbye:
                    logger.info("[RESPONSE] Tony goodbye: %s", goodbye)
                    self._write_transcript("Tony", goodbye)
                    self._play_tts_blocking(goodbye, STATE_SPEAKING_REPLY, "goodbye")
                if self._session and not self._session.is_terminal():
                    try:
                        self._session.transition(CallState.ENDED, "agent ended call gracefully")
                    except ValueError:
                        pass
                self.stop()

    def _play_tts_async_like(self, text: str, state: str, label: str) -> None:
        """Play TTS while preferring speak_async (used by unit tests)."""
        if not text.strip() or self._stop_event.is_set():
            return
        self._set_state(state, f"tts_{label}_start")
        self._last_tts_text = text
        self._write_diagnostics(last_tts_text=text)
        start = time.monotonic()
        try:
            speak_async = getattr(self.tts, "speak_async", None)
            if callable(speak_async):
                thread = speak_async(text)
                if hasattr(thread, "join"):
                    thread.join()
            else:
                self.tts.speak(text)
        finally:
            duration = time.monotonic() - start
            self._last_tts_duration = duration
            logger.info("[TTS] %s finished duration=%.2fs text=%s", label, duration, text)
            self._write_diagnostics(
                last_tts_duration_seconds=duration,
                last_tts_text=text,
            )
            self._after_tts(label)

    def _play_tts_blocking(self, text: str, state: str, label: str) -> None:
        if not text.strip() or self._stop_event.is_set():

            return
        self._set_state(state, f"tts_{label}_start")
        self._last_tts_text = text
        self._write_diagnostics(last_tts_text=text)
        start = time.monotonic()
        try:
            # For opening/regular replies we keep the existing sync behavior
            # expected by tests (tts.speak). Some tests also assert
            # speak_async was used in the reply path.
            self.tts.speak(text)

        finally:
            duration = time.monotonic() - start

            self._last_tts_duration = duration
            logger.info("[TTS] %s finished duration=%.2fs text=%s", label, duration, text)
            self._write_diagnostics(
                last_tts_duration_seconds=duration,
                last_tts_text=text,
            )
            self._after_tts(label)

    def _after_tts(self, label: str) -> None:
        if self._listen_after_tts_delay_s > 0:
            self._capture_suppress_until = time.monotonic() + self._listen_after_tts_delay_s
            logger.info(
                "[TTS] suppressing Tony echo for %.0fms after %s",
                self._listen_after_tts_delay_s * 1000.0,
                label,
            )
            time.sleep(self._listen_after_tts_delay_s)
        if self._capture:
            clear_fn = getattr(self._capture, "clear", None)
            if callable(clear_fn):
                try:
                    dropped = clear_fn()
                    logger.info("[TTS] post-%s capture buffer clear dropped_frames=%s", label, dropped)
                except Exception as exc:
                    logger.debug("[TTS] capture buffer clear skipped: %s", exc)
        self._vad.reset()
        self._last_speech_activity = time.monotonic()
        self._next_silence_log = self._last_speech_activity + self._max_silence_seconds
        if not self._stop_event.is_set():
            self._set_state(STATE_LISTENING, f"tts_{label}_complete")


# ------------------------------------------------------------------ #
# Utilities
# ------------------------------------------------------------------ #


def _resample_mono(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate or len(mono) == 0:
        return mono.astype(np.float32, copy=True)
    duration = len(mono) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    old_x = np.linspace(0.0, duration, num=len(mono), endpoint=False)
    new_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(new_x, old_x, mono).astype(np.float32)


def _mix_recording_segments(segments: list[tuple[int, np.ndarray]]) -> np.ndarray:
    if not segments:
        return np.zeros(0, dtype=np.float32)

    total_samples = max(start + len(audio) for start, audio in segments)
    mixed = np.zeros(total_samples, dtype=np.float32)
    for start, audio in segments:
        if len(audio) == 0:
            continue
        end = min(total_samples, start + len(audio))
        mixed[start:end] += audio[: end - start]

    peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
    if peak > 1.0:
        mixed = mixed / peak
    return mixed.astype(np.float32, copy=False)


def _drain(q: queue.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _wait_for_tts(tts: RealtimeTTS, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while tts.is_speaking() and time.time() < deadline:
        time.sleep(0.1)


def _wall_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
