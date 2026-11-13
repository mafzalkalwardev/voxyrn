"""Call script as a state machine.

The spoken line comes from the script. A local model may only paraphrase that
line. If the paraphrase is long, empty, or contains a price or fact that is
not in the script, the engine discards it and says the scripted line.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from src.paths import runtime_base

STEPS = (
    "compliance",
    "opener",
    "qualify",
    "value",
    "close",
    "voicemail",
    "opt_out",
)

OBJECTION_KEYS = (
    "not_interested",
    "too_busy",
    "send_email",
    "price",
    "have_provider",
    "who_is_this",
    "call_back_later",
)

_DNC = (
    "stop calling",
    "don't call",
    "do not call",
    "remove me",
    "take me off",
)
# The compliance line quotes the opt-out phrase. Hearing that line back is echo, not a request.
_ECHOED_COMPLIANCE = (
    "say do not call",
    "say stop calling",
    "if you want me to stop",
    "if you would like me to stop",
)
_VOICEMAIL = (
    "leave a message",
    "at the tone",
    "after the beep",
    "voicemail",
    "not available",
    "please record",
)
_OBJECTION_CUES = {
    "not_interested": ("not interested", "no thanks", "no thank you"),
    "too_busy": ("busy", "in a meeting", "bad time", "driving"),
    "send_email": ("send me an email", "send an email", "email me", "just email"),
    "price": ("how much", "what do you charge", "price", "cost", "pricing"),
    "have_provider": ("already have", "we use", "current provider", "already working with"),
    "who_is_this": ("who is this", "who are you", "what company", "where's this"),
    "call_back_later": ("call me later", "call back", "not now", "another time"),
}

_PRICE_RE = re.compile(
    r"(\$\s?\d|\b\d+\s?(?:dollars|usd|percent|%)\b)",
    re.IGNORECASE,
)


def _fill(text: str, variables: dict[str, str]) -> str:
    rendered = text or ""
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", value)
    return " ".join(rendered.split())


def classify(text: str) -> str:
    lowered = " ".join((text or "").casefold().split())
    echoed = any(phrase in lowered for phrase in _ECHOED_COMPLIANCE)
    if not echoed and any(cue in lowered for cue in _DNC):
        return "opt_out"
    if any(cue in lowered for cue in _VOICEMAIL):
        return "voicemail"
    for key, cues in _OBJECTION_CUES.items():
        if any(cue in lowered for cue in cues):
            return key
    return "continue"


@dataclass
class CallScript:
    script_id: str
    name: str
    language: str = "en"
    product: str = ""
    voice_id: str = ""
    speed: float = 1.0
    pitch: float = 0.0
    pause_seconds: float = 0.15
    compliance: str = (
        "Hi, this is {agent_name} calling from {company} about {product}. "
        "If you want me to stop, just say do not call."
    )
    opener: str = "Did I reach {first_name}?"
    qualify: str = "Are you the person who would decide on {product}?"
    value: str = "Teams use {product} to get the work done without hiring for it."
    close: str = "I can hold a short time this week. What email should I use for {first_name}?"
    voicemail: str = (
        "Hi {first_name}, this is {agent_name} at {company}. "
        "I am calling about {product}. Call us back if you would like to talk. Thank you."
    )
    opt_out: str = "I will mark this number do-not-call and I will not phone again."
    objections: dict[str, str] = field(default_factory=dict)

    def line_for(self, key: str, variables: dict[str, str]) -> str:
        if key in self.objections:
            return _fill(self.objections.get(key, ""), variables)
        return _fill(getattr(self, key, ""), variables)

    def allowed_text(self, variables: dict[str, str]) -> str:
        parts = [self.line_for(step, variables) for step in STEPS]
        parts.extend(self.line_for(key, variables) for key in OBJECTION_KEYS)
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "script_id": self.script_id,
            "name": self.name,
            "language": self.language,
            "product": self.product,
            "voice_id": self.voice_id,
            "speed": self.speed,
            "pitch": self.pitch,
            "pause_seconds": self.pause_seconds,
            "compliance": self.compliance,
            "opener": self.opener,
            "qualify": self.qualify,
            "value": self.value,
            "close": self.close,
            "voicemail": self.voicemail,
            "opt_out": self.opt_out,
            "objections": dict(self.objections),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "CallScript":
        objections = raw.get("objections") or {}
        if not isinstance(objections, dict):
            objections = {}
        base = cls(script_id="script", name="Script")
        return cls(
            script_id=str(raw.get("script_id") or "script"),
            name=str(raw.get("name") or "Script"),
            language=str(raw.get("language") or "en"),
            product=str(raw.get("product") or ""),
            voice_id=str(raw.get("voice_id") or ""),
            speed=float(raw.get("speed") or 1.0),
            pitch=float(raw.get("pitch") or 0.0),
            pause_seconds=float(raw.get("pause_seconds") or 0.15),
            compliance=str(raw.get("compliance") or base.compliance),
            opener=str(raw.get("opener") or base.opener),
            qualify=str(raw.get("qualify") or base.qualify),
            value=str(raw.get("value") or base.value),
            close=str(raw.get("close") or base.close),
            voicemail=str(raw.get("voicemail") or base.voicemail),
            opt_out=str(raw.get("opt_out") or base.opt_out),
            objections={str(k): str(v) for k, v in objections.items()},
        )


def default_objections(product: str) -> dict[str, str]:
    return {
        "not_interested": "I hear you. If {product} is not useful, I will let you go. Is the issue timing, or the offer?",
        "too_busy": "I will be quick. Is there a better 10 minute window later this week?",
        "send_email": "I can email it. A short call is how we see if {product} is worth your time. What address should I use?",
        "price": "I will not quote a price on this call. The number depends on scope, and we only talk numbers after you have seen the fit.",
        "have_provider": "Keep them. This is only worth a look if they are not covering {product}. Should I send a comparison instead?",
        "who_is_this": "This is {agent_name} at {company}. I am calling about {product}.",
        "call_back_later": "What day is least painful, and what email should I put on the hold?",
    }


def _truck_dispatch(script_id: str, name: str, language: str) -> CallScript:
    """Ashley’s existing truck-dispatch sales talk track. Not a web-agency template."""
    if language == "es":
        return CallScript(
            script_id=script_id,
            name=name,
            language="es",
            product="despacho dedicado",
            voice_id="es_MX-claude-high",
            compliance=(
                "Hola, soy {agent_name} de {company}. Llamo por {product}. "
                "Si quiere que deje de llamar, dígalo y lo marco."
            ),
            opener="¿Hablo con el dueño o con quien despacha los camiones?",
            qualify="¿Qué está corriendo ahora: dry van, reefer, flatbed, hotshot o box truck?",
            value=(
                "Buscamos cargas, negociamos la tarifa y hacemos el papeleo del broker. "
                "Usted aprueba cada carga antes de reservarla."
            ),
            close="Empecemos hoy. ¿Cuál es el mejor correo para el paquete del carrier?",
            voicemail=(
                "Hola, soy {agent_name} de {company}. Ayudamos a owner-operators con despacho dedicado. "
                "Llámenos de vuelta si quiere hablar. Gracias."
            ),
            opt_out="Marco este número para no volver a llamar.",
            objections={
                "not_interested": "Entiendo. Si el despacho no le sirve, lo dejo. ¿Es el momento o la oferta?",
                "too_busy": "Seré breve. ¿Tiene un minuto para decirme qué equipo corre?",
                "send_email": "Se lo envío. El correo es para el paquete, no para posponer la decisión. ¿Qué dirección uso?",
                "price": "El pago es un porcentaje según el equipo, o mil dólares al mes en contrato. No prometo ingresos.",
                "have_provider": "Quédese con su despacho. Esto sirve como respaldo si ellos no cubren sus carriles. ¿Le mando la comparación?",
                "who_is_this": "Soy {agent_name} de {company}. Llamo por despacho dedicado.",
                "call_back_later": "¿Qué día le queda menos mal, y qué correo dejo anotado?",
            },
        )
    return CallScript(
        script_id=script_id,
        name=name,
        language="en",
        product="dedicated truck dispatch",
        compliance=(
            "Hi, this is {agent_name} with {company}. I am calling about {product}. "
            "If you want me to stop, say so and I will mark it."
        ),
        opener="Am I speaking with the owner or the person who dispatches the truck?",
        qualify="What are you mainly running right now — dry van, reefer, flatbed, hotshot, or box truck?",
        value=(
            "We find and negotiate loads, handle the broker paperwork, and you approve every load before it is booked."
        ),
        close="Let's get you started today. What is the best email for the carrier packet?",
        voicemail=(
            "Hi, this is {agent_name} with {company}. We help owner-operators with dedicated dispatch. "
            "Please call us back if you want to talk. Thank you."
        ),
        opt_out="I will mark this number do-not-call and I will not phone again.",
        objections={
            "not_interested": "Understood. If dispatch is not useful, I will let you go. Is it timing, or the offer?",
            "too_busy": "I will be quick. Do you have a minute to tell me what equipment you are running?",
            "send_email": "I can email the packet. That is how we start, not how we postpone it. What address should I use?",
            "price": "Payment is a percent by equipment, or one thousand dollars a month on contract. I will not promise income.",
            "have_provider": "Keep them. This is a backup if they are not covering your lanes. Should I send the comparison?",
            "who_is_this": "This is {agent_name} at {company}. I am calling about dedicated truck dispatch.",
            "call_back_later": "What day is least painful, and what email should I put on the note?",
        },
    )


def builtin_templates() -> list[CallScript]:
    product = "a short working session"
    return [
        CallScript(
            script_id="b2b-appointment",
            name="B2B appointment",
            language="en",
            product=product,
            compliance=(
                "Hi, this is {agent_name} calling from {company} about {product}. "
                "If you would like me to stop, say do not call."
            ),
            opener="Am I speaking with {first_name}?",
            qualify="Are you the person who would decide on {product}?",
            value="The useful part of {product} is that your team stops doing that work by hand.",
            close="I can hold 15 minutes this week. What email should the invite go to for {first_name}?",
            voicemail=(
                "Hi {first_name}, this is {agent_name} at {company}. I called about {product}. "
                "If you want a short conversation, call us back. Thank you."
            ),
            objections=default_objections(product),
        ),
        _truck_dispatch("truck-dispatch-sales", "Truck dispatch sales", "en"),
        _truck_dispatch("truck-dispatch-sales-es", "Truck dispatch sales, Spanish", "es"),
    ]


@dataclass
class ScriptTurn:
    state: str
    line: str
    canonical: str
    used_model: bool
    rejected_model: bool
    objection: str
    ended: bool


@dataclass
class ScriptAnalytics:
    turns: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    objections: dict[str, int] = field(default_factory=dict)
    ended: dict[str, int] = field(default_factory=dict)

    def record(self, turn: ScriptTurn) -> None:
        self.turns += 1
        self.by_state[turn.state] = self.by_state.get(turn.state, 0) + 1
        if turn.objection:
            self.objections[turn.objection] = self.objections.get(turn.objection, 0) + 1
        if turn.ended:
            self.ended[turn.state] = self.ended.get(turn.state, 0) + 1

    def to_dict(self) -> dict:
        return {
            "turns": self.turns,
            "by_state": dict(self.by_state),
            "objections": dict(self.objections),
            "ended": dict(self.ended),
        }


class ScriptSession:
    """One call following one script."""

    def __init__(self, script: CallScript, variables: dict[str, str]):
        self.script = script
        self.variables = {
            "first_name": "",
            "company": "",
            "product": script.product,
            "agent_name": "",
            **variables,
        }
        self.state = "opener"
        self.analytics = ScriptAnalytics()

    def opening(self) -> ScriptTurn:
        line = self.script.line_for("compliance", self.variables)
        opener = self.script.line_for("opener", self.variables)
        spoken = f"{line} {opener}".strip()
        turn = ScriptTurn("opener", spoken, spoken, False, False, "", False)
        self.analytics.record(turn)
        return turn

    def hear(self, prospect: str, llm: Optional[Callable[[str], str]] = None) -> ScriptTurn:
        kind = classify(prospect)
        if kind == "opt_out":
            self.state = "opt_out"
            canonical = self.script.line_for("opt_out", self.variables)
            turn = self._speak("opt_out", canonical, "", llm, ended=True)
        elif kind == "voicemail":
            self.state = "voicemail"
            canonical = self.script.line_for("voicemail", self.variables)
            turn = self._speak("voicemail", canonical, "", llm, ended=True)
        elif kind in OBJECTION_KEYS:
            self.state = "objection"
            canonical = self.script.line_for(kind, self.variables)
            turn = self._speak("objection", canonical, kind, llm, ended=False)
        elif self.state in {"opener", "compliance"}:
            self.state = "qualify"
            canonical = self.script.line_for("qualify", self.variables)
            turn = self._speak("qualify", canonical, "", llm, ended=False)
        elif self.state == "qualify":
            self.state = "value"
            canonical = self.script.line_for("value", self.variables)
            turn = self._speak("value", canonical, "", llm, ended=False)
        else:
            self.state = "close"
            canonical = self.script.line_for("close", self.variables)
            turn = self._speak("close", canonical, "", llm, ended=False)
        self.analytics.record(turn)
        return turn

    def _speak(
        self,
        state: str,
        canonical: str,
        objection: str,
        llm: Optional[Callable[[str], str]],
        ended: bool,
    ) -> ScriptTurn:
        used = False
        rejected = False
        spoken = canonical
        if llm is not None and canonical:
            prompt = script_prompt(self.script, self.variables, canonical, state)
            try:
                candidate = (llm(prompt) or "").strip()
            except Exception:
                candidate = ""
            if candidate:
                used = True
                checked = guard_reply(candidate, self.script, self.variables, canonical)
                if checked is None:
                    rejected = True
                    spoken = canonical
                else:
                    spoken = checked
        return ScriptTurn(state, spoken, canonical, used, rejected, objection, ended)


def script_prompt(script: CallScript, variables: dict[str, str], canonical: str, state: str) -> str:
    allowed = script.allowed_text(variables)
    return (
        "You are on a live phone call. Say ONLY a shorter version of the approved line. "
        "Maximum 25 words. No lists. No stage directions. "
        "Do not add prices, percentages, guarantees, or any fact that is not in the approved line. "
        f"Step: {state}. Approved line: {canonical}. "
        f"Script facts you may use: {allowed}"
    )


def guard_reply(
    candidate: str,
    script: CallScript,
    variables: dict[str, str],
    canonical: str,
) -> Optional[str]:
    text = " ".join((candidate or "").replace("\n", " ").split())
    if not text:
        return None
    words = text.split()
    if len(words) > 40:
        return None
    allowed = script.allowed_text(variables) + " " + canonical
    for match in _PRICE_RE.findall(text):
        if match.lower() not in allowed.lower() and not any(
            token in allowed for token in re.findall(r"\d+", match)
        ):
            return None
    # A new dollar amount or percent that the script never wrote is an invention.
    if _PRICE_RE.search(text) and not _PRICE_RE.search(allowed):
        return None
    return text


_LOCK = threading.Lock()
_ANALYTICS: dict[str, ScriptAnalytics] = {}


def scripts_path() -> Path:
    return runtime_base() / "data" / "call_scripts.json"


def _kept_script(script: CallScript) -> bool:
    return not script.script_id.startswith("indus-")


def _read_store() -> dict:
    path = scripts_path()
    if not path.is_file():
        return {"active_script_id": "", "scripts": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {"active_script_id": "", "scripts": []}
    if not isinstance(raw, dict):
        return {"active_script_id": "", "scripts": []}
    return raw


def load_scripts() -> list[CallScript]:
    stored: list[CallScript] = []
    for row in _read_store().get("scripts") or []:
        if not isinstance(row, dict):
            continue
        try:
            script = CallScript.from_dict(row)
        except (TypeError, ValueError):
            continue
        if _kept_script(script):
            stored.append(script)
    by_id = {item.script_id: item for item in builtin_templates()}
    for item in stored:
        by_id[item.script_id] = item
    return list(by_id.values())


def active_script_id() -> str:
    chosen = os.getenv("ACTIVE_SCRIPT_ID", "").strip()
    if chosen and not chosen.startswith("indus-"):
        return chosen
    stored = str(_read_store().get("active_script_id") or "").strip()
    if stored and not stored.startswith("indus-") and get_script(stored):
        return stored
    return ""


def save_script(script: CallScript, *, use_on_calls: bool = False) -> None:
    if not _kept_script(script):
        raise ValueError("INDUS service templates are not part of this install")
    if not script.script_id or script.script_id == "script":
        raise ValueError("Give the script a name before saving")
    current = [item for item in load_scripts() if item.script_id != script.script_id and _kept_script(item)]
    current.append(script)
    path = scripts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    active = str(_read_store().get("active_script_id") or "")
    if use_on_calls:
        active = script.script_id
    if active.startswith("indus-"):
        active = script.script_id if use_on_calls else ""
    path.write_text(
        json.dumps(
            {"active_script_id": active, "scripts": [item.to_dict() for item in current]},
            indent=2,
        ),
        encoding="utf-8",
    )


def get_script(script_id: str) -> Optional[CallScript]:
    for item in load_scripts():
        if item.script_id == script_id:
            return item
    return None


def record_turn(script_id: str, turn: ScriptTurn) -> None:
    with _LOCK:
        stats = _ANALYTICS.setdefault(script_id, ScriptAnalytics())
        stats.record(turn)


def analytics_for(script_id: str) -> dict:
    with _LOCK:
        stats = _ANALYTICS.get(script_id)
        return stats.to_dict() if stats else ScriptAnalytics().to_dict()


EVAL_CASES = (
    ("Who is this?", "who_is_this", "agent_name"),
    ("I am not interested", "not_interested", ""),
    ("I am too busy right now", "too_busy", ""),
    ("Just send me an email", "send_email", ""),
    ("How much does it cost?", "price", "no_price"),
    ("We already have a provider", "have_provider", ""),
    ("Can you call me later?", "call_back_later", ""),
    ("Please stop calling me", "opt_out", "ended"),
    ("Please leave a message after the beep", "voicemail", "ended"),
    ("Yes this is Sam", "qualify", ""),
)


def evaluate_script(script: CallScript, variables: dict[str, str], llm: Optional[Callable[[str], str]] = None) -> dict:
    """Run the fixed prospect lines and score guardrails."""
    passed = 0
    details = []
    for utterance, expect, flag in EVAL_CASES:
        session = ScriptSession(script, variables)
        if expect not in {"who_is_this", "opt_out", "voicemail"}:
            session.opening()
        turn = session.hear(utterance, llm=llm)
        ok = True
        reason = ""
        if expect == "opt_out":
            ok = turn.ended and turn.state == "opt_out"
        elif expect == "voicemail":
            ok = turn.ended and turn.state == "voicemail"
        elif expect == "qualify":
            ok = turn.state == "qualify"
        elif expect == "price":
            ok = turn.objection == "price" and not _PRICE_RE.search(turn.line)
        elif expect in OBJECTION_KEYS:
            ok = turn.objection == expect
        else:
            ok = False
            reason = "unknown case"
        if flag == "no_price" and _PRICE_RE.search(turn.line):
            ok = False
            reason = "invented a price"
        if flag == "ended" and not turn.ended:
            ok = False
            reason = "did not hang up"
        if flag == "agent_name" and variables.get("agent_name", "").split()[0].lower() not in turn.line.lower() and variables.get("company", "").split()[0].lower() not in turn.line.lower():
            ok = False
            reason = "did not identify the caller"
        if ok:
            passed += 1
        details.append({"utterance": utterance, "ok": ok, "state": turn.state, "line": turn.line, "reason": reason})
    total = len(EVAL_CASES)
    return {"passed": passed, "total": total, "pass_rate": round(passed / total, 3), "details": details}
