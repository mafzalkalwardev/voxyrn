"""Pick Chromium on Linux and Chrome on Windows.

Google Chrome has no Linux arm64 build. The server path uses a distro Chromium
or Playwright's Chromium build, plus a matching chromedriver. Snap Chromium is
rejected: the snap cannot see the app's profile directory or Pulse sockets
reliably when launched from systemd.
"""
from __future__ import annotations

import contextvars
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# Set by GoogleVoiceBrowser.launch so each Chromium gets its own sinks.
pulse_route: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "voxyrn_pulse_route",
    default=None,
)


def _is_snap(path: str) -> bool:
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = path
    lowered = resolved.replace("\\", "/").lower()
    if "/snap/" in lowered or lowered.endswith("/snap") or "/snap/bin/" in lowered:
        return True
    candidate = Path(path)
    if not candidate.is_file():
        return False
    try:
        text = candidate.read_text(encoding="utf-8", errors="ignore")[:1200].lower()
    except OSError:
        return False
    return "snap" in text and "chromium" in text


def _first_real(paths: list[Optional[str]]) -> Optional[str]:
    for path in paths:
        if not path:
            continue
        if Path(path).is_file() and not _is_snap(path):
            return path
    return None


def find_chromium_binary() -> Optional[str]:
    """Return a non-snap Chromium/Chrome binary, or None to let Selenium decide."""
    env_hits = [
        os.getenv("CHROMIUM_BINARY", "").strip(),
        os.getenv("CHROME_BINARY", "").strip(),
    ]
    found = _first_real(env_hits)
    if found:
        return found
    if not sys.platform.startswith("linux"):
        return None
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        which = shutil.which(name)
        if which and not _is_snap(which):
            return which
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        matches = sorted(cache.glob("chromium-*/chrome-linux*/chrome"))
        if matches:
            return str(matches[-1])
    return None


def find_chromedriver() -> Optional[str]:
    env = os.getenv("CHROMEDRIVER_PATH", "").strip()
    found = _first_real([env])
    if found:
        return found
    if not sys.platform.startswith("linux"):
        return None
    for name in ("chromedriver", "chromium.chromedriver", "chromium-chromedriver"):
        which = shutil.which(name)
        if which and not _is_snap(which):
            return which
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.is_dir():
        matches = sorted(cache.glob("chromium-*/chrome-linux*/chromedriver"))
        if matches:
            return str(matches[-1])
    return None


def _no_sandbox() -> bool:
    flag = os.getenv("VOXYRN_CHROMIUM_NO_SANDBOX", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def apply_browser(options) -> None:
    """Point Selenium at Chromium on Linux. Windows keeps the default Chrome."""
    binary = find_chromium_binary()
    if sys.platform.startswith("linux"):
        if not binary:
            raise RuntimeError(
                "Chromium was not found. Google Chrome has no Linux arm64 build, "
                "and the snap Chromium package is not supported. "
                "Install Chromium from Debian/xtradeb or Playwright "
                "(deploy/linux/install.sh) and set CHROMIUM_BINARY if it is not on PATH."
            )
        options.binary_location = binary
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        # Grant the mic prompt without substituting Chrome's fake microphone.
        options.add_argument("--use-fake-ui-for-media-stream")
        options.add_argument("--autoplay-policy=no-user-gesture-required")
        if _no_sandbox():
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-setuid-sandbox")
        return
    if binary:
        options.binary_location = binary


def voxyrn_display() -> str:
    """Xvfb screen for Chromium. :99 is laneora-chrome on the production host."""
    named = os.getenv("VOXYRN_DISPLAY", "").strip()
    if named:
        return named
    current = os.getenv("DISPLAY", "").strip()
    if current:
        return current
    if os.getenv("VOXYRN_SERVER", "").strip().lower() in {"1", "true", "yes", "on"}:
        return ":98"
    return ""


def chromedriver_service():
    """A Selenium Service on Linux, or None so Windows keeps webdriver-manager."""
    if not sys.platform.startswith("linux"):
        return None
    from selenium.webdriver.chrome.service import Service

    from src.linux_audio import chromium_pulse_env

    env = os.environ.copy()
    env.update(chromium_pulse_env())
    route = pulse_route.get()
    if route:
        sink, source = route
        if sink:
            env["PULSE_SINK"] = sink
        if source:
            env["PULSE_SOURCE"] = source
    display = voxyrn_display()
    if display:
        env["DISPLAY"] = display
    path = find_chromedriver()
    if path:
        return Service(executable_path=path, env=env)
    return Service(env=env)
