from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.call_session import CallSession, CallState
from src.conversation_loop import ConversationLoop
from src.main import _run_realtime_loop
from src.voicemail_detector import VoicemailDetector


class _FakeLoop:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self._stopped = False

    def run(self, session=None, opening_line=None, auto_opening=True):
        session.transition(CallState.VOICEMAIL, "test voicemail beep")
        self._stopped = True

    def is_stopped(self):
        return self._stopped

    def stop(self):
        self._stopped = True


def test_realtime_voicemail_detection_plays_voicemail_audio():
    session = CallSession(phone="+15551234567", contact_name="Test Carrier")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)

    browser = MagicMock()
    browser.is_call_active.return_value = True
    voicemail_path = Path("audio/voicemails/test_voicemail.wav")

    with patch("src.realtime_tts.validate_tts_output_device"), \
         patch("src.voice_playback.describe_audio_device", return_value="Cable Input"), \
         patch("src.realtime_tts.RealtimeTTS"), \
         patch("src.conversation_agent.ConversationAgent"), \
         patch("src.stt.GroqWhisperSTT"), \
         patch("src.conversation_loop.ConversationLoop", _FakeLoop), \
         patch("src.main._play_audio", return_value=True) as play_audio:
        _run_realtime_loop(
            session=session,
            contact_name="Test Carrier",
            groq_api_key="test_key",
            groq_model="llama-3.3-70b-versatile",
            agent_name="Tony",
            company_name="Indus Transports LLC",
            company_context="dispatch services",
            company_website="",
            callback_number="+15550000000",
            loopback_device_index=1,
            capture_device="default",
            tts_engine="piper",
            tts_voice="en-US-GuyNeural",
            piper_model_path="models/piper/en_US-lessac-medium.onnx",
            piper_length_scale=1.0,
            piper_noise_scale=0.667,
            piper_noise_w_scale=0.8,
            stt_model="whisper-large-v3-turbo",
            vad_threshold=0.015,
            opening_line="Hi",
            voicemail_path=voicemail_path,
            browser=browser,
            loopback_device="CABLE Input",
            loopback_available=True,
            call_max_duration=30,
            logger=MagicMock(),
            voicemail_detect_seconds=7.0,
        )

    assert play_audio.call_args.args[:3] == (voicemail_path, "CABLE Input", True)
    browser.hangup_call.assert_called_once()
    assert session.state == CallState.ENDED
    assert any("voicemail" in n.lower() for n in session.notes)


def test_realtime_loop_drops_on_the_first_beep():
    session = CallSession(phone="+15551234567", contact_name="Test Carrier")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)

    loop = ConversationLoop.__new__(ConversationLoop)
    loop._voicemail_detected = False
    loop._vm_human_detected = False
    loop._vm_answer_started_monotonic = 100.0
    loop._vm_greeting_hits = 0
    loop._vm_greeting_frames_required = 6
    loop._voicemail_play_on_greeting = True
    loop._vm_latest_transcript = ""
    loop._vm_candidate_confirmations = 0
    loop._voicemail_detector = SimpleNamespace(
        continuous_greeting_duration_seconds=5.0,
        trailing_silence_seconds=0.0,
        beep_ready=True,
        machine_pattern_detected=False,
    )
    loop._voicemail_evidence_detector = VoicemailDetector()
    loop._session = session
    loop.stop = MagicMock()

    assert loop._evaluate_voicemail_evidence(108.0) is True

    assert session.state == CallState.VOICEMAIL
    assert session.outcome == "voicemail"
    loop.stop.assert_called_once()


def test_beep_overrides_an_early_hello():
    session = CallSession(phone="+15551234567", contact_name="Test Carrier")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)

    loop = ConversationLoop.__new__(ConversationLoop)
    loop._voicemail_detected = False
    loop._vm_human_detected = True
    loop._vm_answer_started_monotonic = 100.0
    loop._vm_greeting_hits = 0
    loop._vm_greeting_frames_required = 6
    loop._voicemail_play_on_greeting = True
    loop._vm_latest_transcript = "Hi"
    loop._vm_candidate_confirmations = 0
    loop._voicemail_detector = SimpleNamespace(
        continuous_greeting_duration_seconds=0.4,
        trailing_silence_seconds=0.0,
        beep_ready=True,
        machine_pattern_detected=False,
    )
    loop._voicemail_evidence_detector = VoicemailDetector()
    loop._session = session
    loop.stop = MagicMock()
    loop.tts = MagicMock()

    assert loop._evaluate_voicemail_evidence(108.0) is True
    assert session.state == CallState.VOICEMAIL


def test_saved_greeting_alone_does_not_drop_a_live_hello():
    session = CallSession(phone="+15551234567", contact_name="Live Person")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)

    loop = ConversationLoop.__new__(ConversationLoop)
    loop._voicemail_detected = False
    loop._vm_human_detected = False
    loop._vm_answer_started_monotonic = 100.0
    loop._vm_greeting_hits = 0
    loop._vm_greeting_frames_required = 6
    loop._voicemail_play_on_greeting = True
    loop._vm_latest_transcript = ""
    loop._vm_candidate_confirmations = 0
    loop._greeting_matched = True
    loop._dom_voicemail = False
    loop._last_voicemail_classifier_label = "live_call"
    loop._voicemail_detector = SimpleNamespace(
        continuous_greeting_duration_seconds=0.7,
        trailing_silence_seconds=0.3,
        beep_ready=False,
        machine_pattern_detected=False,
    )
    loop._voicemail_evidence_detector = VoicemailDetector()
    loop._session = session
    loop.stop = MagicMock()
    loop.tts = MagicMock()
    loop._screening_spoken = False
    loop._screening_cleared = False
    loop._screening_hold_until = 0.0

    assert loop._evaluate_voicemail_evidence(100.8) is False
    assert loop._voicemail_detected is False
    loop.stop.assert_not_called()
    assert session.state == CallState.CONNECTED


def test_short_inbound_burst_locks_human_before_fingerprint_can_drop():
    loop = ConversationLoop.__new__(ConversationLoop)
    loop._voicemail_detected = False
    loop._vm_human_detected = False
    loop._vm_latest_transcript = ""
    loop._vm_candidate_confirmations = 0
    loop._greeting_matched = True
    loop._response_active = SimpleNamespace(is_set=lambda: False)
    loop._speech_q = MagicMock()
    loop._speech_q.full.return_value = False
    loop._min_speech_seconds = 0.3
    loop._set_state = MagicMock()
    loop._write_diagnostics = MagicMock()
    loop._write_speech_segment = MagicMock(return_value="")
    loop._kick_early_voicemail_stt = MagicMock()
    loop._last_inbound_segment = None
    loop._last_segment_path = ""
    loop._last_speech_activity = 0.0
    loop._next_silence_log = 0.0
    loop._max_silence_seconds = 8.0

    import numpy as np

    segment = np.zeros(int(16000 * 0.72), dtype=np.float32)
    loop._handle_speech_segment(segment, ended_monotonic=10.0)

    assert loop._vm_human_detected is True
    assert loop._greeting_matched is False
