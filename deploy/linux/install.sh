#!/usr/bin/env bash
# Install Voxyrn on Ubuntu 24.04 (including aarch64) for nginx + systemd.
# Run as root from a checkout, ideally /opt/voxyrn:
#   sudo bash deploy/linux/install.sh
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/linux/install.sh" >&2
  exit 1
fi

APP_DIR="${VOXYRN_APP_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
ARCH="$(dpkg --print-architecture)"
SKIP_LLM="${SKIP_LLM_DOWNLOAD:-0}"
export DEBIAN_FRONTEND=noninteractive

echo "Installing Voxyrn into ${APP_DIR} (${ARCH})"

apt-get update
apt-get install -y \
  python3 python3-venv python3-dev python3-pip \
  build-essential pkg-config \
  ca-certificates curl wget gnupg \
  pipewire pipewire-pulse wireplumber pulseaudio-utils ffmpeg \
  fonts-liberation espeak-ng \
  xvfb x11vnc novnc websockify \
  nginx certbot python3-certbot-nginx \
  libportaudio2 libsndfile1 \
  unzip

# Prefer a real Chromium package. Ubuntu's chromium-browser transitional
# package is a snap stub and is not used.
if ! command -v chromium >/dev/null 2>&1 || [[ -n "$(command -v chromium && readlink -f "$(command -v chromium)" | grep snap || true)" ]]; then
  if [[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]]; then
    apt-get install -y software-properties-common || true
    add-apt-repository -y ppa:xtradeb/apps || true
    apt-get update || true
    apt-get install -y chromium chromium-driver || apt-get install -y chromium || true
  fi
fi

if id voxyrn >/dev/null 2>&1; then
  echo "User voxyrn already exists"
else
  useradd --system --create-home --home-dir /var/lib/voxyrn --shell /usr/sbin/nologin voxyrn
fi
usermod -aG audio voxyrn || true

if [[ ! -d "$APP_DIR/.venv" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
if ! "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-linux.txt"; then
  echo "Full Linux requirements failed. Retrying without llama-cpp-python." >&2
  grep -v '^llama-cpp-python' "$APP_DIR/requirements-linux.txt" > /tmp/voxyrn-reqs.txt
  "$APP_DIR/.venv/bin/pip" install -r /tmp/voxyrn-reqs.txt
  echo "LLM_BACKEND=local will not work until llama-cpp-python installs." >&2
fi

# Chromium fallback: Playwright's arm64/amd64 build, never the snap.
CHROME_BIN=""
if command -v chromium >/dev/null 2>&1; then
  resolved="$(readlink -f "$(command -v chromium)" || true)"
  if [[ "$resolved" != *"/snap/"* ]]; then
    CHROME_BIN="$(command -v chromium)"
  fi
fi
if [[ -z "$CHROME_BIN" ]]; then
  echo "Distro Chromium not usable. Installing Playwright Chromium."
  "$APP_DIR/.venv/bin/pip" install "playwright>=1.49,<2"
  runuser -u voxyrn -- "$APP_DIR/.venv/bin/python" -m playwright install chromium || \
    "$APP_DIR/.venv/bin/python" -m playwright install chromium
  CHROME_BIN="$(find /var/lib/voxyrn /root /home -path '*/ms-playwright/chromium-*/chrome-linux*/chrome' 2>/dev/null | sort | tail -n 1 || true)"
fi
if [[ -z "$CHROME_BIN" || ! -x "$CHROME_BIN" ]]; then
  echo "Chromium is still missing. Set CHROMIUM_BINARY in .env after installing a non-snap build." >&2
else
  echo "Chromium: $CHROME_BIN"
fi

cd "$APP_DIR"
install -d -o voxyrn -g voxyrn "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/chrome_profiles" \
  "$APP_DIR/models/piper" "$APP_DIR/models/stt" "$APP_DIR/models/llm" "$APP_DIR/models/kokoro"

PIPER_ONNX="$APP_DIR/models/piper/en_US-lessac-medium.onnx"
if [[ ! -f "$PIPER_ONNX" ]]; then
  echo "Downloading Piper lessac medium voice"
  curl -fsSL -o "$PIPER_ONNX" \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
  curl -fsSL -o "${PIPER_ONNX}.json" \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
fi

if [[ "$SKIP_LLM" != "1" && ! -f "$APP_DIR/models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf" ]]; then
  echo "Downloading Qwen2.5 1.5B Q4 GGUF (CPU). Set SKIP_LLM_DOWNLOAD=1 to skip."
  curl -fL --retry 3 -o "$APP_DIR/models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
    "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf" \
    || echo "LLM download failed. Place the GGUF at models/llm/ or set LLM_BACKEND=groq." >&2
fi

# faster-whisper downloads base/int8 into models/stt on first use.
"$APP_DIR/.venv/bin/python" - << 'PY' || echo "Whisper base model will download on first transcription." >&2
from faster_whisper import WhisperModel
WhisperModel("base", device="cpu", compute_type="int8", download_root="models/stt")
print("faster-whisper base int8 is cached")
PY

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/deploy/linux/.env.example" "$APP_DIR/.env"
  SECRET="$("$APP_DIR/.venv/bin/python" - << 'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  sed -i "s/^VOXYRN_SECRET_KEY=.*/VOXYRN_SECRET_KEY=${SECRET}/" "$APP_DIR/.env"
  if [[ -n "$CHROME_BIN" ]]; then
    sed -i "s|^CHROMIUM_BINARY=.*|CHROMIUM_BINARY=${CHROME_BIN}|" "$APP_DIR/.env"
  fi
  DRIVER="$(command -v chromedriver || true)"
  if [[ -n "$DRIVER" ]]; then
    sed -i "s|^CHROMEDRIVER_PATH=.*|CHROMEDRIVER_PATH=${DRIVER}|" "$APP_DIR/.env"
  fi
  echo "Wrote $APP_DIR/.env — set VOXYRN_ADMIN_PASSWORD before starting."
fi

chown -R voxyrn:voxyrn "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/chrome_profiles" "$APP_DIR/models" "$APP_DIR/.venv" || true
if [[ -f "$APP_DIR/.env" ]]; then
  chown voxyrn:voxyrn "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
fi

sed "s|@APP_DIR@|${APP_DIR}|g" "$APP_DIR/deploy/linux/voxyrn.service" > /etc/systemd/system/voxyrn.service
sed "s|@APP_DIR@|${APP_DIR}|g" "$APP_DIR/deploy/linux/voxyrn-audio.service" > /etc/systemd/system/voxyrn-audio.service
sed "s|@APP_DIR@|${APP_DIR}|g" "$APP_DIR/deploy/linux/voxyrn-agent@.service" > /etc/systemd/system/voxyrn-agent@.service
chmod 755 "$APP_DIR/deploy/linux/audio-daemon.sh" "$APP_DIR/deploy/linux/gv-login.sh" "$APP_DIR/deploy/linux/install.sh"

install -d /etc/wireplumber/main.lua.d /etc/wireplumber/wireplumber.conf.d \
  /etc/wireplumber/bluetooth.lua.d /etc/pipewire/pipewire.conf.d
install -m 644 "$APP_DIR/deploy/linux/wireplumber/99-voxyrn-no-suspend.lua" \
  /etc/wireplumber/main.lua.d/99-voxyrn-no-suspend.lua
install -m 644 "$APP_DIR/deploy/linux/wireplumber/voxyrn-no-suspend.conf" \
  /etc/wireplumber/wireplumber.conf.d/voxyrn-no-suspend.conf
install -m 644 "$APP_DIR/deploy/linux/wireplumber/51-no-logind.lua" \
  /etc/wireplumber/bluetooth.lua.d/51-no-logind.lua
install -m 644 "$APP_DIR/deploy/linux/pipewire/voxyrn-sinks.conf" \
  /etc/pipewire/pipewire.conf.d/voxyrn-sinks.conf

systemctl daemon-reload
systemctl enable voxyrn-audio.service voxyrn.service
echo
echo "Next:"
echo "  1. Edit ${APP_DIR}/.env (VOXYRN_ADMIN_PASSWORD, and GROQ_API_KEY if you want cloud STT/LLM)."
echo "  2. systemctl restart voxyrn-audio voxyrn"
echo "  3. curl -fsS http://127.0.0.1:8787/healthz"
echo "  4. Install the nginx site in deploy/linux/nginx and run certbot."
echo "  5. Sign in to Google Voice once: sudo -u voxyrn bash ${APP_DIR}/deploy/linux/gv-login.sh"
echo "See deploy/linux/README.md."
