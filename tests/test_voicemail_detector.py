"""Tests for voicemail audio classifier."""

import numpy as np

from src.voicemail_detector import (
    BeepDetector,
    VoicemailAudioClassifier,
    VoicemailDetector,
    campaign_reason,
)


def test_beep_detector_on_tone():
    sr = 16000
    duration = 0.5
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    det = BeepDetector()
    frame_len = int(sr * 0.03)
    found = False
    for i in range(0, len(tone) - frame_len, frame_len):
        if det.process_frame(tone[i : i + frame_len], samplerate=sr):
            found = True
            break
    assert found


def test_classifier_reset():
    clf = VoicemailAudioClassifier()
    clf.reset()
    assert clf.beep_ready is False


def test_silence_label():
    clf = VoicemailAudioClassifier()
    frame = np.zeros(480, dtype=np.float32)
    label = clf.process_frame(frame, samplerate=16000)
    assert label in ("silence", "uncertain", "live_call")


def test_beep_or_mailbox_phrase_is_enough():
    detector = VoicemailDetector()

    too_early = detector.classify(
        answer_elapsed_seconds=0.1,
        beep_detected=True,
    )
    beep = detector.classify(
        answer_elapsed_seconds=0.4,
        beep_detected=True,
    )
    phrase = detector.classify(
        answer_elapsed_seconds=1.0,
        transcript="Please leave a message after the beep",
    )

    assert too_early.candidate is False
    assert beep.candidate is True
    assert beep.confidence >= 0.9
    assert phrase.candidate is True


def test_mailbox_phrase_overrides_an_early_hello():
    overridden = VoicemailDetector().classify(
        transcript="Hi, you've reached Sam. Please leave a message after the beep",
        answer_elapsed_seconds=2.0,
        beep_detected=True,
        human_detected=True,
    )
    hello = VoicemailDetector().classify(
        transcript="Hello, who is this",
        answer_elapsed_seconds=1.0,
        continuous_greeting_duration_seconds=0.8,
        human_detected=True,
    )

    assert overridden.candidate is True
    assert overridden.confidence >= 0.9
    assert hello.candidate is False
    assert hello.confidence <= 0.35


def test_callback_line_needs_a_long_greeting():
    short = VoicemailDetector().classify(
        transcript="I'll call you back",
        answer_elapsed_seconds=1.2,
        continuous_greeting_duration_seconds=1.0,
    )
    long = VoicemailDetector().classify(
        transcript="I am unable to pick up. Leave your name and number and I will call you back",
        answer_elapsed_seconds=3.0,
        continuous_greeting_duration_seconds=3.2,
    )

    assert short.candidate is False
    assert long.candidate is True


def test_name_and_reason_prompt_is_not_voicemail():
    result = VoicemailDetector().classify(
        transcript="Please state your name and the reason for your call",
        answer_elapsed_seconds=2.0,
        continuous_greeting_duration_seconds=3.5,
    )

    assert result.candidate is False
    assert "call screening" in result.factors


def test_campaign_reason_uses_the_company_not_a_freight_default():
    reason = campaign_reason(
        "Voxyrn helps businesses automate outbound sales calls and follow-up.",
        "freight dispatch and load support",
    )
    assert reason.startswith("Voxyrn helps businesses")
    assert "freight" not in reason.lower()


def test_dom_cue_confirms_voicemail():
    result = VoicemailDetector().classify(
        answer_elapsed_seconds=0.5,
        dom_voicemail=True,
    )

    assert result.candidate is True
    assert "DOM voicemail cue" in result.factors


def test_carrier_telephone_number_prompt_is_mailbox():
    text = "telephone number 4174997435 can't take your call now"
    assert VoicemailDetector.is_carrier_mailbox_transcript(text)
    assert VoicemailDetector.keyword_count(text) >= 1

    result = VoicemailDetector().classify(
        transcript=text,
        answer_elapsed_seconds=1.0,
    )
    assert result.candidate is True
    assert result.confidence >= 0.9


def test_not_available_to_pick_your_call_is_mailbox():
    text = (
        "telephone number 4174997435 is not available to pick your call "
        "please leave your message after the tone"
    )
    assert VoicemailDetector.keyword_count(text) >= 2
    result = VoicemailDetector().classify(
        transcript=text,
        answer_elapsed_seconds=1.2,
    )
    assert result.candidate is True
