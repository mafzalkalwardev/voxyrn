"""
Realtime conversational sales agent with campaign-aware speaking behavior.

Manages a multi-turn conversation history and generates short, natural responses
optimised for live phone calls (1–2 sentences, under 25 words per turn).

Usage:
    agent = ConversationAgent(api_key="gsk_...", contact_name="John")
    opening = agent.opening_line()
    # ... play opening via TTS ...
    response = agent.respond_to("Who is this?")
    # ... play response ...
    if agent.should_end_call():
        farewell = agent.goodbye_line()
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from groq import Groq
from src.dispatcher_intelligence import (
    DispatcherConversationState,
    build_dynamic_system_prompt,
    build_guardrail_reply,
    build_instant_sales_reply,
    build_pricing_reply,
    update_state_from_utterance,
)

logger = logging.getLogger("GoogleVoiceAgent")

_SYSTEM_TEMPLATE = """\
You are {agent_name}, a professional freight dispatch sales agent at {company_name}.
You sound like a sharp, warm US dispatch salesperson on a live outbound call.

Company: {company_name}
Website: {company_website}
Callback number: {callback_number}

About us: {company_context}

YOUR GOAL: qualify equipment and lanes, prove they need dedicated dispatch, and CLOSE
them on THIS call — get a verbal yes to start plus the best email for the carrier packet.
There is no later appointment. Nobody will call them back. You sell and start them now.

PHONE CALL RULES — follow all of these:
- You are on a live phone call RIGHT NOW. The prospect just answered.
- Respond ONLY with what you say aloud — no stage directions, labels, or markdown.
- Every reply must be 1–2 short sentences (hard limit: 30 words).
- Sound warm, confident, and genuinely helpful — never scripted or pushy.
- Speak with light emotion: smile on hellos/closes, empathy on objections, energy on the close.
- Use natural contractions and occasional soft reactions (Sure, Absolutely, Got it) when they fit.
- Prefer clear spoken paperwork words: M C Authority Letter, W nine, certificate of insurance, L L C.
- Ask ONE specific qualifying question per turn (equipment, lanes, who books loads, time on boards).
- If they raise an objection, acknowledge briefly, then give ONE concrete value point and stay on the close.
- If they say they're busy or driving: keep selling in sixty seconds — ask equipment type, do NOT schedule a callback.
- If they seem interested: assumptive close — "Let's get you started today. What's the best email for the packet?"
- Never say "15-minute call", "onboarding later", "I'll have someone call you", or "when should I call back".
- After 3 consecutive negative responses, thank them politely and signal that you'll let them go.
- Never guarantee earnings. Never invent company facts.

OBJECTION PLAYBOOK:
- "Who is this?" -> briefly repeat your name, company, and that you help carriers with dispatch/load support.
- "How did you get my number?" -> say you are calling publicly available carrier/business contacts and can remove them if they prefer.
- "Not interested" -> acknowledge, give one short value point, then ask if they want off the list OR to check lane fit now.
- "Already have dispatch" -> respect it, ask if weak weeks still leave them short — offer backup desk and keep qualifying.
- "What do you charge?" -> offer TWO options: equipment percent OR $1000/month salary on contract paid the 1st–5th; ask which they prefer.
- "What services?" -> MAIN only first (load search, rate negotiation, paperwork, tracking — personal dispatch assistant). Go deeper only if they ask.
- "What is the packet?" -> signup email: agreement/salary contract + they return W nine, M C Authority Letter, certificate of insurance.
- "Can you guarantee loads/earnings?" -> never guarantee; say results depend on market, lanes, equipment, and availability.
- "Send info" / "call me later" -> take the email NOW and confirm equipment/lanes on this call so the packet is ready.
- "Busy" / "driving" -> sixty-second qualify (equipment), keep selling; do not book a later call.
- "I need to think" -> agree briefly, still take email for the packet so they are not starting cold.
- "Remove me" or "stop calling" -> apologize, confirm you will mark them do-not-call, and end.
- Hostile or confused prospect -> stay calm, do not argue, end politely."""

_NEGATIVE_SIGNALS = frozenset([
    "not interested", "don't call", "remove me", "stop calling",
    "do not call", "don't need", "already have", "no thanks",
    "busy right now", "not a good time", "take me off",
])

# Trucking-specific terms that indicate a genuine carrier conversation
_ENGAGEMENT_KEYWORDS = frozenset([
    "dry van", "flatbed", "reefer", "step deck", "hotshot", "box truck",
    "sprinter van", "sprinter", "power only", "car hauler",
    "own authority", "my mc", "mc number", "my authority",
    "deadhead", "tonu", "detention", "drop hook", "drop and hook",
    "loadboard", "load board",
    "factoring", "quick pay",
    "what percent", "your percent", "what do you charge", "what's your fee",
    "preferred lanes", "what lanes",
    "rpm", "rate per mile",
])


def _clean_spoken_text(text: str) -> str:
    """Remove any AI output artifacts that should not be spoken aloud."""
    text = (text or "").strip()
    # Strip common label prefixes the LLM sometimes outputs
    for prefix in (
        "Tony:", "Ashley:", "Agent:", "Assistant:", "[Tony]", "[Ashley]", "[Agent]",
        "TONY:", "ASHLEY:", "AGENT:", "AI:", "Bot:", "Tony (agent):", "Ashley (agent):",
        "Dispatch Agent:", "Response:", "Answer:", "Reply:",
        "Tony (Dispatch Agent):", "Ashley (Dispatch Agent):",
        "Tony from Indus:", "Ashley from Indus:",
        "[Dispatch Agent]:", "[Response]:",
    ):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    # Strip surrounding quotes
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    # Strip markdown bold/italic that edge cases produce
    text = text.replace("**", "").replace("__", "").replace("*", "")
    # Collapse whitespace
    import re
    text = re.sub(r"\s+", " ", text).strip()
    return text


class ConversationAgent:
    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        agent_name: str = "Ashley",
        company_name: str = "Indus Transports LLC",
        company_context: str = "",
        company_website: str = "",
        callback_number: str = "+13172100495",
        contact_name: str = "",
        contact_phone: str = "",
        rate_limit_per_minute: int | None = None,
        backend: str = "groq",
    ):
        self._backend = (backend or "groq").strip().lower()
        if self._backend == "local":
            from src.local_llm import LocalChatClient
            self._client = LocalChatClient()
        else:
            if not api_key:
                raise ValueError("api_key is required for ConversationAgent")
            self._client = Groq(api_key=api_key)
        self.model = model
        self.agent_name = agent_name
        self.company_name = company_name
        from src.indus_campaign import COMPANY_CONTEXT as _INDUS_CONTEXT

        self.company_context = company_context or _INDUS_CONTEXT
        self.company_website = company_website or "https://industransports.com"
        self.callback_number = callback_number
        self.contact_name = contact_name
        self.contact_phone = contact_phone
        self.rate_limit_per_minute = rate_limit_per_minute
        self.state = DispatcherConversationState()
        self._system = self._build_system_prompt()
        self._history: list[dict] = []
        self._turn_count: int = 0
        self._consecutive_negatives: int = 0
        self._engaged: bool = False
        self._llm_failures: int = 0
        self._llm_down: bool = False
        self._script_session = None
        self._script_ended = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def opening_line(self) -> str:
        """Generate a short, varied greeting that sounds like a real caller."""
        # Varied openers to avoid sounding scripted on repeated calls
        from src.opening_pool import random_curated

        prompt = (
            f"Write ONE warm outbound freight-dispatch sales greeting. "
            f"You are {self.agent_name}, a sales agent at {self.company_name}. "
            "Name yourself and the company, then ask if you reached the owner/dispatcher "
            "or if now is an okay time. Sound confident and human, not robotic. "
            "Do not pitch loads or pricing yet. Under 22 words. "
            "Return ONLY the greeting text, no quotes, no labels."
        )
        line = self._raw_complete(prompt, max_tokens=55)
        if not line or len(line) < 5 or self.company_name.split()[0].lower() not in line.lower():
            line = random_curated(self.agent_name, self.company_name)
        return line

    def respond_to(self, prospect_text: str) -> str:
        """
        Generate a response to what the prospect said.
        Updates conversation history. Returns the agent's reply.
        """
        scripted = self._reply_from_script(prospect_text)
        if scripted is not None:
            self._history.append({"role": "user", "content": prospect_text})
            self._history.append({"role": "assistant", "content": scripted})
            self._turn_count += 1
            return scripted

        self._history.append({"role": "user", "content": prospect_text})
        self._turn_count += 1
        update_state_from_utterance(self.state, prospect_text)

        text_lower = prospect_text.lower()
        if not self._engaged and any(kw in text_lower for kw in _ENGAGEMENT_KEYWORDS):
            self._engaged = True

        if any(signal in text_lower for signal in _NEGATIVE_SIGNALS):
            self._consecutive_negatives += 1
        else:
            self._consecutive_negatives = 0

        reply = (
            build_guardrail_reply(self.state, prospect_text)
            or build_pricing_reply(self.state, prospect_text)
            or build_instant_sales_reply(
                self.state,
                prospect_text,
                agent_name=self.agent_name,
                company_name=self.company_name,
            )
            or self._complete()
        )
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def should_end_call(self) -> bool:
        """True only when the other person asks not to be called again.

        Turn count, objections, and a missed reply do not hang up. The call
        stays open until they end it or Google Voice shows it has ended.
        """
        if self._script_ended:
            return True
        return self.state.interest_level == "DNC"

    def goodbye_line(self) -> str:
        """Short, warm closing line."""
        return self._raw_complete(
            "Generate one brief, warm closing line to politely end the call. Under 15 words.",
            max_tokens=40,
        )

    def reset(self, contact_name: str = "") -> None:
        self._history.clear()
        self._turn_count = 0
        self._consecutive_negatives = 0
        self._engaged = False
        self._llm_failures = 0
        self._llm_down = False
        self.contact_name = contact_name
        self.state = DispatcherConversationState()
        self._system = self._build_system_prompt()

    def state_snapshot(self) -> dict:
        """Return confirmed conversation state for logs/tests."""
        return {
            "truck_type": self.state.truck_type,
            "interest_level": self.state.interest_level,
            "objections": list(self.state.objections),
            "negotiated_percentage": self.state.negotiated_percentage,
            "local_or_otr": self.state.local_or_otr,
            "preferred_lanes": self.state.preferred_lanes,
            "dispatcher_status": self.state.dispatcher_status,
            "follow_up_status": self.state.follow_up_status,
            "mc_number": self.state.mc_number,
            "dimensions": self.state.dimensions,
            "accessories": self.state.accessories,
            "email": self.state.email,
            "factoring_company": self.state.factoring_company,
            "carrier_style": self.state.carrier_style,
        }

    def build_stt_prompt(self) -> str:
        """Build a per-call STT prompt that includes known carrier context.

        Including the carrier's name and equipment type in the Whisper prompt
        measurably improves transcription accuracy for domain-specific terms.
        """
        parts = [f"{self.company_name} freight dispatch call"]
        if self.contact_name:
            parts.append(f"carrier name: {self.contact_name}")
        if self.state.truck_type:
            parts.append(f"equipment: {self.state.truck_type}")
        if self.state.preferred_lanes:
            parts.append(f"lanes: {self.state.preferred_lanes}")
        if self.state.mc_number:
            parts.append(f"MC: {self.state.mc_number}")
        return ". ".join(parts) + "."

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _reply_from_script(self, prospect_text: str) -> str | None:
        from src.script_engine import ScriptSession, active_script_id, get_script, record_turn

        script_id = active_script_id()
        if not script_id:
            return None

        if self._script_session is None or self._script_session.script.script_id != script_id:
            script = get_script(script_id)
            if script is None:
                return None
            self._script_session = ScriptSession(
                script,
                {
                    "first_name": self.contact_name or "there",
                    "company": self.company_name,
                    "product": script.product,
                    "agent_name": self.agent_name,
                },
            )
        turn = self._script_session.hear(prospect_text, llm=self._paraphrase_script)
        record_turn(script_id, turn)
        self._script_ended = turn.ended
        return turn.line

    def _paraphrase_script(self, prompt: str) -> str:
        return self._raw_complete(prompt, max_tokens=60)

    def _build_system_prompt(self) -> str:
        prompt = build_dynamic_system_prompt(
            agent_name=self.agent_name,
            company_name=self.company_name,
            company_website=self.company_website,
            callback_number=self.callback_number,
            company_context=self.company_context,
            contact_name=self.contact_name,
            state=self.state,
        )
        language = os.getenv("CALL_LANGUAGE", "en").strip().lower()[:2]
        if language == "es":
            prompt += (
                "\n\nLANGUAGE: Speak only Spanish on this call. Match Mexico or Spain "
                "if the contact's wording makes that clear. Do not switch to English "
                "unless they ask. Keep each reply to one or two short spoken sentences."
            )
        return prompt

    def _complete(self) -> str:
        self._system = self._build_system_prompt()
        messages = [{"role": "system", "content": self._system}]
        messages.extend(self._history[-40:])
        # Shorter max_tokens for rushed carriers reduces latency significantly
        # Keep local replies short so first audio starts sooner on CPU-only laptops.
        if self.state.carrier_style == "rushed":
            max_tokens = 45
        elif self.state.carrier_style in ("skeptical", "neutral"):
            max_tokens = 65
        else:
            max_tokens = 80
        temperature = 0.55 if self._backend == "local" else (
            0.65 if self.state.carrier_style == "skeptical" else 0.75
        )
        try:
            resp = self._chat_complete_with_backoff(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                # top_p for tighter distribution on short answers
                top_p=0.92,
            )
            self._llm_failures = 0
            return _clean_spoken_text(resp.choices[0].message.content)
        except Exception as exc:
            logger.error("LLM completion error: %s", exc)
            return self._fallback_when_model_fails()

    def _raw_complete(self, user_prompt: str, max_tokens: int = 80) -> str:
        self._system = self._build_system_prompt()
        try:
            resp = self._chat_complete_with_backoff(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.75,
            )
            return _clean_spoken_text(resp.choices[0].message.content)
        except Exception as exc:
            logger.error("LLM raw completion error: %s", exc)
            return ""

    def _fallback_when_model_fails(self) -> str:
        """Ask them to repeat, then keep the call open and try again next turn."""
        self._llm_failures += 1
        if self._llm_failures == 1:
            return "I missed that last part. Go ahead."
        return "Still with you. Say that one more time."

    def _chat_complete_with_backoff(self, **kwargs):
        delays = (1.0, 2.0, 4.0)
        last_exc: Exception | None = None
        for attempt in range(1, len(delays) + 2):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                is_rate_limited = (
                    "429" in msg
                    or "rate limit" in msg
                    or "rate-limited" in msg
                    or "too many requests" in msg
                    or "rate_limit" in msg
                )
                if not is_rate_limited or attempt > len(delays):
                    break
                delay_s = delays[attempt - 1]
                logger.warning(
                    "Realtime Groq rate limited (attempt %d/%d). Backing off %.1fs. Error: %s",
                    attempt,
                    len(delays) + 1,
                    delay_s,
                    exc,
                )
                time.sleep(delay_s)
        assert last_exc is not None
        raise last_exc
