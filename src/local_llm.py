"""On-device chat model. No network calls."""
from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_LLM = None
_LOADED_PATH = ""


class _Message:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Message(content)


class _Completion:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


def _fit_messages(messages: list, max_chars: int = 6000) -> list:
    """Keep the system prompt and the newest turns inside the local context."""
    system = [message for message in messages if message.get("role") == "system"]
    rest = [message for message in messages if message.get("role") != "system"]
    used = sum(len(str(message.get("content") or "")) for message in system)
    kept: list = []
    for message in reversed(rest):
        size = len(str(message.get("content") or ""))
        if kept and used + size > max_chars:
            break
        kept.append(message)
        used += size
    kept.reverse()
    return [*system, *kept]


def resolve_model_path(path: str | None = None) -> Path:
    raw = path or os.getenv(
        "LOCAL_LLM_PATH",
        "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    )
    model = Path(raw).expanduser()
    if not model.is_absolute():
        model = Path(__file__).resolve().parent.parent / model
    return model


def get_llama(path: str | None = None):
    """Load the GGUF once and reuse it for scripts and live replies."""
    global _LLM, _LOADED_PATH
    model = resolve_model_path(path)
    if not model.is_file():
        raise FileNotFoundError(
            f"Local answer model was not found: {model}. "
            "Download qwen2.5-1.5b-instruct-q4_k_m.gguf into models/llm."
        )
    with _LOCK:
        if _LLM is not None and _LOADED_PATH == str(model):
            return _LLM
        from llama_cpp import Llama

        # Leave cores for Chromium and faster-whisper. LLM_THREADS defaults to 2.
        try:
            threads = int(os.getenv("LLM_THREADS", "2"))
        except ValueError:
            threads = 2
        threads = max(1, min(threads, int(os.cpu_count() or 2)))
        try:
            ctx = int(os.getenv("LOCAL_LLM_CTX", "2048"))
        except ValueError:
            ctx = 2048
        try:
            batch = int(os.getenv("LOCAL_LLM_BATCH", "256"))
        except ValueError:
            batch = 256

        _LLM = Llama(
            model_path=str(model),
            n_ctx=max(512, min(ctx, 4096)),
            n_threads=threads,
            n_gpu_layers=0,
            n_batch=max(32, min(batch, 512)),
            verbose=False,
        )
        logger_msg = (
            f"Local LLM ready: {model.name} threads={threads} ctx={ctx} batch={batch}"
        )
        try:
            import logging

            logging.getLogger("GoogleVoiceAgent").info(logger_msg)
        except Exception:
            pass
        _LOADED_PATH = str(model)
        return _LLM


class LocalChatClient:
    """Small stand-in for the Groq chat client, backed by llama.cpp."""

    def __init__(self, model_path: str | None = None):
        self._model_path = model_path
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, outer: "LocalChatClient"):
            self.completions = outer._Completions(outer)

    class _Completions:
        def __init__(self, outer: "LocalChatClient"):
            self._outer = outer

        def create(self, **kwargs) -> _Completion:
            from src.llm_queue import complete

            messages = _fit_messages(list(kwargs.get("messages") or []))
            max_tokens = int(kwargs.get("max_tokens") or 80)
            temperature = float(kwargs.get("temperature") if kwargs.get("temperature") is not None else 0.7)
            # The queue owns the single loaded model (or the llama.cpp server).
            os.environ.setdefault("LOCAL_LLM_PATH", self._outer._model_path or "")
            content = complete(messages, max_tokens=max_tokens, temperature=temperature)
            return _Completion(content)


def complete_local(messages: list, *, max_tokens: int = 80, temperature: float = 0.7) -> str:
    """One in-process completion. Callers take the shared slot first."""
    llm = get_llama()
    result = llm.create_chat_completion(
        messages=_fit_messages(list(messages)),
        max_tokens=max_tokens,
        temperature=temperature,
    )
    choices = result.get("choices") if isinstance(result, dict) else None
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()
