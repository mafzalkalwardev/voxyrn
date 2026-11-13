"""
Human speech styling for Ashley's live TTS.

- Expand trucking / paperwork abbreviations so Kokoro does not mumble them
- Soften punctuation for warmer, more emotional delivery
"""

from __future__ import annotations

import re

# Longer phrases first so partial matches do not win.
_PRONUNCIATION_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\bMC\s*Authority\s*Letter\b", "M C Authority Letter"),
    (r"\bAuthority\s*Letter\b", "Authority Letter"),
    (r"\bCertificate\s+of\s+Insurance\b", "certificate of insurance"),
    (r"\bInsurance\s+Certificate\b", "insurance certificate"),
    (r"\bNotice\s+of\s+Assignment\b", "notice of assignment"),
    (r"\bBill\s+of\s+Lading\b", "bill of lading"),
    (r"\bProof\s+of\s+Delivery\b", "proof of delivery"),
    (r"\bW[\-\s]?9\s*Form\b", "W nine form"),
    (r"\bW[\-\s]?9\b", "W nine"),
    (r"\bMC\s*(?:number|num|#)\b", "M C number"),
    (r"\bMC#\b", "M C number"),
    (r"\bDOT\s*(?:number|num|#)\b", "D O T number"),
    (r"\bUS\s*DOT\b", "U S D O T"),
    (r"\bUSDOT\b", "U S D O T"),
    (r"\bCOI\b", "certificate of insurance"),
    (r"\bNOA\b", "notice of assignment"),
    (r"\bBOL\b", "bill of lading"),
    (r"\bB/?L\b", "bill of lading"),
    (r"\bPOD\b", "proof of delivery"),
    (r"\bEIN\b", "E I N"),
    (r"\bCDL\b", "C D L"),
    (r"\bFMCSA\b", "F M C S A"),
    (r"\bIFTA\b", "I F T A"),
    (r"\bTONU\b", "T O N U"),
    (r"\bRPM\b", "R P M"),
    (r"\bOTR\b", "over the road"),
    (r"\bLLC\.?\b", "L L C"),
    (r"\bInc\.?\b", "Incorporated"),
    (r"\bCorp\.?\b", "Corporation"),
    (r"\bMC\.?\b", "M C"),
    (r"\bDOT\.?\b", "D O T"),
    (r"\bDBA\b", "D B A"),
    (r"\bGPS\b", "G P S"),
    (r"\bETA\b", "E T A"),
    (r"\bSOP\b", "S O P"),
    (r"\bPDF\b", "P D F"),
    (r"\bUSA\b", "U S A"),
)

_SPOKEN_NUMBERS = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
    11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    30: "thirty", 40: "forty", 50: "fifty", 60: "sixty",
}

# Soft discourse openings — comma creates a natural breath for Kokoro.
_SOFT_OPENERS = (
    (r"^(Hi)\b", r"\1,"),
    (r"^(Hey)\b", r"\1,"),
    (r"^(Hello)\b", r"\1,"),
    (r"^(Sure)\b", r"\1,"),
    (r"^(Absolutely)\b", r"\1,"),
    (r"^(Of course)\b", r"\1,"),
    (r"^(Got it)\b", r"\1,"),
    (r"^(Perfect)\b", r"\1,"),
    (r"^(Great)\b", r"\1,"),
    (r"^(Understood)\b", r"\1,"),
    (r"^(No problem)\b", r"\1,"),
    (r"^(That makes sense)\b", r"\1,"),
)


def clean_tts_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(
        r"^(?:assistant|agent|tony|ashley|voxyrn|indus)\s*:\s*",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def expand_dispatch_terms(text: str) -> str:
    """Expand paperwork and trucking abbreviations for clear spoken delivery."""
    out = text
    for pattern, replacement in _PRONUNCIATION_PHRASES:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


def _speak_small_number(match: re.Match[str]) -> str:
    raw = match.group(0)
    if not re.fullmatch(r"\d{1,2}", raw):
        return raw
    return _SPOKEN_NUMBERS.get(int(raw), raw)


def _speak_phone_chunk(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return match.group(0)
    spoken = " ".join(_SPOKEN_NUMBERS.get(int(d), d) for d in digits)
    # Group like a US phone for natural rhythm: 3-3-4
    parts = spoken.split()
    return f"{' '.join(parts[:3])}, {' '.join(parts[3:6])}, {' '.join(parts[6:])}"


def humanize_for_speech(text: str) -> str:
    """
    Prepare text for warm, human Kokoro delivery on a live sales call.
    """
    text = clean_tts_text(text)
    if not text:
        return ""

    text = text.replace("—", ", ").replace("–", ", ").replace(" - ", ", ")
    text = text.replace("...", ", ")
    text = expand_dispatch_terms(text)

    # US phone numbers → spoken digits with light grouping pauses
    text = re.sub(
        r"\+?1?[\s\-.]?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b",
        _speak_phone_chunk,
        text,
    )
    text = re.sub(r"\b\d{1,2}\b", _speak_small_number, text)

    # Soft emotional openers get a breath comma
    for pattern, repl in _SOFT_OPENERS:
        text = re.sub(pattern, repl, text)

    # Keep enthusiasm light — one exclamation max, convert extras to periods
    if text.count("!") > 1:
        first = text.find("!")
        text = text[: first + 1] + text[first + 1 :].replace("!", ".")

    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r",\s*,+", ", ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# Warm speaking style injected into the live sales LLM prompt.
EMOTIONAL_SPEAKING_STYLE = """
VOICE AND EMOTION (speak this way on every turn):
- Sound warm, confident, and human — like a helpful sales pro, not a call-center robot.
- Smile on greetings and closes. Show light empathy on objections ("that makes sense", "totally fair").
- Use natural contractions (I'm, we've, you're). Prefer short spoken rhythm over stiff sentences.
- One soft reaction word is fine when it fits: Sure, Absolutely, Got it, Perfect — then the point.
- Never sound flat, rushed, or overly formal. Never invent emotion tags or stage directions.
- When saying paperwork terms, prefer clear expansions: M C Authority Letter, W nine, certificate of insurance, L L C.
"""
