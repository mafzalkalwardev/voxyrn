"""One Google Voice account, several Chrome profiles.

Each profile is one live call. The account is not duplicated. Line 1 reuses
chrome_profiles/tony when that session already exists. Extra profiles are
tony_2, tony_3, and so on, cloned from tony the first time a run prepares
them. Displays stay on :98 (laneora owns :99). Separate displays, if turned
on, start at :100. VNC ports start at 5911/6091 so they miss 5901, 5902,
6080, and 6081.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from src.capacity import configured_line_count, sustainable_lines
from src.paths import runtime_base

MAX_LINES = 7
FORBIDDEN_DISPLAYS = {":99"}
VNC_PORT_BASE = 5910
NOVNC_PORT_BASE = 6090
RESERVED_VNC = {5901, 5902, 6080, 6081}


def line_count() -> int:
    return configured_line_count()


def set_line_count(count: int) -> int:
    count = max(1, min(MAX_LINES, int(count)))
    path = runtime_base() / "data" / "lines.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lines": count}, indent=2), encoding="utf-8")
    os.environ["VOXYRN_LINES"] = str(count)
    return count


def capacity_warning(count: int | None = None) -> str:
    count = line_count() if count is None else max(1, min(MAX_LINES, int(count)))
    limit = sustainable_lines()
    if count > limit:
        return (
            f"{count} lines is above the measured capacity of {limit} on this host. "
            "Calls can still be armed, and audio will contend for the CPUs."
        )
    return ""


def line_display(index: int) -> str:
    separate = os.getenv("VOXYRN_SEPARATE_DISPLAYS", "").strip().lower() in {"1", "true", "yes", "on"}
    if not separate:
        base = os.getenv("VOXYRN_DISPLAY", "").strip() or os.getenv("DISPLAY", "").strip() or ":98"
        if base in FORBIDDEN_DISPLAYS:
            base = ":98"
        return base
    number = 99 + max(1, index)
    if number == 99:
        number = 100
    display = f":{number}"
    if display in FORBIDDEN_DISPLAYS:
        display = ":100"
    return display


def line_ports(index: int) -> tuple[int, int]:
    vnc = VNC_PORT_BASE + index
    novnc = NOVNC_PORT_BASE + index
    if vnc in RESERVED_VNC or novnc in RESERVED_VNC:
        raise RuntimeError(f"Line {index} picked a reserved VNC port")
    return vnc, novnc


def profile_name(index: int) -> str:
    if index < 1:
        raise ValueError("lines are 1-based")
    return "tony" if index == 1 else f"tony_{index}"


def _profiles_root() -> Path:
    return runtime_base() / "chrome_profiles"


# Google Voice login writes .gv_session_ok. .voxyrn_session is the older name.
SESSION_MARKERS = (".gv_session_ok", ".voxyrn_session")


def session_ready(name: str) -> bool:
    folder = _profiles_root() / name
    return any((folder / marker).is_file() for marker in SESSION_MARKERS)


def profile_in_use(name: str) -> bool:
    """True when a Chromium process already has this profile open."""
    needle = str((_profiles_root() / name).resolve())
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(["name", "cmdline"]):
        info = proc.info or {}
        command = " ".join(info.get("cmdline") or [])
        label = f"{info.get('name') or ''} {command}".lower()
        if needle in command and "chrom" in label:
            return True
    return False


def _copy_from_tony(dest: Path) -> None:
    source = _profiles_root() / "tony"
    if not source.is_dir():
        raise RuntimeError("chrome_profiles/tony is not on this machine")
    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns("SingletonLock", "SingletonCookie", "SingletonSocket"),
    )


def ensure_profile(index: int) -> str:
    """Reuse tony. Clone it into tony_N when that folder is missing."""
    name = profile_name(index)
    dest = _profiles_root() / name
    if dest.exists():
        return name
    if index > 1 and (_profiles_root() / "tony").is_dir():
        _copy_from_tony(dest)
    return name


def ensure_line_profiles(count: int | None = None) -> list[str]:
    """Create any missing tony_N folders up to the armed line count."""
    count = line_count() if count is None else max(1, min(MAX_LINES, int(count)))
    return [ensure_profile(index) for index in range(1, count + 1)]


def reclone_profile(index: int) -> str:
    """Replace tony_N with a fresh copy of tony. Refuses while Chrome has it open."""
    if index < 2:
        raise ValueError("Line 1 is chrome_profiles/tony. Clone starts at line 2.")
    name = profile_name(index)
    if profile_in_use(name) or profile_in_use("tony"):
        raise RuntimeError("Close Chrome for tony and this line before cloning.")
    dest = _profiles_root() / name
    if dest.exists():
        shutil.rmtree(dest)
    _copy_from_tony(dest)
    return name


def signin_command(index: int) -> list[str]:
    """Chromium command that opens this profile on display :98."""
    from src.browser_launch import find_chromium_binary

    binary = find_chromium_binary()
    if not binary:
        raise RuntimeError("Chromium was not found")
    name = profile_name(index)
    profile = _profiles_root() / name
    position = f"{60 * (index - 1)},{40 * (index - 1)}"
    command = [
        binary,
        f"--user-data-dir={profile}",
        f"--window-position={position}",
        "--no-first-run",
        "--disable-dev-shm-usage",
        "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required",
        "https://voice.google.com",
    ]
    if os.getenv("VOXYRN_CHROMIUM_NO_SANDBOX", "").strip().lower() in {"1", "true", "yes", "on"}:
        command[1:1] = ["--no-sandbox", "--disable-setuid-sandbox"]
    return command


def _display_is_up(display: str) -> bool:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", f"Xvfb {display}"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0


def _ensure_signin_display(index: int) -> None:
    """Xvfb :98 plus this line's localhost VNC. :99 is left alone."""
    display = ":98"
    if not _display_is_up(display):
        if shutil.which("Xvfb"):
            subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x900x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    vnc, novnc = line_ports(index)
    if shutil.which("x11vnc"):
        subprocess.Popen(
            [
                "x11vnc",
                "-display",
                display,
                "-localhost",
                "-nopw",
                "-rfbport",
                str(vnc),
                "-forever",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    if shutil.which("websockify"):
        subprocess.Popen(
            [
                "websockify",
                "--web",
                "/usr/share/novnc",
                f"127.0.0.1:{novnc}",
                f"127.0.0.1:{vnc}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def open_signin(index: int) -> dict:
    """Open this profile's Chromium on :98 so an admin can sign in through VNC."""
    if index < 1 or index > MAX_LINES:
        raise ValueError("Line is out of range")
    name = profile_name(index)
    if profile_in_use(name):
        raise RuntimeError("Chrome is already open for this profile.")
    ensure_profile(index)
    _ensure_signin_display(index)
    env = os.environ.copy()
    env["DISPLAY"] = ":98"
    subprocess.Popen(
        signin_command(index),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    vnc, novnc = line_ports(index)
    return {
        "profile": name,
        "display": ":98",
        "vnc_port": vnc,
        "novnc_port": novnc,
    }


def line_plan(count: int | None = None) -> list[dict]:
    count = line_count() if count is None else max(1, min(MAX_LINES, int(count)))
    rows = []
    for index in range(1, count + 1):
        vnc, novnc = line_ports(index)
        name = profile_name(index)
        signed = session_ready(name)
        rows.append({
            "index": index,
            "profile_name": name,
            "gv_account": "Main line",
            "display": line_display(index),
            "window_position": f"{60 * (index - 1)},{40 * (index - 1)}",
            "vnc_port": vnc,
            "novnc_port": novnc,
            "loopback_device": f"voxyrn_to_gv_{index}",
            "capture_device": f"voxyrn_from_gv_{index}.monitor",
            "pulse_sink": f"voxyrn_from_gv_{index}",
            "pulse_source": f"voxyrn_to_gv_{index}.monitor",
            "signed_in": signed,
            "status": "ready" if signed else "needs_signin",
            "warning": capacity_warning(count),
        })
    return rows


def parallel_profiles(count: int | None = None) -> list[dict]:
    return [
        {
            "profile_name": row["profile_name"],
            "loopback_device": row["loopback_device"],
            "capture_device": row["capture_device"],
            "pulse_sink": row["pulse_sink"],
            "pulse_source": row["pulse_source"],
        }
        for row in line_plan(count)
    ]
