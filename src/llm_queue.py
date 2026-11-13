"""One shared local model for every agent.

Replies wait on a semaphore (LLM_PARALLEL, default 1) so agents do not each
load a GGUF. If LLAMA_SERVER_URL is set, the queue talks to a llama.cpp
server started with --parallel equal to LLM_PARALLEL. Otherwise the
in-process llama-cpp model is used, still one loaded copy.
"""
from __future__ import annotations

import os
import threading
from typing import Any

_SEMAPHORE: threading.Semaphore | None = None
_SEM_SIZE = 0
_SEM_LOCK = threading.Lock()


def parallel_slots() -> int:
    raw = os.getenv("LLM_PARALLEL", "1").strip() or "1"
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _semaphore() -> threading.Semaphore:
    global _SEMAPHORE, _SEM_SIZE
    size = parallel_slots()
    with _SEM_LOCK:
        if _SEMAPHORE is None or _SEM_SIZE != size:
            _SEMAPHORE = threading.Semaphore(size)
            _SEM_SIZE = size
        return _SEMAPHORE


def complete(messages: list[dict[str, Any]], *, max_tokens: int = 80, temperature: float = 0.7) -> str:
    """Run one chat completion on the shared model. Blocks until a slot is free."""
    with _semaphore():
        url = os.getenv("LLAMA_SERVER_URL", "").strip()
        if url:
            return _complete_server(url, messages, max_tokens=max_tokens, temperature=temperature)
        from src.local_llm import complete_local

        return complete_local(messages, max_tokens=max_tokens, temperature=temperature)


def _complete_server(url: str, messages: list, *, max_tokens: int, temperature: float) -> str:
    import httpx

    endpoint = url.rstrip("/") + "/v1/chat/completions"
    response = httpx.post(
        endpoint,
        json={
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=120.0,
    )
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()
