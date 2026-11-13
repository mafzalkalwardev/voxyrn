"""Saved mailbox greetings match later calls and ignore a different voice."""

import numpy as np

from src.greeting_library import GreetingLibrary


def _tone(freq: float, seconds: float = 1.8) -> np.ndarray:
    sr = 16000
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_saved_greeting_matches_itself_and_not_a_different_tone(tmp_path):
    library = GreetingLibrary(tmp_path)
    original = _tone(1000)
    assert library.save(original) is True
    assert library.matches(original) is True
    assert library.save(original) is False
    assert library.matches(_tone(280)) is False

    reloaded = GreetingLibrary(tmp_path)
    assert reloaded.matches(original) is True
    assert reloaded.matches(np.zeros(16000, dtype=np.float32)) is False
