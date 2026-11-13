"""Dead air hangs up. A name-and-reason prompt stays on the line."""

import threading
import time
from unittest.mock import MagicMock, patch

from src.call_session import CallSession, CallState
from src.conversation_loop import ConversationLoop


def test_silent_answered_line_hangs_up_without_a_pitch():
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):
        capture = MagicMock()
        capture.read.return_value = None
        capture.last_error = None
        MockCapture.return_value = capture

        tts = MagicMock()
        tts.is_speaking.return_value = False
        agent = MagicMock()
        agent.opening_line.return_value = "Hi, this is Alex."
        loop = ConversationLoop(
            capture_device_hint="default",
            tts=tts,
            agent=agent,
            stt=MagicMock(),
            wait_for_human_audio=False,
            answered_speak_delay=0.0,
            dead_air_seconds=0.25,
            listen_after_tts_delay_ms=0,
            greeting_library_dir=None,
        )
        session = CallSession(phone="+15550001111", contact_name="Quiet")
        session.transition(CallState.DIALING)
        session.transition(CallState.CONNECTED)

        def stop_if_stuck():
            time.sleep(3.0)
            loop.stop()

        threading.Thread(target=stop_if_stuck, daemon=True).start()
        loop.run(session=session, auto_opening=True)

        tts.speak.assert_not_called()
        assert session.outcome == "no_answer"
        assert session.state == CallState.ENDED


def test_screening_prompt_says_the_campaign_reason_and_stays_up():
    tts = MagicMock()
    tts.is_speaking.return_value = False
    agent = MagicMock()
    agent.agent_name = "Alex"
    agent.company_name = "Voxyrn"
    agent.company_context = (
        "Voxyrn helps businesses automate outbound sales calls and follow-up."
    )
    loop = ConversationLoop(
        capture_device_hint="default",
        tts=tts,
        agent=agent,
        stt=MagicMock(),
        wait_for_human_audio=False,
        listen_after_tts_delay_ms=0,
    )
    session = CallSession(phone="+15550002222", contact_name="Gate")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)
    loop._session = session

    loop._speak_screening_line()

    spoken = tts.speak.call_args.args[0]
    assert spoken.startswith("Alex from Voxyrn, calling about Voxyrn helps")
    assert "freight" not in spoken.lower()
    assert loop._screening_spoken is True
    assert session.state == CallState.CONNECTED
