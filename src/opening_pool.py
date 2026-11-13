"""Rotating opening lines so outbound calls do not sound identical."""

from __future__ import annotations

import hashlib
import random
from datetime import date
from typing import Optional

from src.indus_campaign import curated_openings


def _templates(agent_name: str, company_name: str) -> list[str]:
    return curated_openings(agent_name, company_name)


def pick_opening(
    phone: str,
    agent_name: str,
    company_name: str,
    *,
    llm_line: Optional[str] = None,
    avoid_text: Optional[str] = None,
) -> str:
    """Pick a stable-but-varied opening for this phone (changes daily)."""
    # Prefer curated Indus sales openings for live client demos — LLM lines are
    # often too generic ("how are you today?") when the cloud key is offline.
    openings = _templates(agent_name, company_name)
    day_key = date.today().isoformat()
    digest = hashlib.sha256(f"{phone}|{day_key}".encode()).hexdigest()
    idx = int(digest[:8], 16) % len(openings)
    line = openings[idx]
    if avoid_text and line.strip() == avoid_text.strip():
        line = openings[(idx + 1) % len(openings)]

    # Only use an LLM line when it clearly names the company (keeps brand on video).
    if llm_line and llm_line.strip():
        cleaned = llm_line.strip()
        company_token = (company_name or "").split()[0].lower() if company_name else ""
        if company_token and company_token in cleaned.lower():
            if not avoid_text or cleaned != avoid_text.strip():
                return cleaned
    return line


def random_curated(agent_name: str, company_name: str) -> str:
    return random.choice(_templates(agent_name, company_name))


def warmup_phrases(agent_name: str, company_name: str, count: int = 8) -> list[str]:
    """Phrases to pre-cache in TTSCache at batch start."""
    from src.indus_campaign import CLOSE_LINE, PITCH_LINE, QUALIFY_LINE, voicemail_text

    openings = _templates(agent_name, company_name)
    extras = [PITCH_LINE, QUALIFY_LINE, CLOSE_LINE, voicemail_text()]
    phrases = list(openings[: max(count, 1)]) + extras
    seen: set[str] = set()
    out: list[str] = []
    for phrase in phrases:
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            out.append(phrase)
    return out
