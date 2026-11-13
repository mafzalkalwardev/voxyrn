"""PulseAudio / PipeWire routing for headless Google Voice on Linux.

TTS is played into the ``voxyrn_to_gv`` null sink. Chromium's microphone is
that sink's monitor, so the call hears the agent. Chromium's speaker is the
``voxyrn_from_gv`` null sink, and speech-to-text records that sink's monitor.

PipeWire accepts the same ``pactl`` commands through ``pipewire-pulse``.
Windows VB-CABLE setup stays in ``src.audio_setup``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger("GoogleVoiceAgent")

SINK_TO_GV = "voxyrn_to_gv"
SINK_FROM_GV = "voxyrn_from_gv"
TTS_MONITOR = f"{SINK_TO_GV}.monitor"
CAPTURE_MONITOR = f"{SINK_FROM_GV}.monitor"

# VB-CABLE names saved on Windows. On Linux the same route is two null sinks:
# "CABLE-A Input" is where TTS is played, "CABLE-A Output" is what the call is heard on.
_WINDOWS_LOOPBACK = frozenset({"cable input", "cable a input"})
_WINDOWS_CAPTURE = frozenset({"cable output", "cable a output"})


def map_windows_audio_name(name: str, *, kind: str) -> str:
    """Replace a Windows VB-CABLE device name with the Linux null sink."""
    raw = (name or "").strip()
    if not raw or not sys.platform.startswith("linux"):
        return raw
    key = " ".join(raw.casefold().replace("-", " ").replace("_", " ").split())
    if kind == "loopback" and key in _WINDOWS_LOOPBACK:
        return SINK_TO_GV
    if kind == "capture" and key in _WINDOWS_CAPTURE:
        return CAPTURE_MONITOR
    return raw
_LISTEN_TAG = "voxyrn_listen_in"
_players: list[subprocess.Popen] = []
_players_lock = threading.Lock()


def stop_pulse_playback() -> None:
    """Interrupt pacat/paplay started by this process."""
    with _players_lock:
        procs = list(_players)
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()


def pactl_available() -> bool:
    return shutil.which("pactl") is not None


def _pactl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pactl", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def list_sinks() -> list[str]:
    if not pactl_available():
        return []
    proc = _pactl(["list", "short", "sinks"])
    names: list[str] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            names.append(parts[1].strip())
    return names


def list_modules() -> list[tuple[str, str]]:
    """Return (module_id, module_name_and_args) rows from pactl."""
    if not pactl_available():
        return []
    proc = _pactl(["list", "short", "modules"])
    rows: list[tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0].strip(), "\t".join(parts[1:])))
    return rows


# module-suspend-on-idle reads this node property. 0 means "do not suspend".
# Space-separated. Commas are swallowed into device.description and never
# become real node properties, so PipeWire keeps the sink suspended.
_AWAKE_PROPS = (
    "session.suspend-timeout-seconds=0 "
    "node.always-process=true "
    "node.passive=false "
    "node.suspend-on-idle=false"
)


def sink_states() -> dict[str, str]:
    """Map sink name to RUNNING, IDLE, or SUSPENDED."""
    if not pactl_available():
        return {}
    proc = _pactl(["list", "short", "sinks"])
    states: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            state = parts[-1].strip().upper() if len(parts) >= 5 else ""
            states[parts[1].strip()] = state
    return states


def sink_suspend_timeout(name: str) -> Optional[str]:
    """Return the session.suspend-timeout-seconds property, if pactl printed it."""
    if not pactl_available():
        return None
    proc = _pactl(["list", "sinks"])
    current = ""
    in_props = False
    for line in (proc.stdout or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("Name:"):
            current = stripped.split(":", 1)[1].strip()
            in_props = False
        elif current == name and stripped == "Properties:":
            in_props = True
        elif current == name and in_props and stripped.startswith("session.suspend-timeout-seconds"):
            return stripped.split("=", 1)[-1].strip().strip('"')
        elif current == name and in_props and stripped and not line.startswith((" ", "\t")):
            in_props = False
    return None


def _module_id_for_sink(name: str) -> Optional[str]:
    for module_id, rest in list_modules():
        if f"sink_name={name}" in rest.split():
            return module_id
        if f"sink_name={name}" in rest:
            return module_id
    return None


def wake_sink(name: str) -> None:
    """Resume a PipeWire node. A suspended null sink makes paplay and pacat block."""
    if pactl_available():
        _pactl(["suspend-sink", name, "0"])


def _module_args_for_sink(name: str) -> str:
    for _module_id, rest in list_modules():
        if f"sink_name={name}" in rest:
            return rest
    return ""


def ensure_null_sink(name: str, description: str) -> bool:
    """Load the null sink, or reload it so PipeWire will not suspend it when idle."""
    props = f"device.description={description},{_AWAKE_PROPS}"
    exists = name in list_sinks()
    module_args = _module_args_for_sink(name) if exists else ""
    stays_awake = "session.suspend-timeout-seconds=0" in module_args or sink_suspend_timeout(name) == "0"
    if exists and stays_awake:
        wake_sink(name)
        return True
    if exists:
        module_id = _module_id_for_sink(name)
        if module_id:
            logger.info(
                "Reloading Pulse sink %s so session.suspend-timeout-seconds=0",
                name,
            )
            _pactl(["unload-module", module_id])
    proc = _pactl(
        [
            "load-module",
            "module-null-sink",
            f"sink_name={name}",
            f"sink_properties={props}",
        ]
    )
    if proc.returncode != 0:
        logger.warning(
            "Could not load null sink %s: %s",
            name,
            (proc.stderr or proc.stdout or "").strip(),
        )
        return name in list_sinks()
    wake_sink(name)
    return name in list_sinks() or proc.returncode == 0


def _pulse_env(sink_name: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PULSE_SINK"] = sink_name
    return env


def probe_pulse_sink(sink_name: str, timeout_s: float = 5.0) -> tuple[bool, str]:
    """Play a short silence buffer. Fails fast when PipeWire leaves the sink suspended."""
    if not pactl_available():
        return False, "pactl is not installed."
    if sink_name not in list_sinks():
        return False, f"Pulse sink {sink_name} is not loaded."
    wake_sink(sink_name)
    player = shutil.which("pacat") or shutil.which("paplay")
    if not player:
        return False, "Neither pacat nor paplay is installed (package pulseaudio-utils)."
    silence = b"\x00\x00" * 1600  # 100 ms of s16le at 16 kHz
    cmd = [
        player,
        f"--device={sink_name}",
        "--playback",
        "--format=s16le",
        "--rate=16000",
        "--channels=1",
        "--latency-msec=30",
    ]
    # paplay reads a file, not raw stdin. pacat is the raw client.
    if Path(player).name.startswith("paplay"):
        return False, "pacat is required to probe the Pulse sink (pulseaudio-utils)."
    try:
        proc = subprocess.run(
            cmd,
            input=silence,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            env=_pulse_env(sink_name),
        )
    except subprocess.TimeoutExpired:
        return False, _suspend_timeout_message(sink_name)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "pacat failed").strip()
        return False, f"pacat could not open {sink_name}: {detail}"
    state = sink_states().get(sink_name, "")
    if state == "SUSPENDED":
        return False, _suspend_timeout_message(sink_name)
    return True, f"Pulse sink {sink_name} accepted playback (state {state or 'open'})."


def _suspend_timeout_message(sink_name: str) -> str:
    return (
        f"Playback to {sink_name} timed out because PipeWire suspended the idle sink. "
        "Install deploy/linux/wireplumber (session.suspend-timeout-seconds=0), "
        "restart voxyrn-audio.service, and run this check again."
    )


def chromium_pulse_env() -> dict[str, str]:
    """Environment for the Chromium process only.

    The browser plays call audio into ``SINK_FROM_GV`` and reads the agent
    voice from ``TTS_MONITOR``. Python TTS/STT use explicit devices instead,
    so they are not pinned to these defaults.
    """
    return {
        "PULSE_SINK": SINK_FROM_GV,
        "PULSE_SOURCE": TTS_MONITOR,
    }


def _listen_module_ids() -> list[str]:
    ids: list[str] = []
    for module_id, rest in list_modules():
        if _LISTEN_TAG in rest:
            ids.append(module_id)
    return ids


def listen_in_active() -> Optional[bool]:
    """True when call audio is looped to the default speakers. None if Pulse is down."""
    if not pactl_available():
        return None
    return bool(_listen_module_ids())


def set_linux_listen_in(enabled: bool) -> bool:
    """Loop the Google Voice monitor to the default sink, or remove that loop.

    A VPS often has no physical speakers. Unloading the loop is the silent
    default. Recordings and STT read the monitor directly and do not depend
    on this switch. Returns False when Pulse is unavailable.
    """
    if not pactl_available():
        return False
    for module_id in _listen_module_ids():
        _pactl(["unload-module", module_id])
    if not enabled:
        return True
    proc = _pactl(
        [
            "load-module",
            "module-loopback",
            f"source={CAPTURE_MONITOR}",
            "sink=@DEFAULT_SINK@",
            "latency_msec=40",
            f"sink_input_properties=media.name={_LISTEN_TAG}",
        ]
    )
    if proc.returncode != 0:
        logger.warning(
            "Listen-in loopback was not loaded (no default sink is common on a VPS): %s",
            (proc.stderr or proc.stdout or "").strip(),
        )
        return False
    return True


def linux_speakers_muted() -> Optional[bool]:
    active = listen_in_active()
    if active is None:
        return None
    return not active


def play_wav_to_pulse_sink(wav_path: str | Path, sink_name: str) -> float:
    """Play a WAV into a Pulse/PipeWire sink by name. Returns duration in seconds.

    PortAudio on this stack only exposes ``pulse`` and ``default``, so the named
    null sink is selected with ``--device`` and ``PULSE_SINK`` rather than a
    sounddevice index.
    """
    paplay = shutil.which("paplay") or shutil.which("pacat")
    if not paplay:
        raise RuntimeError(
            "paplay is not installed. Install pulseaudio-utils so TTS can reach Chromium."
        )
    wav_path = Path(wav_path)
    if not wav_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {wav_path}")
    try:
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate() or 1
            duration = wf.getnframes() / float(rate)
    except wave.Error:
        duration = 0.0
    if sink_name not in list_sinks():
        ensure_null_sink(sink_name, sink_name)
    wake_sink(sink_name)
    cmd = [paplay, f"--device={sink_name}", str(wav_path)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(8.0, duration + 8.0),
            check=False,
            env=_pulse_env(sink_name),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(_suspend_timeout_message(sink_name)) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "paplay failed").strip()
        raise RuntimeError(
            f"paplay could not play to Pulse sink '{sink_name}': {detail}. "
            "Create the sinks with pactl (the live run does this automatically)."
        )
    logger.info("TTS played %.2fs to Pulse sink %s via PULSE_SINK", duration, sink_name)
    return duration


def play_float32_to_pulse_sink(samples, samplerate: int, sink_name: str, stop_event=None) -> None:
    """Stream float32 mono audio to a named sink with pacat."""
    import numpy as np

    pacat = shutil.which("pacat")
    if not pacat:
        raise RuntimeError("pacat is not installed. Install pulseaudio-utils.")
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.ascontiguousarray(audio, dtype="<f4")
    if sink_name not in list_sinks():
        ensure_null_sink(sink_name, sink_name)
    wake_sink(sink_name)
    proc = subprocess.Popen(
        [
            pacat,
            f"--device={sink_name}",
            "--playback",
            "--format=float32le",
            f"--rate={int(samplerate)}",
            "--channels=1",
            "--latency-msec=30",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=_pulse_env(sink_name),
    )
    with _players_lock:
        _players.append(proc)
    try:
        assert proc.stdin is not None
        raw = audio.tobytes()
        chunk = max(int(samplerate) * 4, 4)
        for start in range(0, len(raw), chunk):
            if stop_event is not None and stop_event.is_set():
                break
            proc.stdin.write(raw[start:start + chunk])
        proc.stdin.close()
        try:
            proc.wait(timeout=max(8.0, (len(audio) / float(samplerate or 1)) + 8.0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(_suspend_timeout_message(sink_name)) from exc
        stopped = stop_event is not None and stop_event.is_set()
        if not stopped and proc.returncode not in (0, None):
            err = (proc.stderr.read() if proc.stderr else b"") or b""
            raise RuntimeError(
                f"pacat exited {proc.returncode} for sink {sink_name}: {err.decode(errors='replace').strip()}"
            )
    except BrokenPipeError:
        if stop_event is None or not stop_event.is_set():
            raise
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
        with _players_lock:
            if proc in _players:
                _players.remove(proc)


def capture_pulse_monitor(source: str, samplerate: int, frame_size: int, stop_event, put_frame) -> None:
    """Block, reading float32 mono frames from a Pulse monitor source into put_frame."""
    import numpy as np

    parec = shutil.which("parec")
    if not parec:
        raise RuntimeError(
            "parec is not installed. Install pulseaudio-utils so STT can hear the call."
        )
    env = os.environ.copy()
    env["PULSE_SOURCE"] = source
    proc = subprocess.Popen(
        [
            parec,
            f"--device={source}",
            "--format=float32le",
            f"--rate={int(samplerate)}",
            "--channels=1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    frame_bytes = int(frame_size) * 4
    try:
        assert proc.stdout is not None
        while not stop_event.is_set():
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            put_frame(np.frombuffer(buf, dtype=np.float32).copy())
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


def ensure_linux_call_audio(
    *,
    loopback_hint: str = SINK_TO_GV,
    capture_hint: str = CAPTURE_MONITOR,
    chrome_profile_dir: Path | None = None,
    set_windows_devices: bool = True,
    live_listen_enabled: bool = False,
):
    """Create the virtual sinks and Chromium mic prefs. Windows args are ignored."""
    from src.audio_setup import AudioSetupResult, patch_chrome_mic_prefs

    del loopback_hint, capture_hint, set_windows_devices
    result = AudioSetupResult(ok=False)
    result.loopback_hint = SINK_TO_GV
    result.capture_hint = CAPTURE_MONITOR
    result.mic_name = TTS_MONITOR
    result.speaker_name = SINK_FROM_GV

    if not pactl_available():
        result.messages.append(
            "pactl not found. Install pulseaudio-utils and run PulseAudio or PipeWire "
            "(see deploy/linux/README.md). TTS will not enter the call until the sinks exist."
        )
        return result

    tts_ok = ensure_null_sink(SINK_TO_GV, "Voxyrn_TTS_to_Google_Voice")
    call_ok = ensure_null_sink(SINK_FROM_GV, "Voxyrn_Google_Voice_Speaker")
    result.mic_set = tts_ok
    result.speaker_set = call_ok
    if tts_ok:
        result.messages.append(
            f"TTS sink {SINK_TO_GV} ready. Chromium microphone should be {TTS_MONITOR}."
        )
    else:
        result.messages.append(f"Failed to create Pulse sink {SINK_TO_GV}.")
    if call_ok:
        result.messages.append(
            f"Call sink {SINK_FROM_GV} ready. STT captures {CAPTURE_MONITOR}."
        )
    else:
        result.messages.append(f"Failed to create Pulse sink {SINK_FROM_GV}.")

    if set_linux_listen_in(bool(live_listen_enabled)):
        result.speaker_muted = not bool(live_listen_enabled)
        result.messages.append(
            "Listen-in looped to the default sink."
            if live_listen_enabled
            else "Listen-in off. Call audio stays on the virtual monitor (recordings still capture it)."
        )
    else:
        result.messages.append(
            "Could not change listen-in. A headless VPS often has no default speaker; "
            "use the Recordings tab to hear the call."
        )

    if chrome_profile_dir is not None:
        result.chrome_prefs_ok = patch_chrome_mic_prefs(Path(chrome_profile_dir))
        if result.chrome_prefs_ok:
            result.messages.append("Chromium profile allows the microphone for voice.google.com.")
        else:
            result.messages.append("Chromium microphone preference was not written.")

    result.ok = bool(tts_ok and call_ok)
    for msg in result.messages:
        logger.info("[AUDIO-SETUP] %s", msg)
    return result
