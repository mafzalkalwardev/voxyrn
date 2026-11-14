"""The realtime gate is the Rust core when that module is built."""
import numpy as np

from src.infer_device import inference_plan
from src.rust_core import CallEngine, chunk_reply, load_error, native_implementation, native_library_path


def test_barge_in_uses_the_capture_threshold():
    engine = CallEngine()
    loud = np.full(320, 0.2, dtype=np.float32)
    decision = engine.push(loud, agent_speaking=True, threshold=0.02)
    assert decision["barge_in"] is True
    quiet = np.zeros(320, dtype=np.float32)
    assert engine.push(quiet, agent_speaking=True, threshold=0.02)["barge_in"] is False
    assert engine.memory_bytes() == 8 * 16_000 * 4


def test_short_burst_then_silence_is_a_person():
    engine = CallEngine()
    speech = np.full(int(0.6 * 16_000), 0.2, dtype=np.float32)
    silence = np.zeros(int(0.5 * 16_000), dtype=np.float32)
    engine.push(speech, agent_speaking=False, threshold=0.02)
    decision = engine.push(silence, agent_speaking=False, threshold=0.02)
    assert decision["first_turn"] == "human"


def test_reply_starts_on_the_first_sentence():
    assert chunk_reply("Got it. Let me check that for you.") == [
        "Got it.",
        "Let me check that for you.",
    ]
    assert chunk_reply("It is 1.5 percent, then we send the lanes.") == [
        "It is 1.5 percent, then we send the lanes.",
    ]


def test_native_module_loads_when_it_was_built():
    path = native_library_path()
    if path is None:
        assert native_implementation() == "python"
        return
    assert load_error() is None
    assert native_implementation() == "rust"
    engine = CallEngine()
    frame = np.full(320, 0.2, dtype=np.float32)
    assert engine.push(frame, agent_speaking=False, threshold=0.02)["implementation"] == "rust"
    assert engine.push(frame, agent_speaking=False, threshold=0.02)["backend"] == "cpu"


def test_cuda_is_selected_only_when_a_gpu_is_listed(monkeypatch):
    monkeypatch.delenv("STT_COMPUTE_TYPE", raising=False)
    monkeypatch.delenv("VOXYRN_CUDA", raising=False)
    monkeypatch.setattr("src.infer_device.shutil.which", lambda _name: None)
    cpu = inference_plan()
    assert cpu == {"device": "cpu", "compute_type": "int8", "gpu_layers": 0, "backend": "cpu"}

    monkeypatch.setattr("src.infer_device.shutil.which", lambda _name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        "src.infer_device.subprocess.check_output",
        lambda *_args, **_kwargs: "GPU 0: Example (UUID: GPU-test)\n",
    )
    gpu = inference_plan()
    assert gpu["device"] == "cuda"
    assert gpu["compute_type"] == "float16"
    assert gpu["gpu_layers"] == -1

    monkeypatch.setenv("VOXYRN_CUDA", "0")
    assert inference_plan()["device"] == "cpu"
