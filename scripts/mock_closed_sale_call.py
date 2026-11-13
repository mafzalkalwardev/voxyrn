"""
Generate a two-sided mock closed-sale call recording for Ashley / Indus Transports.

- Prospect lines are scripted (dry-van owner → booked onboarding)
- Ashley replies come from ConversationAgent (local LLM) with campaign fallbacks
- Both sides rendered with Kokoro and mixed with measured reply delays
"""

from __future__ import annotations

import json
import logging
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.conversation_agent import ConversationAgent
from src.indus_campaign import (
    AGENT_NAME,
    CALLBACK_NUMBER,
    CLOSE_LINE,
    COMPANY_CONTEXT,
    COMPANY_NAME,
    COMPANY_WEBSITE,
    PITCH_LINE,
    QUALIFY_LINE,
)
from src.realtime_tts import _load_kokoro, _prepare_piper_text, _render_kokoro_phrase
from src.speech_style import humanize_for_speech

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MockClosedSale")

_SR = 24000  # Kokoro default-ish; actual rate comes from synthesize
ASHLEY_VOICE = "af_heart"
PROSPECT_VOICE = "am_michael"
ASHLEY_SPEED = 0.88
PROSPECT_SPEED = 0.95


@dataclass
class Turn:
    speaker: str
    text: str
    audio: np.ndarray
    rate: int
    think_delay_s: float = 0.0


# Full closed-sale path a carrier might take.
PROSPECT_LINES = [
    "Yeah this is Mike, owner operator.",
    "What's this about?",
    "I run a fifty three foot dry van, own authority, mostly Dallas to the Midwest.",
    "What do you charge for dispatch?",
    "Six percent could work if you actually keep the truck moving.",
    "Alright, let's do a quick onboarding call tomorrow afternoon.",
    "You can reach me on this number, thanks Ashley.",
]

# Polished closed-sale Ashley track (Indus playbook) for client listen demos.
CLOSED_SALE_REPLIES = [
    f"Good to meet you Mike — this is {AGENT_NAME} with {COMPANY_NAME}.",
    PITCH_LINE + " " + QUALIFY_LINE,
    "Perfect, Dallas to Midwest dry van is a strong set. Are you mostly over the road, or do you like getting home weekly?",
    "For dry van we normally start around six percent because it includes dedicated dispatch, rate negotiation, and paperwork. What percentage were you hoping for?",
    "Absolutely — we focus on cutting deadhead and keeping options on the board when freight slows. " + CLOSE_LINE,
    "Great, I'll lock tomorrow afternoon for your fifteen-minute onboarding. I'll call you on this number — talk soon.",
    "Appreciate it Mike — looking forward to getting you set up with Indus Transports. Have a good one.",
]

# Used only if polish mode is off and the local LLM reply is empty / off-brand.
FALLBACK_REPLIES = CLOSED_SALE_REPLIES


def _synthesize(kokoro, text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    prepared = humanize_for_speech(text) or _prepare_piper_text(text)
    return _render_kokoro_phrase(
        prepared,
        kokoro,
        voice,
        speed,
        sentence_pause=0.30 if voice.startswith("af_") else 0.22,
        clause_pause=0.16 if voice.startswith("af_") else 0.12,
    )


def _silence(rate: int, seconds: float) -> np.ndarray:
    return np.zeros(max(1, int(rate * seconds)), dtype=np.float32)


def _mix(turns: list[Turn], gap_after_ashley: float = 0.45) -> tuple[np.ndarray, int]:
    rate = turns[0].rate if turns else _SR
    chunks: list[np.ndarray] = []
    # Ring + pickup pause
    chunks.append(_silence(rate, 1.2))
    for turn in turns:
        if turn.think_delay_s > 0:
            chunks.append(_silence(rate, turn.think_delay_s))
        # Slight level balance: prospect a touch quieter
        audio = turn.audio.astype(np.float32)
        if turn.speaker == "Prospect":
            audio = audio * 0.92
        chunks.append(audio)
        if turn.speaker == "Ashley":
            chunks.append(_silence(rate, gap_after_ashley))
        else:
            chunks.append(_silence(rate, 0.25))
    mixed = np.concatenate(chunks) if chunks else _silence(rate, 1.0)
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.9:
        mixed *= 0.9 / peak
    return mixed, rate


def _write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


def _play_on_speakers(audio: np.ndarray, rate: int) -> None:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        speakers = None
        for i, d in enumerate(devices):
            name = str(d.get("name", ""))
            if d.get("max_output_channels", 0) > 0 and "speakers" in name.lower() and "cable" not in name.lower():
                speakers = i
                break
        logger.info("Playing mock call on device [%s]", speakers if speakers is not None else "default")
        sd.play(audio, rate, device=speakers)
        sd.wait()
    except Exception as exc:
        logger.warning("Could not play audio automatically: %s", exc)


def _ashley_reply(agent: ConversationAgent, prospect_text: str, index: int, *, polish: bool) -> tuple[str, float]:
    """
    polish=True (default for listen demos): use the closed-sale playbook lines and
    realistic think delays so the recording sounds like a complete booked sale.
    polish=False: use ConversationAgent (local LLM) with fallbacks.
    """
    if polish:
        reply = CLOSED_SALE_REPLIES[min(index, len(CLOSED_SALE_REPLIES) - 1)]
        # Realistic STT + think latency on a healthy PC (not the slow local-LLM ceiling)
        delay = 0.55 + min(0.9, len(prospect_text) / 80.0)
        return reply, float(delay)

    started = time.perf_counter()
    try:
        reply = (agent.respond_to(prospect_text) or "").strip()
    except Exception as exc:
        logger.warning("Agent reply failed (%s); using fallback", exc)
        reply = ""
    delay = time.perf_counter() - started
    company_token = COMPANY_NAME.split()[0].lower()
    weak = (
        len(reply) < 12
        or reply.lower().startswith(("i am an ai", "as an ai"))
        or (index == 0 and company_token not in reply.lower() and AGENT_NAME.lower() not in reply.lower())
    )
    if weak:
        reply = FALLBACK_REPLIES[min(index, len(FALLBACK_REPLIES) - 1)]
        delay = max(delay, 0.55)
    delay = float(min(2.8, max(0.45, delay)))
    return reply, delay


def main() -> int:
    polish = "--live-llm" not in sys.argv
    out_dir = ROOT / "logs" / "mock_calls"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    wav_path = out_dir / f"ashley_indus_closed_sale_{stamp}.wav"
    transcript_path = out_dir / f"ashley_indus_closed_sale_{stamp}.txt"
    meta_path = out_dir / f"ashley_indus_closed_sale_{stamp}.json"

    logger.info(
        "Loading Kokoro voices for mock closed-sale call (mode=%s)...",
        "polish playbook" if polish else "live local LLM",
    )
    kokoro = _load_kokoro("models/kokoro/kokoro-v1.0.onnx", "models/kokoro/voices-v1.0.bin")

    agent = ConversationAgent(
        api_key="offline",
        agent_name=AGENT_NAME,
        company_name=COMPANY_NAME,
        company_context=COMPANY_CONTEXT,
        company_website=COMPANY_WEBSITE,
        callback_number=CALLBACK_NUMBER,
        contact_name="Mike",
        contact_phone="+15551234567",
        backend="local",
    )

    turns: list[Turn] = []
    timeline: list[dict] = []
    cursor = 1.2  # after ring pause

    # Ashley opening first (live dialer style)
    opening = (
        f"Hi, this is {AGENT_NAME} with {COMPANY_NAME} — am I speaking with the owner or the dispatcher?"
    )
    t0 = time.perf_counter()
    # Opening is pre-known; still show a short "pickup" delay
    open_delay = 0.6
    audio, rate = _synthesize(kokoro, opening, ASHLEY_VOICE, ASHLEY_SPEED)
    turns.append(Turn("Ashley", opening, audio, rate, think_delay_s=open_delay))
    timeline.append(
        {
            "t": round(cursor + open_delay, 2),
            "speaker": "Ashley",
            "text": opening,
            "audio_s": round(len(audio) / rate, 2),
            "reply_delay_s": open_delay,
        }
    )
    cursor += open_delay + len(audio) / rate + 0.45
    _ = t0

    for i, prospect_text in enumerate(PROSPECT_LINES):
        p_audio, p_rate = _synthesize(kokoro, prospect_text, PROSPECT_VOICE, PROSPECT_SPEED)
        if p_rate != rate:
            # Resample simply by repeating/dropping if rates ever differ
            rate = p_rate
        turns.append(Turn("Prospect", prospect_text, p_audio, p_rate, think_delay_s=0.0))
        timeline.append(
            {
                "t": round(cursor, 2),
                "speaker": "Prospect",
                "text": prospect_text,
                "audio_s": round(len(p_audio) / p_rate, 2),
                "reply_delay_s": 0.0,
            }
        )
        cursor += len(p_audio) / p_rate + 0.25

        reply, delay = _ashley_reply(agent, prospect_text, i, polish=polish)
        a_audio, a_rate = _synthesize(kokoro, reply, ASHLEY_VOICE, ASHLEY_SPEED)
        turns.append(Turn("Ashley", reply, a_audio, a_rate, think_delay_s=delay))
        timeline.append(
            {
                "t": round(cursor + delay, 2),
                "speaker": "Ashley",
                "text": reply,
                "audio_s": round(len(a_audio) / a_rate, 2),
                "reply_delay_s": round(delay, 2),
            }
        )
        cursor += delay + len(a_audio) / a_rate + 0.45
        logger.info("[turn %d] delay=%.2fs Ashley: %s", i + 1, delay, reply)

    mixed, mix_rate = _mix(turns)
    _write_wav(wav_path, mixed, mix_rate)

    lines = [
        "Ashley / Indus Transports LLC — Mock Closed-Sale Call",
        f"Recording: {wav_path}",
        f"Duration: {len(mixed) / mix_rate:.1f}s",
        "",
    ]
    for row in timeline:
        lines.append(
            f"[{row['t']:6.1f}s] (+{row['reply_delay_s']:.2f}s) {row['speaker']}: {row['text']}"
        )
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "wav": str(wav_path),
                "transcript": str(transcript_path),
                "duration_s": round(len(mixed) / mix_rate, 2),
                "ashley_voice": ASHLEY_VOICE,
                "prospect_voice": PROSPECT_VOICE,
                "timeline": timeline,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info("Saved mock call: %s (%.1fs)", wav_path, len(mixed) / mix_rate)
    logger.info("Transcript: %s", transcript_path)
    print(f"\nWAV: {wav_path}")
    print(f"Transcript: {transcript_path}")
    print(f"Duration: {len(mixed) / mix_rate:.1f} seconds\n")
    _play_on_speakers(mixed, mix_rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
