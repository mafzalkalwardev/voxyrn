import wave

import numpy as np
import pytest

from src import realtime_tts


def test_unknown_tts_engine_is_rejected(monkeypatch):
    monkeypatch.setattr(realtime_tts, "_edge_available", lambda: True)

    with pytest.raises(ValueError, match="Unsupported TTS_ENGINE"):
        realtime_tts.RealtimeTTS(device_index=0, engine="unknown")


def test_piper_wav_is_decoded_to_float32(monkeypatch, tmp_path):
    class FakeVoice:
        def synthesize_wav(self, text, wav_file):
            assert text == "Hello"
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(np.zeros(2205, dtype=np.int16).tobytes())

    data, rate = realtime_tts._piper_synthesize("Hello", FakeVoice())

    assert data.dtype == np.float32
    assert data.shape == (2205,)
    assert rate == 22050


def test_piper_text_removes_labels_and_normalizes_pauses():
    text = realtime_tts._prepare_piper_text("Assistant: Hi... there — thanks **so much**.")

    assert text == "Hi, there, thanks so much."


def test_piper_prosody_bounds_reject_extreme_values():
    with pytest.raises(ValueError, match="piper_length_scale"):
        realtime_tts._bounded_float(2.0, 0.75, 1.35, "piper_length_scale")


def test_local_piper_model_synthesizes_speech():
    from pathlib import Path

    model = Path("models/piper/en_US-lessac-medium.onnx")
    if not model.is_file() or not Path(str(model) + ".json").is_file():
        pytest.skip("local Piper voice model is not installed")

    voice = realtime_tts._load_piper_voice(model)
    data, rate = realtime_tts._piper_synthesize("Hello from Voxyrn.", voice)

    assert data.dtype == np.float32
    assert data.ndim == 1
    assert data.size > 1000
    assert rate >= 16000
    assert float(np.max(np.abs(data))) > 0.01
