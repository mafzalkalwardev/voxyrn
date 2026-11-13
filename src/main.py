from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from selenium.common.exceptions import WebDriverException

from src.call_log import CallLogger
from src.call_session import CallSession, CallState
from src.config import Config
from src.contacts import load_contacts
from src.google_voice import GoogleVoiceBrowser
from src.ai_groq import GroqAgent
from src.logger import setup_logger
from src.tts import save_text_to_speech, ensure_audio_dir
from src.voice_playback import play_wav_loopback, find_playable_loopback_device, print_devices
from src.paths import runtime_base
from src.audio_routing import safe_capture_hint
from src.live_call_status import clear_live_call, publish_live_call

BASE_DIR = runtime_base()


def _resolve_headless(args: argparse.Namespace, cfg: Config) -> bool:
    if args.headless is None:
        return bool(getattr(cfg, "chrome_headless", True))
    return bool(args.headless)


def _prepare_live_call_audio(
    cfg: Config,
    *,
    loopback_device: str,
    capture_device: str,
    profile_name: str,
) -> str:
    """
    Auto-set Windows mic/speaker and Chrome mic prefs before launching Chrome.
    Returns the capture hint to use for prospect/voicemail listening.
    """
    from src.audio_setup import ensure_live_call_audio

    if not getattr(cfg, "auto_audio_setup", True):
        return safe_capture_hint(capture_device, loopback_device)

    profile_dir = BASE_DIR / "chrome_profiles" / profile_name
    result = ensure_live_call_audio(
        loopback_hint=loopback_device,
        capture_hint=capture_device,
        chrome_profile_dir=profile_dir,
        set_windows_devices=True,
        live_listen_enabled=bool(getattr(cfg, "live_listen_enabled", False)),
    )
    if not result.ok:
        logging.getLogger("GoogleVoiceAgent").warning(
            "Auto audio setup incomplete — live call may not hear Ashley or the prospect. %s",
            "; ".join(result.messages) or "unknown error",
        )
    return result.capture_hint or safe_capture_hint(capture_device, loopback_device)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Google Voice Dispatch Sales Agent")
    p.add_argument("--contacts", help="Path to Excel or CSV contact list")
    p.add_argument("--profile", default=None, help="Chrome profile name")
    p.add_argument("--objective", default=None,
                   help="Call objective sent to AI script generator")
    p.add_argument("--offer", default=None,
                   help="Offer summary used in voicemail generation")
    p.add_argument("--callback-number", default=None,
                   help="Callback number spoken in generated voicemails")
    p.add_argument("--agent-name", default=None,
                   help="Agent name spoken in generated scripts")
    p.add_argument("--company-name", default=None,
                   help="Company name spoken in generated scripts")
    p.add_argument("--company-context", default=None,
                   help="Company details used by the AI script generator")
    p.add_argument("--limit", type=int, default=10, help="Maximum calls to attempt")
    p.add_argument("--output-dir", default="audio", help="Directory for generated audio files")
    p.add_argument("--loopback-device", default=None,
                   help="Name hint for virtual audio cable (default from config)")
    p.add_argument("--call-timeout", type=int, default=None,
                   help="Seconds to wait for call connection (default from config)")
    p.add_argument("--call-max-duration", type=int, default=None,
                   help="Seconds before a forced hangup. 0 stays on the line until the other person hangs up.")
    p.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Run Google Voice Chrome headless (default from config chrome_headless=true). "
             "Use --no-headless to show the browser window.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Generate scripts and audio only — skip all dialing")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--realtime", dest="realtime", action="store_true", default=True,
                      help="Use realtime conversation loop (default)")
    mode.add_argument("--static-playback", dest="realtime", action="store_false",
                      help="Use pregenerated WAV playback instead of realtime conversation")
    p.add_argument("--capture-device", default=None,
                   help="Audio capture device hint for realtime mode (default from config)")
    p.add_argument("--tts-voice", default=None,
                   help="edge-tts voice name for realtime mode (default from config)")
    p.add_argument("--list-audio-devices", action="store_true",
                   help="Print sounddevice audio devices and exit")
    p.add_argument("--preflight", action="store_true",
                   help="Run readiness checks and exit without dialing")
    p.add_argument("--audio-route-test", action="store_true",
                   help="Play a short test phrase to the configured loopback output without dialing")
    p.add_argument("--safe-test", metavar="PHONE",
                   help="Safe one-number test mode: run preflight, confirm, then dial exactly "
                        "this number once. Does not read from --contacts.")
    p.add_argument("--diagnose-call-state", metavar="PHONE",
                   help="DOM diagnostic mode: dial PHONE, capture DOM snapshots every 1.5s "
                        "under logs/diagnostics/, hang up after 90s. "
                        "Use only with the designated test number.")
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Dial every contact in the list; do not skip numbers completed in a prior run.",
    )
    p.add_argument(
        "--reset-batch-progress",
        action="store_true",
        help="Clear saved batch progress for the current contacts file before dialing.",
    )
    return p.parse_args()


def _install_shutdown_handlers(browser_holder: list) -> None:
    def _shutdown(signum: int, frame) -> None:  # noqa: ARG001
        logger = logging.getLogger("GoogleVoiceAgent")
        logger.info("Shutdown signal %s — closing browser", signum)
        for browser in list(browser_holder):
            try:
                if browser.is_call_active():
                    browser.hangup_call()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _shutdown)


def _scaled_groq_rate_limit(cfg: Config) -> int:
    """Scale client throttle when multiple Groq accounts are configured."""
    return cfg.groq_max_retries_per_minute * max(1, len(cfg.groq_api_keys))


def _run_preflight(cfg: Config) -> int:
    from src.preflight import run_all

    results = run_all(
        groq_api_key=cfg.groq_api_key,
        contacts_file=cfg.contacts_file,
        profile_name=cfg.profile_name,
        loopback_device=cfg.loopback_device,
        capture_device=cfg.capture_device,
    )
    worst = 0
    for result in results:
        status = result.status.upper()
        if result.status == "fail":
            worst = 1
        print(f"[{status:4}] {result.name}: {result.message}")
    return worst


def _run_audio_route_test(args: argparse.Namespace, cfg: Config) -> int:
    loopback_device = args.loopback_device or cfg.loopback_device
    output_dir = ensure_audio_dir(args.output_dir) / "diagnostics"
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = output_dir / f"audio_route_test_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    phrase = (
        "Indus Transports audio route test. "
        "If Google Voice microphone is set to CABLE Output, this audio reaches the call."
    )

    device_index = find_playable_loopback_device(loopback_device)
    if device_index is None:
        from src.voice_playback import pulse_failure_detail

        detail = pulse_failure_detail()
        extra = f" {detail}" if detail else ""
        print(f"[FAIL] No playable loopback output device found for '{loopback_device}'.{extra}")
        return 1

    try:
        from src.realtime_tts import save_tts_wav

        save_tts_wav(
            phrase,
            wav_path,
            engine=cfg.tts_engine,
            voice=cfg.tts_voice,
            piper_model_path=cfg.piper_model_path,
            piper_length_scale=cfg.piper_length_scale,
            piper_noise_scale=cfg.piper_noise_scale,
            piper_noise_w_scale=cfg.piper_noise_w_scale,
        )
        duration = play_wav_loopback(
            wav_path,
            device_hint=loopback_device,
            fallback_to_default=False,
        )
    except Exception as exc:
        print(f"[FAIL] Audio route test failed: {exc}")
        return 1

    print(f"[OK  ] Played {duration:.1f}s to loopback device '{loopback_device}'.")
    print(f"[INFO] Test WAV saved at: {wav_path}")
    return 0


def _run_call(
    contact: dict,
    index: int,
    browser: GoogleVoiceBrowser | None,
    ai: GroqAgent,
    call_logger: CallLogger,
    logger: logging.Logger,
    script_dir: Path,
    voicemail_dir: Path,
    objective: str,
    offer: str,
    callback_number: str,
    agent_name: str,
    company_name: str,
    company_context: str,
    company_website: str,
    loopback_device: str,
    loopback_device_index: int | None,
    loopback_available: bool,
    call_timeout: int,
    call_max_duration: int,
    dry_run: bool,
    realtime: bool = False,
    capture_device: str = "default",
    tts_engine: str = "edge",
    tts_voice: str = "en-US-GuyNeural",
    piper_model_path: str = "models/piper/en_US-lessac-medium.onnx",
    piper_length_scale: float = 1.07,
    piper_noise_scale: float = 0.5,
    piper_noise_w_scale: float = 0.92,
    stt_model: str = "whisper-large-v3",
    vad_threshold: float = 0.015,
    groq_api_key: str = "",
    answered_speak_delay: float = 4.0,
    wait_for_human_audio: bool = True,
    human_audio_timeout: float = 8.0,
    answer_confirm_polls: int = 2,
    stt_retry_count: int = 2,
    vad_silence_frames: int = 12,
    vad_speech_frames: int = 2,
    min_ring_seconds: float = 0.0,
    max_ring_seconds: float = 45.0,
    voicemail_detect_seconds: float = 15.0,
    tts_warmup: bool = True,
    tts_allow_sapi_fallback: bool = False,
    silence_does_not_end_call: bool = True,
    use_stt_context: bool = True,
    max_silence_seconds: float = 8.0,
    llm_model_realtime: str = "llama-3.1-8b-instant",
    listen_after_tts_delay_ms: int = 150,
    use_thinking_fillers: bool = True,
    filler_probability: float = 0.7,
    stream_llm_replies: bool = True,
    voicemail_max_wait_seconds: float = 8.0,
    voicemail_play_after_seconds: float = 4.0,
    voicemail_play_on_greeting: bool = True,
    voicemail_greeting_frames_required: int = 6,
    screening_purpose_text: str = "",
    groq_max_retries_per_minute: int = 60,
) -> str | None:
    phone = contact["phone"]
    name = contact["name"]
    session = CallSession(phone=phone, contact_name=name)
    publish_live_call(
        phase="preparing",
        phone=phone,
        name=name,
        index=index,
        message=f"Preparing call to {name or phone}",
    )

    logger.info("[%d] Preparing AI audio assets for %s (%s)", index, name, phone)
    script_path: Path | None = None
    opening_line: str | None = None
    if not realtime:
        script_text = ai.generate_call_script(
            contact_name=name,
            objective=objective,
            context=f"Contact: {name}, Phone: {phone}",
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
        )
        script_path = script_dir / f"script_{index}_{phone.replace('+', '')}.wav"
        script_text_path = script_path.with_suffix(".txt")
        script_text_path.write_text(script_text, encoding="utf-8")
        from src.realtime_tts import save_tts_wav

        save_tts_wav(
            script_text,
            script_path,
            engine=tts_engine,
            voice=tts_voice,
            piper_model_path=piper_model_path,
            piper_length_scale=piper_length_scale,
            piper_noise_scale=piper_noise_scale,
            piper_noise_w_scale=piper_noise_w_scale,
        )

    voicemail_text = _fast_voicemail_text(
        agent_name=agent_name,
        company_name=company_name,
        company_context=company_context,
        callback_number=callback_number,
        offer=offer,
    )

    voicemail_path = voicemail_dir / f"voicemail_{index}_{phone.replace('+', '')}.wav"
    voicemail_text_path = voicemail_path.with_suffix(".txt")
    voicemail_text_path.write_text(voicemail_text, encoding="utf-8")
    _reuse_or_render_voicemail(
        voicemail_text,
        voicemail_path,
        voicemail_dir,
        tts_engine=tts_engine,
        tts_voice=tts_voice,
        piper_model_path=piper_model_path,
        piper_length_scale=piper_length_scale,
        piper_noise_scale=piper_noise_scale,
        piper_noise_w_scale=piper_noise_w_scale,
        logger=logger,
        index=index,
    )

    # Pre-generate realtime opening line before dialing so pickup is not silent.
    if realtime:
        agent_opening_ok = False
        try:
            opening_line = _generate_realtime_opening_line(
                phone=phone,
                contact_name=name,
                groq_api_key=groq_api_key,
                groq_model=ai.model,
                agent_name=agent_name,
                company_name=company_name,
                company_context=company_context,
                company_website=company_website,
                callback_number=callback_number,
                logger=logger,
                index=index,
            )
            agent_opening_ok = bool(opening_line)
        except Exception as exc:
            logger.error("[%d] Opening line generation failed (will use fallback): %s", index, exc)
            agent_opening_ok = False

        if not agent_opening_ok:
            opening_line = _fallback_opening_line(agent_name=agent_name, company_name=company_name)
            logger.warning("[%d] Using fallback static opening line: %s", index, opening_line)

    if realtime:
        logger.info("[%d] Realtime mode ready; voicemail fallback: %s", index, voicemail_path.name)
    else:
        logger.info("[%d] Static audio ready: %s | %s", index, script_path.name, voicemail_path.name)

    if dry_run:
        logger.info("[%d] DRY RUN — skipping dial for %s", index, phone)
        session.outcome = "DRY_RUN"
        call_logger.log_session(session, notes="dry-run mode")
        publish_live_call(
            phase="result",
            phone=phone,
            name=name,
            outcome="DRY_RUN",
            state="DRY_RUN",
            index=index,
            message="Dry run — no dial",
            hold_result=True,
        )
        return None

    # ---- Dial ----
    logger.info("[%d] Dialing %s...", index, phone)
    session.transition(CallState.DIALING)
    publish_live_call(
        phase="dialing",
        phone=phone,
        name=name,
        state="DIALING",
        index=index,
        message=f"Dialing {phone}",
    )
    dialed = browser.dial_number(phone, connect_timeout=call_timeout)
    if not dialed:
        session.transition(CallState.FAILED, "dial_number returned False")
        session.outcome = "DIAL_FAILED"
        call_logger.log_session(session)
        _archive_call_result(session, contact, {}, logger, index)
        logger.warning("[%d] Dial failed for %s", index, phone)
        publish_live_call(
            phase="result",
            phone=phone,
            name=name,
            outcome="DIAL_FAILED",
            state="FAILED",
            index=index,
            message="Dial failed",
            hold_result=True,
        )
        return phone

    publish_live_call(
        phase="ringing",
        phone=phone,
        name=name,
        state="RINGING",
        index=index,
        message=f"Ringing {phone}",
    )

    # ---- Poll for ANSWERED or VOICEMAIL ----
    # Google Voice shows a hangup button while an outbound call is only ringing,
    # so this wait must use real answer evidence and the shorter answer timeout.
    final_state = browser.detect_call_state(
        session,
        timeout=float(call_timeout),
        poll_interval=0.5,
        ctrl_confirm_polls=answer_confirm_polls,
        min_ring_seconds=min_ring_seconds,
        max_ring_seconds=max_ring_seconds,
        voicemail_detect_seconds=voicemail_detect_seconds,
    )

    if final_state == CallState.CONNECTED:
        publish_live_call(
            phase="connected",
            phone=phone,
            name=name,
            state="CONNECTED",
            index=index,
            message=f"Connected with {name or phone}",
        )
        if realtime and loopback_device_index is not None:
            logger.info("[%d] Call connected — starting realtime conversation loop", index)
            from src.opening_pool import pick_opening
            from src.realtime_tts import RealtimeTTS, validate_tts_output_device
            from src.voicemail_detector import campaign_reason

            screening_reason = campaign_reason(company_context, screening_purpose_text)

            opening_line = pick_opening(
                phone,
                agent_name,
                company_name,
                llm_line=opening_line,
            )
            shared_tts = None
            screening_spoken = False
            try:
                validate_tts_output_device(loopback_device_index)
                shared_tts = RealtimeTTS(
                    device_index=loopback_device_index,
                    pulse_sink=loopback_device,
                    engine=tts_engine,
                    voice=tts_voice,
                    piper_model_path=piper_model_path,
                    piper_length_scale=piper_length_scale,
                    piper_noise_scale=piper_noise_scale,
                    piper_noise_w_scale=piper_noise_w_scale,
                    use_cache=tts_warmup,
                    allow_sapi_fallback=tts_allow_sapi_fallback,
                )
                if loopback_device_index is not None:
                    screening_spoken = _handle_call_screening(
                        browser,
                        loopback_device_index,
                        agent_name,
                        company_name,
                        screening_reason,
                        logger,
                        tts_voice=tts_voice,
                        tts_engine=tts_engine,
                        piper_model_path=piper_model_path,
                        piper_length_scale=piper_length_scale,
                        piper_noise_scale=piper_noise_scale,
                        piper_noise_w_scale=piper_noise_w_scale,
                        tts=shared_tts,
                        allow_sapi_fallback=tts_allow_sapi_fallback,
                    )
                if opening_line:
                    shared_tts.prewarm_line(opening_line)
            except Exception as exc:
                logger.warning("[%d] Shared RealtimeTTS setup failed: %s", index, exc)
                shared_tts = None
            _run_realtime_loop(
                session=session,
                contact_name=name,
                groq_api_key=groq_api_key,
                groq_model=ai.model,
                agent_name=agent_name,
                company_name=company_name,
                company_context=company_context,
                company_website=company_website,
                callback_number=callback_number,
                loopback_device_index=loopback_device_index,
                capture_device=capture_device,
                tts_engine=tts_engine,
                tts_voice=tts_voice,
                piper_model_path=piper_model_path,
                piper_length_scale=piper_length_scale,
                piper_noise_scale=piper_noise_scale,
                piper_noise_w_scale=piper_noise_w_scale,
                stt_model=stt_model,
                vad_threshold=vad_threshold,
                opening_line=opening_line,
                voicemail_path=voicemail_path,
                browser=browser,
                loopback_device=loopback_device,
                loopback_available=loopback_available,
                call_max_duration=call_max_duration,
                logger=logger,
                answered_speak_delay=answered_speak_delay,
                wait_for_human_audio=wait_for_human_audio,
                human_audio_timeout=human_audio_timeout,
                stt_retry_count=stt_retry_count,
                vad_silence_frames=vad_silence_frames,
                vad_speech_frames=vad_speech_frames,
                voicemail_detect_seconds=voicemail_detect_seconds,
                tts_warmup=tts_warmup,
                silence_does_not_end_call=silence_does_not_end_call,
                use_stt_context=use_stt_context,
                max_silence_seconds=max_silence_seconds,
                llm_model_realtime=llm_model_realtime,
                listen_after_tts_delay_ms=listen_after_tts_delay_ms,
                use_thinking_fillers=use_thinking_fillers,
                filler_probability=filler_probability,
                stream_llm_replies=stream_llm_replies,
                voicemail_play_on_greeting=voicemail_play_on_greeting,
                voicemail_greeting_frames_required=voicemail_greeting_frames_required,
                groq_max_retries_per_minute=groq_max_retries_per_minute,
                shared_tts=shared_tts,
                tts_allow_sapi_fallback=tts_allow_sapi_fallback,
                screening_purpose=screening_reason,
                screening_already_spoken=screening_spoken,
            )
        else:
            logger.info("[%d] Call connected — playing script audio", index)
            if script_path is None:
                raise RuntimeError("Static playback requested but script audio was not generated")
            if not _play_audio(script_path, loopback_device, loopback_available, logger):
                session.transition(CallState.FAILED, "script audio playback failed")
                if browser.is_call_active():
                    browser.hangup_call()
                session.outcome = session.state.value
                call_logger.log_session(session)
                _archive_call_result(session, contact, {}, logger, index)
                return phone
            # Wait for natural end (up to 60s after audio finishes)
            followup_state = browser.detect_call_state(
                session,
                timeout=60.0,
                poll_interval=0.5,
                ctrl_confirm_polls=answer_confirm_polls,
                min_ring_seconds=min_ring_seconds,
                max_ring_seconds=max_ring_seconds,
                voicemail_detect_seconds=voicemail_detect_seconds,
            )
            if followup_state == CallState.VOICEMAIL:
                logger.info("[%d] Voicemail detected after initial connection", index)
                _deliver_voicemail_and_hangup(
                    browser,
                    session,
                    voicemail_path,
                    loopback_device,
                    loopback_available,
                    logger,
                    max_wait=1.2,
                    ready_after=0.0,
                )
            elif followup_state == CallState.CONNECTED:
                logger.info("[%d] Call still active after playback - hanging up", index)
                browser.hangup_call()
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "hung up after script playback")

    elif final_state == CallState.VOICEMAIL:
        publish_live_call(
            phase="voicemail",
            phone=phone,
            name=name,
            outcome="voicemail",
            state="VOICEMAIL",
            index=index,
            message="Voicemail detected — playing drop",
        )
        logger.info("[%d] Voicemail detected — fast voicemail delivery", index)
        _deliver_voicemail_and_hangup(
            browser,
            session,
            voicemail_path,
            loopback_device,
            loopback_available,
            logger,
            max_wait=1.2,
            ready_after=0.0,
        )

    elif final_state in (CallState.ENDED, CallState.FAILED):
        logger.info("[%d] Call ended/failed before playback for %s", index, phone)
        if final_state == CallState.FAILED and browser.is_call_active():
            browser.hangup_call()

    if session.outcome not in {"no_answer", "voicemail"}:
        session.outcome = session.state.value
    call_logger.log_session(session)
    outcome_label = (session.outcome or session.state.value or "ended").strip()
    logger.info(
        "[%d] %s done — state=%s outcome=%s total=%.1fs connected=%.1fs",
        index,
        phone,
        session.state.value,
        outcome_label,
        session.total_duration_seconds() or 0.0,
        session.connected_duration_seconds() or 0.0,
    )
    publish_live_call(
        phase="result",
        phone=phone,
        name=name,
        outcome=outcome_label,
        state=session.state.value,
        index=index,
        message=f"Call result: {outcome_label}",
        hold_result=True,
    )

    if realtime:
        _extract_and_upsert_lead(
            session=session,
            contact=contact,
            groq_api_key=groq_api_key,
            model=ai.model,
            logger=logger,
            index=index,
            opening_line=opening_line or "",
        )
    else:
        _archive_call_result(session, contact, {}, logger, index)
        try:
            from src.call_intelligence import record_call_from_session

            record_call_from_session(session, contact, lead={})
        except Exception as exc:
            logger.warning("[%d] Call intelligence update failed: %s", index, exc)
    return phone


def _extract_and_upsert_lead(
    session: CallSession,
    contact: dict,
    groq_api_key: str,
    model: str,
    logger: logging.Logger,
    index: int,
    opening_line: str = "",
) -> None:
    """Extract structured lead data and archive the call into the CRM store."""
    lead: dict = {}
    try:
        if session.transcript_path and groq_api_key:
            from src.leads import extract_lead_from_transcript, upsert_lead  # type: ignore

            lead = extract_lead_from_transcript(
                transcript_path=session.transcript_path,
                contact=contact,
                groq_api_key=groq_api_key,
                model=model,
            )
            lead["phone_number"] = session.phone
            lead["contact_name"] = lead.get("contact_name") or session.contact_name
            lead["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
            lead["transcript_file"] = str(session.transcript_path)
            if not lead.get("call_outcome"):
                lead["call_outcome"] = session.outcome or session.state.value
            upsert_lead(lead)
            logger.info("[%d] Lead upserted for %s", index, session.phone)
    except Exception as exc:
        logger.warning("[%d] Lead extraction error: %s", index, exc)
    _archive_call_result(
        session=session,
        contact=contact,
        lead=lead,
        logger=logger,
        index=index,
        groq_api_key=groq_api_key,
        model=model,
    )
    try:
        from src.call_intelligence import record_call_from_session

        record_call_from_session(session, contact, lead=lead, opening_line=opening_line)
    except Exception as exc:
        logger.warning("[%d] Call intelligence update failed: %s", index, exc)


def _archive_call_result(
    session: CallSession,
    contact: dict,
    lead: dict,
    logger: logging.Logger,
    index: int,
    groq_api_key: str = "",
    model: str = "llama-3.3-70b-versatile",
) -> None:
    """Persist call artifacts in connected/voicemail/failed storage and CRM tables."""
    try:
        from src.crm import finalize_call_session  # type: ignore

        result = finalize_call_session(
            session=session,
            contact=contact,
            lead=lead,
            groq_api_key=groq_api_key,
            model=model,
        )
        logger.info(
            "[%d] CRM archive: type=%s stored=%s call_id=%s carrier_id=%s",
            index,
            result.get("call_type", ""),
            result.get("stored", False),
            result.get("call_id", ""),
            result.get("carrier_id", ""),
        )
    except Exception as exc:
        logger.warning("[%d] CRM archive error: %s", index, exc)


def _fast_voicemail_text(
    *,
    agent_name: str,
    company_name: str,
    company_context: str,
    callback_number: str,
    offer: str,
) -> str:
    """A short drop. No model call, so a mailbox does not stall the dialer."""
    from src.indus_campaign import AGENT_NAME, COMPANY_NAME, voicemail_text

    callback = callback_number or "this number"
    # Prefer the polished Indus Transports drop for Ashley client demos.
    if (
        agent_name.strip().lower() == AGENT_NAME.lower()
        and "indus" in (company_name or "").lower()
    ):
        return voicemail_text(callback)
    source = (offer or company_context or "a quick follow-up").replace("\n", " ").strip()
    brief = " ".join(source.split()[:16]).rstrip(".,;")
    return (
        f"Hi, this is {agent_name} from {company_name}. "
        f"I called about {brief}. "
        f"Please call me back at {callback}. Again, {callback}."
    )


def _reuse_or_render_voicemail(
    text: str,
    dest: Path,
    cache_dir: Path,
    *,
    tts_engine: str,
    tts_voice: str,
    piper_model_path: str,
    piper_length_scale: float,
    piper_noise_scale: float,
    piper_noise_w_scale: float,
    logger: logging.Logger,
    index: int,
) -> None:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    cached = cache_dir / f"_drop_{digest}.wav"
    if cached.is_file() and cached.stat().st_size > 1000:
        shutil.copyfile(cached, dest)
        logger.info("[%d] Reused cached voicemail drop", index)
        return
    try:
        from src.realtime_tts import save_tts_wav

        save_tts_wav(
            text,
            dest,
            engine=tts_engine,
            voice=tts_voice,
            piper_model_path=piper_model_path,
            piper_length_scale=piper_length_scale,
            piper_noise_scale=piper_noise_scale,
            piper_noise_w_scale=piper_noise_w_scale,
        )
    except Exception as exc:
        if str(tts_engine).strip().lower() in {"piper", "piper-tts", "kokoro"}:
            logger.error("[%d] Voicemail audio failed: %s", index, exc)
            raise
        logger.warning("[%d] Voicemail voice failed (%s); using SAPI fallback", index, exc)
        save_text_to_speech(text, dest)
    if dest.is_file() and dest.stat().st_size > 1000:
        shutil.copyfile(dest, cached)


def _fallback_opening_line(agent_name: str, company_name: str) -> str:
    from src.opening_pool import random_curated

    return random_curated(agent_name, company_name)


def _screening_response_text(agent_name: str, company_name: str, purpose: str) -> str:
    reason = (purpose or "a quick follow-up").strip()
    return f"{agent_name} from {company_name}, calling about {reason}."


def _handle_call_screening(
    browser: GoogleVoiceBrowser,
    loopback_device_index: int,
    agent_name: str,
    company_name: str,
    purpose: str,
    logger: logging.Logger,
    tts_voice: str = "en-US-GuyNeural",
    tts_engine: str = "edge",
    piper_model_path: str = "models/piper/en_US-lessac-medium.onnx",
    piper_length_scale: float = 1.07,
    piper_noise_scale: float = 0.5,
    piper_noise_w_scale: float = 0.92,
    tts: object | None = None,
    allow_sapi_fallback: bool = False,
) -> bool:
    """Respond to Google Voice name/purpose screening, then wait for live answer."""
    if not browser.is_call_screening_active():
        return False
    logger.info("Call screening detected — speaking name and purpose")
    try:
        from src.realtime_tts import RealtimeTTS

        own_tts = tts is None
        if own_tts:
            tts = RealtimeTTS(
                device_index=loopback_device_index,
                pulse_sink=loopback_device,
                engine=tts_engine,
                voice=tts_voice,
                piper_model_path=piper_model_path,
                piper_length_scale=piper_length_scale,
                piper_noise_scale=piper_noise_scale,
                piper_noise_w_scale=piper_noise_w_scale,
                use_cache=True,
                allow_sapi_fallback=allow_sapi_fallback,
            )
        line = _screening_response_text(agent_name, company_name, purpose)
        assert tts is not None
        if hasattr(tts, "prewarm_line"):
            tts.prewarm_line(line)  # type: ignore[attr-defined]
        tts.speak(line)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning("Screening TTS failed: %s", exc)
        return False
    logger.info("Call screening heard — said name and reason; still listening for a person or a mailbox")
    return True


def _deliver_voicemail_and_hangup(
    browser: GoogleVoiceBrowser,
    session: CallSession,
    voicemail_path: Path,
    loopback_device: str,
    loopback_available: bool,
    logger: logging.Logger,
    *,
    max_wait: float = 1.2,
    ready_after: float = 0.0,
) -> bool:
    session.outcome = "voicemail"
    browser.wait_for_voicemail_ready(max_wait=max_wait, min_ready_after=ready_after)
    if not _play_audio(voicemail_path, loopback_device, loopback_available, logger):
        if not session.is_terminal():
            session.transition(CallState.FAILED, "voicemail audio playback failed")
        session.outcome = "voicemail"
        return False
    time.sleep(0.35)
    if browser.is_call_active():
        browser.hangup_call()
    if not session.is_terminal():
        session.transition(CallState.ENDED, "hung up after voicemail playback")
    session.outcome = "voicemail"
    return True


def _generate_realtime_opening_line(
    phone: str,
    contact_name: str,
    groq_api_key: str,
    groq_model: str,
    agent_name: str,
    company_name: str,
    company_context: str,
    company_website: str,
    callback_number: str,
    logger: logging.Logger,
    index: int,
) -> str:
    """Prepare the first spoken line before dialing so pickup is not silent."""
    fallback = _fallback_opening_line(agent_name, company_name)
    try:
        from src.conversation_agent import ConversationAgent

        logger.info("[%d] Opening line generation started before dialing", index)
        agent = ConversationAgent(
            api_key=groq_api_key,
            model=groq_model,
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
            callback_number=callback_number,
            contact_name=contact_name,
            contact_phone=phone,
        )
        line = agent.opening_line()
    except Exception as exc:
        logger.error("[%d] Opening line generation failed: %s", index, exc)
        line = ""

    from src.opening_pool import pick_opening

    line = pick_opening(
        phone,
        agent_name,
        company_name,
        llm_line=line or None,
    )
    if not line:
        line = fallback
        logger.warning("[%d] Opening line empty; using fallback: %s", index, line)
    else:
        logger.info("[%d] Opening line prepared before dialing: %s", index, line)
    return line


def _run_realtime_loop(
    session: CallSession,
    contact_name: str,
    groq_api_key: str,
    groq_model: str,
    agent_name: str,
    company_name: str,
    company_context: str,
    company_website: str,
    callback_number: str,
    loopback_device_index: int,
    capture_device: str,
    tts_engine: str,
    tts_voice: str,
    piper_model_path: str,
    piper_length_scale: float,
    piper_noise_scale: float,
    piper_noise_w_scale: float,
    stt_model: str,
    vad_threshold: float,
    opening_line: str | None,
    voicemail_path: Path,
    browser: GoogleVoiceBrowser,
    loopback_device: str,
    loopback_available: bool,
    call_max_duration: int,
    logger: logging.Logger,
    answered_speak_delay: float = 4.0,
    wait_for_human_audio: bool = True,
    human_audio_timeout: float = 8.0,
    stt_retry_count: int = 2,
    vad_silence_frames: int = 12,
    vad_speech_frames: int = 2,
    voicemail_detect_seconds: float = 15.0,
    tts_warmup: bool = True,
    silence_does_not_end_call: bool = True,
    use_stt_context: bool = True,
    max_silence_seconds: float = 8.0,
    llm_model_realtime: str = "llama-3.1-8b-instant",
    listen_after_tts_delay_ms: int = 150,
    use_thinking_fillers: bool = True,
    filler_probability: float = 0.7,
    stream_llm_replies: bool = True,
    voicemail_play_on_greeting: bool = True,
    voicemail_greeting_frames_required: int = 6,
    groq_max_retries_per_minute: int = 60,
    voicemail_max_wait_seconds: float = 8.0,
    voicemail_play_after_seconds: float = 4.0,
    shared_tts: object | None = None,
    tts_allow_sapi_fallback: bool = False,
    screening_purpose: str = "",
    screening_already_spoken: bool = False,
) -> None:
    from src.conversation_agent import ConversationAgent
    from src.conversation_loop import ConversationLoop
    from src.realtime_tts import RealtimeTTS, validate_tts_output_device
    from src.stt import GroqWhisperSTT
    from src.vad import VADConfig
    from src.voice_playback import describe_audio_device

    # Build transcript path: logs/transcripts/<phone>_<timestamp>.txt
    # The logs/ directory is git-ignored; transcripts are never committed.
    transcript_ts = time.strftime("%Y%m%d_%H%M%S")
    safe_phone = session.phone.replace("+", "").replace(" ", "")
    transcript_path = BASE_DIR / "logs" / "transcripts" / f"{safe_phone}_{transcript_ts}.txt"
    recording_path = BASE_DIR / "logs" / "recordings" / f"{safe_phone}_{transcript_ts}.wav"
    session.transcript_path = transcript_path
    session.recording_path = recording_path

    try:
        validate_tts_output_device(loopback_device_index)
        logger.info("Realtime selected output device: %s", describe_audio_device(loopback_device_index))
        logger.info("Realtime selected capture device: CAPTURE_DEVICE='%s'", capture_device)
        if shared_tts is not None:
            tts = shared_tts
        else:
            tts = RealtimeTTS(
                device_index=loopback_device_index,
                pulse_sink=loopback_device,
                engine=tts_engine,
                voice=tts_voice,
                piper_model_path=piper_model_path,
                piper_length_scale=piper_length_scale,
                piper_noise_scale=piper_noise_scale,
                piper_noise_w_scale=piper_noise_w_scale,
                use_cache=tts_warmup,
                allow_sapi_fallback=tts_allow_sapi_fallback,
            )
            if opening_line and hasattr(tts, "prewarm_line"):
                tts.prewarm_line(opening_line)  # type: ignore[attr-defined]
        agent = ConversationAgent(
            api_key=groq_api_key,
            model=llm_model_realtime,
            agent_name=agent_name,
            company_name=company_name,
            company_context=company_context,
            company_website=company_website,
            callback_number=callback_number,
            contact_name=contact_name,
            contact_phone=session.phone,
            rate_limit_per_minute=groq_max_retries_per_minute,
            backend=os.getenv("LLM_BACKEND", "groq"),
        )
        if os.getenv("STT_BACKEND", "groq").strip().lower() == "local":
            from src.stt import LocalWhisperSTT
            stt = LocalWhisperSTT(
                model_size=os.getenv("STT_LOCAL_MODEL", "base"),
                model_dir=os.getenv("STT_MODEL_DIR", "models/stt"),
                retry_count=stt_retry_count,
                use_stt_context=use_stt_context,
            )
        else:
            stt = GroqWhisperSTT(
                api_key=groq_api_key,
                model=stt_model,
                retry_count=stt_retry_count,
                use_stt_context=use_stt_context,
            )
        vad_cfg = VADConfig(
            speech_threshold=vad_threshold,
            silence_trigger_frames=vad_silence_frames,
            speech_trigger_frames=vad_speech_frames,
        )
        loop = ConversationLoop(
            capture_device_hint=capture_device,
            tts=tts,
            agent=agent,
            stt=stt,
            vad_config=vad_cfg,
            transcript_path=transcript_path,
            recording_path=recording_path,
            answered_speak_delay=answered_speak_delay,
            wait_for_human_audio=wait_for_human_audio,
            human_audio_timeout=human_audio_timeout,
            voicemail_detect_seconds=voicemail_detect_seconds,
            silence_does_not_end_call=silence_does_not_end_call,
            max_silence_seconds=max_silence_seconds,
            listen_after_tts_delay_ms=listen_after_tts_delay_ms,
            use_thinking_fillers=use_thinking_fillers,
            filler_probability=filler_probability,
            stream_llm_replies=stream_llm_replies,
            voicemail_play_on_greeting=voicemail_play_on_greeting,
            voicemail_greeting_frames_required=voicemail_greeting_frames_required,
            screening_purpose=screening_purpose,
            screening_already_spoken=screening_already_spoken,
        )
        logger.info("Transcript will be saved to: %s", transcript_path)
        logger.info("Recording will be saved to: %s", recording_path)
    except Exception as exc:
        logger.error("Realtime setup error: %s", exc)
        if browser.is_call_active():
            browser.hangup_call()
        if not session.is_terminal():
            session.transition(CallState.FAILED, f"realtime setup error: {exc}")
        return
    monitor_stop = threading.Event()
    monitor = threading.Thread(
        target=_monitor_live_call,
        args=(browser, loop, session, monitor_stop, float(call_max_duration), logger),
        daemon=True,
        name="GoogleVoiceCallMonitor",
    )
    monitor.start()
    try:
        time.sleep(0.2)
        loop.run(session=session, opening_line=opening_line, auto_opening=True)
    except Exception as exc:
        logger.error("Realtime loop error: %s", exc)
        if not session.is_terminal():
            session.transition(CallState.FAILED, f"realtime loop error: {exc}")
    finally:
        monitor_stop.set()
        monitor.join(timeout=2.0)
        if session.state == CallState.VOICEMAIL:
            publish_live_call(
                phase="voicemail",
                phone=session.phone,
                name=session.contact_name,
                outcome="voicemail",
                state="VOICEMAIL",
                message="Voicemail detected — playing drop",
            )
            logger.info("Realtime voicemail detected; fast delivery: %s", voicemail_path)
            _deliver_voicemail_and_hangup(
                browser,
                session,
                voicemail_path,
                loopback_device,
                loopback_available,
                logger,
                max_wait=1.2,
                ready_after=0.0,
            )
            return
        if session.outcome == "no_answer":
            logger.info("Silent answer; hanging up without a pitch")
            if browser.is_call_active():
                browser.hangup_call()
            return
        if not session.is_terminal():
            if browser.is_call_active():
                browser.hangup_call()
                session.transition(CallState.ENDED, "realtime loop stopped; hung up active call")
            else:
                session.transition(CallState.ENDED, "Google Voice call ended")


def _monitor_live_call(
    browser: GoogleVoiceBrowser,
    loop,
    session: CallSession,
    stop_event: threading.Event,
    max_duration: float,
    logger: logging.Logger,
    *,
    inactive_polls_required: int = 6,
    warmup_seconds: float = 5.0,
) -> None:
    started = time.monotonic()
    inactive_hits = 0
    logged_panel = False
    while not stop_event.is_set() and not loop.is_stopped():
        if not logged_panel:
            logged_panel = True
            panel = getattr(browser, "answered_call_panel_text", None)
            if callable(panel):
                text = panel()
                if isinstance(text, str):
                    logger.info("[DOM] answered call panel: %s", text or "<empty>")
        cue = getattr(browser, "voicemail_dom_cue", None)
        if callable(cue) and cue() is True:
            note = getattr(loop, "note_dom_voicemail", None)
            if callable(note):
                note()
        if max_duration > 0 and time.monotonic() - started >= max_duration:
            logger.warning("Realtime call max duration reached; ending call.")
            if browser.is_call_active():
                browser.hangup_call()
            loop.stop()
            return

        if getattr(loop, "is_agent_busy", lambda: False)():
            inactive_hits = 0
        elif browser.is_call_active():
            inactive_hits = 0
        else:
            inactive_hits += 1
            if (
                time.monotonic() - started > warmup_seconds
                and inactive_hits >= inactive_polls_required
            ):
                logger.info(
                    "Google Voice call no longer active (%d polls); stopping realtime loop.",
                    inactive_hits,
                )
                if not session.is_terminal():
                    try:
                        session.transition(CallState.ENDED, "Google Voice call ended")
                    except ValueError:
                        pass
                loop.stop()
                return

        time.sleep(1.0)


def _play_audio(
    path: Path,
    device_hint: str,
    loopback_available: bool,
    logger: logging.Logger,
) -> bool:
    if loopback_available:
        try:
            duration = play_wav_loopback(path, device_hint=device_hint, fallback_to_default=False)
            logger.info("Audio played: %.1fs via loopback", duration)
            return True
        except Exception as exc:
            logger.error("Audio playback failed: %s", exc)
            return False
    logger.error(
        "No loopback device — cannot inject audio. Configure VB-CABLE / LOOPBACK_DEVICE. File: %s",
        path,
    )
    return False


def _run_call_state_diagnostic(args: argparse.Namespace, cfg: Config) -> None:
    """
    Dial PHONE, capture DOM snapshots every 1.5 s to logs/diagnostics/, hang up after 90 s.
    Shows what Google Voice DOM looks like in each phase so we can verify CONNECTED signals.
    Only use with the designated safe test number (+17085681794).

    Usage:
        python -m src.main --diagnose-call-state +17085681794
    """
    import json
    import sys

    phone = args.diagnose_call_state.strip()
    diag_dir = BASE_DIR / "logs" / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("  CALL-STATE DIAGNOSTIC — INDUS TRANSPORTS LLC")
    print("=" * 60)
    print(f"\n  Target: {phone}")
    print(f"  Snapshots → {diag_dir}")
    print("\n  This will place a LIVE call. Answer on the other phone,")
    print("  stay silent 5s, then speak normally for 10s, then hang up.")
    print("  The diagnostic will capture DOM state at each transition.")
    print()
    try:
        answer = input("Type YES to proceed: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return
    if answer != "YES":
        print("Aborted.")
        return

    logger = setup_logger()
    profile_name = args.profile or cfg.profile_name

    browser = GoogleVoiceBrowser(profile_name=profile_name, headless=False)
    logger.info("[DIAG] Launching Chrome for diagnostic call to %s", phone)
    browser.launch()

    if not browser.is_logged_in():
        logger.warning("[DIAG] Not logged in — please sign in manually.")
        if not browser.wait_for_manual_login(timeout=300):
            browser.close()
            raise SystemExit("Login timed out.")

    snapshots: list[dict] = []
    phase = "PRE_DIAL"
    snap_idx = 0

    def save_snap(p: str) -> None:
        nonlocal snap_idx
        snap = browser.take_dom_snapshot(p)
        snap["snap_idx"] = snap_idx
        snapshots.append(snap)
        fname = diag_dir / f"snap_{snap_idx:03d}_{p}.json"
        fname.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        logger.info(
            "[DIAG] %s snap #%d — buttons=%d answered_controls=%s timer=%s call_active=%s",
            p, snap_idx,
            len(snap["buttons"]),
            snap["answered_controls_found"],
            snap["call_timer_found"],
            snap["call_active_found"],
        )
        snap_idx += 1

    try:
        save_snap(phase)

        logger.info("[DIAG] Dialing %s ...", phone)
        print(f"\n  >>> Dialing {phone} now. Answer on the other phone.")
        dialed = browser.dial_number(phone, connect_timeout=30)
        if not dialed:
            logger.error("[DIAG] dial_number returned False — aborting.")
            return
        phase = "DIALING"
        save_snap(phase)

        # Poll for up to 90 s, saving a snapshot every 1.5 s
        deadline = time.time() + 90.0
        last_phase = phase
        while time.time() < deadline:
            time.sleep(1.5)

            # Determine observed phase from DOM
            if browser._connected_timer_present():
                phase = "CONNECTED_TIMER"
            elif browser._answered_controls_present()[0]:
                ctrl_labels = browser._answered_controls_present()[1]
                phase = "CONNECTED_CONTROLS"
                logger.info("[DIAG] Answered controls: %s", ctrl_labels)
            elif browser._voicemail_cue_present() or browser._page_contains_voicemail():
                phase = "VOICEMAIL"
            elif browser._any_present("call_ended_banner"):
                phase = "ENDED"
            elif browser._any_present("call_active"):
                phase = "RINGING_OR_CONNECTED"
            else:
                phase = "UNKNOWN_OR_ENDED"

            save_snap(phase)

            if phase in ("ENDED", "VOICEMAIL", "UNKNOWN_OR_ENDED") and last_phase not in (
                "PRE_DIAL", "DIALING"
            ):
                logger.info("[DIAG] Call appears ended — stopping polling.")
                break
            last_phase = phase

    finally:
        phase = "HANGUP"
        save_snap(phase)
        logger.info("[DIAG] Hanging up.")
        browser.hangup_call()
        time.sleep(1)
        save_snap("POST_HANGUP")
        browser.close()

    # Summary
    print("\n" + "=" * 60)
    print(f"  Diagnostic complete — {snap_idx} snapshots saved to:")
    print(f"  {diag_dir}")
    print()
    connected_snaps = [s for s in snapshots if "CONNECTED" in s["phase"]]
    ringing_snaps = [s for s in snapshots if "RINGING" in s["phase"]]
    print(f"  RINGING snapshots: {len(ringing_snaps)}")
    print(f"  CONNECTED snapshots: {len(connected_snaps)}")
    if connected_snaps:
        best = connected_snaps[0]
        print(f"  First CONNECTED at snap #{best['snap_idx']}: "
              f"timer={best['call_timer_found']} "
              f"controls={best['answered_controls_found']}")
    print("=" * 60)


def _run_safe_test(args: argparse.Namespace, cfg: "Config") -> None:
    """
    Safe one-number test mode.

    1. Runs all preflight checks and prints results.
    2. Shows the target number prominently.
    3. Requires explicit keyboard confirmation before dialing.
    4. Dials exactly that one number — ignores --contacts and --limit.
    5. Uses the same realtime pipeline as a live run.

    Usage:
        python -m src.main --safe-test +15551234567
    """
    import sys

    phone = args.safe_test.strip()
    if not phone.startswith("+"):
        print(f"\n[WARN] Number '{phone}' has no country code (+1...). "
              "Google Voice works best with E.164 format.")

    # --- Preflight ---
    print("\n" + "=" * 60)
    print("  SAFE TEST MODE — INDUS TRANSPORTS LLC")
    print("=" * 60)
    print(f"\n  Target number: {phone}\n")
    print("Running preflight checks first...\n")
    exit_code = _run_preflight(cfg)
    if exit_code != 0:
        raise SystemExit(
            "\nPreflight FAILED. Fix the issues above before running a live test."
        )
    print("\nAll preflight checks passed.\n")

    # --- Explicit confirmation ---
    print("=" * 60)
    print(f"  ABOUT TO DIAL: {phone}")
    print("  This is a LIVE call. The number will ring immediately.")
    print("=" * 60)
    try:
        answer = input("\nType YES to confirm and dial, or anything else to abort: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return

    if answer != "YES":
        print("Aborted — you did not type YES.")
        return

    # --- Setup ---
    cfg.validate()
    logger = setup_logger()
    call_logger = CallLogger()

    from src.linux_audio import map_windows_audio_name

    profile_name    = args.profile or cfg.profile_name
    loopback_device = map_windows_audio_name(
        args.loopback_device or cfg.loopback_device, kind="loopback"
    )
    callback_number = args.callback_number or cfg.callback_number
    agent_name      = args.agent_name or cfg.agent_name
    company_name    = args.company_name or cfg.company_name
    company_context = args.company_context or cfg.company_context
    company_website = cfg.company_website
    from src.indus_campaign import OBJECTIVE as _INDUS_OBJECTIVE, OFFER as _INDUS_OFFER

    objective = args.objective or cfg.call_objective or _INDUS_OBJECTIVE
    offer = args.offer or cfg.call_offer or _INDUS_OFFER
    call_timeout    = args.call_timeout or cfg.call_timeout
    call_max_duration = args.call_max_duration or cfg.call_max_duration
    capture_device  = safe_capture_hint(
        map_windows_audio_name(args.capture_device or cfg.capture_device, kind="capture"),
        loopback_device,
    )
    tts_engine      = cfg.tts_engine
    tts_voice       = args.tts_voice or cfg.tts_voice
    piper_model_path = cfg.piper_model_path

    if not callback_number:
        raise SystemExit(
            "CALLBACK_NUMBER is not configured. Add it to .env or pass --callback-number."
        )

    ai = GroqAgent(api_key=cfg.groq_api_key, model=cfg.llm_model_batch, backend=cfg.llm_backend)
    output_dir   = ensure_audio_dir(args.output_dir)
    script_dir   = output_dir / "scripts"
    voicemail_dir = output_dir / "voicemails"
    script_dir.mkdir(parents=True, exist_ok=True)
    voicemail_dir.mkdir(parents=True, exist_ok=True)

    loopback_device_index = find_playable_loopback_device(loopback_device)
    loopback_available    = loopback_device_index is not None
    if not loopback_available:
        logger.warning("Loopback device '%s' not found — audio injection disabled.", loopback_device)
    if args.realtime and not loopback_available:
        raise SystemExit(
            "Realtime mode needs a loopback output device. "
            "Install VB-CABLE or run with --static-playback."
        )

    contact = {"phone": phone, "name": "Safe-Test Contact"}

    capture_device = _prepare_live_call_audio(
        cfg,
        loopback_device=loopback_device,
        capture_device=capture_device,
        profile_name=profile_name,
    )

    browser = GoogleVoiceBrowser(profile_name=profile_name, headless=args.headless)
    logger.info("Launching Chrome for safe test call to %s", phone)
    browser.launch()

    if not browser.is_logged_in():
        logger.warning("Google Voice not logged in — please sign in manually.")
        if not browser.wait_for_manual_login(timeout=300):
            browser.close()
            raise SystemExit("Login timed out.")

    # Confirm Chrome sees CABLE Output after Windows default mic was set.
    _cable_out = loopback_device.replace("Input", "Output").replace("INPUT", "OUTPUT")
    browser.ensure_microphone_device(_cable_out)

    try:
        _run_call(
            contact, 1, browser, ai, call_logger, logger,
            script_dir, voicemail_dir,
            objective, offer,
            callback_number,
            agent_name, company_name, company_context, company_website,
            loopback_device, loopback_device_index, loopback_available,
            call_timeout, call_max_duration,
            dry_run=False,
            realtime=args.realtime,
            capture_device=capture_device,
            tts_engine=tts_engine,
            tts_voice=tts_voice,
            piper_model_path=piper_model_path,
            piper_length_scale=cfg.piper_length_scale,
            piper_noise_scale=cfg.piper_noise_scale,
            piper_noise_w_scale=cfg.piper_noise_w_scale,
            stt_model=cfg.stt_model,
            vad_threshold=cfg.vad_threshold,
            groq_api_key=cfg.groq_api_key,
            answered_speak_delay=cfg.answered_speak_delay_seconds,
            wait_for_human_audio=cfg.wait_for_human_audio,
            human_audio_timeout=cfg.human_audio_timeout_seconds,
            answer_confirm_polls=cfg.answer_confirm_polls,
            stt_retry_count=cfg.stt_retry_count,
            vad_silence_frames=cfg.vad_silence_frames,
            vad_speech_frames=cfg.vad_speech_frames,
            min_ring_seconds=getattr(cfg, "min_ring_seconds", 0.0),
            max_ring_seconds=getattr(cfg, "max_ring_seconds", 45.0),
            voicemail_detect_seconds=getattr(cfg, "voicemail_detect_seconds", 15.0),
            tts_warmup=cfg.tts_warmup,
        )
    finally:
        browser.close()

    logger.info("Safe test call completed.")


def _run_parallel_live_batch(
    *,
    args: argparse.Namespace,
    cfg: Config,
    slots,
    contacts: list[dict],
    fingerprint: str,
    browser_holder: list,
    call_logger: CallLogger,
    logger: logging.Logger,
    script_dir: Path,
    voicemail_dir: Path,
    shared_call_kwargs: dict,
) -> None:
    """Run the Auto Dialer fixed-slot queue with one browser/audio route per slot."""
    from src.batch_progress import mark_completed
    from src.parallel_dialer import ParallelDialer

    holder_lock = threading.Lock()

    def context_factory(slot):
        logger.info(
            "[Slot %d] Launching Chrome profile=%s loopback=%s capture=%s",
            slot.slot_id + 1,
            slot.profile_name,
            slot.loopback_device,
            slot.capture_device,
        )
        loopback_index = find_playable_loopback_device(slot.loopback_device)
        if loopback_index is None:
            raise RuntimeError(
                f"[Slot {slot.slot_id + 1}] loopback device not found: {slot.loopback_device}"
            )
        slot_capture = _prepare_live_call_audio(
            cfg,
            loopback_device=slot.loopback_device,
            capture_device=slot.capture_device,
            profile_name=slot.profile_name,
        )
        browser = GoogleVoiceBrowser(
            profile_name=slot.profile_name,
            headless=args.headless,
            avoid_page_reload=cfg.avoid_gv_page_reload,
            pulse_sink=getattr(slot, "pulse_sink", "") or "",
            pulse_source=getattr(slot, "pulse_source", "") or "",
        )
        with holder_lock:
            browser_holder.append(browser)
        browser.launch()
        if not browser.is_logged_in():
            from src.gv_accounts import load_accounts
            account = next(
                (
                    row for row in load_accounts(include_password=True)
                    if row["profile_name"].casefold() == slot.profile_name.casefold()
                ),
                {},
            )
            logger.warning(
                "[Slot %d] Google Voice login required for %s",
                slot.slot_id + 1,
                account.get("email") or slot.profile_name,
            )
            status = browser.ensure_logged_in(
                str(account.get("email") or ""),
                str(account.get("password") or ""),
                timeout=300,
            )
            if status != "ready":
                raise RuntimeError(
                    f"[Slot {slot.slot_id + 1}] login did not complete: {status}"
                )
        cable_output = slot.loopback_device.replace("Input", "Output").replace("INPUT", "OUTPUT")
        browser.ensure_microphone_device(cable_output)
        return {
            "browser": browser,
            "ai": GroqAgent(api_key=cfg.groq_api_key, model=cfg.llm_model_batch, backend=cfg.llm_backend),
            "loopback_index": loopback_index,
            "capture_device": slot_capture,
            "calls_completed": 0,
        }

    def process_item(slot, context, index, contact):
        browser = context["browser"]
        if not browser.is_logged_in():
            raise RuntimeError(f"[Slot {slot.slot_id + 1}] Google Voice login required")
        logger.info(
            "[Slot %d:%s] Assigned contact %d: %s",
            slot.slot_id + 1,
            slot.account_email or slot.profile_name,
            index,
            contact.get("phone", ""),
        )
        from src.account_pacing import PACER

        allowed, reason = PACER.allow(slot.account_id or slot.profile_name)
        if not allowed:
            logger.info("[Slot %d] pacing hold: %s", slot.slot_id + 1, reason)
            time.sleep(PACER.min_gap_s())
        PACER.mark(slot.account_id or slot.profile_name)
        kwargs = dict(shared_call_kwargs)
        kwargs.update(
            loopback_device=slot.loopback_device,
            loopback_device_index=context["loopback_index"],
            loopback_available=True,
            capture_device=context["capture_device"],
        )
        completed_phone = None
        for attempt in range(2):
            try:
                completed_phone = _run_call(
                    contact,
                    index,
                    browser,
                    context["ai"],
                    call_logger,
                    logger,
                    script_dir,
                    voicemail_dir,
                    **kwargs,
                )
                break
            except WebDriverException as exc:
                logger.error(
                    "[Slot %d] Chrome/WebDriver error for contact %d (attempt %d/2): %s",
                    slot.slot_id + 1,
                    index,
                    attempt + 1,
                    exc,
                )
                if attempt == 0 and browser.recover_session():
                    continue
                break

        if completed_phone:
            mark_completed(fingerprint, completed_phone, index)
            context["calls_completed"] += 1
            restart_every = int(cfg.chrome_restart_every_n_calls)
            if restart_every > 0 and context["calls_completed"] % restart_every == 0:
                logger.info("[Slot %d] Restarting Chrome after %d calls", slot.slot_id + 1, restart_every)
                browser.close()
                browser.launch()
                if not browser.is_logged_in() and not browser.wait_for_manual_login(timeout=120):
                    raise RuntimeError(f"[Slot {slot.slot_id + 1}] re-login timed out")
        return completed_phone

    def context_close(slot, context):
        browser = context["browser"]
        try:
            browser.close()
        finally:
            with holder_lock:
                if browser in browser_holder:
                    browser_holder.remove(browser)
            logger.info("[Slot %d] Browser closed", slot.slot_id + 1)

    indexed_contacts = list(enumerate(contacts, start=1))
    scheduler = ParallelDialer(
        slots,
        cooldown_min=float(cfg.call_cooldown_seconds),
        cooldown_max=float(cfg.call_cooldown_seconds),
        restart_limit=3,
    )
    scheduler.run(
        indexed_contacts,
        context_factory=context_factory,
        process_item=process_item,
        context_close=context_close,
    )


def main() -> None:
    args = _parse_args()
    if args.list_audio_devices:
        print_devices()
        return

    cfg = Config.load()
    args.headless = _resolve_headless(args, cfg)
    if args.preflight:
        raise SystemExit(_run_preflight(cfg))
    if args.audio_route_test:
        raise SystemExit(_run_audio_route_test(args, cfg))

    # ---- DOM diagnostic mode ----
    if args.diagnose_call_state:
        _run_call_state_diagnostic(args, cfg)
        return

    # ---- Safe one-number test mode ----
    if args.safe_test:
        _run_safe_test(args, cfg)
        return

    cfg.validate()
    logger = setup_logger()
    logger.info("Chrome headless=%s (Google Voice live dials)", args.headless)
    clear_live_call(message="Dialer starting")
    from src.groq_pool import get_groq_pool

    try:
        pool = get_groq_pool(refresh=True)
        logger.info(
            "Groq API pool ready: %d key(s), failover enabled",
            pool.key_count,
        )
    except ValueError:
        pass
    call_logger = CallLogger()

    from src.linux_audio import map_windows_audio_name

    contact_path = Path(args.contacts or cfg.contacts_file)
    profile_name = args.profile or cfg.profile_name
    loopback_device = map_windows_audio_name(
        args.loopback_device or cfg.loopback_device, kind="loopback"
    )
    callback_number = args.callback_number or cfg.callback_number
    agent_name = args.agent_name or cfg.agent_name
    company_name = args.company_name or cfg.company_name
    company_context = args.company_context or cfg.company_context
    company_website = cfg.company_website
    from src.indus_campaign import OBJECTIVE as _INDUS_OBJECTIVE, OFFER as _INDUS_OFFER

    objective = args.objective or cfg.call_objective or _INDUS_OBJECTIVE
    offer = args.offer or cfg.call_offer or _INDUS_OFFER
    call_timeout = args.call_timeout or cfg.call_timeout
    call_max_duration = args.call_max_duration or cfg.call_max_duration
    capture_device = safe_capture_hint(
        map_windows_audio_name(args.capture_device or cfg.capture_device, kind="capture"),
        loopback_device,
    )
    tts_engine = cfg.tts_engine
    tts_voice = args.tts_voice or cfg.tts_voice
    piper_model_path = cfg.piper_model_path

    if not callback_number:
        raise SystemExit(
            "CALLBACK_NUMBER is not configured. Add it to .env or pass --callback-number."
        )

    from src.batch_progress import (
        contacts_fingerprint,
        filter_contacts,
        mark_completed,
        reset_progress,
    )

    logger.info("Loading contacts from %s", contact_path)
    all_contacts = load_contacts(contact_path)
    if not all_contacts:
        raise SystemExit("No valid contacts loaded — check your contacts file.")

    fingerprint = contacts_fingerprint(contact_path)
    if args.reset_batch_progress:
        reset_progress(fingerprint)
        logger.info("Cleared batch progress for %s", contact_path)

    contacts = filter_contacts(
        all_contacts[: args.limit],
        fingerprint,
        resume=not args.no_resume,
    )
    from src.lead_crm import filter_callable

    contacts, dnc_skipped = filter_callable(contacts)
    if dnc_skipped:
        logger.info("Do-not-call list skipped %d contact(s)", dnc_skipped)
    if cfg.use_call_intelligence:
        from src.call_intelligence import filter_dialable_contacts

        contacts, intel_skipped, _skipped_rows = filter_dialable_contacts(contacts)
        if intel_skipped:
            logger.info(
                "Call intelligence skipped %d contact(s) (DNC, wrong number, repeat failures)",
                intel_skipped,
            )
    if not contacts:
        raise SystemExit(
            "No contacts left to dial — all numbers in this list were completed. "
            "Use --no-resume or --reset-batch-progress to dial again."
        )
    logger.info(
        "Loaded %d contacts (%d in batch after resume filter), limit=%d",
        len(all_contacts),
        len(contacts),
        args.limit,
    )

    ai = GroqAgent(api_key=cfg.groq_api_key, model=cfg.llm_model_batch, backend=cfg.llm_backend)

    output_dir = ensure_audio_dir(args.output_dir)
    script_dir = output_dir / "scripts"
    voicemail_dir = output_dir / "voicemails"
    script_dir.mkdir(parents=True, exist_ok=True)
    voicemail_dir.mkdir(parents=True, exist_ok=True)

    # One Google Voice account, one live call. Host headroom is logged and is
    # not used to open a second line.
    from src.capacity import capacity_report, google_voice_concurrent_calls
    from src.gv_accounts import load_accounts
    enabled_accounts = [row for row in load_accounts(include_password=True) if row.get("enabled")]
    account_by_profile = {str(row["profile_name"]).casefold(): row for row in enabled_accounts}
    from src.agent_fleet import FLEET, prepare_slot_audio

    fleet_plan = FLEET.plan(enabled_accounts)
    if enabled_accounts:
        first_account = enabled_accounts[0]
        profile_name = first_account["profile_name"]
        if fleet_plan.slots:
            loopback_device = fleet_plan.slots[0].loopback_device
            capture_device = fleet_plan.slots[0].capture_device
        else:
            loopback_device = first_account["loopback_device"] or loopback_device
            capture_device = first_account["capture_device"] or capture_device
        for slot in fleet_plan.slots:
            prepare_slot_audio(slot)
        logger.info(
            "Fleet admitted %d of %d Google Voice account(s) (max %d, queued %d): %s",
            len(fleet_plan.slots),
            len(enabled_accounts),
            fleet_plan.max_agents,
            len(fleet_plan.queued_accounts),
            ", ".join(slot.email or slot.profile_name for slot in fleet_plan.slots),
        )

    # Auto-route Windows mic/speakers first, then resolve PortAudio cable names.
    # VB-CABLE often renames itself ("CABLE Input" → "CABLE In 16 Ch"); setup alone
    # cannot invent a missing driver, but matching must tolerate those aliases.
    if not args.dry_run:
        capture_device = _prepare_live_call_audio(
            cfg,
            loopback_device=loopback_device,
            capture_device=capture_device,
            profile_name=profile_name,
        )

    loopback_device_index = find_playable_loopback_device(loopback_device)
    loopback_available = loopback_device_index is not None
    from src.gv_lines import line_count, parallel_profiles

    requested_slots = line_count()
    logger.info(
        "Live agents=%d. Each Google Voice account places %d call at a time. %s",
        requested_slots,
        google_voice_concurrent_calls(),
        capacity_report(),
    )
    parallel_requested = requested_slots > 1
    if not loopback_available and not parallel_requested:
        logger.warning(
            "Loopback device '%s' not found — audio injection disabled. "
            "Install VB-CABLE from https://vb-audio.com/Cable/",
            loopback_device,
        )
    if not args.dry_run and not loopback_available and not parallel_requested:
        if sys.platform.startswith("linux"):
            from src.voice_playback import pulse_failure_detail

            detail = pulse_failure_detail()
            extra = f" {detail}" if detail else ""
            raise SystemExit(
                "Live calling needs the Pulse sink "
                f"'{loopback_device}' (pacat/paplay, not a PortAudio device name). "
                "Start voxyrn-audio.service and run: python -m src.main --audio-route-test."
                + extra
            )
        raise SystemExit(
            "Live calling needs an output loopback device so TTS reaches Google Voice. "
            "Install/configure VB-CABLE, set LOOPBACK_DEVICE or --loopback-device to the "
            "playback cable input (also accepts 'CABLE In 16 Ch' / VB-Audio Speakers)."
        )

    if args.dry_run:
        logger.info("=== DRY RUN mode — no browser will be opened ===")
        for i, contact in enumerate(contacts[: args.limit], start=1):
            _run_call(
                contact, i, None, ai, call_logger, logger,
                script_dir, voicemail_dir,
                objective, offer,
                callback_number,
                agent_name, company_name, company_context, company_website,
                loopback_device, loopback_device_index, loopback_available,
                call_timeout, call_max_duration,
                dry_run=True,
                realtime=args.realtime,
                capture_device=capture_device,
                tts_engine=tts_engine,
                tts_voice=tts_voice,
                piper_model_path=piper_model_path,
                piper_length_scale=cfg.piper_length_scale,
                piper_noise_scale=cfg.piper_noise_scale,
                piper_noise_w_scale=cfg.piper_noise_w_scale,
                stt_model=cfg.stt_model,
                vad_threshold=cfg.vad_threshold,
                groq_api_key=cfg.groq_api_key,
                answered_speak_delay=cfg.answered_speak_delay_seconds,
                wait_for_human_audio=cfg.wait_for_human_audio,
                human_audio_timeout=cfg.human_audio_timeout_seconds,
                answer_confirm_polls=cfg.answer_confirm_polls,
                stt_retry_count=cfg.stt_retry_count,
                vad_silence_frames=cfg.vad_silence_frames,
                vad_speech_frames=cfg.vad_speech_frames,
                min_ring_seconds=getattr(cfg, "min_ring_seconds", 0.0),
                max_ring_seconds=getattr(cfg, "max_ring_seconds", 45.0),
                voicemail_detect_seconds=getattr(cfg, "voicemail_detect_seconds", 15.0),
                tts_warmup=cfg.tts_warmup,
                silence_does_not_end_call=cfg.silence_does_not_end_call,
                use_stt_context=cfg.use_stt_context,
                max_silence_seconds=cfg.max_silence_seconds,
            )
        logger.info("Dry run complete. Audio files in %s/", output_dir)
        return

    browser_holder: list = []
    _install_shutdown_handlers(browser_holder)

    shared_call_kwargs = dict(
        objective=objective,
        offer=offer,
        callback_number=callback_number,
        agent_name=agent_name,
        company_name=company_name,
        company_context=company_context,
        company_website=company_website,
        loopback_device=loopback_device,
        loopback_device_index=loopback_device_index,
        loopback_available=loopback_available,
        call_timeout=call_timeout,
        call_max_duration=call_max_duration,
        dry_run=False,
        realtime=args.realtime,
        capture_device=capture_device,
        tts_engine=tts_engine,
        tts_voice=tts_voice,
        piper_model_path=piper_model_path,
        stt_model=cfg.stt_model,
        vad_threshold=cfg.vad_threshold,
        groq_api_key=cfg.groq_api_key,
        answered_speak_delay=cfg.answered_speak_delay_seconds,
        wait_for_human_audio=cfg.wait_for_human_audio,
        human_audio_timeout=cfg.human_audio_timeout_seconds,
        answer_confirm_polls=cfg.answer_confirm_polls,
        stt_retry_count=cfg.stt_retry_count,
        vad_silence_frames=cfg.vad_silence_frames,
        vad_speech_frames=cfg.vad_speech_frames,
        min_ring_seconds=cfg.min_ring_seconds,
        max_ring_seconds=cfg.max_ring_seconds,
        voicemail_detect_seconds=cfg.voicemail_detect_seconds,
        tts_warmup=cfg.tts_warmup,
        tts_allow_sapi_fallback=cfg.tts_allow_sapi_fallback,
        silence_does_not_end_call=cfg.silence_does_not_end_call,
        use_stt_context=cfg.use_stt_context,
        max_silence_seconds=cfg.max_silence_seconds,
        llm_model_realtime=cfg.llm_model_realtime,
        listen_after_tts_delay_ms=cfg.listen_after_tts_delay_ms,
        use_thinking_fillers=cfg.use_thinking_fillers,
        filler_probability=cfg.filler_probability,
        stream_llm_replies=cfg.stream_llm_replies,
        voicemail_max_wait_seconds=cfg.voicemail_max_wait_seconds,
        voicemail_play_after_seconds=cfg.voicemail_play_after_seconds,
        voicemail_play_on_greeting=cfg.voicemail_play_on_greeting,
        voicemail_greeting_frames_required=cfg.voicemail_greeting_frames_required,
        screening_purpose_text=cfg.screening_purpose_text,
        groq_max_retries_per_minute=_scaled_groq_rate_limit(cfg),
    )

    from src.parallel_dialer import build_parallel_slots

    try:
        parallel_slots = build_parallel_slots(
            requested=requested_slots,
            max_concurrent_dials=requested_slots,
            force_requested_slots=requested_slots > 1,
            primary_profile=profile_name,
            primary_loopback=loopback_device,
            primary_capture=capture_device,
            parallel_profiles=parallel_profiles(requested_slots) if requested_slots > 1 else [],
            allow_shared_audio_routes=cfg.allow_shared_audio_routes,
        )
    except ValueError as exc:
        raise SystemExit(f"Parallel calling configuration error: {exc}") from exc

    if len(parallel_slots) > 1:
        parallel_contacts = contacts
        if cfg.max_calls_per_run > 0:
            parallel_contacts = contacts[: cfg.max_calls_per_run]
        logger.info(
            "Starting controlled parallel call loop: %d slots, %d contacts",
            len(parallel_slots),
            len(parallel_contacts),
        )
        _run_parallel_live_batch(
            args=args,
            cfg=cfg,
            slots=parallel_slots,
            contacts=parallel_contacts,
            fingerprint=fingerprint,
            browser_holder=browser_holder,
            call_logger=call_logger,
            logger=logger,
            script_dir=script_dir,
            voicemail_dir=voicemail_dir,
            shared_call_kwargs=shared_call_kwargs,
        )
        logger.info("All parallel calls complete. Logs: logs/call_logs.csv | Audio: %s/", output_dir)
        return

    first_slot = fleet_plan.slots[0] if fleet_plan.slots else None
    browser = GoogleVoiceBrowser(
        profile_name=profile_name,
        headless=args.headless,
        avoid_page_reload=cfg.avoid_gv_page_reload,
        pulse_sink=first_slot.pulse_sink if first_slot else "",
        pulse_source=first_slot.pulse_source if first_slot else "",
    )
    browser_holder.append(browser)
    logger.info("Launching Chrome profile: %s", profile_name)
    browser.launch()

    if not browser.is_logged_in():
        account = account_by_profile.get(profile_name.casefold(), {})
        logger.warning("Google Voice session needs sign-in for profile %s", profile_name)
        status = browser.ensure_logged_in(
            str(account.get("email") or ""),
            str(account.get("password") or ""),
            timeout=300,
        )
        if status != "ready":
            browser.close()
            raise SystemExit(f"Google Voice login did not complete: {status}")

    # Confirm Chrome sees CABLE Output after Windows default mic was set.
    _cable_out = loopback_device.replace("Input", "Output").replace("INPUT", "OUTPUT")
    browser.ensure_microphone_device(_cable_out)

    logger.info("Logged in. Starting call loop (%d contacts).", len(contacts))

    if (
        cfg.tts_warmup
        and loopback_device_index is not None
        and str(tts_engine).strip().lower() not in {"piper", "piper-tts"}
    ):
        try:
            from src.opening_pool import warmup_phrases
            from src.tts_cache import TTSCache

            cache = TTSCache(tts_voice=tts_voice)
            cache.warm(warmup_phrases(agent_name, company_name))
        except Exception as exc:
            logger.debug("Opening TTS warmup skipped: %s", exc)
    elif cfg.tts_warmup and str(tts_engine).strip().lower() in {"piper", "piper-tts"}:
        logger.info("Piper voice stays loaded and repeated lines are cached during the call")

    call_cooldown_seconds = float(cfg.call_cooldown_seconds)
    from src.log_rotation import rotate_logs_if_needed

    calls_completed = 0
    try:
        for i, contact in enumerate(contacts, start=1):
            if cfg.max_calls_per_run > 0 and calls_completed >= cfg.max_calls_per_run:
                logger.info("Reached max_calls_per_run=%d; stopping batch.", cfg.max_calls_per_run)
                break
            if i % 50 == 1:
                rotate_logs_if_needed()
            if not browser.is_logged_in():
                raise SystemExit("Google Voice login required.")
            completed_phone: str | None = None
            for attempt in range(2):
                try:
                    completed_phone = _run_call(
                        contact,
                        i,
                        browser,
                        ai,
                        call_logger,
                        logger,
                        script_dir,
                        voicemail_dir,
                        **shared_call_kwargs,
                    )
                    break
                except WebDriverException as exc:
                    logger.error(
                        "[%d] Chrome/WebDriver error (attempt %d/2): %s",
                        i,
                        attempt + 1,
                        exc,
                    )
                    if attempt == 0 and browser.recover_session():
                        logger.info("[%d] Browser recovered — retrying contact", i)
                        continue
                    logger.error("Stopping call loop — could not recover browser session.")
                    completed_phone = None
                    break
            if completed_phone:
                mark_completed(fingerprint, completed_phone, i)
                calls_completed += 1
            if (
                cfg.chrome_restart_every_n_calls > 0
                and calls_completed > 0
                and calls_completed % cfg.chrome_restart_every_n_calls == 0
            ):
                logger.info("Chrome restart interval reached (%d calls); refreshing session", calls_completed)
                browser.close()
                browser.launch()
                if not browser.is_logged_in():
                    logger.warning("Re-login required after Chrome restart")
                    if not browser.wait_for_manual_login(timeout=120):
                        break
            if browser.driver is None:
                logger.error("Chrome/Google Voice session is no longer available; stopping call loop.")
                break
            if call_cooldown_seconds > 0:
                time.sleep(call_cooldown_seconds)
            else:
                time.sleep(1.0)

    finally:
        browser_holder.clear()
        browser.close()

    logger.info("All calls complete. Logs: logs/call_logs.csv | Audio: %s/", output_dir)


if __name__ == "__main__":
    main()
