"""Import a Google Voice browser profile onto the server.

Google's login often refuses a bare cookie jar (device binding, 2FA, and
httpOnly session cookies). Copying a Chromium user-data directory that has
already signed in is the reliable non-interactive path. The other path is a
one-time headed login through the localhost noVNC session in
``deploy/linux/gv-login.sh``.

This module does not bypass Google's sign-in. If the copied profile is stale,
voice.google.com will still show the login page and an operator has to finish
it in the noVNC window.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from src.paths import runtime_base


def profile_dir(profile_name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in profile_name).strip("_")
    if not safe:
        raise ValueError("Profile name is empty")
    return runtime_base() / "chrome_profiles" / safe


def import_profile(source: Path, profile_name: str, *, overwrite: bool = False) -> Path:
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Profile directory not found: {source}")
    dest = profile_dir(profile_name)
    if dest.exists():
        if not overwrite:
            raise FileExistsError(
                f"{dest} already exists. Pass --overwrite to replace it, "
                "or pick another --name."
            )
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    cookies = list(dest.rglob("Cookies"))
    marker = dest / ".gv_session_ok"
    if cookies:
        marker.write_text("imported-profile\n", encoding="utf-8")
    return dest


def import_cookie_json(cookie_file: Path, profile_name: str) -> Path:
    """Store a cookie export next to the profile for the next browser launch.

    The file is a JSON list of Selenium cookie dicts (name, value, domain).
    ``google_voice.launch`` loads it once. Google frequently rejects imported
    session cookies; treat a successful noVNC login or a profile copy as the
    supported methods.
    """
    cookie_file = Path(cookie_file).expanduser().resolve()
    raw = json.loads(cookie_file.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "cookies" in raw:
        raw = raw["cookies"]
    if not isinstance(raw, list) or not raw:
        raise ValueError("Cookie file must be a non-empty JSON list of cookies")
    dest = profile_dir(profile_name)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "imported_cookies.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    return target


def load_imported_cookies(driver, profile_dir_path: Path) -> int:
    """Add cookies previously stored by import_cookie_json. Returns how many were added."""
    target = Path(profile_dir_path) / "imported_cookies.json"
    if not target.is_file():
        return 0
    try:
        cookies = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    added = 0
    for cookie in cookies:
        if not isinstance(cookie, dict) or "name" not in cookie or "value" not in cookie:
            continue
        payload = {
            "name": str(cookie["name"]),
            "value": str(cookie["value"]),
            "domain": str(cookie.get("domain") or ".google.com"),
            "path": str(cookie.get("path") or "/"),
        }
        if cookie.get("secure") is not None:
            payload["secure"] = bool(cookie["secure"])
        if cookie.get("httpOnly") is not None:
            payload["httpOnly"] = bool(cookie["httpOnly"])
        try:
            driver.add_cookie(payload)
            added += 1
        except Exception:
            continue
    try:
        target.unlink()
    except OSError:
        pass
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a Google Voice Chromium profile or cookie export")
    sub = parser.add_subparsers(dest="cmd", required=True)

    profile = sub.add_parser("import-profile", help="Copy a Chromium user-data directory")
    profile.add_argument("--from", dest="source", required=True)
    profile.add_argument("--name", default="sales_profile")
    profile.add_argument("--overwrite", action="store_true")

    cookies = sub.add_parser("import-cookies", help="Stage a JSON cookie export (best effort)")
    cookies.add_argument("--from", dest="source", required=True)
    cookies.add_argument("--name", default="sales_profile")

    args = parser.parse_args(argv)
    if args.cmd == "import-profile":
        dest = import_profile(Path(args.source), args.name, overwrite=args.overwrite)
        print(f"Imported profile to {dest}")
        print("If Google still asks for a password, finish sign-in with deploy/linux/gv-login.sh.")
        return 0
    dest = import_cookie_json(Path(args.source), args.name)
    print(f"Staged cookies at {dest}")
    print("Google often rejects imported session cookies. Prefer import-profile or the noVNC login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
