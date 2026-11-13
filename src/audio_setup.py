"""
Auto-configure Windows + Chrome audio for live Google Voice dials.

Single-cable route this enforces:
  TTS  → CABLE Input (playback to VB-CABLE)
  Chrome mic → CABLE Output (Ashley reaches the call)
  Chrome speaker / Windows default playback → real Speakers
  CAPTURE_DEVICE → default (WASAPI loopback of Speakers = prospect/voicemail)

Speakers can be muted for silent dialing while loopback capture + TTS
observer still write both sides into logs/recordings. Use listen-in to unmute.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("GoogleVoiceAgent")

# DEVICE_STATE_ACTIVE
_ACTIVE = 0x1
_PROP_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
_PROP_DESC = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"


@dataclass
class AudioEndpoint:
    flow: str  # "Capture" | "Render"
    guid: str
    name: str
    device_id: str
    state: int


@dataclass
class AudioSetupResult:
    ok: bool
    mic_set: bool = False
    speaker_set: bool = False
    speaker_muted: Optional[bool] = None
    chrome_prefs_ok: bool = False
    loopback_hint: str = "CABLE Input"
    capture_hint: str = "default"
    mic_name: str = ""
    speaker_name: str = ""
    messages: list[str] = field(default_factory=list)


def _flow_prefix(flow: str) -> str:
    return "{0.0.1.00000000}" if flow == "Capture" else "{0.0.0.00000000}"


def list_audio_endpoints(flow: str) -> list[AudioEndpoint]:
    """Enumerate MMDevices endpoints from the registry. Empty on non-Windows."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    root_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\{flow}"
    out: list[AudioEndpoint] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_path)
    except OSError as exc:
        logger.warning("Cannot open MMDevices %s: %s", flow, exc)
        return out

    i = 0
    while True:
        try:
            guid = winreg.EnumKey(root, i)
            i += 1
        except OSError:
            break
        try:
            key = winreg.OpenKey(root, guid)
            state = int(winreg.QueryValueEx(key, "DeviceState")[0])
            props = winreg.OpenKey(key, "Properties")
            # Prefer device friendly label ("CABLE Output") over interface
            # name ("VB-Audio Virtual Cable") so VB-CABLE matching works.
            names: list[str] = []
            for prop in (_PROP_NAME, _PROP_DESC):
                try:
                    value = str(winreg.QueryValueEx(props, prop)[0]).strip()
                    if value and value not in names:
                        names.append(value)
                except OSError:
                    continue
            name = " | ".join(names) if names else guid
            device_id = f"{_flow_prefix(flow)}.{guid}"
            out.append(
                AudioEndpoint(
                    flow=flow,
                    guid=guid,
                    name=name,
                    device_id=device_id,
                    state=state,
                )
            )
        except OSError:
            continue
    return out


def _name_score(name: str, hint: str) -> int:
    n = name.lower()
    h = hint.lower().strip()
    if not h:
        return -1
    # VB-CABLE often exposes interface name "VB-Audio Virtual Cable" plus
    # endpoint label "CABLE Output" / "CABLE Input" in the combined name.
    if "cable" in h and ("vb-audio" in n or "vb audio" in n or "cable" in n):
        if "output" in h and "output" in n:
            return 90
        # "CABLE Input", "CABLE In 16 Ch", or Speakers|VB-Audio render endpoint
        if "input" in h and ("input" in n or re.search(r"\bin\b", n)):
            return 90
        if "input" in h and "speakers" in n and ("vb-audio" in n or "vb audio" in n):
            return 85
        # Capture flow + generic VB-Audio name still counts as cable output mic
        if "output" in h and "output" not in n and "input" not in n:
            return 70
        if "input" in h and "output" not in n and "input" not in n:
            return 70
    if n == h or n.startswith(h + " ") or f"| {h.lower()}" in f"| {n}":
        return 100
    if h in n:
        score = 80
        if "point" in n:
            score -= 20
        if "16ch" in n or "16 ch" in n:
            score -= 15
        return score
    tokens = [t for t in re.split(r"[^a-z0-9]+", h) if t]
    if tokens and all(t in n for t in tokens):
        return 50
    return -1


def find_endpoint(
    flow: str,
    hint: str,
    *,
    active_only: bool = True,
    exclude_hints: list[str] | None = None,
) -> Optional[AudioEndpoint]:
    exclude = [e.lower() for e in (exclude_hints or [])]
    best: Optional[AudioEndpoint] = None
    best_score = -1
    for ep in list_audio_endpoints(flow):
        if active_only and (ep.state & 0xF) != _ACTIVE:
            continue
        low = ep.name.lower()
        if any(x and x in low for x in exclude):
            continue
        score = _name_score(ep.name, hint)
        if score > best_score:
            best = ep
            best_score = score
    return best if best_score >= 0 else None


def find_speaker_endpoint(prefer_hint: str = "Speakers") -> Optional[AudioEndpoint]:
    """Pick a real speaker output — never a VB-CABLE render device."""
    cable_bits = ("cable", "vb-audio", "vb audio")
    for hint in (prefer_hint, "Realtek", "Speakers"):
        ep = find_endpoint("Render", hint, exclude_hints=list(cable_bits))
        if ep and not any(b in ep.name.lower() for b in cable_bits):
            return ep
    for ep in list_audio_endpoints("Render"):
        if (ep.state & 0xF) != _ACTIVE:
            continue
        if any(b in ep.name.lower() for b in cable_bits):
            continue
        return ep
    return None


_PS_SET_DEFAULT = r"""
$code = @'
using System;
using System.Runtime.InteropServices;
public class AudioPolicy {
  [ComImport, Guid("F8679F50-850A-41CF-9C72-430F290290C8"),
   InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPolicyConfig {
    int Unused1();
    int Unused2();
    int Unused3();
    int Unused4();
    int Unused5();
    int Unused6();
    int Unused7();
    int Unused8();
    int Unused9();
    int Unused10();
    [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string deviceId, int role);
  }
  [ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
  class PolicyConfigClient {}
  public static int Set(string id) {
    var cfg = (IPolicyConfig)new PolicyConfigClient();
    int last = 0;
    for (int role = 0; role <= 2; role++) {
      last = cfg.SetDefaultEndpoint(id, role);
    }
    return last;
  }
}
'@
Add-Type -TypeDefinition $code -Language CSharp -ErrorAction Stop | Out-Null
[AudioPolicy]::Set('__DEVICE_ID__')
"""


def set_windows_default_device(device_id: str, label: str = "") -> None:
    """Set Windows default device for console, multimedia, and communications roles."""
    ps = _PS_SET_DEFAULT.replace("__DEVICE_ID__", device_id.replace("'", "''"))
    with tempfile.NamedTemporaryFile(
        "w", suffix=".ps1", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(ps)
        path = fh.name
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if proc.returncode != 0:
            raise OSError(proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}")
        out = (proc.stdout or "").strip().splitlines()
        hr_text = out[-1].strip() if out else "0"
        try:
            hr = int(hr_text)
        except ValueError:
            hr = 0 if not hr_text else -1
        if hr != 0:
            raise OSError(f"SetDefaultEndpoint HRESULT {hr_text}")
        logger.info(
            "Windows default audio set: %s (%s)",
            label or device_id,
            device_id,
        )
    finally:
        Path(path).unlink(missing_ok=True)


def _speakers_endpoint_volume():
    """Return pycaw EndpointVolume for the Windows default speakers."""
    from pycaw.pycaw import AudioUtilities

    return AudioUtilities.GetSpeakers().EndpointVolume


def get_default_speaker_muted() -> Optional[bool]:
    """Return True/False if default Speakers are muted, or None on failure."""
    if sys.platform != "win32":
        from src.linux_audio import linux_speakers_muted

        return linux_speakers_muted()
    try:
        return bool(_speakers_endpoint_volume().GetMute())
    except Exception as exc:
        logger.debug("get_default_speaker_muted failed: %s", exc)
        return None


def set_default_speaker_muted(muted: bool) -> bool:
    """
    Mute/unmute the Windows default playback device (Speakers).

    WASAPI loopback capture still receives call audio while Speakers are muted
    on this Realtek/Windows stack, so recordings and STT keep both sides without
    blasting the room. Toggle listen-in in Voxyrn to unmute when you want to hear.

    On Linux, muted=True removes the Pulse loopback to the default sink.
    The virtual monitor used for recordings is unchanged.
    """
    if sys.platform != "win32":
        from src.linux_audio import set_linux_listen_in

        return set_linux_listen_in(not muted)
    try:
        vol = _speakers_endpoint_volume()
        vol.SetMute(1 if muted else 0, None)
        now = bool(vol.GetMute())
        ok = now is bool(muted)
        if ok:
            logger.info("Windows default Speakers muted=%s", muted)
        else:
            logger.warning("Speaker mute mismatch: wanted=%s got=%s", muted, now)
        return ok
    except Exception as exc:
        logger.warning("set_default_speaker_muted(%s) failed: %s", muted, exc)
        return False


def patch_chrome_mic_prefs(
    profile_dir: Path,
    origin: str = "https://voice.google.com:443",
) -> bool:
    """
    Ensure the Chrome profile allows microphone for Google Voice.
    Run while Chrome is not locking Preferences (before launch).
    """
    prefs_path = Path(profile_dir) / "Default" / "Preferences"
    try:
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if prefs_path.exists():
            raw = prefs_path.read_text(encoding="utf-8")
            if raw.strip():
                data = json.loads(raw)
        profile = data.setdefault("profile", {})
        content = profile.setdefault("content_settings", {})
        exceptions = content.setdefault("exceptions", {})
        mic = exceptions.setdefault("media_stream_mic", {})
        key = f"{origin},*"
        mic[key] = {
            "last_modified": "13300000000000000",
            "setting": 1,
        }
        media = exceptions.setdefault("media_stream", {})
        media[key] = {
            "last_modified": "13300000000000000",
            "setting": {"audio": 1, "video": 2},
        }
        # Default content setting: ask→allow mic
        values = profile.setdefault("default_content_setting_values", {})
        values["media_stream_mic"] = 1
        prefs_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        logger.info("Chrome mic permission granted in prefs for %s", origin)
        return True
    except Exception as exc:
        logger.warning("Could not patch Chrome Preferences for mic: %s", exc)
        return False


def paired_or_cable(loopback_hint: str) -> str:
    from src.audio_routing import paired_capture_hint

    return paired_capture_hint(loopback_hint) or "CABLE Output"


def resolve_live_route(
    loopback_hint: str = "CABLE Input",
    capture_hint: str = "default",
) -> tuple[str, str, Optional[AudioEndpoint], Optional[AudioEndpoint]]:
    """Resolve loopback + capture hints and matching Windows endpoints."""
    from src.audio_routing import safe_capture_hint

    loopback = (loopback_hint or "CABLE Input").strip() or "CABLE Input"
    capture = safe_capture_hint(capture_hint, loopback)
    mic_hint = paired_or_cable(loopback)
    mic = find_endpoint("Capture", mic_hint)
    speaker = find_speaker_endpoint()
    return loopback, capture, mic, speaker


def ensure_live_call_audio(
    *,
    loopback_hint: str = "CABLE Input",
    capture_hint: str = "default",
    chrome_profile_dir: Path | None = None,
    set_windows_devices: bool = True,
    live_listen_enabled: bool = False,
) -> AudioSetupResult:
    """
    Configure laptop + Chrome prefs so a live dial can hear and speak correctly.
    Safe to call before every live-call session.

    When live_listen_enabled is False, Speakers are muted so you are not forced
    to hear both sides all day. Capture + recordings still get both voices.
    """
    if sys.platform != "win32":
        from src.linux_audio import ensure_linux_call_audio

        return ensure_linux_call_audio(
            loopback_hint=loopback_hint,
            capture_hint=capture_hint,
            chrome_profile_dir=chrome_profile_dir,
            set_windows_devices=set_windows_devices,
            live_listen_enabled=live_listen_enabled,
        )

    result = AudioSetupResult(ok=False)
    loopback, capture, mic, speaker = resolve_live_route(loopback_hint, capture_hint)
    result.loopback_hint = loopback
    result.capture_hint = capture

    if mic is None:
        result.messages.append(
            f"VB-CABLE capture '{paired_or_cable(loopback)}' not found. "
            "Install VB-CABLE from https://vb-audio.com/Cable/"
        )
        return result

    result.mic_name = mic.name
    if speaker:
        result.speaker_name = speaker.name

    if set_windows_devices:
        try:
            set_windows_default_device(mic.device_id, label=f"mic={mic.name}")
            result.mic_set = True
            result.messages.append(f"Windows default microphone → {mic.name}")
        except Exception as exc:
            result.messages.append(f"Failed to set Windows mic to {mic.name}: {exc}")
            logger.warning("Auto mic setup failed: %s", exc)

        if speaker is not None:
            try:
                set_windows_default_device(
                    speaker.device_id, label=f"speaker={speaker.name}"
                )
                result.speaker_set = True
                result.messages.append(f"Windows default speaker → {speaker.name}")
            except Exception as exc:
                result.messages.append(
                    f"Failed to set Windows speaker to {speaker.name}: {exc}"
                )
                logger.warning("Auto speaker setup failed: %s", exc)
        else:
            result.messages.append(
                "No non-cable Speakers device found; leave Windows playback as-is "
                "so WASAPI loopback can still hear the call."
            )

        # Silent by default: mute Speakers unless the operator wants listen-in.
        want_mute = not bool(live_listen_enabled)
        if set_default_speaker_muted(want_mute):
            result.speaker_muted = want_mute
            result.messages.append(
                "Speakers unmuted (listen-in ON)"
                if live_listen_enabled
                else "Speakers muted (silent dialing; recordings still capture both sides)"
            )
        else:
            result.messages.append("Could not change Speakers mute for listen-in control")

    if chrome_profile_dir is not None:
        result.chrome_prefs_ok = patch_chrome_mic_prefs(Path(chrome_profile_dir))
        if result.chrome_prefs_ok:
            result.messages.append("Chrome profile allows mic for voice.google.com")
        else:
            result.messages.append("Chrome Preferences mic patch skipped/failed")

    result.ok = bool(result.mic_set) or (mic is not None and not set_windows_devices)
    if result.ok:
        logger.info(
            "Live-call audio ready: TTS→%s | Chrome mic→%s | Speakers→%s | mute=%s | capture→%s",
            loopback,
            result.mic_name,
            result.speaker_name or "(unchanged)",
            result.speaker_muted,
            capture,
        )
    for msg in result.messages:
        logger.info("[AUDIO-SETUP] %s", msg)
    return result
