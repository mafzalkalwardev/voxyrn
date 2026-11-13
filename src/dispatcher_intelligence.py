"""Conversation intelligence and guardrails for campaign-aware sales calls.

This module keeps campaign knowledge, state tracking, and guardrails out of the
realtime audio loop. The legacy carrier profile remains available for existing
campaigns, while the speaking policy is reusable across sales campaigns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


EQUIPMENT_PRICING: dict[str, int] = {
    "Box Truck": 10,
    "Hotshot": 10,
    "Car Hauler": 10,
    "Sprinter Van": 15,
    "Power Only": 8,
    "Dry Van": 6,
    "Reefer": 6,
    "Step Deck": 6,
    "Flatbed": 6,
}

_TRUCK_ALIASES: dict[str, str] = {
    "box truck": "Box Truck",
    "boxtruck": "Box Truck",
    "straight truck": "Box Truck",
    "hotshot": "Hotshot",
    "hot shot": "Hotshot",
    "car hauler": "Car Hauler",
    "car carrier": "Car Hauler",
    "sprinter": "Sprinter Van",
    "sprinter van": "Sprinter Van",
    "cargo van": "Sprinter Van",
    "power only": "Power Only",
    "dry van": "Dry Van",
    "van trailer": "Dry Van",
    "reefer": "Reefer",
    "refrigerated": "Reefer",
    "flatbed": "Flatbed",
    "step deck": "Step Deck",
    "stepdeck": "Step Deck",
}

_ACCESSORY_TERMS = (
    "tarps", "tarp", "straps", "chains", "binders", "ramps", "liftgate",
    "lift gate", "e-track", "etracks", "pallet jack", "reefer unit",
    "winch", "headache rack", "dunnage", "load bars",
)

_FACTORING_NAMES = (
    "otrcapital", "otr capital", "rts", "triumph", "wex", "loves",
    "love's", "tafs", "apex", "e capital", "ecapital", "truckstop factoring",
)

# Ballpark national knowledge for live sales talk (DAT/Truckstop/EIA-style public benchmarks).
# Always speak as approximate ranges — never promise a carrier will earn a specific RPM.
MARKET_KNOWLEDGE = (
    "Five-year cycle: 2021 was the freight boom peak; late 2022 into 2023-2024 was a deep freight recession with soft spot rates and excess capacity; 2025 stayed soft into early year then firming; late 2025 into 2026 saw another strong rate rebound as capacity tightened.",
    "2021 peak national spot all-in ballparks (DAT-style): dry van about $2.90-$2.95/mi, reefer about $3.40-$3.50/mi, flatbed about $3.00-$3.05/mi — among the highest modern peaks.",
    "Late-2022 after the peak: spot van around the low-to-mid $2.40s/mi, reefer near high $2.70s-$2.80s, flatbed near mid-to-high $2.70s — already well off the 2021 highs.",
    "2023-2024 trough: many carriers saw the weakest spot pricing of the cycle; dry van often struggled near or under roughly $2.00-$2.20 all-in on soft weeks, with long stretches of weak load-to-truck ratios.",
    "December 2025 monthly DAT-style spot averages were still soft vs peak: van about $2.29/mi, reefer about $2.69/mi, flatbed about $2.53/mi — best monthly averages of that year but far below 2021.",
    "Current 2026 market (fall): national broker-to-carrier spot all-in often quoted near dry van about $3.00-$3.15/mi, reefer about $3.60-$3.70/mi, flatbed about $3.55-$3.70/mi — flatbed has set new highs above its 2021 record on some weeks.",
    "Line-haul (ex-fuel) usually runs roughly $0.70-$1.00 under all-in depending on diesel; always separate fuel when comparing offers.",
    "Midwest outbound (Chicago, Detroit, Cleveland, Indy) is often a money zone — denser freight, tighter capacity, rates frequently $0.20-$0.50 above national averages.",
    "Texas outbound (Dallas, Houston, San Antonio, Laredo/McAllen) stays strong on dry van, flatbed, and produce/reefer cross-border volume.",
    "Florida inbound pays; Florida outbound non-produce is often weak ($1.50-$2.00/mi class pain) unless reefer produce season — always plan the reload before accepting Florida inbound.",
    "Northeast can pay on inbound headhauls but outbound/backhaul and New England deadhead risk are high; tolls, parking, and tight windows eat margin.",
    "Southeast-to-Northeast and produce lanes (Florida/California/South Texas) can be among the best-paying structural imbalances when in season.",
    "Mountain West and sparse markets need reload planning before entry or deadhead kills the week.",
    "Deadhead reduction comes from pre-booking reloads, avoiding one-way weak markets, and matching equipment to lane demand.",
    "Local work can be steadier but often pays less per mile; OTR usually creates more lane options and gross potential.",
)

RATE_BENCHMARKS = (
    "Speak rates as ballpark national averages only. Every lane, broker, and week differs. Never guarantee RPM or weekly gross.",
    "Dry Van now: roughly $2.40-$3.15/mi all-in national; soft weeks lower, tight Midwest/SE-NE weeks higher. 2021 peak near $2.93; Dec 2025 soft near $2.29.",
    "Reefer now: roughly $2.70-$3.70/mi all-in; produce seasons and RoadCheck weeks spike hard. 2021 peak near $3.46; Dec 2025 soft near $2.69.",
    "Flatbed / Step Deck now: roughly $2.90-$3.70/mi all-in; 2026 has printed new highs above the 2021 peak near $3.00-$3.05 on some weeks. Dec 2025 soft near $2.53.",
    "Hotshot: typical long-haul board talk often $1.50-$2.50/mi; urgent/short expedited can print much higher per mile — judge by total dollars and empty miles, not RPM alone.",
    "Box Truck: soft 2023 trough near ~$1.50-$1.75/mi on many boards; 2025-2026 firmer with many regional/expedited books nearer ~$2.00-$3.10/mi depending on length — short hauls inflate RPM.",
    "Sprinter / cargo van: often ~$1.00-$1.50/mi on longer runs; total dollars per load matter more than RPM because trips run long and thin.",
    "Power Only: often roughly mid $2s/mi when booked, but availability and trailer interchange rules dominate — smaller sample, negotiate per move.",
    "Car Hauler: quote by unit/trip more than generic RPM; auction/dealer lanes and insurance limits drive the number.",
    "Best-paying pattern right now: tight Midwest outbound, Texas outbound, SE→NE imbalance, in-season produce/reefer, and strong flatbed industrial lanes.",
    "Lowest-paying pattern: Florida non-produce outbound, sparse Mountain backhauls, soft New England outbound, and any market where trucks piled up after a headhaul.",
)

FUEL_COST_KNOWLEDGE = (
    "Fuel is usually the second-largest carrier cost after labor/driver time — rate talk without diesel context misleads.",
    "EIA-style U.S. on-highway diesel annual averages: 2021 ~$3.29/gal, 2022 peak year ~$4.99/gal (highest modern annual), 2023 ~$4.21, 2024 ~$3.76, 2025 ~$3.66.",
    "2026 diesel has been elevated again — weekly national averages climbed from the mid-$3s early year toward roughly $6.00-$6.50/gal in fall peaks (check current week before quoting fuel pain).",
    "When diesel spikes, all-in spot rates rise but line-haul may lag — carriers feel squeezed until brokers adjust; negotiate all-in and ask if fuel is separate.",
    "High fuel makes weak outbound markets (Florida dry, Mountain deadhead) even more dangerous — protect miles and avoid cheap long empties.",
)

EQUIPMENT_KNOWLEDGE = (
    "Dry Van: ask for trailer length, swing/roll doors, food-grade status, and preferred no-touch freight.",
    "Reefer: ask about unit condition, temperature range, chute, produce/meat experience, and detention tolerance.",
    "Flatbed/Step Deck: ask about tarps, chains, straps, ramps, oversize experience, and load height.",
    "Hotshot: ask trailer length, dovetail/ramps, CDL/non-CDL, weight limits, and whether they run regional or OTR.",
    "Box Truck: ask length, dock height, liftgate, pallet jack, and local/regional preference.",
    "Sprinter Van: ask cargo dimensions, weight capacity, expedited experience, and team/solo availability.",
    "Car Hauler: ask units capacity, enclosed/open, insurance limits, auction/dealer experience, and lanes.",
    "Power Only: ask if they prefer drop-and-hook, Amazon/UPS style work, trailer interchange, and lane flexibility.",
)

SALARY_MONTHLY_USD = 1000
SALARY_PAY_WINDOW = "between the first and the fifth of every month"

NEGOTIATION_RULES = (
    "Two payment options: (1) percentage of gross per load by equipment, or (2) flat one-thousand-dollar monthly salary with a signed contract.",
    "Default percentages: Box Truck 10%, Hotshot 10%, Car Hauler 10%, Sprinter Van 15%, Power Only 8%, Dry Van 6%, Reefer 6%, Step Deck 6%, Flatbed 6%.",
    "Salary plan: one thousand dollars per month, paid between the first and the fifth of each starting month, under a simple dispatch contract.",
    "When they ask what you charge, offer BOTH options briefly, then ask which they prefer.",
    "Ask what percentage they hoped for before discounting the percent plan.",
    "Negotiate percent down by only 1-2 points, and only for quality carriers with clean paperwork and realistic lanes.",
    "Frame either plan around dedicated coverage — we work like their personal dispatch assistant.",
    "Prioritize closing quality carriers, but never sound desperate or chase a bad-fit carrier.",
)

SERVICES_MAIN = (
    "Main services (say these first when asked what you do): dedicated load search, rate negotiation with brokers, "
    "broker paperwork and setup packets, and tracking / road support — we work as their personal dispatch assistant."
)

SERVICES_DEEP = (
    "Only if they ask for more detail: we hunt lanes that fit their truck, negotiate RPM, handle rate cons and broker "
    "packets, help with M C / W nine / certificate of insurance setup, track loads, reduce deadhead with reload "
    "planning, and stay available when boards get quiet. They still approve every load before booking."
)

PACKET_EXPLAIN = (
    "The carrier packet is the signup paperwork emailed after they say yes: our dispatch agreement or salary contract, "
    "plus what we need from them — W nine, M C Authority Letter, certificate of insurance, and factoring notice of "
    "assignment if they factor. Once signed docs are back, we can set them up with brokers and start working loads."
)

COMPLIANCE_RULES = (
    "Never guarantee specific income, miles, rates, RPM, or freight volume.",
    "Market rate numbers are approximate national ballparks that change weekly — say 'about' / 'ballpark' and tie advice to their equipment and lanes.",
    "Do not pressure a carrier after they repeat that they are not interested.",
    "If they ask to be removed or say stop calling, apologize, confirm removal, and end.",
    "Never claim to be a broker, shipper, government agency, or Google Voice.",
    "Do not mention call recording unless recording is actually enabled and consent rules are handled.",
)

OBJECTION_HANDLING = (
    "Who is this: briefly repeat Ashley, Indus Transports LLC, and freight dispatch/load support.",
    "Already have dispatch: respect it, ask if weak weeks still leave gaps, offer backup desk, keep qualifying.",
    "What services: first give MAIN services only; go deeper only if they ask for more — personal dispatch assistant angle.",
    "Too expensive: offer percent vs one-thousand-dollar salary option; ask which fits better.",
    "Busy/driving: keep selling in sixty seconds with one equipment question — never schedule a later callback.",
    "Send info / call later / what is the packet: explain packet briefly, take email NOW, lock equipment/lanes.",
    "Need to think: agree briefly, still take email so the packet is waiting — no deferred appointment.",
    "New authority: educate gently about paperwork, factoring, setup packets, lanes, and avoiding bad freight.",
    "Close: assumptive — verbal yes + best email for packet. Never pitch a 15-minute call for later.",
)

LANE_STRATEGY = (
    "Ask where they are based, where they like to run, and where they refuse to go.",
    "For weak outbound states (especially Florida non-produce), talk about booking reloads before committing to the inbound move.",
    "Steer quality OTR trucks toward Midwest density, Texas outbound, and SE-to-NE when it fits home time.",
    "For local carriers, qualify radius, home-daily expectations, appointment tolerance, and minimum daily gross.",
    "For OTR carriers, qualify preferred regions, home time, and willingness to chase stronger markets when boards go quiet.",
    "If they mention rates, use RATE_BENCHMARKS as ballpark only, then ask their equipment and home base before advising.",
)

PAYMENT_FACTORING_KNOWLEDGE = (
    "Ask whether they use factoring, quick pay, or broker direct pay.",
    "Confirm whether their factoring company approves brokers before booking loads.",
    "Explain that setup packets, COI, W-9, MC authority, and factoring NOA often slow down first loads.",
    "Do not promise same-day payment; explain payment depends on broker terms, quick pay, or factoring approval.",
)


@dataclass
class DispatcherConversationState:
    truck_type: str = ""
    interest_level: str = ""
    objections: list[str] = field(default_factory=list)
    negotiated_percentage: str = ""
    local_or_otr: str = ""
    preferred_lanes: str = ""
    dispatcher_status: str = ""
    follow_up_status: str = ""
    mc_number: str = ""
    company_name: str = ""
    dimensions: str = ""
    accessories: str = ""
    email: str = ""
    factoring_company: str = ""
    pricing_discussion: str = ""
    carrier_style: str = "unknown"
    turn_count: int = 0

    def summary_lines(self) -> list[str]:
        fields = (
            ("truck_type", self.truck_type),
            ("interest_level", self.interest_level),
            ("objections", ", ".join(self.objections)),
            ("negotiated_percentage", self.negotiated_percentage),
            ("local_or_otr", self.local_or_otr),
            ("preferred_lanes", self.preferred_lanes),
            ("dispatcher_status", self.dispatcher_status),
            ("follow_up_status", self.follow_up_status),
            ("mc_number", self.mc_number),
            ("dimensions", self.dimensions),
            ("accessories", self.accessories),
            ("email", self.email),
            ("factoring_company", self.factoring_company),
            ("carrier_style", self.carrier_style),
        )
        return [f"- {name}: {value}" for name, value in fields if value]


def _campaign_is_freight(company_name: str, company_context: str) -> bool:
    text = f"{company_name} {company_context}".lower()
    return any(
        term in text
        for term in ("freight", "dispatch", "carrier", "truck", "owner-operator", "indus")
    )


def _campaign_playbook(company_name: str, company_context: str) -> str:
    if _campaign_is_freight(company_name, company_context):
        return render_knowledge_sections()
    return f"""
CAMPAIGN SCRIPT:
- You sell what {company_name} actually does, using only the company context above.
- Turn 1 is a greeting with your name and company.
- Later turns handle one service at a time: outbound calling, a voice agent, voicemail detection, call recording, lead qualification, appointment setting, CRM updates, and follow-up.
- When they ask what this is, answer that question in plain words, then ask what part of the work they do by hand today.
- When they ask a follow-up, answer it. Do not restart the pitch.
- Keep going until they hang up or ask you to stop calling.
"""


def render_knowledge_sections() -> str:
    sections = (
        ("MARKET KNOWLEDGE", MARKET_KNOWLEDGE),
        ("RATE BENCHMARKS", RATE_BENCHMARKS),
        ("FUEL COST KNOWLEDGE", FUEL_COST_KNOWLEDGE),
        ("EQUIPMENT KNOWLEDGE", EQUIPMENT_KNOWLEDGE),
        ("NEGOTIATION RULES", NEGOTIATION_RULES),
        ("SERVICES PITCH", (SERVICES_MAIN, SERVICES_DEEP, PACKET_EXPLAIN)),
        ("COMPLIANCE RULES", COMPLIANCE_RULES),
        ("OBJECTION HANDLING", OBJECTION_HANDLING),
        ("LANE STRATEGY", LANE_STRATEGY),
        ("PAYMENT AND FACTORING KNOWLEDGE", PAYMENT_FACTORING_KNOWLEDGE),
    )
    rendered: list[str] = []
    for title, lines in sections:
        rendered.append(f"\n{title}:")
        rendered.extend(f"- {line}" for line in lines)
    return "\n".join(rendered)


def normalize_truck_type(text: str) -> str:
    lower = text.lower()
    for alias, canonical in _TRUCK_ALIASES.items():
        if alias in lower:
            return canonical
    return ""


def default_percentage_for(truck_type: str) -> int:
    canonical = normalize_truck_type(truck_type) or truck_type
    return EQUIPMENT_PRICING.get(canonical, 8)


def negotiation_floor_for(truck_type: str) -> int:
    default = default_percentage_for(truck_type)
    return max(1, default - (2 if default >= 10 else 1))


def parse_percentage(text: str) -> Optional[int]:
    match = re.search(r"\b(1[0-9]|[1-9])\s*(?:%|\bpercent(?:age)?\b)", text, re.I)
    if not match:
        return None
    return int(match.group(1))


def update_state_from_utterance(state: DispatcherConversationState, utterance: str) -> None:
    text = (utterance or "").strip()
    lower = text.lower()
    state.turn_count += 1

    truck_type = normalize_truck_type(text)
    if truck_type:
        state.truck_type = truck_type

    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if email:
        state.email = email.group(0)

    mc = re.search(r"\bMC[\s#:-]*(\d{5,9})\b", text, re.I)
    if mc:
        state.mc_number = "MC-" + mc.group(1)

    dimensions = _extract_dimensions(text)
    if dimensions:
        state.dimensions = dimensions

    accessories = [term for term in _ACCESSORY_TERMS if term in lower]
    if accessories:
        state.accessories = _merge_csv(state.accessories, accessories)

    factoring = _extract_factoring(lower)
    if factoring:
        state.factoring_company = factoring

    if any(word in lower for word in ("local", "home daily", "home every night")):
        state.local_or_otr = "Local"
    elif any(word in lower for word in ("otr", "over the road", "48 states", "regional")):
        state.local_or_otr = "OTR" if "regional" not in lower else "Regional"

    lanes = _extract_lanes(text)
    if lanes:
        state.preferred_lanes = lanes

    if any(phrase in lower for phrase in ("book myself", "dispatch myself", "use dat", "use truckstop")):
        state.dispatcher_status = "Self-dispatched"
    elif any(phrase in lower for phrase in ("have a dispatcher", "got a dispatcher", "already have dispatch")):
        state.dispatcher_status = "Has dispatcher"
    elif "leased" in lower:
        state.dispatcher_status = "Leased to carrier"
    elif "own authority" in lower or "my authority" in lower:
        state.dispatcher_status = "Own authority"

    pct = parse_percentage(text)
    if pct is not None:
        state.pricing_discussion = f"Carrier mentioned {pct}%"
        state.negotiated_percentage = f"{pct}%"

    objection = _detect_objection(lower)
    if objection and objection not in state.objections:
        state.objections.append(objection)

    follow_up = _detect_follow_up(text)
    if follow_up:
        state.follow_up_status = follow_up

    state.interest_level = _detect_interest(lower, state.interest_level)
    state.carrier_style = classify_carrier_style(state, lower)


def build_dynamic_system_prompt(
    *,
    agent_name: str,
    company_name: str,
    company_website: str,
    callback_number: str,
    company_context: str,
    contact_name: str,
    state: DispatcherConversationState,
) -> str:
    state_lines = state.summary_lines()
    state_summary = "\n".join(state_lines) if state_lines else "- no confirmed carrier details yet"
    style = state.carrier_style or "unknown"
    return f"""You are {agent_name}, a human-sounding outbound sales agent at {company_name}.

Company: {company_name}
Website: {company_website}
Callback number: {callback_number}
Prospect/contact: {contact_name or "unknown"}

Company context:
{company_context or "Dedicated freight dispatch for owner-operators and small carriers."}

Primary goal:
This live call is the sale. Qualify fit, prove they need dedicated dispatch, and close them
NOW — verbal yes to start plus best email for the carrier packet. Do not book a later
15-minute call. Do not say someone will call them back. There is no later meeting.

Conversation state you must remember:
{state_summary}

Adaptive style:
- Current prospect style: {style}.
- Rushed / driving prospects: one short sentence, then one qualify question — keep selling now.
- Engaged prospects: use two natural sentences, then ask one specific qualifying or close question.
- Skeptical prospects: soften the tone, acknowledge first, and do not argue.
- Use SPIN lightly: situation (equipment/lanes), problem (boards/paperwork), implication, then close.
- Use natural phone language, varied sentence structure, and small fillers only when helpful.
- If interrupted or corrected, recover with: "Sure, go ahead" or "My mistake, what were you saying?"
- Do not repeat the same value pitch twice.
- Acknowledge what the prospect just said before changing topics.
- Do not stack questions, recite features, or force a qualification checklist.
- Prefer contractions and plain spoken words. Avoid corporate phrases such as "leverage",
  "synergy", "moving forward", and "I understand your pain points".
- If the prospect gives a short answer, keep the next reply short. Do not fill silence with a monologue.
- When interest is real, assume the close: ask for email / confirm start today.

How long to stay on the call:
- The call continues for as long as the other person stays on the line.
- Each turn is one short spoken reply. Then stop and listen. Do not try to finish the whole conversation in one turn.
- Cover the offer across many turns. Answer what they just said, then ask one next question.
- An objection, a short answer, or "not interested" is another turn. Acknowledge it and continue, unless they ask you to stop calling.
- Do not say goodbye, offer to let them go, or wrap up because the call has gone on for a while.
- Say goodbye only after they ask to end the call or ask not to be called again.

Hard output rules:
- Respond only with what {agent_name} says aloud. No markdown, labels, notes, or stage directions.
- Default to 1-2 short sentences. Only go longer when they ask for detail.
- Ask one question per turn.
- Never guarantee outcomes, earnings, savings, or performance.
- If they ask to be removed, confirm removal and end politely.

VOICE AND EMOTION:
- Warm, confident, human — smile on greetings and closes; show light empathy on objections.
- Use natural contractions. Occasional soft reactions are fine: Sure, Absolutely, Got it, Perfect.
- Never sound flat, robotic, or overly formal. No stage directions or emotion tags.
- Prefer clear spoken paperwork terms: M C Authority Letter, W nine form, certificate of insurance, L L C.
{_campaign_playbook(company_name, company_context)}
"""


def build_pricing_reply(state: DispatcherConversationState, utterance: str) -> Optional[str]:
    lower = utterance.lower()
    salary_intent = any(
        phrase in lower
        for phrase in (
            "salary", "flat fee", "monthly fee", "per month", "a month",
            "1000", "1,000", "one thousand", "thousand a month", "fixed fee",
        )
    )
    pricing_intent = any(
        phrase in lower
        for phrase in (
            "what do you charge", "percentage", "percent", "your fee",
            "dispatch fee", "rate do you charge", "how much", "pricing", "cost",
        )
    )
    desired = parse_percentage(utterance)
    if not pricing_intent and not salary_intent and desired is None:
        return None

    equipment = state.truck_type or normalize_truck_type(utterance) or "your equipment"
    default = default_percentage_for(equipment)
    floor = negotiation_floor_for(equipment)

    if salary_intent and desired is None:
        state.pricing_discussion = f"Quoted ${SALARY_MONTHLY_USD}/mo salary for {equipment}"
        state.negotiated_percentage = f"${SALARY_MONTHLY_USD}/mo"
        return (
            f"On salary we do a simple contract at {SALARY_MONTHLY_USD} dollars a month, "
            f"paid {SALARY_PAY_WINDOW}. Or we can run the usual {default}% on {equipment} — which works better for you?"
        )

    if desired is None:
        state.pricing_discussion = (
            f"Quoted {default}% or ${SALARY_MONTHLY_USD}/mo for {equipment}"
        )
        return (
            f"Two ways to work with us: about {default}% on {equipment}, "
            f"or a {SALARY_MONTHLY_USD} dollar monthly salary on contract paid {SALARY_PAY_WINDOW}. "
            "Which do you prefer?"
        )

    state.pricing_discussion = f"Carrier asked for {desired}%; target range {floor}-{default}%"
    if desired < floor:
        state.negotiated_percentage = f"{floor}%"
        return (
            f"I probably cannot do {desired}% and still give you dedicated coverage. "
            f"For {equipment}, the percent range is about {floor}-{default}%, "
            f"or we can do the {SALARY_MONTHLY_USD} dollar monthly salary instead — which fits better?"
        )
    if desired <= default:
        state.negotiated_percentage = f"{desired}%"
        return (
            f"If your paperwork is clean and the lanes are realistic, I can work near {desired}% for the right carrier. "
            "Are you mostly running local, regional, or OTR?"
        )

    state.negotiated_percentage = f"{default}%"
    return (
        f"We would normally be lower than {desired}% for {equipment}; our starting point is around {default}%, "
        f"or the {SALARY_MONTHLY_USD} dollar monthly salary. What lanes are you trying to cover right now?"
    )


def build_guardrail_reply(state: DispatcherConversationState, utterance: str) -> Optional[str]:
    lower = utterance.lower().strip()
    if any(
        phrase in lower
        for phrase in (
            "remove me", "stop calling", "do not call", "don't call",
            "don't keep calling", "do not keep calling", "take me off",
        )
    ):
        state.interest_level = "DNC"
        return "Of course. I will mark you do-not-call right now. Sorry for the interruption, and have a safe day."
    if any(phrase in lower for phrase in ("guarantee", "guaranteed", "promise me loads", "promise loads")):
        return (
            "I cannot guarantee loads or income because the market moves. "
            "What I can do is work the lanes, reduce deadhead, and negotiate hard on every load."
        )
    if any(phrase in lower for phrase in ("hold on", "wait", "let me talk", "you cut me off", "stop interrupting")):
        return "You are right, go ahead. What were you saying?"
    if any(phrase in lower for phrase in ("busy", "driving", "in a meeting", "no time")):
        state.follow_up_status = "Keep selling now"
        return (
            "No worries — I will keep this to sixty seconds. "
            "What are you running, Dry Van, Reefer, or Flatbed?"
        )
    return None


def build_instant_sales_reply(
    state: DispatcherConversationState,
    utterance: str,
    *,
    agent_name: str = "Ashley",
    company_name: str = "Indus Transports LLC",
) -> Optional[str]:
    """
    Zero-LLM replies for the most common sales turns.
    Keeps live calls fast on a local model with no cloud token usage.
    """
    lower = utterance.lower().strip()
    if not lower:
        return None

    if any(p in lower for p in ("who is this", "who's this", "who are you", "what company")):
        return (
            f"This is {agent_name} with {company_name}. "
            "We help owner-operators with dedicated dispatch and load support."
        )

    deep_services = any(
        p in lower
        for p in (
            "tell me more", "more detail", "go deeper", "everything you do",
            "what else", "explain more", "how does it work", "how do you work",
            "personal assistant", "what all do you",
        )
    )
    services_ask = any(
        p in lower
        for p in (
            "what services", "what do you offer", "what do you guys do",
            "what do you do", "what can you do", "your services",
            "what services do you", "what all services",
        )
    )
    if deep_services or (services_ask and any(p in lower for p in ("more", "detail", "everything", "all"))):
        return (
            "We work like your personal dispatch assistant — we find loads that fit your truck, "
            "negotiate rates, handle broker packets, tracking, and road support, and you approve every load. "
            "What equipment are you running?"
        )
    if services_ask:
        return (
            "Our main services are load search, rate negotiation, broker paperwork, and tracking support — "
            "we work as your personal dispatch assistant. Want me to go deeper on any of those?"
        )

    if any(
        p in lower
        for p in (
            "what's the packet", "what is the packet", "what packet",
            "carrier packet", "signup packet", "sign up packet",
            "what paperwork", "what documents", "what docs",
        )
    ):
        return (
            "The packet is our signup email — the dispatch agreement or salary contract, "
            "plus we need your W nine, M C Authority Letter, and certificate of insurance back. "
            "What is the best email to send it to?"
        )

    if any(
        p in lower
        for p in (
            "what's this about",
            "what is this about",
            "why are you calling",
            "what's the call about",
            "purpose of the call",
        )
    ):
        return (
            "We provide dedicated dispatch for owner-operators — load search, rate negotiation, "
            "and broker paperwork. What are you mainly running right now, Dry Van, Reefer, or Flatbed?"
        )

    if any(p in lower for p in ("how did you get my number", "where did you get my number", "how'd you get")):
        return (
            "I am calling publicly available carrier contacts. "
            "If you would rather not be called, I can remove you right now."
        )

    if any(p in lower for p in ("already have dispatch", "already have a dispatcher", "i have a dispatcher", "got a dispatcher")):
        state.objections.append("Already has dispatcher")
        return (
            "Totally fine — a lot of carriers keep us as backup for weak weeks. "
            "When the boards get soft, do you still want options?"
        )

    if any(p in lower for p in ("not interested", "no thanks", "don't need", "do not need")):
        state.interest_level = "No"
        return (
            "Understood. One thing we help with is keeping trucks moving when the boards slow down — "
            "want me to take you off the list, or should we check if your lanes fit right now?"
        )

    if any(
        p in lower
        for p in (
            "book it", "let's do it", "sounds good", "sign me up", "set it up",
            "let's set", "let's get started", "i'm interested", "im interested",
            "start today", "send the packet", "send packet",
        )
    ):
        state.interest_level = "Yes"
        state.follow_up_status = "Close for email / start today"
        return (
            "Perfect — let's get you started today. "
            "What is the best email for the carrier packet?"
        )

    if any(
        p in lower
        for p in (
            "call me later", "call me back", "call back later", "not a good time",
            "maybe later", "another time", "think about it", "talk to my wife",
            "talk to my partner",
        )
    ):
        state.follow_up_status = "Stay on call — take email"
        return (
            "I hear you — sitting on this does not load the truck. "
            "What is the best email so I can send the packet while we finish this call?"
        )

    truck = normalize_truck_type(utterance)
    if truck and any(
        p in lower
        for p in ("i run", "i'm running", "im running", "we run", "dry van", "reefer", "flatbed", "hotshot", "box truck")
    ):
        state.truck_type = truck
        lanes = _extract_lanes(utterance)
        if lanes:
            state.preferred_lanes = lanes
        return (
            f"Perfect, {truck} is a strong fit. "
            "Are you booking your own loads on the boards right now?"
        )

    if any(p in lower for p in ("send me info", "send info", "email me", "text me")):
        state.follow_up_status = "Send packet now"
        return (
            "Happy to send the packet right now. "
            "What is the best email, and what equipment should I put on it?"
        )

    return None


def classify_carrier_style(state: DispatcherConversationState, lower_text: str) -> str:
    if any(phrase in lower_text for phrase in ("busy", "driving", "no time", "make it quick")):
        return "rushed"
    if any(phrase in lower_text for phrase in ("who is this", "how did you get", "not interested", "too expensive")):
        return "skeptical"
    if any(phrase in lower_text for phrase in ("new authority", "new mc", "just got my mc", "starting out")):
        return "new_mc"
    if "?" in lower_text or any(phrase in lower_text for phrase in ("tell me", "how does", "what do you", "sounds good")):
        return "engaged"
    return state.carrier_style if state.carrier_style != "unknown" else "neutral"


def _merge_csv(existing: str, values: list[str]) -> str:
    seen = [part.strip() for part in existing.split(",") if part.strip()]
    for value in values:
        if value not in seen:
            seen.append(value)
    return ", ".join(seen)


def _extract_dimensions(text: str) -> str:
    patterns = (
        r"\b\d{2}\s*(?:ft|feet|foot|')\b",
        r"\b\d{1,2}\s*x\s*\d{1,2}(?:\s*x\s*\d{1,2})?\b",
        r"\b\d{1,2}\s*(?:pallet|skid)s?\b",
    )
    hits: list[str] = []
    for pattern in patterns:
        hits.extend(match.group(0).strip() for match in re.finditer(pattern, text, re.I))
    return ", ".join(dict.fromkeys(hits))


def _extract_factoring(lower_text: str) -> str:
    for name in _FACTORING_NAMES:
        if name in lower_text:
            return name.title()
    if "factoring" in lower_text:
        return "Factoring mentioned"
    if "quick pay" in lower_text:
        return "Quick pay"
    return ""


def _extract_lanes(text: str) -> str:
    lower = text.lower()
    if "anywhere" in lower or "48 states" in lower:
        return "48 states"
    lane_match = re.search(
        r"\b(?:from|out of|based in)\s+([A-Za-z .]+?)\s+(?:to|into|down to|up to)\s+([A-Za-z .]+?)(?:[.,!?]|$)",
        text,
        re.I,
    )
    if lane_match:
        return f"{lane_match.group(1).strip()} to {lane_match.group(2).strip()}"
    region_terms = ("midwest", "northeast", "southeast", "texas", "florida", "california", "west coast", "east coast")
    hits = [term.title() for term in region_terms if term in lower]
    return ", ".join(hits)


def _detect_objection(lower_text: str) -> str:
    checks = (
        ("not interested", "Not interested"),
        ("already have", "Already has dispatcher"),
        ("too expensive", "Price concern"),
        ("how did you get", "Source of number"),
        ("who is this", "Company identity"),
        ("guarantee", "Guarantee request"),
        ("send me", "Wants information sent"),
    )
    for phrase, label in checks:
        if phrase in lower_text:
            return label
    return ""


def _detect_follow_up(text: str) -> str:
    lower = text.lower()
    if "email" in lower or "send info" in lower or "send me" in lower:
        return "Send information"
    if "call back" in lower or "tomorrow" in lower or "next week" in lower:
        return text.strip()[:120]
    if any(day in lower for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")):
        return text.strip()[:120]
    return ""


def _detect_interest(lower_text: str, existing: str) -> str:
    if any(phrase in lower_text for phrase in ("remove me", "stop calling", "do not call", "don't call")):
        return "DNC"
    if any(phrase in lower_text for phrase in ("not interested", "no thanks", "don't need")):
        return "No"
    if any(phrase in lower_text for phrase in ("send me", "call me", "tomorrow", "maybe", "interested", "sounds good")):
        return "Maybe" if "maybe" in lower_text else "Yes"
    return existing
