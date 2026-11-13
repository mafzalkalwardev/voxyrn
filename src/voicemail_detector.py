"""
Audio-based voicemail and beep detector for Google Voice dispatch calls.

This module provides two complementary detection methods:

1. **BeepDetector**: Analyses incoming audio for the characteristic voicemail
   beep tone (typically 800–1500 Hz for ~0.5–1 second).  Works by computing
   per-frame FFT magnitude and checking for sustained energy in the beep band.

2. **VoicemailAudioClassifier**: A rule-based classifier that combines energy,
   silence pattern, and beep detection to label a short audio window as
   "voicemail_greeting", "beep_detected", "live_call", or "uncertain".

Usage in ConversationLoop:
    detector = VoicemailAudioClassifier()
    while capturing_frames:
        result = detector.process_frame(frame, samplerate=16000)
        if result == "beep_detected":
            # start voicemail playback
        elif result == "voicemail_greeting":
            # wait for beep before speaking
        elif result == "live_call":
            # proceed with conversation
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

import numpy as np

_DIGIT_RUN = re.compile(r"\d[\d\s\-]{5,}\d")

logger = logging.getLogger("GoogleVoiceAgent")


VOICEMAIL_KEYWORDS = (
    "leave a message",
    "leave your message",
    "leave a voicemail",
    "leave your name and number",
    "leave your name",
    "leave your number",
    "please leave your message",
    "please leave a message",
    "after the tone",
    "after the beep",
    "at the tone",
    "at the talent",  # common Whisper mishear of "at the tone"
    "not available",
    "is not available",
    "not available to take",
    "not available to pick",
    "not available to answer",
    "unavailable to take",
    "unavailable to pick",
    "to pick up your call",
    "to pick your call",
    "mailbox",
    "your call has been forwarded",
    "please record your message",
    "record your message",
    "you've reached",
    "you have reached",
    "voicemail",
    "unable to pick",
    "unable to answer",
    "unable to take",
    "can't take your call",
    "cannot take your call",
    "cant take your call",
    "can't come to the phone",
    "cannot come to the phone",
    "not able to answer",
    "not able to take your call",
    "no one is available",
    "person you are trying to reach",
    "the person you called",
)

# Carrier / Google Voice style: "Telephone number 417… can't take your call"
_CARRIER_TELEPHONE_NUMBER = (
    "telephone number",
    "the number you have dialed",
    "the number you dialed",
    "the wireless customer",
)

# A live person can say this in one short sentence. It is a mailbox only
# inside a long greeting, or next to a real mailbox phrase.
CALLBACK_PHRASES = (
    "i will call you back",
    "i'll call you back",
    "i’ll call you back",
    "call you back",
)

# A person may still pick up after these. They are not a mailbox.
SCREENING_PHRASES = (
    "state your name",
    "say your name",
    "record your name",
    "reason for your call",
    "reason for calling",
    "reason of your call",
    "reason of the call",
    "reason for the call",
    "purpose of your call",
    "purpose of the call",
    "who's calling",
    "who is calling",
    "screening service",
    "call is being screened",
    "this call may be screened",
    "try to connect you",
    "name and the reason",
    "name and reason",
    "tell me the reason",
    "tell us the reason",
    "tell the reason",
)

_CALLBACK_MONOLOGUE_SECONDS = 2.5
_FREIGHT_SCREENING_DEFAULT = "freight dispatch and load support"


def campaign_reason(company_context: str, explicit: str = "") -> str:
    """One short reason to say when a screener asks who is calling."""
    text = " ".join((explicit or "").split()).strip()
    if text and text.lower() != _FREIGHT_SCREENING_DEFAULT:
        return text.rstrip(".,;")
    source = " ".join((company_context or "").replace("\n", " ").split()).strip()
    if not source:
        return "a quick follow-up"
    return " ".join(source.split()[:12]).rstrip(".,;")


@dataclass(frozen=True)
class VoicemailDetection:
    """Multi-signal voicemail result copied from the Auto Dialer detector."""

    candidate: bool
    confidence: float
    factors: list[str] = field(default_factory=list)


class VoicemailDetector:
    """Score transcript, timing, tone, machine-pattern, and DOM evidence."""

    def classify(
        self,
        *,
        transcript: str = "",
        answer_elapsed_seconds: float = 0.0,
        continuous_greeting_duration_seconds: float = 0.0,
        voicemail_keywords_detected_count: int = 0,
        beep_detected: bool = False,
        beep_confidence: float = 0.0,
        dom_voicemail: bool = False,
        repeated_machine_pattern: bool = False,
        human_detected: bool = False,
    ) -> VoicemailDetection:
        factors: list[str] = []
        text_keywords = self.keyword_count(transcript)
        keyword_count = max(int(voicemail_keywords_detected_count or 0), text_keywords)

        if continuous_greeting_duration_seconds >= 4.0:
            factors.append("long scripted greeting")
        if keyword_count > 0:
            factors.append("voicemail keyword")
        if self.callback_phrase(transcript) and (
            continuous_greeting_duration_seconds >= _CALLBACK_MONOLOGUE_SECONDS
            or keyword_count > 0
        ):
            factors.append("personal callback greeting")
        if beep_detected or beep_confidence >= 0.6:
            factors.append("beep tone")
        if repeated_machine_pattern:
            factors.append("machine-like pattern")
        if dom_voicemail:
            factors.append("DOM voicemail cue")

        confidence = min(1.0, (len(factors) / 2.0) * 0.5)
        if "beep tone" in factors:
            confidence = min(1.0, confidence + 0.35)
        if "voicemail keyword" in factors or "personal callback greeting" in factors:
            confidence = min(1.0, confidence + 0.20)

        # A screener asking for a name is not a mailbox, unless a beep or a
        # mailbox phrase is already in the same audio.
        if (
            self.is_screening_transcript(transcript)
            and "beep tone" not in factors
            and "voicemail keyword" not in factors
        ):
            return VoicemailDetection(
                candidate=False,
                confidence=0.15,
                factors=["call screening"],
            )

        # A beep, a mailbox phrase, or an on-screen cue overrides an early "hi".
        if ("beep tone" in factors) and answer_elapsed_seconds >= 0.3:
            return VoicemailDetection(candidate=True, confidence=0.95, factors=factors)
        if ("voicemail keyword" in factors) and answer_elapsed_seconds >= 0.8:
            return VoicemailDetection(candidate=True, confidence=0.9, factors=factors)
        if (
            "personal callback greeting" in factors
            and answer_elapsed_seconds >= 0.8
            and continuous_greeting_duration_seconds >= _CALLBACK_MONOLOGUE_SECONDS
        ):
            return VoicemailDetection(candidate=True, confidence=0.9, factors=factors)
        if ("DOM voicemail cue" in factors) and answer_elapsed_seconds >= 0.3:
            return VoicemailDetection(candidate=True, confidence=0.9, factors=factors)

        if human_detected:
            return VoicemailDetection(candidate=False, confidence=min(confidence, 0.35), factors=factors)

        candidate = (
            answer_elapsed_seconds >= 2.5
            and len(factors) >= 2
            and confidence >= 0.75
        )
        return VoicemailDetection(candidate=candidate, confidence=confidence, factors=factors)

    @staticmethod
    def keyword_count(text: str) -> int:
        lower = (text or "").lower()
        count = sum(1 for phrase in VOICEMAIL_KEYWORDS if phrase in lower)
        if VoicemailDetector.is_carrier_mailbox_transcript(lower):
            count += 1
        return count

    @staticmethod
    def is_carrier_mailbox_transcript(text: str) -> bool:
        """True for carrier prompts like 'Telephone number 417… can't take your call'."""
        lower = (text or "").lower()
        if not lower:
            return False
        has_number_cue = any(cue in lower for cue in _CARRIER_TELEPHONE_NUMBER)
        has_digits = bool(_DIGIT_RUN.search(lower))
        if has_number_cue and has_digits:
            return True
        if has_number_cue and any(
            phrase in lower
            for phrase in (
                "can't take",
                "cannot take",
                "cant take",
                "not available",
                "leave your message",
                "leave a message",
                "after the tone",
                "at the tone",
            )
        ):
            return True
        return False

    @staticmethod
    def callback_phrase(text: str) -> bool:
        lower = (text or "").lower()
        return any(phrase in lower for phrase in CALLBACK_PHRASES)

    @staticmethod
    def is_screening_transcript(text: str) -> bool:
        lower = (text or "").lower()
        if not lower or VoicemailDetector.keyword_count(lower) > 0:
            return False
        return any(phrase in lower for phrase in SCREENING_PHRASES)

# ---------------------------------------------------------------------------
# Beep frequency range
# Most carrier voicemail beeps are in 800–1500 Hz.  Some older systems use
# 350 Hz + 440 Hz dial tones.  We cover a broad range and require duration.
# ---------------------------------------------------------------------------
_BEEP_LOW_HZ = 700
_BEEP_HIGH_HZ = 1600
_BEEP_MIN_DURATION_S = 0.35  # beep must last at least 350 ms
_BEEP_ENERGY_RATIO = 0.55    # beep band must have ≥55% of total frame energy

# ---------------------------------------------------------------------------
# Voicemail greeting pattern
# A greeting typically has: sustained speech (6–25 s) followed by silence,
# then a beep.  We detect the silence-before-beep window.
# ---------------------------------------------------------------------------
_VM_SILENCE_FRAMES_REQUIRED = 8   # consecutive silence frames ≈ 240 ms
_VM_SPEECH_FRAMES_REQUIRED = 20   # at least 600 ms of prior speech
_VM_WINDOW_SECONDS = 30.0         # max window to wait for beep after greeting


@dataclass
class BeepDetectorConfig:
    samplerate: int = 16000
    frame_ms: int = 30
    beep_low_hz: float = _BEEP_LOW_HZ
    beep_high_hz: float = _BEEP_HIGH_HZ
    min_duration_s: float = _BEEP_MIN_DURATION_S
    energy_ratio_threshold: float = _BEEP_ENERGY_RATIO
    speech_rms_threshold: float = 0.01   # RMS to consider frame "active"


class BeepDetector:
    """
    Detect single-tone voicemail beep using FFT energy analysis.

    Feed audio frames one at a time via `process_frame()`.
    Returns True on the first frame that completes a confirmed beep.
    """

    def __init__(self, config: Optional[BeepDetectorConfig] = None) -> None:
        self.config = config or BeepDetectorConfig()
        self._beep_frames_active = 0
        self._beep_confirmed = False
        self._reset_beep()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def beep_confirmed(self) -> bool:
        return self._beep_confirmed

    def process_frame(self, frame: np.ndarray, samplerate: Optional[int] = None) -> bool:
        """
        Process one audio frame.
        Returns True the instant a beep is confirmed (one-shot).
        """
        sr = samplerate or self.config.samplerate
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

        if rms < self.config.speech_rms_threshold:
            # Silence — reset beep counter
            self._beep_frames_active = 0
            return False

        is_beep = self._is_beep_frame(frame, sr)
        if is_beep:
            self._beep_frames_active += 1
            frames_needed = int(
                self.config.min_duration_s * 1000 / self.config.frame_ms
            )
            if self._beep_frames_active >= frames_needed and not self._beep_confirmed:
                self._beep_confirmed = True
                logger.info(
                    "[BEEP] Voicemail beep confirmed (%d frames, %.0f Hz band)",
                    self._beep_frames_active,
                    (self.config.beep_low_hz + self.config.beep_high_hz) / 2,
                )
                return True
        else:
            self._beep_frames_active = 0

        return False

    def reset(self) -> None:
        self._reset_beep()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _reset_beep(self) -> None:
        self._beep_frames_active = 0
        self._beep_confirmed = False

    def _is_beep_frame(self, frame: np.ndarray, samplerate: int) -> bool:
        """
        Return True if the frame's FFT shows the majority of energy in the beep band.
        """
        n = len(frame)
        if n < 64:
            return False

        # Hamming window to reduce spectral leakage
        windowed = frame * np.hamming(n)
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(n, d=1.0 / samplerate)

        if len(spectrum) < 2 or spectrum.sum() == 0:
            return False

        # Energy in beep band vs total
        mask_beep = (freqs >= self.config.beep_low_hz) & (freqs <= self.config.beep_high_hz)
        energy_beep = float(spectrum[mask_beep].sum())
        energy_total = float(spectrum.sum())

        ratio = energy_beep / energy_total if energy_total > 0 else 0.0
        return ratio >= self.config.energy_ratio_threshold


# ---------------------------------------------------------------------------
# High-level voicemail audio classifier
# ---------------------------------------------------------------------------

_VM_LABEL_LIVE = "live_call"
_VM_LABEL_GREETING = "voicemail_greeting"
_VM_LABEL_BEEP = "beep_detected"
_VM_LABEL_UNCERTAIN = "uncertain"
_VM_LABEL_SILENCE = "silence"


class VoicemailAudioClassifier:
    """
    Rule-based classifier that combines:
    - Sustained low-energy speech bursts (typical of pre-recorded greetings)
    - Silence patterns between greeting and beep
    - Actual beep detection via BeepDetector

    Call `process_frame()` for every audio frame captured from the call.
    Returns one of: "live_call", "voicemail_greeting", "beep_detected", "uncertain", "silence".

    After `beep_detected` is returned, `beep_ready` is set to True and the
    caller should start voicemail playback immediately.
    """

    def __init__(
        self,
        samplerate: int = 16000,
        frame_ms: int = 30,
        speech_rms_threshold: float = 0.01,
        vm_window_seconds: float = _VM_WINDOW_SECONDS,
    ) -> None:
        self._sr = samplerate
        self._frame_ms = frame_ms
        self._speech_rms = speech_rms_threshold
        self._vm_window = vm_window_seconds

        cfg = BeepDetectorConfig(
            samplerate=samplerate,
            frame_ms=frame_ms,
            speech_rms_threshold=speech_rms_threshold,
        )
        self._beep = BeepDetector(cfg)

        self._speech_frames: int = 0
        self._continuous_speech_frames: int = 0
        self._longest_speech_frames: int = 0
        self._silence_frames: int = 0
        self._start_time: float = time.monotonic()
        self._last_label: str = _VM_LABEL_UNCERTAIN
        self.beep_ready: bool = False
        self.machine_pattern_detected: bool = False

        # Sliding window of RMS values for pattern analysis
        self._rms_window: Deque[float] = deque(maxlen=200)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def process_frame(self, frame: np.ndarray, samplerate: Optional[int] = None) -> str:
        """
        Feed one audio frame. Returns current classification label.
        """
        sr = samplerate or self._sr
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
        self._rms_window.append(rms)

        # Check beep first (highest priority)
        if not self.beep_ready and self._beep.process_frame(frame, samplerate=sr):
            self.beep_ready = True
            self._last_label = _VM_LABEL_BEEP
            return _VM_LABEL_BEEP

        if rms >= self._speech_rms:
            self._speech_frames += 1
            self._continuous_speech_frames += 1
            self._longest_speech_frames = max(
                self._longest_speech_frames,
                self._continuous_speech_frames,
            )
            self._silence_frames = 0
        else:
            self._continuous_speech_frames = 0
            self._silence_frames += 1

        label = self._classify()
        self._last_label = label
        return label

    def reset(self) -> None:
        """Reset state for a new call."""
        self._beep.reset()
        self._speech_frames = 0
        self._continuous_speech_frames = 0
        self._longest_speech_frames = 0
        self._silence_frames = 0
        self._start_time = time.monotonic()
        self._last_label = _VM_LABEL_UNCERTAIN
        self.beep_ready = False
        self.machine_pattern_detected = False
        self._rms_window.clear()

    @property
    def continuous_greeting_duration_seconds(self) -> float:
        """Longest uninterrupted active-audio run observed in this call."""
        return self._longest_speech_frames * self._frame_ms / 1000.0

    @property
    def trailing_silence_seconds(self) -> float:
        return self._silence_frames * self._frame_ms / 1000.0

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _classify(self) -> str:
        elapsed = time.monotonic() - self._start_time

        if elapsed > self._vm_window:
            # Too long to still be a voicemail — likely a long live call
            return _VM_LABEL_LIVE

        if self._speech_frames < _VM_SPEECH_FRAMES_REQUIRED:
            # Not enough speech yet to classify
            return _VM_LABEL_UNCERTAIN

        # Enough prior speech + current silence → voicemail greeting likely done
        if self._silence_frames >= _VM_SILENCE_FRAMES_REQUIRED:
            return _VM_LABEL_GREETING

        # Look for monotone energy pattern (pre-recorded greeting tends to be
        # more consistent than live speech)
        if len(self._rms_window) >= 30:
            rms_arr = np.array(list(self._rms_window))
            cv = rms_arr.std() / (rms_arr.mean() + 1e-9)
            # Low coefficient of variation = consistent = likely pre-recorded
            if cv < 0.45 and self._speech_frames > 40:
                self.machine_pattern_detected = True
                return _VM_LABEL_GREETING

        return _VM_LABEL_LIVE


# ---------------------------------------------------------------------------
# Convenience: audio-based beep wait utility
# ---------------------------------------------------------------------------

def wait_for_audio_beep(
    capture,  # AudioCapture instance
    timeout: float = 35.0,
    samplerate: int = 16000,
    on_status: Optional[callable] = None,
) -> bool:
    """
    Wait for a voicemail beep by analysing incoming audio from `capture`.

    capture   — AudioCapture instance with a `read(timeout)` method
    timeout   — max seconds to wait
    on_status — optional callback(label: str) called every classified frame

    Returns True when beep is detected, False on timeout.
    """
    detector = BeepDetector(BeepDetectorConfig(samplerate=samplerate))
    deadline = time.monotonic() + timeout
    frames_seen = 0

    logger.info("[BEEP_WAIT] Listening for voicemail beep (timeout=%.1fs)", timeout)

    while time.monotonic() < deadline:
        frame = capture.read(timeout=0.05) if capture else None
        if frame is None:
            continue
        frame_arr = np.asarray(frame, dtype=np.float32)
        frames_seen += 1
        if detector.process_frame(frame_arr, samplerate=samplerate):
            logger.info(
                "[BEEP_WAIT] Beep confirmed after %d frames (%.1fs)",
                frames_seen,
                frames_seen * 0.03,
            )
            return True

    logger.info(
        "[BEEP_WAIT] Beep not detected in %.1fs (%d frames); proceeding",
        timeout,
        frames_seen,
    )
    return False
