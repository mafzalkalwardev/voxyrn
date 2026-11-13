import os
import json
from pathlib import Path
from dotenv import load_dotenv
from src.paths import runtime_base

BASE_DIR = runtime_base()
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "dialer_config.json"

load_dotenv(BASE_DIR / ".env")

_DEFAULTS = {
    "contacts_file": str(DATA_DIR / "contacts.xlsx"),
    "profile_name": "sales_profile",
    "callback_number": "+13172100495",
    "agent_name": "Ashley",
    "company_name": "Indus Transports LLC",
    "company_website": "https://industransports.com",
    "company_context": (
        "Indus Transports LLC is a United States freight dispatch company that partners "
        "with owner-operators and small fleets. We find and negotiate loads, handle broker "
        "paperwork and rate confirmations, support tracking, and help keep trucks moving "
        "across all 48 states. We work Dry Van, Reefer, Flatbed, Step Deck, Hotshot, "
        "Box Truck, Sprinter Van, Power Only, and Car Hauler. On this live call Ashley "
        "qualifies equipment and lanes, sells dedicated dispatch, and closes the carrier "
        "to start today — verbal yes plus email for the packet. Never guarantee earnings, "
        "miles, or freight volume. Never push the deal to a later callback."
    ),
    "screening_purpose_text": (
        "dedicated freight dispatch and load support for owner-operators with Indus Transports"
    ),
    "call_objective": (
        "qualify equipment and lanes, prove they need dedicated dispatch, and close them "
        "to start on this call — get a verbal yes and the best email for the carrier packet"
    ),
    "call_offer": (
        "dedicated dispatch, high-paying load search, rate negotiation, broker paperwork, "
        "tracking support, and 24/7 road support across 48 states — carrier approves every load"
    ),
    "groq_model": "llama-3.3-70b-versatile",
    "rotation_minutes": 60,
    "autocut_seconds": 15,
    "loopback_device": "CABLE Input",
    "call_timeout": 60,
    "call_max_duration": 0,
    # Realtime conversation settings
    "capture_device": "default",
    "tts_engine": "edge",
    "tts_voice": "en-US-GuyNeural",
    "piper_model_path": "models/piper/en_US-lessac-medium.onnx",
    "piper_length_scale": 1.07,
    "piper_noise_scale": 0.5,
    "piper_noise_w_scale": 0.92,
    "tts_allow_sapi_fallback": False,
    "stt_model": "whisper-large-v3",
    "vad_threshold": 0.015,
    # Answer detection — controls when Tony first speaks after CONNECTED is detected
    "answered_speak_delay_seconds": 0.6,  # short pickup pause; long delays cause hang-ups
    "wait_for_human_audio": True,         # listen for inbound audio before speaking
    "human_audio_timeout_seconds": 2.5,   # max seconds to listen before speaking anyway
    "answer_confirm_polls": 2,            # consecutive DOM polls required to confirm CONNECTED
    "min_ring_seconds": 2.0,              # ignore transient DOM cues immediately after dialing
    "max_ring_seconds": 45.0,             # stop waiting for no-answer calls after this window
    "voicemail_detect_seconds": 15.0,     # active voicemail cue window after answer evidence
    "call_cooldown_seconds": 10.0,        # pause between live calls
    "n_slots": 1,
    "max_concurrent_dials": 5,
    "force_requested_slots": False,
    "allow_shared_audio_routes": False,
    "parallel_profiles": [],
    # VAD tuning — operators can adjust from Settings page
    "vad_silence_frames": 7,              # frames of silence before utterance ends (7x30ms=210ms)
    "vad_speech_frames": 2,               # frames of speech required to start utterance
    # STT reliability
    "stt_retry_count": 2,                 # number of STT retries on empty/failure
    # TTS pre-warming
    "tts_warmup": True,                   # pre-generate common phrases at startup
    # Before live dials: set Windows mic/speaker + Chrome mic permission automatically
    "auto_audio_setup": True,
    # Live Google Voice Chrome window (Connect/Verify stays headed)
    "chrome_headless": True,
    # Hear both sides on Speakers while dialing (False = silent; recordings still save both)
    "live_listen_enabled": False,
}


def _server_mode() -> bool:
    return os.getenv("VOXYRN_SERVER", "").strip().lower() in {"1", "true", "yes", "on"}


def _groq_key_present() -> bool:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return bool(key) and not key.startswith("your_")


def _load_json() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


class Config:
    def __init__(self):
        j = _load_json()

        self.contacts_file = Path(
            os.getenv("CONTACTS_FILE", j.get("contacts_file", _DEFAULTS["contacts_file"]))
        )
        self.profile_name = os.getenv(
            "PROFILE_NAME", j.get("profile_name", _DEFAULTS["profile_name"])
        )
        self.callback_number = os.getenv(
            "CALLBACK_NUMBER",
            os.getenv("GOOGLE_VOICE_NUMBER", j.get("callback_number", _DEFAULTS["callback_number"])),
        )
        self.agent_name = os.getenv(
            "AGENT_NAME", j.get("agent_name", _DEFAULTS["agent_name"])
        )
        self.company_name = os.getenv(
            "COMPANY_NAME", j.get("company_name", _DEFAULTS["company_name"])
        )
        self.company_website = os.getenv(
            "COMPANY_WEBSITE", j.get("company_website", _DEFAULTS["company_website"])
        )
        self.company_context = os.getenv(
            "COMPANY_CONTEXT", j.get("company_context", _DEFAULTS["company_context"])
        )
        self.screening_purpose_text: str = os.getenv(
            "SCREENING_PURPOSE_TEXT",
            str(j.get("screening_purpose_text", _DEFAULTS.get("screening_purpose_text", ""))),
        ).strip()
        self.call_objective: str = os.getenv(
            "CALL_OBJECTIVE",
            str(j.get("call_objective", _DEFAULTS.get("call_objective", ""))),
        ).strip()
        self.call_offer: str = os.getenv(
            "CALL_OFFER",
            str(j.get("call_offer", _DEFAULTS.get("call_offer", ""))),
        ).strip()
        self.groq_api_key: str = os.getenv("GROQ_API_KEY", "")
        self.groq_model: str = os.getenv(
            "GROQ_MODEL", j.get("groq_model", _DEFAULTS["groq_model"])
        )
        llm_fallback = "groq"
        if _server_mode() and "LLM_BACKEND" not in os.environ and "llm_backend" not in j:
            llm_fallback = "groq" if _groq_key_present() else "local"
        self.llm_backend: str = os.getenv(
            "LLM_BACKEND", j.get("llm_backend", llm_fallback)
        ).strip().lower()
        self.local_llm_path: str = os.getenv(
            "LOCAL_LLM_PATH",
            j.get("local_llm_path", "models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf"),
        )
        self.llm_model_realtime: str = os.getenv(
            "LLM_MODEL_REALTIME", j.get("llm_model_realtime", self.groq_model)
        )
        self.llm_model_batch: str = os.getenv(
            "LLM_MODEL_BATCH", j.get("llm_model_batch", self.groq_model)
        )
        stt_fallback = "groq"
        if _server_mode() and "STT_BACKEND" not in os.environ and "stt_backend" not in j:
            stt_fallback = "groq" if _groq_key_present() else "local"
        self.stt_backend: str = os.getenv(
            "STT_BACKEND", j.get("stt_backend", stt_fallback)
        ).strip().lower()
        self.stt_local_model: str = os.getenv(
            "STT_LOCAL_MODEL", j.get("stt_local_model", "base")
        )
        self.stt_compute_type: str = os.getenv(
            "STT_COMPUTE_TYPE", j.get("stt_compute_type", "int8")
        ).strip() or "int8"
        self.rotation_minutes: int = int(
            os.getenv("ROTATION_MINUTES", j.get("rotation_minutes", _DEFAULTS["rotation_minutes"]))
        )
        self.autocut_seconds: int = int(
            os.getenv("AUTOCUT_SECONDS", j.get("autocut_seconds", _DEFAULTS["autocut_seconds"]))
        )
        loop_fallback = _DEFAULTS["loopback_device"]
        if _server_mode() and "LOOPBACK_DEVICE" not in os.environ and "loopback_device" not in j:
            loop_fallback = "voxyrn_to_gv"
        from src.linux_audio import map_windows_audio_name

        self.loopback_device: str = map_windows_audio_name(
            os.getenv("LOOPBACK_DEVICE", j.get("loopback_device", loop_fallback)),
            kind="loopback",
        )
        self.call_timeout: int = int(
            os.getenv("CALL_TIMEOUT", j.get("call_timeout", _DEFAULTS["call_timeout"]))
        )
        self.call_max_duration: int = int(
            os.getenv("CALL_MAX_DURATION", j.get("call_max_duration", _DEFAULTS["call_max_duration"]))
        )
        capture_fallback = _DEFAULTS["capture_device"]
        if _server_mode() and "CAPTURE_DEVICE" not in os.environ and "capture_device" not in j:
            capture_fallback = "voxyrn_from_gv.monitor"
        self.capture_device: str = map_windows_audio_name(
            os.getenv("CAPTURE_DEVICE", j.get("capture_device", capture_fallback)),
            kind="capture",
        )
        tts_fallback = _DEFAULTS["tts_engine"]
        if _server_mode() and "TTS_ENGINE" not in os.environ and "tts_engine" not in j:
            tts_fallback = "piper"
        self.tts_engine: str = os.getenv(
            "TTS_ENGINE", j.get("tts_engine", tts_fallback)
        ).strip().lower()
        self.tts_voice: str = os.getenv(
            "TTS_VOICE", j.get("tts_voice", _DEFAULTS["tts_voice"])
        )
        self.piper_model_path: str = os.getenv(
            "PIPER_MODEL_PATH", j.get("piper_model_path", _DEFAULTS["piper_model_path"])
        )
        self.piper_length_scale: float = float(
            os.getenv("PIPER_LENGTH_SCALE", j.get("piper_length_scale", _DEFAULTS["piper_length_scale"]))
        )
        self.piper_noise_scale: float = float(
            os.getenv("PIPER_NOISE_SCALE", j.get("piper_noise_scale", _DEFAULTS["piper_noise_scale"]))
        )
        self.piper_noise_w_scale: float = float(
            os.getenv("PIPER_NOISE_W_SCALE", j.get("piper_noise_w_scale", _DEFAULTS["piper_noise_w_scale"]))
        )
        self.kokoro_model_path: str = os.getenv(
            "KOKORO_MODEL_PATH", j.get("kokoro_model_path", "models/kokoro/kokoro-v1.0.onnx")
        )
        self.kokoro_voices_path: str = os.getenv(
            "KOKORO_VOICES_PATH", j.get("kokoro_voices_path", "models/kokoro/voices-v1.0.bin")
        )
        self.kokoro_voice: str = os.getenv(
            "KOKORO_VOICE", j.get("kokoro_voice", "af_heart")
        )
        self.tts_allow_sapi_fallback: bool = os.getenv(
            "TTS_ALLOW_SAPI_FALLBACK",
            str(j.get("tts_allow_sapi_fallback", _DEFAULTS["tts_allow_sapi_fallback"])),
        ).lower() not in ("false", "0", "no")
        self.stt_model: str = os.getenv(
            "STT_MODEL", j.get("stt_model", _DEFAULTS["stt_model"])
        )
        self.vad_threshold: float = float(
            os.getenv("VAD_THRESHOLD", j.get("vad_threshold", _DEFAULTS["vad_threshold"]))
        )
        self.answered_speak_delay_seconds: float = float(
            os.getenv("ANSWERED_SPEAK_DELAY_SECONDS", j.get("answered_speak_delay_seconds", _DEFAULTS["answered_speak_delay_seconds"]))
        )
        self.wait_for_human_audio: bool = os.getenv(
            "WAIT_FOR_HUMAN_AUDIO", str(j.get("wait_for_human_audio", _DEFAULTS["wait_for_human_audio"]))
        ).lower() not in ("false", "0", "no")
        self.human_audio_timeout_seconds: float = float(
            os.getenv("HUMAN_AUDIO_TIMEOUT_SECONDS", j.get("human_audio_timeout_seconds", _DEFAULTS["human_audio_timeout_seconds"]))
        )
        self.answer_confirm_polls: int = int(
            os.getenv("ANSWER_CONFIRM_POLLS", j.get("answer_confirm_polls", _DEFAULTS["answer_confirm_polls"]))
        )
        self.min_ring_seconds: float = float(
            os.getenv("MIN_RING_SECONDS", j.get("min_ring_seconds", _DEFAULTS["min_ring_seconds"]))
        )
        self.max_ring_seconds: float = float(
            os.getenv("MAX_RING_SECONDS", j.get("max_ring_seconds", _DEFAULTS["max_ring_seconds"]))
        )
        self.voicemail_detect_seconds: float = float(
            os.getenv("VOICEMAIL_DETECT_SECONDS", j.get("voicemail_detect_seconds", _DEFAULTS["voicemail_detect_seconds"]))
        )
        self.call_cooldown_seconds: float = float(
            os.getenv("CALL_COOLDOWN_SECONDS", j.get("call_cooldown_seconds", _DEFAULTS["call_cooldown_seconds"]))
        )
        self.n_slots: int = int(os.getenv("N_SLOTS", j.get("n_slots", _DEFAULTS["n_slots"])))
        self.max_concurrent_dials: int = int(
            os.getenv(
                "MAX_CONCURRENT_DIALS",
                j.get("max_concurrent_dials", _DEFAULTS["max_concurrent_dials"]),
            )
        )
        self.force_requested_slots: bool = os.getenv(
            "FORCE_REQUESTED_SLOTS",
            str(j.get("force_requested_slots", _DEFAULTS["force_requested_slots"])),
        ).lower() in ("true", "1", "yes")
        self.allow_shared_audio_routes: bool = os.getenv(
            "ALLOW_SHARED_AUDIO_ROUTES",
            str(j.get("allow_shared_audio_routes", _DEFAULTS["allow_shared_audio_routes"])),
        ).lower() in ("true", "1", "yes")
        raw_parallel_profiles = j.get("parallel_profiles", _DEFAULTS["parallel_profiles"])
        self.parallel_profiles: list[dict] = (
            list(raw_parallel_profiles) if isinstance(raw_parallel_profiles, list) else []
        )
        self.vad_silence_frames: int = int(
            os.getenv("VAD_SILENCE_FRAMES", j.get("vad_silence_frames", _DEFAULTS["vad_silence_frames"]))
        )
        self.vad_speech_frames: int = int(
            os.getenv("VAD_SPEECH_FRAMES", j.get("vad_speech_frames", _DEFAULTS["vad_speech_frames"]))
        )
        self.stt_retry_count: int = int(
            os.getenv("STT_RETRY_COUNT", j.get("stt_retry_count", _DEFAULTS["stt_retry_count"]))
        )
        self.tts_warmup: bool = os.getenv(
            "TTS_WARMUP", str(j.get("tts_warmup", _DEFAULTS["tts_warmup"]))
        ).lower() not in ("false", "0", "no")
        self.auto_audio_setup: bool = os.getenv(
            "AUTO_AUDIO_SETUP",
            str(j.get("auto_audio_setup", _DEFAULTS["auto_audio_setup"])),
        ).lower() not in ("false", "0", "no")
        self.chrome_headless: bool = os.getenv(
            "CHROME_HEADLESS",
            str(j.get("chrome_headless", _DEFAULTS["chrome_headless"])),
        ).lower() not in ("false", "0", "no")
        self.live_listen_enabled: bool = os.getenv(
            "LIVE_LISTEN_ENABLED",
            str(j.get("live_listen_enabled", _DEFAULTS["live_listen_enabled"])),
        ).lower() in ("true", "1", "yes")
        self.use_call_intelligence: bool = os.getenv(
            "USE_CALL_INTELLIGENCE",
            str(j.get("use_call_intelligence", True)),
        ).lower() not in ("false", "0", "no")
        self.silence_does_not_end_call: bool = os.getenv(
            "SILENCE_DOES_NOT_END_CALL",
            str(j.get("silence_does_not_end_call", True)),
        ).lower() not in ("false", "0", "no")
        self.use_stt_context: bool = os.getenv(
            "USE_STT_CONTEXT",
            str(j.get("use_stt_context", True)),
        ).lower() not in ("false", "0", "no")
        self.max_silence_seconds: float = float(
            os.getenv("MAX_SILENCE_SECONDS", j.get("max_silence_seconds", 8.0))
        )
        self.listen_after_tts_delay_ms: int = int(
            os.getenv("LISTEN_AFTER_TTS_DELAY_MS", j.get("listen_after_tts_delay_ms", 250))
        )
        self.use_thinking_fillers: bool = os.getenv(
            "USE_THINKING_FILLERS",
            str(j.get("use_thinking_fillers", True)),
        ).lower() not in ("false", "0", "no")
        self.filler_probability: float = float(
            os.getenv("FILLER_PROBABILITY", j.get("filler_probability", 0.0))
        )
        self.stream_llm_replies: bool = os.getenv(
            "STREAM_LLM_REPLIES",
            str(j.get("stream_llm_replies", True)),
        ).lower() not in ("false", "0", "no")
        self.voicemail_max_wait_seconds: float = float(
            os.getenv("VOICEMAIL_MAX_WAIT_SECONDS", j.get("voicemail_max_wait_seconds", 1.2))
        )
        self.voicemail_play_after_seconds: float = float(
            os.getenv("VOICEMAIL_PLAY_AFTER_SECONDS", j.get("voicemail_play_after_seconds", 0.0))
        )
        self.voicemail_play_on_greeting: bool = os.getenv(
            "VOICEMAIL_PLAY_ON_GREETING",
            str(j.get("voicemail_play_on_greeting", True)),
        ).lower() not in ("false", "0", "no")
        self.voicemail_greeting_frames_required: int = int(
            os.getenv(
                "VOICEMAIL_GREETING_FRAMES_REQUIRED",
                j.get("voicemail_greeting_frames_required", 6),
            )
        )
        self.chrome_restart_every_n_calls: int = int(
            os.getenv("CHROME_RESTART_EVERY_N_CALLS", j.get("chrome_restart_every_n_calls", 0))
        )
        self.avoid_gv_page_reload: bool = os.getenv(
            "AVOID_GV_PAGE_RELOAD",
            str(j.get("avoid_gv_page_reload", True)),
        ).lower() not in ("false", "0", "no")
        self.max_calls_per_run: int = int(
            os.getenv("MAX_CALLS_PER_RUN", j.get("max_calls_per_run", 0))
        )
        self.groq_max_retries_per_minute: int = int(
            os.getenv("GROQ_MAX_RETRIES_PER_MINUTE", j.get("groq_max_retries_per_minute", 60))
        )
        extra_keys = os.getenv("GROQ_API_KEYS", j.get("groq_api_keys", ""))
        if isinstance(extra_keys, str):
            parsed_keys = [part.strip() for part in extra_keys.split(",") if part.strip()]
        else:
            parsed_keys = [str(part).strip() for part in extra_keys if str(part).strip()]
        if self.groq_api_key and self.groq_api_key not in parsed_keys:
            parsed_keys.insert(0, self.groq_api_key)
        self.groq_api_keys: list[str] = parsed_keys

    def validate(self) -> None:
        local_answers = self.llm_backend == "local"
        local_hearing = self.stt_backend == "local"
        if not (local_answers and local_hearing):
            if not self.groq_api_key or self.groq_api_key.startswith("your_"):
                raise SystemExit(
                    "GROQ_API_KEY not configured. Copy .env.example to .env and set a real key."
                )
            if self.groq_model in ("groq-alpha", ""):
                raise SystemExit(
                    f"GROQ_MODEL '{self.groq_model}' is not valid. "
                    "Use e.g. llama-3.3-70b-versatile. See console.groq.com for model list."
                )
        if local_answers:
            model = Path(self.local_llm_path).expanduser()
            if not model.is_absolute():
                model = BASE_DIR / model
            if not model.is_file():
                raise SystemExit(
                    "Local answers are selected but the model file is missing. "
                    f"Expected {model}."
                )
        if self.tts_engine not in {"edge", "edge-tts", "piper", "piper-tts", "kokoro"}:
            raise SystemExit(
                f"TTS_ENGINE '{self.tts_engine}' is not supported. Use 'edge', 'piper', or 'kokoro'."
            )
        if self.tts_engine in {"piper", "piper-tts"}:
            model = Path(self.piper_model_path).expanduser()
            if not model.is_absolute():
                model = BASE_DIR / model
            config_path = Path(str(model) + ".json")
            if not model.is_file() or not config_path.is_file():
                raise SystemExit(
                    "Piper TTS is selected but the voice model is incomplete. "
                    f"Expected {model} and {config_path}."
                )
        if self.tts_engine == "kokoro":
            model = Path(self.kokoro_model_path).expanduser()
            voices = Path(self.kokoro_voices_path).expanduser()
            if not model.is_absolute():
                model = BASE_DIR / model
            if not voices.is_absolute():
                voices = BASE_DIR / voices
            if not model.is_file() or not voices.is_file():
                raise SystemExit(
                    "Kokoro TTS is selected but the voice files are missing. "
                    f"Expected {model} and {voices}."
                )

    @classmethod
    def load(cls) -> "Config":
        return cls()
