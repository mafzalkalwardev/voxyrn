#!/usr/bin/env bash
# PulseAudio or PipeWire for the voxyrn service user.
# Chromium and the Python process share XDG_RUNTIME_DIR=/run/voxyrn.
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/voxyrn}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# WirePlumber exits immediately without a session bus.
start_session_bus() {
  if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
    return 0
  fi
  local bus="$XDG_RUNTIME_DIR/bus"
  if [[ -S "$bus" ]]; then
    export DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}"
    return 0
  fi
  if ! command -v dbus-daemon >/dev/null 2>&1; then
    echo "dbus-daemon is not installed; WirePlumber may exit." >&2
    return 0
  fi
  dbus-daemon --session --address="unix:path=${bus}" --fork --nopidfile
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${bus}"
}

sink_exists() {
  local name="$1"
  pactl list short sinks 2>/dev/null | awk '{print $2}' | grep -qx "$name"
}

# Load once. A second module-null-sink with the same sink_name creates a duplicate.
load_awake_sink() {
  local name="$1"
  local description="$2"
  local props="device.description=${description} session.suspend-timeout-seconds=0 node.always-process=true node.passive=false node.suspend-on-idle=false"
  if sink_exists "$name"; then
    pactl suspend-sink "$name" 0 || true
    return 0
  fi
  pactl load-module module-null-sink "sink_name=${name}" "sink_properties=${props}"
  pactl suspend-sink "$name" 0 || true
}

if command -v pipewire >/dev/null 2>&1 && command -v pipewire-pulse >/dev/null 2>&1; then
  start_session_bus
  pipewire &
  pipewire_pid=$!
  sleep 0.3
  wireplumber_pid=""
  if command -v wireplumber >/dev/null 2>&1; then
    wireplumber &
    wireplumber_pid=$!
  fi
  sleep 0.3
  pipewire-pulse &
  pulse_pid=$!
  trap 'kill "$pipewire_pid" ${wireplumber_pid:+"$wireplumber_pid"} "$pulse_pid" 2>/dev/null || true' EXIT
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if pactl info >/dev/null 2>&1; then
      break
    fi
    sleep 0.3
  done
  # Unnumbered names are the single-account route, the Linux stand-in for
  # VB-CABLE "CABLE-A Input" (playback) and "CABLE-A Output" (capture).
  load_awake_sink voxyrn_to_gv Voxyrn_TTS_to_Google_Voice || true
  load_awake_sink voxyrn_from_gv Voxyrn_Google_Voice_Speaker || true
  lines="${VOXYRN_LINES:-1}"
  if ! [[ "$lines" =~ ^[0-9]+$ ]]; then
    lines=1
  fi
  if (( lines > 7 )); then
    lines=7
  fi
  for ((n = 1; n <= lines; n++)); do
    load_awake_sink "voxyrn_to_gv_${n}" "Voxyrn_TTS_line_${n}" || true
    load_awake_sink "voxyrn_from_gv_${n}" "Voxyrn_speaker_line_${n}" || true
  done
  pactl set-default-sink voxyrn_to_gv || true
  pactl set-default-source voxyrn_from_gv.monitor || true
  wait "$pulse_pid"
  exit $?
fi

if command -v pulseaudio >/dev/null 2>&1; then
  exec pulseaudio --daemonize=no --exit-idle-time=-1 --log-target=stderr
fi

echo "Neither pipewire-pulse nor pulseaudio is installed." >&2
exit 1
