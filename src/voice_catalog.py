"""English and Spanish voices the server can run. Model files are downloaded, not committed."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceChoice:
    engine: str
    voice_id: str
    language: str
    label: str


# Edge neural voices. These need network at synthesis time.
ENGLISH_EDGE = (
    VoiceChoice("edge", "en-US-GuyNeural", "en", "English, US, Guy"),
    VoiceChoice("edge", "en-US-JennyNeural", "en", "English, US, Jenny"),
    VoiceChoice("edge", "en-GB-SoniaNeural", "en", "English, UK, Sonia"),
    VoiceChoice("edge", "en-GB-RyanNeural", "en", "English, UK, Ryan"),
)
SPANISH_EDGE = (
    VoiceChoice("edge", "es-MX-DaliaNeural", "es", "Spanish, Mexico, Dalia"),
    VoiceChoice("edge", "es-MX-JorgeNeural", "es", "Spanish, Mexico, Jorge"),
    VoiceChoice("edge", "es-ES-ElviraNeural", "es", "Spanish, Spain, Elvira"),
    VoiceChoice("edge", "es-ES-AlvaroNeural", "es", "Spanish, Spain, Alvaro"),
)

# Piper ONNX names. install.sh can fetch these from rhasspy/piper-voices.
ENGLISH_PIPER = (
    VoiceChoice("piper", "en_US-lessac-medium", "en", "English, US, female, Lessac, medium"),
    VoiceChoice("piper", "en_US-lessac-high", "en", "English, US, female, Lessac, high"),
    VoiceChoice("piper", "en_US-amy-medium", "en", "English, US, female, Amy, medium"),
    VoiceChoice("piper", "en_US-ryan-high", "en", "English, US, male, Ryan, high"),
    VoiceChoice("piper", "en_US-joe-medium", "en", "English, US, male, Joe, medium"),
    VoiceChoice("piper", "en_GB-alba-medium", "en", "English, UK, female, Alba, medium"),
    VoiceChoice("piper", "en_GB-cori-medium", "en", "English, UK, female, Cori, medium"),
    VoiceChoice("piper", "en_GB-alan-medium", "en", "English, UK, male, Alan, medium"),
    VoiceChoice("piper", "en_GB-northern_english_male-medium", "en", "English, UK, male, Northern, medium"),
)
SPANISH_PIPER = (
    VoiceChoice("piper", "es_ES-davefx-medium", "es", "Spanish, Spain, male, Davefx, medium"),
    VoiceChoice("piper", "es_ES-sharvard-medium", "es", "Spanish, Spain, female, Sharvard, medium"),
    VoiceChoice("piper", "es_MX-ald-medium", "es", "Spanish, Mexico, male, Ald, medium"),
    VoiceChoice("piper", "es_MX-claude-high", "es", "Spanish, Mexico, female, Claude, high"),
)
# Kokoro ids inside voices-v1.0.bin. The bin is downloaded, not committed.
ENGLISH_KOKORO = (
    VoiceChoice("kokoro", "af_heart", "en", "English, US, Heart"),
    VoiceChoice("kokoro", "af_bella", "en", "English, US, Bella"),
    VoiceChoice("kokoro", "am_adam", "en", "English, US, Adam"),
    VoiceChoice("kokoro", "am_michael", "en", "English, US, Michael"),
    VoiceChoice("kokoro", "bf_emma", "en", "English, UK, Emma"),
    VoiceChoice("kokoro", "bm_george", "en", "English, UK, George"),
)
SPANISH_KOKORO = (
    VoiceChoice("kokoro", "ef_dora", "es", "Spanish, Dora"),
    VoiceChoice("kokoro", "em_alex", "es", "Spanish, Alex"),
)
CATALOG = (
    ENGLISH_EDGE
    + SPANISH_EDGE
    + ENGLISH_PIPER
    + SPANISH_PIPER
    + ENGLISH_KOKORO
    + SPANISH_KOKORO
)

SUPPORTED_LANGUAGES = frozenset({"en", "es"})

# Piper lessac medium, 4 vCPU x86: 12.3 seconds of speech synthesized in 0.26 seconds (47.6x).
# That host is not ARM. An ARM estimate is about 0.6 of this figure. Kokoro was not timed
# because its model is not installed. Edge is a hosted voice, so its factor is not a CPU cost.
VOICE_RTF = {
    "piper": 47.6,
    "kokoro": 6.0,
    "edge": 1.0,
    "espeak": 40.0,
}
VOICE_RTF_MEASURED = {"piper"}


def rtf_label(engine: str) -> str:
    value = VOICE_RTF.get(engine)
    if value is None:
        return ""
    kind = "measured on x86" if engine in VOICE_RTF_MEASURED else "estimate"
    return f"{value:.1f} {kind}"


def voices_for(language: str, engine: str | None = None) -> tuple[VoiceChoice, ...]:
    lang = (language or "en").strip().lower()[:2]
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError("Voxyrn voices are English and Spanish only")
    chosen = tuple(item for item in CATALOG if item.language == lang)
    if not chosen:
        raise ValueError(f"No catalog voice for {lang}")
    if engine:
        key = engine.strip().lower()
        chosen = tuple(item for item in chosen if item.engine == key)
    return chosen


def engine_of_voice(voice_id: str) -> str:
    for item in CATALOG:
        if item.voice_id == voice_id:
            return item.engine
    return "espeak"


def language_of_voice(voice_id: str) -> str:
    for item in CATALOG:
        if item.voice_id == voice_id:
            return item.language
    lowered = (voice_id or "").lower()
    if lowered.startswith("es_") or lowered.startswith("es-"):
        return "es"
    return "en"
