"""Tests for live-call Windows/Chrome audio auto-setup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.audio_setup import (
    AudioEndpoint,
    AudioSetupResult,
    _name_score,
    ensure_live_call_audio,
    patch_chrome_mic_prefs,
    resolve_live_route,
)


def test_name_score_matches_cable_output_friendly_and_interface():
    assert _name_score("CABLE Output | VB-Audio Virtual Cable", "CABLE Output") >= 80
    assert _name_score("VB-Audio Virtual Cable", "CABLE Output") >= 70
    assert _name_score("Speakers | Realtek(R) Audio", "CABLE Output") < 0


def test_patch_chrome_mic_prefs_grants_voice_google(tmp_path: Path):
    profile = tmp_path / "sales_profile"
    assert patch_chrome_mic_prefs(profile) is True
    prefs = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
    mic = prefs["profile"]["content_settings"]["exceptions"]["media_stream_mic"]
    assert "https://voice.google.com:443,*" in mic
    assert mic["https://voice.google.com:443,*"]["setting"] == 1


def test_ensure_live_call_audio_sets_devices_and_capture_default(tmp_path: Path):
    mic = AudioEndpoint(
        flow="Capture",
        guid="{aaa}",
        name="CABLE Output | VB-Audio Virtual Cable",
        device_id="{0.0.1.00000000}.{aaa}",
        state=1,
    )
    speaker = AudioEndpoint(
        flow="Render",
        guid="{bbb}",
        name="Speakers | Realtek(R) Audio",
        device_id="{0.0.0.00000000}.{bbb}",
        state=1,
    )
    set_calls: list[str] = []

    def fake_set(device_id: str, label: str = "") -> None:
        set_calls.append(device_id)

    with (
        patch("src.audio_setup.sys.platform", "win32"),
        patch("src.audio_setup.resolve_live_route", return_value=("CABLE Input", "default", mic, speaker)),
        patch("src.audio_setup.set_windows_default_device", side_effect=fake_set),
    ):
        result = ensure_live_call_audio(
            loopback_hint="CABLE Input",
            capture_hint="CABLE Output",  # must be forced back to default
            chrome_profile_dir=tmp_path / "profile",
            set_windows_devices=True,
        )

    assert isinstance(result, AudioSetupResult)
    assert result.ok is True
    assert result.mic_set is True
    assert result.speaker_set is True
    assert result.capture_hint == "default"
    assert mic.device_id in set_calls
    assert speaker.device_id in set_calls
    assert result.chrome_prefs_ok is True


def test_resolve_live_route_uses_safe_capture():
    with (
        patch(
            "src.audio_setup.find_endpoint",
            return_value=AudioEndpoint("Capture", "{x}", "CABLE Output", "{0.0.1.00000000}.{x}", 1),
        ),
        patch(
            "src.audio_setup.find_speaker_endpoint",
            return_value=AudioEndpoint("Render", "{y}", "Speakers", "{0.0.0.00000000}.{y}", 1),
        ),
    ):
        loopback, capture, mic, speaker = resolve_live_route("CABLE Input", "CABLE Output")
    assert loopback == "CABLE Input"
    assert capture == "default"
    assert mic is not None
    assert speaker is not None
