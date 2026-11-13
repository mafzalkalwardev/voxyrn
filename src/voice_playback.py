"""
Audio loopback injection for Google Voice calls on Windows.

Setup:
  1. Install VB-CABLE from https://vb-audio.com/Cable/
  2. Set "CABLE Input" as a playback/output device.
  3. Set "CABLE Output" as the microphone/input Chrome uses for Google Voice.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("GoogleVoiceAgent")

# PortAudio on PipeWire only lists "pulse" and "default", never the null-sink
# name. This sentinel means "play with pacat/paplay and PULSE_SINK", not a
# sounddevice index. It is intentionally outside the PortAudio index range.
PULSE_OUTPUT_INDEX = -1001
_pulse_sink_name = ""
_pulse_failure = ""


def list_audio_devices() -> list[dict]:
    """Return all audio devices visible to sounddevice."""
    try:
        import sounddevice as sd

        return [
            {
                "index": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "max_output_channels": d["max_output_channels"],
                "default_samplerate": int(d["default_samplerate"]),
            }
            for i, d in enumerate(sd.query_devices())
        ]
    except (ImportError, OSError) as exc:
        logger.warning("audio devices unavailable (%s)", exc)
        return []


def describe_audio_device(device_index: int) -> str:
    """Return a stable human-readable PortAudio device description."""
    if device_index == PULSE_OUTPUT_INDEX:
        name = _pulse_sink_name or "voxyrn_to_gv"
        return f"[pulse] {name} (PULSE_SINK, not a PortAudio device name)"
    try:
        import sounddevice as sd

        d = sd.query_devices(device_index)
        return (
            f"[{device_index}] {d['name']} "
            f"(in={d['max_input_channels']}, out={d['max_output_channels']}, "
            f"rate={int(d['default_samplerate'])})"
        )
    except Exception as exc:
        return f"[{device_index}] <unavailable: {exc}>"


def _loopback_match_score(device_name: str, name_hint: str, max_output_channels: int) -> int:
    """
    Score how well a PortAudio output device matches the configured loopback hint.

    VB-CABLE renames itself across installs / Windows builds:
      "CABLE Input (VB-Audio Virtual Cable)"
      "CABLE In 16 Ch (2- VB-Audio Virtual Cable)"
      "Speakers (VB-Audio Virtual Cable)"   ← still the cable playback endpoint
    A plain substring check for "CABLE Input" misses the "CABLE In …" form.
    """
    if max_output_channels <= 0:
        return -1
    name = (device_name or "").lower()
    hint = (name_hint or "").strip().lower()
    if not hint:
        return -1
    if hint in name:
        score = 100
        if "point" in name:
            score -= 15
        return score

    # "CABLE Output" is the mic side — never alias it to a playback device.
    if "output" in hint and "input" not in hint and " in " not in f" {hint} ":
        return -1

    wants_cable = "cable" in hint or "vb-audio" in hint or "vb audio" in hint
    if not wants_cable:
        return -1

    is_vb = "vb-audio" in name or "vb audio" in name or "cable" in name
    if not is_vb:
        return -1
    # Never treat the mic-side endpoint as TTS output.
    if "cable output" in name or name.startswith("cable output"):
        return -1

    if "cable in" in name or "cable input" in name:
        score = 95
    elif "speakers" in name and ("vb-audio" in name or "vb audio" in name):
        score = 80
    elif "output" in name and ("vb-audio" in name or "vb audio" in name) and "cable output" not in name:
        # WDM-KS "Output (VB-Audio Point)" style render endpoint
        score = 70
    else:
        return -1
    if "point" in name:
        score -= 10
    if "16ch" in name.replace(" ", "") or "16 ch" in name:
        score -= 5
    return score


def find_loopback_devices(name_hint: str = "CABLE Input") -> list[int]:
    """Return playable-output device indexes matching name_hint, best match first."""
    scored: list[tuple[int, int]] = []
    for dev in list_audio_devices():
        score = _loopback_match_score(
            str(dev.get("name") or ""),
            name_hint,
            int(dev.get("max_output_channels") or 0),
        )
        if score < 0:
            continue
        scored.append((score, int(dev["index"])))
        logger.debug(
            "Loopback device candidate score=%d: [%d] %s",
            score,
            dev["index"],
            dev["name"],
        )
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [index for _score, index in scored]


def find_loopback_device(name_hint: str = "CABLE Input") -> Optional[int]:
    """Return the best matching output device index, or None."""
    matches = find_loopback_devices(name_hint)
    if matches:
        return matches[0]
    logger.debug("No loopback device matching '%s'", name_hint)
    return None


def probe_output_device(device_index: int, duration_s: float = 0.03) -> tuple[bool, str]:
    """
    Open an output stream and write a tiny silent buffer.
    This catches "device unavailable" before the app starts dialing.
    """
    if device_index == PULSE_OUTPUT_INDEX:
        from src.linux_audio import probe_pulse_sink

        return probe_pulse_sink(_pulse_sink_name or "voxyrn_to_gv")
    try:
        import numpy as np
        import sounddevice as sd

        device = sd.query_devices(device_index)
        channels = max(1, min(int(device.get("max_output_channels") or 1), 2))
        samplerate = int(device.get("default_samplerate") or 44100)
        frames = max(1, int(samplerate * duration_s))
        silence = np.zeros((frames, channels), dtype="float32")
        with sd.OutputStream(
            device=device_index,
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
        ) as stream:
            stream.write(silence)
        return True, f"device [{device_index}] opened at {samplerate} Hz"
    except Exception as exc:
        return False, str(exc)


def pulse_failure_detail() -> str:
    return _pulse_failure


def find_playable_loopback_device(name_hint: str = "CABLE Input") -> Optional[int]:
    """Return the first matching output device that can actually be opened.

    On Linux, a ``voxyrn_*`` hint is a Pulse/PipeWire sink name. sounddevice
    cannot see that name (it lists ``pulse`` and ``default``), so the returned
    index is ``PULSE_OUTPUT_INDEX`` and playback uses pacat/paplay.
    """
    global _pulse_sink_name, _pulse_failure
    if _pulse_sink_hint(name_hint):
        from src.linux_audio import SINK_TO_GV, ensure_null_sink, probe_pulse_sink

        sink = (name_hint or "").strip()
        if sink.endswith(".monitor"):
            sink = sink[: -len(".monitor")]
        if not sink:
            sink = SINK_TO_GV
        description = (
            "Voxyrn_TTS_to_Google_Voice"
            if sink == SINK_TO_GV
            else "Voxyrn_Google_Voice_Speaker"
        )
        if not ensure_null_sink(sink, description):
            _pulse_failure = f"Pulse sink {sink} could not be created with pactl."
            logger.warning(_pulse_failure)
            return None
        ok, detail = probe_pulse_sink(sink)
        _pulse_sink_name = sink
        if not ok:
            _pulse_failure = detail
            logger.warning("Pulse sink %s is not playable: %s", sink, detail)
            return None
        _pulse_failure = ""
        logger.info("Selected Pulse sink for LOOPBACK_DEVICE='%s': %s", name_hint, detail)
        return PULSE_OUTPUT_INDEX

    for device_index in find_loopback_devices(name_hint):
        ok, detail = probe_output_device(device_index)
        if ok:
            logger.info(
                "Selected output device for LOOPBACK_DEVICE='%s': %s (%s)",
                name_hint,
                describe_audio_device(device_index),
                detail,
            )
            return device_index
        logger.warning("Loopback device [%d] is not playable: %s", device_index, detail)
    return None


def _device_default_samplerate(sd, device_index: int, fallback: int) -> int:
    try:
        device = sd.query_devices(device_index)
        return int(device.get("default_samplerate") or fallback)
    except Exception:
        return fallback


def _device_output_channels(sd, device_index: int) -> int:
    """Return a conservative output channel count for a PortAudio device."""
    try:
        device = sd.query_devices(device_index)
        return max(1, min(int(device.get("max_output_channels") or 1), 2))
    except Exception:
        return 1


def _match_output_channels(data, channels: int):
    """Return float32 audio shaped as (frames, channels)."""
    import numpy as np

    arr = np.asarray(data, dtype="float32")
    if arr.ndim == 0:
        arr = arr.reshape(1, 1)
    elif arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    elif arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1)

    if arr.shape[1] == channels:
        return np.ascontiguousarray(arr, dtype="float32")

    if arr.shape[1] > channels:
        if channels == 1:
            arr = arr.mean(axis=1, keepdims=True)
        else:
            arr = arr[:, :channels]
        return np.ascontiguousarray(arr, dtype="float32")

    mono = arr.mean(axis=1, keepdims=True)
    return np.ascontiguousarray(
        np.repeat(mono, channels, axis=1),
        dtype="float32",
    )


def _resample_audio(data, source_rate: int, target_rate: int):
    if int(source_rate) == int(target_rate):
        return data

    try:
        import numpy as np
    except ImportError:
        logger.warning(
            "numpy not available; playing at source sample rate %s instead of %s",
            source_rate,
            target_rate,
        )
        return data

    arr = np.asarray(data, dtype="float32")
    if arr.size == 0:
        return arr

    duration = len(arr) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    old_x = np.linspace(0.0, duration, num=len(arr), endpoint=False)
    new_x = np.linspace(0.0, duration, num=target_len, endpoint=False)

    if arr.ndim == 1:
        return np.interp(new_x, old_x, arr).astype("float32")

    channels = [
        np.interp(new_x, old_x, arr[:, i]).astype("float32")
        for i in range(arr.shape[1])
    ]
    return np.stack(channels, axis=1)


def _stream_audio_to_device(
    data,
    samplerate: int,
    device_index: int,
    block: bool = True,
    stop_event: Optional[threading.Event] = None,
    pulse_sink: str | None = None,
) -> None:
    """Write numpy-compatible audio to an explicit sounddevice OutputStream."""
    if device_index == PULSE_OUTPUT_INDEX:
        from src.linux_audio import play_float32_to_pulse_sink

        play_float32_to_pulse_sink(
            data,
            int(samplerate),
            pulse_sink or _pulse_sink_name or "voxyrn_to_gv",
            stop_event=stop_event,
        )
        return
    import sounddevice as sd

    target_rate = _device_default_samplerate(sd, device_index, int(samplerate))
    if target_rate != int(samplerate):
        data = _resample_audio(data, int(samplerate), target_rate)
    channels = _device_output_channels(sd, device_index)
    data = _match_output_channels(data, channels)
    duration = len(data) / float(target_rate) if target_rate else 0.0

    def _write() -> None:
        logger.info(
            "TTS playback started: device=%s duration=%.2fs rate=%d channels=%d",
            describe_audio_device(device_index),
            duration,
            target_rate,
            channels,
        )
        with sd.OutputStream(
            device=device_index,
            samplerate=target_rate,
            channels=channels,
            dtype="float32",
        ) as stream:
            chunk_size = max(1, int(target_rate * 0.05))
            for start in range(0, len(data), chunk_size):
                if stop_event is not None and stop_event.is_set():
                    logger.info("TTS playback interrupted: device=[%d]", device_index)
                    try:
                        stream.abort()
                    except Exception:
                        pass
                    break
                stream.write(data[start:start + chunk_size])
        logger.info(
            "TTS playback finished: device=[%d] duration=%.2fs",
            device_index,
            duration,
        )

    if block:
        _write()
    else:
        threading.Thread(target=_write, daemon=True, name="AudioDeviceWriter").start()


def play_wav_to_device(
    wav_path: str | Path,
    device_index: int,
    block: bool = True,
) -> float:
    """
    Play a WAV file to a specific output device by index.
    Returns playback duration in seconds.
    """
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio file not found: {wav_path}")
    if device_index == PULSE_OUTPUT_INDEX:
        from src.linux_audio import play_wav_to_pulse_sink

        return play_wav_to_pulse_sink(wav_path, _pulse_sink_name or "voxyrn_to_gv")

    import sounddevice as sd
    import soundfile as sf

    data, samplerate = sf.read(str(wav_path), dtype="float32")
    duration = len(data) / samplerate
    playback_rate = _device_default_samplerate(sd, device_index, int(samplerate))

    logger.info(
        "Playing %.1fs -> device [%d] at %s Hz: %s",
        duration,
        device_index,
        playback_rate,
        wav_path.name,
    )
    _stream_audio_to_device(data, int(samplerate), device_index, block=block)
    return duration


def _play_default_output(wav_path: str | Path) -> float:
    try:
        import sounddevice as sd
        import soundfile as sf

        data, samplerate = sf.read(str(wav_path), dtype="float32")
        sd.play(data, samplerate=samplerate)
        sd.wait()
        return len(data) / samplerate
    except ImportError:
        return _play_ffplay_fallback(wav_path)


def _pulse_sink_hint(device_hint: str) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    hint = (device_hint or "").strip().lower()
    return hint.startswith("voxyrn_")


def play_wav_loopback(
    wav_path: str | Path,
    device_hint: str = "CABLE Input",
    fallback_to_default: bool = False,
) -> float:
    """
    Play a WAV file to the virtual loopback cable so it appears as mic input.
    On Linux, hints that start with voxyrn_ are Pulse/PipeWire sink names.
    """
    if _pulse_sink_hint(device_hint):
        from src.linux_audio import play_wav_to_pulse_sink

        sink = device_hint.strip()
        if sink.endswith(".monitor"):
            sink = sink[: -len(".monitor")]
        return play_wav_to_pulse_sink(wav_path, sink)

    errors = []
    for device_index in find_loopback_devices(device_hint):
        try:
            return play_wav_to_device(wav_path, device_index, block=True)
        except Exception as exc:
            errors.append(f"[{device_index}] {exc}")
            logger.warning(
                "Loopback device [%d] failed, trying next match: %s",
                device_index,
                exc,
            )

    detail = "; ".join(errors) if errors else "no matching output device"
    if fallback_to_default:
        logger.warning(
            "Loopback device '%s' not available (%s) - playing to system default.",
            device_hint,
            detail,
        )
        return _play_default_output(wav_path)

    raise RuntimeError(
        f"Loopback device '{device_hint}' not available: {detail}\n"
        "Install or repair VB-CABLE from https://vb-audio.com/Cable/, "
        "or pass --loopback-device with the exact output device name."
    )


def _play_ffplay_fallback(wav_path: str | Path) -> float:
    """Use ffplay as a last resort if sounddevice is unavailable."""
    ffplay = shutil.which("ffplay")
    if not ffplay:
        raise RuntimeError(
            "Neither sounddevice nor ffplay is available. "
            "Run: pip install sounddevice soundfile"
        )

    wav_path = Path(wav_path)
    try:
        import soundfile as sf

        duration = sf.info(str(wav_path)).duration
    except Exception:
        duration = 0.0

    subprocess.call([ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(wav_path)])
    return duration


def print_devices() -> None:
    devices = list_audio_devices()
    if not devices:
        print("No devices found. Install sounddevice: pip install sounddevice soundfile")
        return

    print(f"{'Idx':>4}  {'Name':<45}  {'In':>3}  {'Out':>3}  {'Rate':>6}")
    print("-" * 68)
    for d in devices:
        print(
            f"{d['index']:>4}  {d['name']:<45}  "
            f"{d['max_input_channels']:>3}  {d['max_output_channels']:>3}  "
            f"{d['default_samplerate']:>6}"
        )


if __name__ == "__main__":
    print_devices()
