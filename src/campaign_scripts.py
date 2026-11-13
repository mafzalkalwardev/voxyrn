"""Generic outbound script pieces.

This is not an Indus service catalog. The freight campaign in
``src/indus_campaign.py`` stays the default for that product. Here: the
shared structure, an objection library, one B2B appointment-setting example,
a no-dial test render, and a tiny in-memory analytics counter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

STRUCTURE = (
    "opener",
    "reason",
    "qualify",
    "value",
    "objection",
    "close",
)

OBJECTIONS = {
    "en": {
        "who is this": "This is {agent} at {company}. I set a short appointment with the person who buys {offer}.",
        "not interested": "Understood. If a {minutes}-minute look at {offer} is useful later, what day should I avoid?",
        "send an email": "I can send it. A {minutes}-minute call is how we see if it is worth your time. Does {day} work?",
        "busy": "I'll be brief. Is there a {minutes}-minute window {day}?",
        "already have a vendor": "Keep them. This is only worth a look if they are missing {offer}. Should I check {day}?",
        "stop calling": "I will mark this number do-not-call and stop. Sorry for the interruption.",
    },
    "es": {
        "quién es": "Soy {agent} de {company}. Busco una cita breve con quien decide {offer}.",
        "no me interesa": "De acuerdo. Si más adelante vale una llamada de {minutes} minutos sobre {offer}, ¿qué día no le queda bien?",
        "mándame un correo": "Se lo envío. La llamada de {minutes} minutos es para ver si vale su tiempo. ¿Le queda {day}?",
        "estoy ocupado": "Seré breve. ¿Tiene {minutes} minutos el {day}?",
        "ya tenemos proveedor": "Quédense con él. Esto solo sirve si les falta {offer}. ¿Reviso el {day}?",
        "no llame": "Marco este número para no volver a llamar. Disculpe la interrupción.",
    },
}


@dataclass
class ScriptTemplate:
    template_id: str
    name: str
    language: str
    goal: str
    steps: dict[str, str]

    def render(self, **values: str) -> dict[str, str]:
        rendered = {}
        for key in STRUCTURE:
            text = self.steps.get(key, "")
            for name, value in values.items():
                text = text.replace("{" + name + "}", value)
            rendered[key] = text
        return rendered


B2B_APPOINTMENT = ScriptTemplate(
    template_id="b2b-appointment",
    name="B2B appointment",
    language="en",
    goal="Book a short meeting with the person who can buy. Do not pitch the whole service on this call.",
    steps={
        "opener": "Hi, this is {agent} with {company}. Did I catch you with a minute?",
        "reason": "I'm calling to set a {minutes}-minute conversation about {offer}, not to sell it on this call.",
        "qualify": "Are you the person who would decide on that, or should I ask for someone else?",
        "value": "Teams use the meeting to see if {offer} fits the way they work today.",
        "objection": "If now is bad, I can hold {day}. What time is least painful?",
        "close": "I'll send a calendar hold for {day}. What's the best email for the invite?",
    },
)

B2B_APPOINTMENT_ES = ScriptTemplate(
    template_id="b2b-appointment-es",
    name="Cita B2B",
    language="es",
    goal="Agendar una reunión corta con quien puede comprar. No expliques todo el servicio en esta llamada.",
    steps={
        "opener": "Hola, soy {agent} de {company}. ¿Le agarro un minuto?",
        "reason": "Llamo para apartar {minutes} minutos sobre {offer}, no para venderlo en esta llamada.",
        "qualify": "¿Usted decide eso, o le pido a otra persona?",
        "value": "La reunión sirve para ver si {offer} encaja con cómo trabajan hoy.",
        "objection": "Si ahora no puede, dejo {day}. ¿A qué hora le estorba menos?",
        "close": "Le mando la invitación para {day}. ¿Cuál es el mejor correo?",
    },
)

TEMPLATES = {
    B2B_APPOINTMENT.template_id: B2B_APPOINTMENT,
    B2B_APPOINTMENT_ES.template_id: B2B_APPOINTMENT_ES,
}


@dataclass
class ScriptAnalytics:
    """Counts from test renders and live outcomes. Not a CRM."""

    rendered: int = 0
    objections_hit: dict[str, int] = field(default_factory=dict)
    booked: int = 0
    declined: int = 0

    def record_render(self) -> None:
        self.rendered += 1

    def record_objection(self, key: str) -> None:
        self.objections_hit[key] = self.objections_hit.get(key, 0) + 1

    def record_outcome(self, outcome: str) -> None:
        if outcome == "booked":
            self.booked += 1
        elif outcome == "declined":
            self.declined += 1


def preview_script(template_id: str = "b2b-appointment", **values: str) -> dict[str, str]:
    """Render a script with sample values. Does not dial."""
    template = TEMPLATES[template_id]
    defaults = {
        "agent": "Alex",
        "company": "Northwind Studio",
        "offer": "a website and inbox cleanup",
        "minutes": "15",
        "day": "Thursday",
    }
    defaults.update(values)
    return template.render(**defaults)


def objection_reply(text: str, language: str = "en", **values: str) -> str | None:
    library = OBJECTIONS.get(language if language in OBJECTIONS else "en", {})
    lowered = " ".join((text or "").casefold().split())
    defaults = {"agent": "Alex", "company": "the company", "offer": "the offer", "minutes": "15", "day": "Thursday"}
    defaults.update(values)
    for key, reply in library.items():
        if key in lowered:
            for name, value in defaults.items():
                reply = reply.replace("{" + name + "}", value)
            return reply
    return None
