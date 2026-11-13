#!/usr/bin/env bash
# One-time Google Voice sign-in on the server.
# Binds noVNC to 127.0.0.1 only. Display :99 is laneora-chrome; Voxyrn uses :98.
# From your laptop (ports come from VOXYRN_VNC_PORT and VOXYRN_NOVNC_PORT):
#   ssh -L 6081:127.0.0.1:6081 <user>@<server>
#   open http://127.0.0.1:6081/vnc.html
# Sign in, then leave the Chromium window. The profile is reused by headless runs.
set -euo pipefail
APP_DIR="${VOXYRN_APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
PROFILE_NAME="${1:-sales_profile}"
DISPLAY_NUM="${VOXYRN_DISPLAY:-${DISPLAY:-:98}}"
VNC_PORT="${VOXYRN_VNC_PORT:-5902}"
NOVNC_PORT="${VOXYRN_NOVNC_PORT:-6081}"
export DISPLAY="$DISPLAY_NUM"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/voxyrn}"
export PULSE_SERVER="${PULSE_SERVER:-unix:/run/voxyrn/pulse/native}"

for reserved in 1080 5001 9222 12525 27017; do
  if [[ "$VNC_PORT" == "$reserved" || "$NOVNC_PORT" == "$reserved" ]]; then
    echo "Refusing reserved port $reserved" >&2
    exit 1
  fi
done

CHROME="${CHROMIUM_BINARY:-}"
if [[ -z "$CHROME" ]]; then
  CHROME="$(command -v chromium || command -v chromium-browser || true)"
fi
if [[ -z "$CHROME" ]]; then
  echo "Chromium not found. Run deploy/linux/install.sh first." >&2
  exit 1
fi

PROFILE="$APP_DIR/chrome_profiles/$PROFILE_NAME"
mkdir -p "$PROFILE"

if ! pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null 2>&1; then
  Xvfb "$DISPLAY_NUM" -screen 0 1280x900x24 >/tmp/voxyrn-xvfb.log 2>&1 &
  sleep 0.5
fi

if ! ss -ltn | grep -q "127.0.0.1:${VNC_PORT} "; then
  x11vnc -display "$DISPLAY_NUM" -localhost -nopw -rfbport "$VNC_PORT" -forever \
    >/tmp/voxyrn-x11vnc.log 2>&1 &
fi

if ! ss -ltn | grep -q "127.0.0.1:${NOVNC_PORT} "; then
  websockify --web /usr/share/novnc "127.0.0.1:${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}" \
    >/tmp/voxyrn-novnc.log 2>&1 &
fi

echo "noVNC is on 127.0.0.1:${NOVNC_PORT} (SSH tunnel required)."
echo "Opening Chromium for profile ${PROFILE_NAME}. Close it after Google Voice shows the dial pad."
exec "$CHROME" \
  --user-data-dir="$PROFILE" \
  --no-first-run \
  --disable-dev-shm-usage \
  --use-fake-ui-for-media-stream \
  --autoplay-policy=no-user-gesture-required \
  "https://voice.google.com"
