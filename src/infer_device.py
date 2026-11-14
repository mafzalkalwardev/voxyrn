"""Choose CPU or CUDA for local speech and the local answer model.

The audio gate itself is Rust. CUDA is used for model math only when this
machine has an NVIDIA GPU. The ARM server does not, so it stays on int8 CPU.
"""
from __future__ import annotations

import os
import shutil
import subprocess


def _cuda_disabled() -> bool:
    return os.getenv("VOXYRN_CUDA", "auto").strip().lower() in {"0", "false", "off", "cpu"}


def _gpu_lines() -> list[str]:
    if _cuda_disabled():
        return []
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        out = subprocess.check_output(
            [smi, "-L"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in out.splitlines() if line.strip().lower().startswith("gpu ")]


def inference_plan() -> dict:
    """Runtime device for faster-whisper and the local GGUF model."""
    requested = os.getenv("STT_COMPUTE_TYPE", "").strip()
    if _gpu_lines():
        return {
            "device": "cuda",
            "compute_type": requested or "float16",
            "gpu_layers": -1,
            "backend": "cuda",
        }
    return {
        "device": "cpu",
        "compute_type": requested or "int8",
        "gpu_layers": 0,
        "backend": "cpu",
    }
